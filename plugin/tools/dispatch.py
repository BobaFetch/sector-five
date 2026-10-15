#!/usr/bin/env python3
"""Sector 5 decision-model dispatcher (Jev-first, Clef-compatible fallback).

Stdlib-only. Reads keys from the environment or a gitignored `.env` at the
sector-five repo root (never committed, never printed).

Exit codes:
  0  routed or override (route) / proceed (gate) / ready or off (check) / recorded (unrouted)
  2  usage or provider error (message on stderr)
  3  escalate to the user (route confidence below threshold, or --expect mismatch below --trust)
  4  blocked (gate statement holds at or above threshold)
  5  no credentials (check)
  6  invalid config.json (check)
"""
import argparse
import datetime
import json
import os
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
    # A confident disagreement usually means the ownership map is wrong,
    # so the pick wins and planning amends the map.
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
    check.set_defaults(func=cmd_check)
    unrouted = sub.add_parser("unrouted", help="record a step assigned without dispatch")
    unrouted.add_argument("--task", required=True, help="task text (the state)")
    unrouted.add_argument("--reason", help="reason dispatch was unavailable")
    unrouted.add_argument("--format", choices=["text", "json"], default="text")
    unrouted.add_argument("--no-log", action="store_true",
                          help="do not append to .sectorfive/dispatch-log.jsonl")
    unrouted.set_defaults(func=cmd_unrouted)
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
