# Dispatcher runbook

`tools/dispatch.py` (stdlib-only Python 3) calls a System One decision model.
Jev first, Clef-hosted fallback. Reference: `../contract.md` dispatcher section.

## Prerequisites

Keys from env or gitignored `.env` at the plugin root (see `.env.example` next to it):

- `TYPESAFE_API_KEY` — Jev (primary).
- `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_AUTH_TOKEN` — Clef fallback.

All commands below run from the plugin directory.

`--provider auto` (default) tries Jev, then Clef on error. Pin with
`--provider jev|clef` to skip fallback.

## Route a task

```sh
python3 tools/dispatch.py route --task "Add cursor pagination to /orders" \
  --plan .sectorfive/plans/004.md --min-confidence 0.6
```

Exit 0 prints role + tier + difficulty and means dispatch. Exit 3 means
role confidence is below threshold — escalate to the user, do not guess.
`--format json` prints the full decision object for scripting.

## Yes/no gate

```sh
python3 tools/dispatch.py gate --state "<diff summary>" \
  --statement "This change touches files outside the assignee's ownership"
```

Exit 0 proceeds; exit 4 blocks until planning rules. Default threshold 0.5
(`--threshold` to tune). Canonical gate statements are in `contract.md`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | routed / proceed |
| 2 | usage or provider error (stderr explains; no keys printed) |
| 3 | escalate — confidence too low |
| 4 | blocked — gate statement holds |

## Outcome log

Unless `--no-log`, each call appends one JSON line to
`./.sectorfive/dispatch-log.jsonl` when run from a working repo that has
`.sectorfive/`. Use it to recalibrate thresholds and criteria.

## Troubleshooting

- `TYPESAFE_API_KEY is not set` → export it or add repo-root `.env`.
- `all providers failed` → check keys, then network, then `--timeout`.
- `provider answer missing ...` → provider changed shape; inspect with
  `--format json` and report.
