#!/usr/bin/env python3
"""Sector 5 decision-model dispatcher (Jev-first, Clef-compatible fallback).

Stdlib-only. Reads keys from the environment or a gitignored `.env` at the
sector-five repo root (never committed, never printed).

Exit codes:
  0  routed (route) / proceed (gate)
  2  usage or provider error (message on stderr)
  3  escalate to the user (route confidence below threshold)
  4  blocked (gate statement holds at or above threshold)
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
    "backend": "Server logic, APIs, data models, schema, auth, infra, integrations.",
    "frontend": "Screens, components, client state, routing, forms, API wiring.",
    "polish": "Polish-only pass over a working flow: copy, spacing, a11y, tokens.",
    "qa": "Tests, reviews, verification, e2e/browser validation, veto verdicts.",
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


def cmd_route(args):
    state = args.task
    if args.plan:
        with open(args.plan) as fh:
            state += "\n\n--- plan ---\n" + fh.read()
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
    confident = float(role.get("confidence", 0.0)) >= args.min_confidence
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
        "verdict": "routed" if confident else "escalate",
    }
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
        print("verdict: %s" % decision["verdict"].upper())
    return 0 if confident else 3


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
    route.set_defaults(func=cmd_route)
    gate = sub.add_parser("gate", parents=[common],
                          help="yes/no gate over a state")
    gate.add_argument("--state", required=True)
    gate.add_argument("--statement", required=True)
    gate.add_argument("--threshold", type=float, default=0.5)
    gate.set_defaults(func=cmd_gate)
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
