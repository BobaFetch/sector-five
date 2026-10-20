#!/usr/bin/env python3
"""Sector 5 decision-model dispatcher (Jev-first, Clef-compatible fallback).

Stdlib-only. Reads keys from the environment or a gitignored `.env` at the
sector-five repo root (never committed, never printed).

Exit codes:
  0  routed or override (route) / proceed (gate) / ready or off (check) /
     recorded (unrouted) / done (done) / ok (work)
  2  usage or provider error (message on stderr)
  3  escalate to the user (route confidence below threshold, --expect mismatch
     below --trust, or done judgments unavailable under onUnavailable "escalate")
  4  blocked (gate statement holds at or above threshold) / not done or blocked
     (done) / transition refused (work advance)
  5  no credentials (check)
  6  invalid config.json or done.json (check, done)
  7  pending (done: checks still running or a bot score not posted yet)
"""
import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
CLEF_MODEL = "clef-flash"
CLEF_URL_TMPL = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}"
    "/ai/run/@cf/cloudflare/clef"
)

ROLES = {
    "backend": "Server logic, APIs, data models, schema, auth, infra, integrations, "
               "telemetry, ops scripts; also fixes to these after a qa veto.",
    "frontend": "Screens, components, client state, routing, forms, API wiring; "
                "also fixes to these after a qa veto.",
    "polish": "Polish-only pass over a working flow: copy, spacing, a11y, tokens.",
    "qa": "Writing tests and regression coverage, reviews, verification, "
          "e2e/browser validation, veto verdicts.",
}
TIERS = {
    "flagship": "Hard or high-stakes work: use the role's pinned flagship model.",
    "standard": "Routine work: a mid-tier model is sufficient.",
}
DIFFICULTY = ["Trivial", "Routine", "Complex", "High-risk"]


def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def post_json(url, headers, body, timeout):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        raise RuntimeError("HTTP %s from %s: %s" % (exc.code, url, detail))
    except urllib.error.URLError as exc:
        raise RuntimeError("request to %s failed: %s" % (url, exc.reason))


def unwrap(payload):
    # Workers AI wraps decisions in a result envelope; Jev returns them bare.
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise RuntimeError("unexpected provider response shape")
    return answers


def call_jev(state, questions, timeout):
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is not set (env or .env)")
    payload = post_json(
        JEV_URL,
        {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        {"state": state, "model": JEV_MODEL, "questions": questions},
        timeout,
    )
    return unwrap(payload)


def call_clef(state, questions, timeout):
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_AUTH_TOKEN")
    if not account or not token:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_AUTH_TOKEN are not set (env or .env)"
        )
    payload = post_json(
        CLEF_URL_TMPL.format(account_id=account),
        {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        {"model": CLEF_MODEL, "state": state, "questions": questions},
        timeout,
    )
    return unwrap(payload)


def decide_provider(explicit):
    jev_ok = bool(os.environ.get("TYPESAFE_API_KEY"))
    clef_ok = bool(
        os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        and os.environ.get("CLOUDFLARE_AUTH_TOKEN")
    )
    if explicit == "jev":
        if not jev_ok:
            raise RuntimeError("TYPESAFE_API_KEY is not set (env or .env)")
        return [("jev", call_jev)]
    if explicit == "clef":
        if not clef_ok:
            raise RuntimeError("Clef credentials are not set (env or .env)")
        return [("clef", call_clef)]
    order = []
    if jev_ok:
        order.append(("jev", call_jev))
    if clef_ok:
        order.append(("clef", call_clef))
    if not order:
        raise RuntimeError("no provider credentials: set TYPESAFE_API_KEY (env or .env)")
    return order


def ask(state, questions, provider, timeout):
    last_error = None
    for name, fn in decide_provider(provider):
        try:
            return name, fn(state, questions, timeout)
        except Exception as exc:  # noqa: BLE001 - fallback chain
            last_error = exc
            if provider != "auto":
                raise
    raise RuntimeError("all providers failed: %s" % last_error)


def append_log(entry):
    d = os.path.join(os.getcwd(), ".sectorfive")
    if not os.path.isdir(d):
        return
    entry["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(os.path.join(d, "dispatch-log.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def load_config(path):
    with open(path) as fh:
        config = json.load(fh)
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    if type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("config version must be 1")
    dispatcher = config.get("dispatcher")
    if not isinstance(dispatcher, dict):
        raise ValueError("dispatcher must be an object")
    for field, choices in (
        ("status", ("on", "pending", "off")),
        ("provider", ("auto", "jev", "clef")),
        ("fallbackMode", ("pinned", "ask", "block")),
    ):
        if dispatcher.get(field) not in choices:
            raise ValueError("invalid dispatcher.%s" % field)
    harness = config.get("harness")
    if not isinstance(harness, dict):
        raise ValueError("harness must be an object")
    if harness.get("mode") not in ("multi", "single"):
        raise ValueError("invalid harness.mode")
    roles = harness.get("roles")
    if not isinstance(roles, dict):
        raise ValueError("harness.roles must be an object")
    for role, assignment in roles.items():
        if role not in ("planning", *ROLES):
            raise ValueError("invalid harness role")
        if not isinstance(assignment, str):
            raise ValueError("harness role assignment must be a string")
        model, separator, effort = assignment.partition(" @ ")
        harness_name, slash, model_name = model.partition("/")
        if not separator or not slash or not all(
            part.strip() for part in (harness_name, model_name, effort)
        ):
            raise ValueError("harness role assignment must be harness/model @ effort")
    return config


def check_banner(config, provider, ready):
    if config is None:
        return
    dispatcher = config["dispatcher"]
    status = dispatcher["status"]
    indicator = ("✓" if ready else "✗") if status == "on" else status
    print("dispatch: %s %s · fallback: %s · harness: %s" % (
        provider, indicator, dispatcher["fallbackMode"], config["harness"]["mode"]))


def cmd_check(args):
    config = None
    path = args.config if args.config is not None else os.path.join(
        os.getcwd(), ".sectorfive", "config.json")
    if args.config is not None or os.path.lexists(path):
        try:
            config = load_config(path)
        except (OSError, ValueError):
            # JSON errors can contain config contents; report no input values.
            print("dispatch: error: invalid config.json", file=sys.stderr)
            return 6
    try:
        rules, rules_path = load_rules(args.rules)
    except (OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(
            exc, json.JSONDecodeError) else "unreadable or not JSON"
        print("dispatch: error: invalid done.json: %s" % message, file=sys.stderr)
        return 6
    print("done rules: %s (%d checks, %d judgments)" % (
        rules_path or "contract defaults", len(rules.get("checks", {})),
        len(rules.get("judgments", []))))
    provider = args.provider or (config["dispatcher"]["provider"] if config else "auto")
    for key in ("TYPESAFE_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_AUTH_TOKEN"):
        print("%s: %s" % (key, "present" if os.environ.get(key) else "missing"))
    if config and config["dispatcher"]["status"] == "off":
        check_banner(config, provider, True)
        return 0
    try:
        name, call = decide_provider(provider)[0]
    except RuntimeError:
        check_banner(config, provider, False)
        print("dispatch: error: no credentials for %s" % provider, file=sys.stderr)
        return 5
    questions = {
        "check": {"type": "noul", "instructions": "Is the dispatcher available?"},
    }
    try:
        answers = call("Sector 5 dispatcher readiness check.", questions, 30.0)
        value = float(answers["check"]["noul"])
        if not 0.0 <= value <= 1.0:
            raise ValueError("invalid probe answer")
    except Exception:  # noqa: BLE001 - report no provider response or key material
        check_banner(config, name, False)
        print("dispatch: error: %s probe failed" % name, file=sys.stderr)
        return 2
    check_banner(config, name, True)
    print("provider: %s ready" % name)
    return 0


def cmd_unrouted(args):
    decision = {"kind": "unrouted", "verdict": "UNROUTED", "task": args.task}
    if args.reason is not None:
        decision["reason"] = args.reason
    if args.issue is not None:
        decision["issue"] = args.issue
    if not args.no_log:
        append_log(dict(decision, task=args.task[:2000]))
    if args.format == "json":
        print(json.dumps(decision, indent=2))
    else:
        print("verdict: UNROUTED")
    return 0


def cmd_route(args):
    state = args.task
    if args.plan:
        # Plans describe every step; label the step so the plan's dominant
        # domain doesn't outvote the step being routed.
        with open(args.plan) as fh:
            state = ("Step to route: " + args.task +
                     "\n\n--- plan context (route only the step above) ---\n" +
                     fh.read())
    questions = {
        "role": {
            "type": "choice",
            "instructions": "Which Sector 5 role should own this task?",
            "criteria": ROLES,
        },
        "tier": {
            "type": "choice",
            "instructions": "Which model tier should execute this task?",
            "criteria": TIERS,
        },
        "difficulty": {
            "type": "score",
            "instructions": "How difficult or risky is this task?",
            "criteria": DIFFICULTY,
        },
    }
    provider, answers = ask(state, questions, args.provider, args.timeout)
    role = answers["role"]
    tier = answers["tier"]
    difficulty = answers["difficulty"]
    confidence = float(role.get("confidence", 0.0))
    confident = confidence >= args.min_confidence
    disagrees = args.expect is not None and role.get("choice") != args.expect
    # A confident disagreement usually means the ownership map is wrong
    # (yonaka D18), so the pick wins and planning amends the map.
    override = disagrees and confident and confidence >= args.trust
    mismatch = disagrees and not override
    decision = {
        "kind": "route",
        "provider": provider,
        "role": role.get("choice"),
        "role_confidence": role.get("confidence"),
        "role_probabilities": role.get("probabilities"),
        "tier": tier.get("choice"),
        "tier_confidence": tier.get("confidence"),
        "difficulty": difficulty.get("score"),
        "difficulty_confidence": difficulty.get("confidence"),
        "min_confidence": args.min_confidence,
        "verdict": "override" if override else "mismatch" if mismatch else (
            "routed" if confident else "escalate"),
    }
    if args.expect is not None:
        decision["expected_role"] = args.expect
    if args.issue is not None:
        decision["issue"] = args.issue
    if not args.no_log:
        append_log(dict(decision, task=state[:2000]))
    if args.format == "json":
        print(json.dumps(decision, indent=2))
    else:
        print("provider: %s" % provider)
        print("role: %s (confidence %.2f, threshold %.2f)" % (
            decision["role"], decision["role_confidence"] or 0.0,
            args.min_confidence))
        print("tier: %s (confidence %.2f)" % (
            decision["tier"], tier.get("confidence") or 0.0))
        print("difficulty: %s (confidence %.2f)" % (
            decision["difficulty"], difficulty.get("confidence") or 0.0))
        if disagrees:
            print("expected: %s" % args.expect)
        print("verdict: %s" % decision["verdict"].upper())
    return 0 if confident and not mismatch else 3


def cmd_gate(args):
    questions = {
        "gate": {"type": "noul", "instructions": args.statement},
    }
    provider, answers = ask(args.state, questions, args.provider, args.timeout)
    value = float(answers["gate"].get("noul", 0.0))
    blocked = value >= args.threshold
    decision = {
        "kind": "gate",
        "provider": provider,
        "statement": args.statement,
        "noul": value,
        "threshold": args.threshold,
        "verdict": "blocked" if blocked else "proceed",
    }
    if not args.no_log:
        append_log(dict(decision, state=args.state[:2000]))
    if args.format == "json":
        print(json.dumps(decision, indent=2))
    else:
        print("provider: %s" % provider)
        print("noul: %.3f (threshold %.2f)" % (value, args.threshold))
        print("verdict: %s" % decision["verdict"].upper())
    return 4 if blocked else 0


# --- Work items: one state file per issue (.sectorfive/work/<issue>.json) ---

PHASES = ["planned", "building", "review", "open", "done", "merged"]
WORK_FIELDS = {
    "owner": "role", "branch": "str", "pr": "int", "plan": "str", "lane": "str",
    "qa.verdict": ("approve", "veto"), "qa.sha": "str", "qa.report": "str",
    "ci.preexisting": "list",
}
ISSUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def meta_dir():
    d = os.path.join(os.getcwd(), ".sectorfive")
    if not os.path.isdir(d):
        raise RuntimeError("no .sectorfive/ in the current directory")
    return d


def work_path(issue):
    if not ISSUE_RE.match(issue or ""):
        raise RuntimeError("invalid issue key %r" % issue)
    return os.path.join(meta_dir(), "work", issue + ".json")


def load_work(issue):
    path = work_path(issue)
    if not os.path.isfile(path):
        raise RuntimeError("no work item for %s (run: work new --issue %s)"
                           % (issue, issue))
    with open(path) as fh:
        return json.load(fh)


def save_work(item):
    path = work_path(item["issue"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(item, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def new_work(issue, plan):
    return {
        "version": 1, "issue": issue, "phase": "planned", "plan": plan,
        "owner": None, "branch": None, "pr": None, "lane": None,
        "qa": {"verdict": None, "sha": None, "report": None},
        "ci": {"preexisting": []},
        "done": None,
        "history": [{"ts": now(), "from": None, "to": "planned", "note": None}],
    }


def set_field(item, key, raw):
    kind = WORK_FIELDS.get(key)
    if kind is None:
        raise RuntimeError("unknown field %r (allowed: %s)"
                           % (key, ", ".join(sorted(WORK_FIELDS))))
    if raw == "":
        value = [] if kind == "list" else None
    elif kind == "role":
        if raw not in ROLES:
            raise RuntimeError("owner must be one of %s" % ", ".join(sorted(ROLES)))
        value = raw
    elif kind == "int":
        if not raw.isdigit():
            raise RuntimeError("%s must be a number" % key)
        value = int(raw)
    elif kind == "list":
        value = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(kind, tuple):
        if raw not in kind:
            raise RuntimeError("%s must be one of %s" % (key, ", ".join(kind)))
        value = raw
    else:
        value = raw
    target = item
    parts = key.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def log_has_dispatch(issue):
    path = os.path.join(meta_dir(), "dispatch-log.jsonl")
    if not os.path.isfile(path):
        return False
    with open(path) as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("issue") == issue and entry.get("kind") in ("route", "unrouted"):
                return True
    return False


def dispatcher_on():
    path = os.path.join(meta_dir(), "config.json")
    try:
        return load_config(path)["dispatcher"]["status"] == "on"
    except (OSError, ValueError):
        return False


def transition_problems(item, target):
    """Return the reasons `item` may not enter `target` (empty list = allowed)."""
    current = item["phase"]
    if target == "blocked":
        return []
    if target not in PHASES:
        return ["unknown phase %r" % target]
    if current == "merged":
        return ["work item is already merged"]
    if current != "blocked":
        cur, nxt = PHASES.index(current), PHASES.index(target)
        if nxt > cur + 1:
            return ["advance one phase at a time (%s → %s)" % (current, PHASES[cur + 1])]
        if nxt == cur:
            return ["already %s" % current]
    problems = []
    if not item.get("plan"):
        problems.append("no plan recorded (work set plan=<path>)")
    if target in ("building", "review", "open", "done", "merged"):
        if not item.get("owner"):
            problems.append("no owner recorded (work set owner=<role>)")
        if target == "building" and dispatcher_on() and not log_has_dispatch(item["issue"]):
            problems.append("dispatcher is on but no route/unrouted line for "
                            "this issue (route --issue %s ...)" % item["issue"])
    if target in ("review", "open", "done", "merged") and not item.get("branch"):
        problems.append("no branch recorded")
    if target in ("open", "done", "merged"):
        qa = item.get("qa") or {}
        if not item.get("pr"):
            problems.append("no PR recorded")
        if qa.get("verdict") != "approve" or not qa.get("sha"):
            problems.append("no qa approve recorded with the reviewed head SHA")
    if target in ("done", "merged"):
        done = item.get("done") or {}
        if done.get("verdict") != "done":
            problems.append("done check has not passed (run: done --issue %s)"
                            % item["issue"])
        elif done.get("sha") != (item.get("qa") or {}).get("sha"):
            problems.append("done check ran on %s but qa approved %s; re-run both "
                            "on the current head" % ((done.get("sha") or "?")[:12],
                                                     ((item.get("qa") or {}).get("sha")
                                                      or "?")[:12]))
    return problems


def closeout_entry(item):
    done = item.get("done") or {}
    facts = done.get("facts") or {}
    entry = {"kind": "closeout", "issue": item["issue"], "pr": item.get("pr"),
             "head_sha": done.get("sha")}
    for name in ("ci", "reviewThreads", "botScore"):
        if name in facts:
            entry[name] = facts[name]
    if "threads" in done:
        entry["threads"] = done["threads"]
    return entry


def cmd_work(args):
    if args.action == "new":
        path = work_path(args.issue)
        if os.path.exists(path):
            raise RuntimeError("work item %s already exists" % args.issue)
        item = new_work(args.issue, args.plan)
        save_work(item)
        print("%s: planned" % args.issue)
        return 0
    if args.action == "show":
        d = os.path.join(meta_dir(), "work")
        issues = [args.issue] if args.issue else sorted(
            name[:-5] for name in os.listdir(d) if name.endswith(".json")
        ) if os.path.isdir(d) else []
        items = [load_work(issue) for issue in issues]
        if args.format == "json":
            print(json.dumps(items if not args.issue else items[0], indent=2))
        else:
            for item in items:
                done = item.get("done") or {}
                print("%s\t%s\t%s\tPR %s\tlane %s\tdone: %s" % (
                    item["issue"], item["phase"], item.get("owner") or "-",
                    item.get("pr") or "-", item.get("lane") or "-",
                    done.get("verdict") or "-"))
        return 0
    item = load_work(args.issue)
    if args.action == "set":
        for pair in args.pairs:
            key, sep, raw = pair.partition("=")
            if not sep:
                raise RuntimeError("expected field=value, got %r" % pair)
            set_field(item, key.strip(), raw.strip())
        save_work(item)
        print("%s: updated %s" % (args.issue, ", ".join(
            p.partition("=")[0].strip() for p in args.pairs)))
        return 0
    # advance
    if args.to == "blocked" and not args.note:
        raise RuntimeError("blocking needs --note with the reason")
    problems = transition_problems(item, args.to)
    if problems:
        for problem in problems:
            print("refused: %s" % problem)
        return 4
    previous = item["phase"]
    item["phase"] = args.to
    item["history"].append({"ts": now(), "from": previous, "to": args.to,
                            "note": args.note})
    save_work(item)
    if args.to == "merged":
        append_log(closeout_entry(item))
    print("%s: %s → %s" % (args.issue, previous, args.to))
    return 0


# --- Done check: owner-configured rules in .sectorfive/done.json ---

DEFAULT_RULES = {
    "version": 1,
    "checks": {"ci": {}, "reviewThreads": {}, "qa": {}, "draft": {}, "mergeable": {}},
    "judgments": [],
}
CHECK_KEYS = {
    "ci": {"allowPreexisting": bool, "ignore": list, "required": list, "docsOnly": list},
    "reviewThreads": {"ignoreOutdated": bool},
    "botScore": {"author": str, "pattern": str, "min": (int, float),
                 "commitPattern": str},
    "planFile": {"glob": str, "caseInsensitive": bool},
    "prBody": {"sections": list},
    "qa": {},
    "draft": {},
    "mergeable": {"maxBehind": int},
}
EVIDENCE = ("plan", "pr", "qa", "reviews", "diff")
JUDGMENT_KEYS = {"id": str, "statement": str, "threshold": (int, float),
                 "evidence": list}


def validate_rules(rules):
    if not isinstance(rules, dict):
        raise ValueError("done rules must be an object")
    unknown = set(rules) - {"version", "checks", "judgments", "judgment"}
    if unknown:
        raise ValueError("unknown top-level key %s" % sorted(unknown)[0])
    if rules.get("version") != 1 or type(rules.get("version")) is not int:
        raise ValueError("version must be 1")
    checks = rules.get("checks", {})
    if not isinstance(checks, dict):
        raise ValueError("checks must be an object")
    for name, opts in checks.items():
        if name not in CHECK_KEYS:
            raise ValueError("unknown check %r" % name)
        if not isinstance(opts, dict):
            raise ValueError("checks.%s must be an object" % name)
        for key, value in opts.items():
            kind = CHECK_KEYS[name].get(key)
            if kind is None:
                raise ValueError("unknown option checks.%s.%s" % (name, key))
            if not isinstance(value, kind) or isinstance(value, bool) and kind != bool:
                raise ValueError("checks.%s.%s has the wrong type" % (name, key))
            if kind is list and not all(isinstance(v, str) for v in value):
                raise ValueError("checks.%s.%s must list strings" % (name, key))
    if "botScore" in checks:
        bot = checks["botScore"]
        for key in ("author", "pattern", "min"):
            if key not in bot:
                raise ValueError("checks.botScore.%s is required" % key)
        for key in ("pattern", "commitPattern"):
            if key not in bot:
                continue
            try:
                groups = re.compile(bot[key]).groups
            except re.error:
                raise ValueError("checks.botScore.%s is not a valid regex" % key)
            if groups < 1:
                raise ValueError("checks.botScore.%s needs a capture group" % key)
    if checks.get("mergeable", {}).get("maxBehind", 0) < 0:
        raise ValueError("checks.mergeable.maxBehind must be 0 or more")
    if "planFile" in checks and "glob" not in checks["planFile"]:
        raise ValueError("checks.planFile.glob is required")
    if "prBody" in checks and not checks["prBody"].get("sections"):
        raise ValueError("checks.prBody.sections is required")
    judgments = rules.get("judgments", [])
    if not isinstance(judgments, list):
        raise ValueError("judgments must be a list")
    seen = set()
    for j in judgments:
        if not isinstance(j, dict):
            raise ValueError("each judgment must be an object")
        for key, value in j.items():
            kind = JUDGMENT_KEYS.get(key)
            if kind is None:
                raise ValueError("unknown judgment option %r" % key)
            if not isinstance(value, kind) or isinstance(value, bool):
                raise ValueError("judgment %s has the wrong type" % key)
        if not j.get("id") or not j.get("statement"):
            raise ValueError("each judgment needs an id and a statement")
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,47}$", j["id"]) or j["id"] in seen:
            raise ValueError("judgment ids must be unique lowercase slugs")
        seen.add(j["id"])
        if not 0.0 < float(j.get("threshold", 0.5)) <= 1.0:
            raise ValueError("judgment threshold must be in (0, 1]")
        for source in j.get("evidence", EVIDENCE):
            if source not in EVIDENCE:
                raise ValueError("unknown evidence %r (use %s)"
                                 % (source, ", ".join(EVIDENCE)))
    if len(judgments) > 64:
        raise ValueError("at most 64 judgments")
    judgment = rules.get("judgment", {})
    if not isinstance(judgment, dict):
        raise ValueError("judgment must be an object")
    for key, value in judgment.items():
        if key == "onUnavailable":
            if value not in ("escalate", "facts_only"):
                raise ValueError("judgment.onUnavailable must be escalate or facts_only")
        elif key == "maxStateChars":
            if type(value) is not int or value < 1000:
                raise ValueError("judgment.maxStateChars must be an integer >= 1000")
        else:
            raise ValueError("unknown option judgment.%s" % key)
    return rules


def load_rules(path):
    """Owner rules from `path`, the contract defaults when it doesn't exist."""
    if path is None:
        path = os.path.join(os.getcwd(), ".sectorfive", "done.json")
        if not os.path.lexists(path):
            return DEFAULT_RULES, None
    with open(path) as fh:
        return validate_rules(json.load(fh)), path


def gh(*argv):
    try:
        proc = subprocess.run(["gh", *argv], capture_output=True, text=True,
                              timeout=60)
    except FileNotFoundError:
        raise RuntimeError("the done check reads PR facts with the gh CLI; "
                           "install it or pass --facts")
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh %s timed out" % argv[0])
    if proc.returncode != 0:
        raise RuntimeError("gh %s failed: %s" % (argv[0], proc.stderr.strip()[:300]))
    return proc.stdout


THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes { isResolved isOutdated
          comments(first: 1) { nodes { author { login } body } } }
      }
    }
  }
}"""


def gather_facts(pr, rules, issue):
    """Read what the done check needs about one PR from GitHub via gh."""
    view = json.loads(gh("pr", "view", str(pr), "--json",
                         "number,title,body,isDraft,headRefOid,baseRefName,"
                         "statusCheckRollup,reviews,url,files,commits,mergeable"))
    repo = json.loads(gh("repo", "view", "--json", "owner,name"))
    threads = json.loads(gh(
        "api", "graphql", "-f", "query=" + THREADS_QUERY,
        "-F", "owner=" + repo["owner"]["login"], "-F", "name=" + repo["name"],
        "-F", "number=%d" % int(pr)))
    nodes = threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    facts = {
        "pr": view["number"], "url": view.get("url"), "title": view.get("title") or "",
        "body": view.get("body") or "", "isDraft": bool(view.get("isDraft")),
        "headSha": view["headRefOid"],
        "changedFiles": ["%s (+%s -%s)" % (f.get("path"), f.get("additions"),
                                           f.get("deletions"))
                         for f in view.get("files") or []],
        "checks": [normalize_check(c) for c in view.get("statusCheckRollup") or []],
        "headCommittedAt": ((view.get("commits") or [{}])[-1]).get("committedDate"),
        "mergeable": view.get("mergeable"),
        # Oldest first, so the last score found wins. Bots edit their summary
        # comment in place, so a comment counts from its last edit.
        "comments": sorted(
            issue_comments(pr) +
            [{"author": (r.get("author") or {}).get("login", ""),
              "body": r.get("body") or "", "at": r.get("submittedAt"),
              "commit": (r.get("commit") or {}).get("oid")}
             for r in view.get("reviews") or []],
            key=lambda c: c.get("at") or ""),
        "threads": [{"resolved": t["isResolved"], "outdated": t["isOutdated"],
                     "author": ((t["comments"]["nodes"] or [{}])[0].get("author") or {})
                     .get("login", ""),
                     "body": ((t["comments"]["nodes"] or [{}])[0].get("body") or "")}
                    for t in nodes],
    }
    checks = rules.get("checks", {})
    judgments = rules.get("judgments", [])
    if "mergeable" in checks:
        try:
            facts["behindBy"] = int(gh(
                "api", "repos/{owner}/{repo}/compare/%s...%s" % (
                    view["baseRefName"], facts["headSha"]), "--jq", ".behind_by").strip())
        except (RuntimeError, ValueError):
            facts["behindBy"] = None  # e.g. the base branch was deleted
    if not facts["checks"] and checks.get("ci", {}).get("docsOnly"):
        docs_only_fallback(facts, [c["oid"] for c in view.get("commits") or []],
                           checks["ci"]["docsOnly"])
    if "planFile" in checks or any("plan" in j.get("evidence", EVIDENCE) for j in judgments):
        tree = gh("api", "repos/{owner}/{repo}/git/trees/%s?recursive=1" % facts["headSha"],
                  "--jq", ".tree[] | select(.type == \"blob\") | .path")
        facts["files"] = [line for line in tree.splitlines() if line]
        if "planFile" in checks:
            match = match_plan_file(facts["files"], checks["planFile"], issue)
            if match:
                facts["planText"] = gh(
                    "api", "-H", "Accept: application/vnd.github.raw",
                    "repos/{owner}/{repo}/contents/%s?ref=%s" % (match, facts["headSha"]))
    if any("diff" in j.get("evidence", EVIDENCE) for j in judgments):
        facts["diff"] = gh("pr", "diff", str(pr))
    return facts


def issue_comments(pr):
    out = gh("api", "--paginate", "repos/{owner}/{repo}/issues/%d/comments" % int(pr),
             "--jq", ".[] | {author: .user.login, body: .body, at: .updated_at}")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def commit_checks(sha):
    runs = gh("api", "--paginate", "repos/{owner}/{repo}/commits/%s/check-runs" % sha,
              "--jq", ".check_runs[] | {name, status, conclusion}")
    statuses = gh("api", "repos/{owner}/{repo}/commits/%s/status" % sha,
                  "--jq", ".statuses[] | {context, state}")
    return [normalize_check(json.loads(line))
            for line in (runs + "\n" + statuses).splitlines() if line.strip()]


def docs_only_fallback(facts, commits, globs):
    """The head has no checks. If every commit above the newest one with CI
    touched only docs, use that commit's checks (CI often skips docs-only pushes)."""
    skipped = 0
    for sha in reversed(commits):
        files = gh("api", "repos/{owner}/{repo}/commits/%s" % sha,
                   "--jq", ".files[].filename").split()
        if files and all(any(fnmatch.fnmatchcase(f, g) for g in globs) for f in files):
            skipped += 1
            continue
        if skipped:
            facts["checks"] = commit_checks(sha)
            facts["checksFrom"] = {"sha": sha, "docsOnlyCommits": skipped}
        return


def normalize_check(check):
    if check.get("__typename") == "StatusContext" or "context" in check:
        state = (check.get("state") or "").upper()
        status = ("pending" if state in ("PENDING", "EXPECTED", "") else
                  "success" if state == "SUCCESS" else "failure")
        return {"name": check.get("context") or "", "status": status}
    if (check.get("status") or "").upper() != "COMPLETED":
        status = "pending"
    elif (check.get("conclusion") or "").upper() in ("SUCCESS", "NEUTRAL", "SKIPPED"):
        status = "success"
    else:
        status = "failure"
    return {"name": check.get("name") or "", "status": status}


def match_plan_file(files, opts, issue):
    pattern = opts["glob"].replace("{issue}", issue)
    fold = opts.get("caseInsensitive", True)
    for path in files:
        if fnmatch.fnmatchcase(path.lower() if fold else path,
                               pattern.lower() if fold else pattern):
            return path
    return None


def score_is_stale(comment, opts, facts):
    """Return what the score was for when it predates the head, else None."""
    head = facts.get("headSha") or ""
    reviewed = None
    if opts.get("commitPattern"):
        found = re.search(opts["commitPattern"], comment["body"])
        if found:
            reviewed = found.group(1)
    reviewed = reviewed or comment.get("commit")
    if reviewed and head:
        same = head.startswith(reviewed) or reviewed.startswith(head)
        return None if same else reviewed[:12]
    at, head_at = comment.get("at"), facts.get("headCommittedAt")
    if at and head_at and at < head_at:
        return "an earlier commit (scored %s)" % at
    return None


def fact(status, detail):
    return {"status": status, "detail": detail}


def evaluate_facts(rules, facts, item):
    """Run the configured fact checks. Each result is pass, fail, or pending."""
    checks = rules.get("checks", {})
    results = {}
    if "ci" in checks:
        opts = checks["ci"]
        ignore = set(opts.get("ignore", []))
        allowed = set((item or {}).get("ci", {}).get("preexisting", [])) \
            if opts.get("allowPreexisting") else set()
        runs = [c for c in facts.get("checks", []) if c["name"] not in ignore]
        absent = sorted(set(opts.get("required", [])) - {c["name"] for c in runs})
        pending = [c["name"] for c in runs if c["status"] == "pending"]
        failed = [c["name"] for c in runs
                  if c["status"] == "failure" and c["name"] not in allowed]
        if not runs:
            results["ci"] = fact("pending", "no checks reported yet")
        elif failed:
            results["ci"] = fact("fail", "failed: " + ", ".join(sorted(failed)))
        elif pending:
            results["ci"] = fact("pending", "running: " + ", ".join(sorted(pending)))
        elif absent:
            # Every reported check finished, so a missing required one never ran.
            results["ci"] = fact("fail", "required checks missing: " + ", ".join(absent))
        else:
            excused = sorted(c["name"] for c in runs if c["status"] == "failure")
            results["ci"] = fact("pass", "%d checks passed%s" % (
                len(runs) - len(excused),
                "; pre-existing failures: " + ", ".join(excused) if excused else ""))
        source = facts.get("checksFrom")
        if source:
            results["ci"]["detail"] += " (on %s; %d docs-only commit(s) above had no CI)" % (
                source["sha"][:12], source["docsOnlyCommits"])
    if "reviewThreads" in checks:
        skip_outdated = checks["reviewThreads"].get("ignoreOutdated", False)
        open_threads = [t for t in facts.get("threads", [])
                        if not t["resolved"] and not (skip_outdated and t["outdated"])]
        results["reviewThreads"] = fact(
            "fail" if open_threads else "pass",
            "%d unresolved" % len(open_threads) if open_threads else
            "%d threads, all resolved" % len(facts.get("threads", [])))
    if "botScore" in checks:
        opts = checks["botScore"]
        pattern = re.compile(opts["pattern"])
        score = scored = None
        for comment in facts.get("comments", []):
            if opts["author"].lower() in comment["author"].lower():
                found = pattern.search(comment["body"])
                if found:
                    try:
                        score, scored = float(found.group(1)), comment
                    except ValueError:
                        pass
        stale = score is not None and score_is_stale(scored, opts, facts)
        if score is None:
            results["botScore"] = fact("pending", "no %s score posted" % opts["author"])
        elif stale:
            # A new push resets the review: wait for the bot to score this head.
            results["botScore"] = fact("pending", "score %g is for %s, head is %s" % (
                score, stale, (facts.get("headSha") or "?")[:12]))
        else:
            results["botScore"] = fact(
                "pass" if score >= opts["min"] else "fail",
                "score %g (min %g)" % (score, opts["min"]))
    if "planFile" in checks:
        issue = (item or {}).get("issue") or facts.get("issue", "")
        match = match_plan_file(facts.get("files", []), checks["planFile"], issue)
        results["planFile"] = fact("pass", match) if match else fact(
            "fail", "no file matches %s on the head commit"
            % checks["planFile"]["glob"].replace("{issue}", issue))
    if "prBody" in checks:
        body = facts.get("body", "")
        missing = [name for name in checks["prBody"]["sections"]
                   if not re.search(r"(?im)^\s*(?:#{1,6}\s*|\*\*)?\s*" + re.escape(name)
                                    + r"\b", body)]
        results["prBody"] = fact("fail", "missing: " + ", ".join(missing)) if missing \
            else fact("pass", "all %d sections present" % len(checks["prBody"]["sections"]))
    if "qa" in checks:
        qa = (item or {}).get("qa") or {}
        if qa.get("verdict") != "approve":
            results["qa"] = fact("fail", "no qa approve recorded")
        elif qa.get("sha") != facts.get("headSha"):
            results["qa"] = fact("fail", "qa approved %s, head is %s" % (
                (qa.get("sha") or "?")[:12], (facts.get("headSha") or "?")[:12]))
        else:
            results["qa"] = fact("pass", "approve on head")
    if "mergeable" in checks:
        state = (facts.get("mergeable") or "UNKNOWN").upper()
        behind = facts.get("behindBy")
        limit = checks["mergeable"].get("maxBehind")
        if state == "CONFLICTING":
            results["mergeable"] = fact("fail", "conflicts with the base branch")
        elif state != "MERGEABLE":
            results["mergeable"] = fact("pending", "GitHub is still computing mergeability")
        elif limit is not None and behind is None:
            results["mergeable"] = fact("pending", "could not compare with the base branch")
        elif limit is not None and behind > limit:
            results["mergeable"] = fact("fail", "%d commits behind base (max %d): merge "
                                        "the base in and let CI run again" % (behind, limit))
        else:
            results["mergeable"] = fact("pass", "no conflicts" + (
                "; %d commits behind base" % behind if behind else ""))
    if "draft" in checks:
        results["draft"] = fact("fail", "PR is a draft") if facts.get("isDraft") \
            else fact("pass", "ready for review")
    return results


def judgment_state(issue, facts, item, sources, limit):
    parts = ["Work item: %s" % issue]
    if "plan" in sources:
        plan = facts.get("planText")
        local = (item or {}).get("plan")
        if not plan and local and os.path.isfile(local):
            with open(local) as fh:
                plan = fh.read()
        if plan:
            parts.append("--- plan ---\n" + plan)
    if "pr" in sources:
        parts.append("--- pull request ---\n%s\n\n%s" % (facts.get("title", ""),
                                                        facts.get("body", "")))
    if "qa" in sources:
        report = ((item or {}).get("qa") or {}).get("report")
        if report and os.path.isfile(report):
            with open(report) as fh:
                parts.append("--- qa report ---\n" + fh.read())
    if "reviews" in sources:
        notes = [t["author"] + ": " + t["body"] for t in facts.get("threads", [])]
        notes += [c["author"] + ": " + c["body"] for c in facts.get("comments", [])]
        if notes:
            parts.append("--- review comments ---\n" + "\n\n".join(notes))
    if "diff" in sources and facts.get("changedFiles"):
        parts.append("--- changed files ---\n" + "\n".join(facts["changedFiles"]))
    state = "\n\n".join(parts)
    if len(state) > limit:
        return state[:limit] + "\n[truncated]"
    if "diff" in sources and facts.get("diff"):
        state += "\n\n--- diff ---\n" + fit_diff(facts["diff"], limit - len(state) - 20)
    return state


def fit_diff(diff, budget):
    """Trim a diff to `budget` chars, giving every file a fair share so the
    files late in the diff (often the tests) are never cut off entirely."""
    if len(diff) <= budget:
        return diff
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    chunks = [c for c in chunks if c]
    if budget <= 0 or not chunks:
        return "[diff omitted: no room left in the state]"
    sizes = {i: len(c) for i, c in enumerate(chunks)}
    remaining = budget
    # Small files keep everything; the leftover is split among the large ones.
    order = sorted(sizes, key=sizes.get)
    caps = {}
    for n, i in enumerate(order):
        share = remaining // (len(order) - n)
        caps[i] = min(sizes[i], share)
        remaining -= caps[i]
    out = []
    for i, chunk in enumerate(chunks):
        if caps[i] < len(chunk):
            out.append(chunk[:max(caps[i] - 12, 0)] + "\n[trimmed]\n")
        else:
            out.append(chunk)
    return "".join(out)


def cmd_done(args):
    try:
        rules, rules_path = load_rules(args.rules)
    except (OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(
            exc, json.JSONDecodeError) else "unreadable or not JSON"
        print("dispatch: error: invalid done.json: %s" % message, file=sys.stderr)
        return 6
    if not ISSUE_RE.match(args.issue):
        raise RuntimeError("invalid issue key %r" % args.issue)
    item = None
    try:
        item = load_work(args.issue)
    except RuntimeError:
        if args.pr is None and args.facts is None:
            raise
    pr = args.pr if args.pr is not None else (item or {}).get("pr")
    if args.facts is not None:
        with open(args.facts) as fh:
            facts = json.load(fh)
    elif pr is None:
        raise RuntimeError("no PR for %s (pass --pr or work set pr=<n>)" % args.issue)
    else:
        facts = gather_facts(pr, rules, args.issue)
    facts.setdefault("issue", args.issue)
    results = evaluate_facts(rules, facts, item)
    statuses = {r["status"] for r in results.values()}
    judged = {}
    if "fail" in statuses:
        verdict = "not_done"
    elif "pending" in statuses:
        verdict = "pending"
    elif not rules.get("judgments"):
        verdict = "done"
    else:
        settings = rules.get("judgment", {})
        limit = settings.get("maxStateChars", 16000)
        questions = {}
        for j in rules["judgments"]:
            questions[j["id"]] = {"type": "noul", "instructions": j["statement"]}
        # One state per distinct evidence set keeps each statement on what it asked for.
        groups = {}
        for j in rules["judgments"]:
            groups.setdefault(tuple(j.get("evidence", EVIDENCE)), []).append(j)
        provider = None
        try:
            for sources, members in groups.items():
                state = judgment_state(args.issue, facts, item, sources, limit)
                provider, answers = ask(
                    state, {j["id"]: questions[j["id"]] for j in members},
                    args.provider, args.timeout)
                for j in members:
                    value = float(answers[j["id"]].get("noul", 0.0))
                    threshold = float(j.get("threshold", 0.5))
                    judged[j["id"]] = {"noul": value, "threshold": threshold,
                                       "blocked": value >= threshold}
            verdict = "blocked" if any(v["blocked"] for v in judged.values()) else "done"
        except (RuntimeError, KeyError, ValueError, TypeError) as exc:
            if settings.get("onUnavailable", "escalate") == "facts_only":
                verdict = "done"
                judged = {"skipped": "dispatcher unavailable: %s" % str(exc)[:200]}
            else:
                verdict = "escalate"
                judged = {"error": "dispatcher unavailable: %s" % str(exc)[:200]}
    threads = facts.get("threads", [])
    authors = {}
    for t in threads:
        authors[t["author"]] = authors.get(t["author"], 0) + 1
    decision = {
        "kind": "done", "issue": args.issue, "pr": facts.get("pr", pr),
        "head_sha": facts.get("headSha"), "rules": rules_path or "defaults",
        "verdict": verdict, "facts": results, "judgments": judged,
        "threads": {"total": len(threads), "by_author": authors},
    }
    if not args.no_log:
        append_log(dict(decision))
    if item is not None:
        item["done"] = {"verdict": verdict, "sha": facts.get("headSha"), "ts": now(),
                        "facts": results, "judgments": judged,
                        "threads": decision["threads"]}
        save_work(item)
    if args.format == "json":
        print(json.dumps(decision, indent=2))
    else:
        print("rules: %s" % decision["rules"])
        for name, r in results.items():
            print("%-18s %-7s %s" % (name, r["status"].upper(), r["detail"]))
        for name, j in judged.items():
            if isinstance(j, dict):
                print("%-18s %-7s noul %.3f (threshold %.2f)" % (
                    name, "BLOCKED" if j["blocked"] else "PASS", j["noul"], j["threshold"]))
            else:
                print("%-18s %s" % (name, j))
        print("verdict: %s" % verdict.upper())
    return {"done": 0, "escalate": 3, "pending": 7}.get(verdict, 4)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", choices=["auto", "jev", "clef"],
                        default="auto", help="decision provider (default: auto)")
    common.add_argument("--timeout", type=float, default=30.0,
                        help="per-request seconds (default: 30)")
    common.add_argument("--format", choices=["text", "json"], default="text")
    common.add_argument("--no-log", action="store_true",
                        help="do not append to .sectorfive/dispatch-log.jsonl")
    sub = parser.add_subparsers(dest="command", required=True)
    route = sub.add_parser("route", parents=[common],
                           help="route a task to a role + tier")
    route.add_argument("--task", required=True, help="task text (the state)")
    route.add_argument("--plan", help="optional plan file appended to the state")
    route.add_argument("--issue", help="work item key, logged so work advance can "
                                       "confirm the step was routed")
    route.add_argument("--min-confidence", type=float, default=0.60)
    route.add_argument("--expect", choices=sorted(ROLES),
                       help="planned role from ownership; a different pick escalates "
                            "unless its confidence reaches --trust")
    route.add_argument("--trust", type=float, default=0.90,
                       help="confidence at which a pick overrides --expect (default: 0.90)")
    route.set_defaults(func=cmd_route)
    gate = sub.add_parser("gate", parents=[common],
                          help="yes/no gate over a state")
    gate.add_argument("--state", required=True)
    gate.add_argument("--statement", required=True)
    gate.add_argument("--threshold", type=float, default=0.5)
    gate.set_defaults(func=cmd_gate)
    check = sub.add_parser("check", help="check credentials and probe without logging")
    check.add_argument("--provider", choices=["auto", "jev", "clef"],
                       help="decision provider (default: config provider or auto)")
    check.add_argument("--config", help="config path (default: .sectorfive/config.json)")
    check.add_argument("--rules", help="done rules path (default: .sectorfive/done.json)")
    check.set_defaults(func=cmd_check)
    unrouted = sub.add_parser("unrouted", help="record a step assigned without dispatch")
    unrouted.add_argument("--task", required=True, help="task text (the state)")
    unrouted.add_argument("--reason", help="reason dispatch was unavailable")
    unrouted.add_argument("--issue", help="work item key")
    unrouted.add_argument("--format", choices=["text", "json"], default="text")
    unrouted.add_argument("--no-log", action="store_true",
                          help="do not append to .sectorfive/dispatch-log.jsonl")
    unrouted.set_defaults(func=cmd_unrouted)
    done = sub.add_parser("done", parents=[common],
                          help="check a work item against the repo's done rules")
    done.add_argument("--issue", required=True, help="work item key, e.g. ENG-123")
    done.add_argument("--pr", type=int, help="PR number (default: the work item's)")
    done.add_argument("--rules", help="rules path (default: .sectorfive/done.json, "
                                      "else the contract defaults)")
    done.add_argument("--facts", help="read PR facts from this JSON file instead of gh")
    done.set_defaults(func=cmd_done)
    work = sub.add_parser("work", help="per-issue work item state")
    work_sub = work.add_subparsers(dest="action", required=True)
    w_new = work_sub.add_parser("new", help="create a work item in phase planned")
    w_new.add_argument("--issue", required=True)
    w_new.add_argument("--plan", help="durable plan path")
    w_set = work_sub.add_parser("set", help="record fields: owner, branch, pr, plan, lane, "
                                            "qa.verdict, qa.sha, qa.report, ci.preexisting")
    w_set.add_argument("--issue", required=True)
    w_set.add_argument("pairs", nargs="+", metavar="field=value")
    w_adv = work_sub.add_parser("advance", help="move to a phase if its requirements hold")
    w_adv.add_argument("--issue", required=True)
    w_adv.add_argument("--to", required=True, choices=PHASES + ["blocked"])
    w_adv.add_argument("--note", help="reason or context (required for blocked)")
    w_show = work_sub.add_parser("show", help="list work items, or show one")
    w_show.add_argument("--issue")
    w_show.add_argument("--format", choices=["text", "json"], default="text")
    work.set_defaults(func=cmd_work)
    return parser


def main(argv=None):
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print("dispatch: error: %s" % exc, file=sys.stderr)
        return 2
    except KeyError as exc:
        print("dispatch: error: provider answer missing %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
