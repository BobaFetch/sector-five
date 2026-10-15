# Dispatcher runbook

`tools/dispatch.py` (stdlib-only Python 3) calls a System One decision model.
Jev first, Clef-hosted fallback. Reference: `../contract.md` dispatcher section.

## Prerequisites

Keys from env or gitignored `.env` at the plugin root (see `.env.example` next to it):

- `TYPESAFE_API_KEY` — Jev (primary).
- `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_AUTH_TOKEN` — Clef fallback.

Examples below run from the plugin directory. In target-repo sessions, stay in
the working repo and invoke the installed dispatcher by its resolved path so
config and logs use that repo's `.sectorfive/`.

```sh
export TYPESAFE_API_KEY="<Jev key>"
# Or use Clef:
export CLOUDFLARE_ACCOUNT_ID="<Cloudflare account ID>"
export CLOUDFLARE_AUTH_TOKEN="<Cloudflare token>"
```

Or run `cp .env.example .env` and add your values to that gitignored
plugin-root `.env` (never commit it). Verify with `python3 tools/dispatch.py check`.

`--provider auto` (default) tries Jev, then Clef on error. Pin with
`--provider jev|clef` to skip fallback for route/gate. `check` probes only the
selected provider (auto selects Jev when its key is present, otherwise Clef).

## Readiness and init config

```sh
python3 tools/dispatch.py check
python3 tools/dispatch.py check --provider clef --config .sectorfive/config.json
```

`check` reports whether credentials are present (never printing them), validates
the config if there is one, and runs a minimal live probe without logging. Status
`off` skips the probe and exits 0. `--config` sets an explicit config path;
otherwise `check` looks for `.sectorfive/config.json` in the current working repo.
The provider defaults to the config's choice, or `auto` without a config.

Planning writes config after the init summary is confirmed. Its shape is:

```json
{
  "version": 1,
  "dispatcher": { "status": "on", "provider": "auto", "fallbackMode": "pinned" },
  "harness": { "mode": "multi", "roles": { "frontend": "codex/gpt-6.1-sol @ high" } }
}
```

Status is `on|pending|off`, provider `auto|jev|clef`, fallbackMode
`pinned|ask|block` (`pinned` is the init default), and harness mode `multi|single`.
Role assignments use `harness/model @ effort`. The [contract's Init interview](../contract.md#init-interview)
is the source of truth for the schema and confirmed choices.

Planning runs `check` at session start, then opens with the mode banner, e.g.
`[planning] dispatch: jev ✓ · fallback: pinned · harness: multi`.
Status `pending` re-prompts for credentials, and mode changes are always announced.

- `on`: planning routes every step before assigning its role and model. If the
  dispatcher is unavailable, the configured fallback applies: `pinned` (see
  "Record an unrouted step" below), `ask` stops for the user, `block` holds the step.
- `pending` or `off`: planning assigns roles directly and says so in the banner.

## Route a task

```sh
python3 tools/dispatch.py route --task "Add cursor pagination to /orders" \
  --plan .sectorfive/plans/004.md --min-confidence 0.6
```

Exit 0 prints role + tier + difficulty and means dispatch. Exit 3 means
role confidence is below threshold — escalate to the user, do not guess.

Pass the planned role (from `ownership.md`) with `--expect`:

```sh
python3 tools/dispatch.py route --task "Regression tests for sign-out returnTo" \
  --plan .sectorfive/plans/004.md --expect qa
```

If the pick differs at or above `--trust` (default 0.90), the verdict is
`OVERRIDE` (exit 0): follow the pick and amend `ownership.md`. Two role rules
still win over the pick: tests belong to qa, and qa never edits product code.
Below `--trust`, the verdict is `MISMATCH` (exit 3): resolve it from the
contract's role definitions or ask the user. With
`--plan`, the step text comes first and is labeled, so the plan's main domain
doesn't outweigh the step being routed.
`--format json` prints the full decision object for scripting.

## Yes/no gate

```sh
python3 tools/dispatch.py gate --state "<diff summary>" \
  --statement "This change touches files outside the assignee's ownership"
```

Exit 0 proceeds; exit 4 blocks until planning rules. Default threshold 0.5
(`--threshold` to tune). Canonical gate statements are in `contract.md`.

## Record an unrouted step

```sh
python3 tools/dispatch.py unrouted --task "Add cursor pagination to /orders" \
  --reason "Dispatcher probe failed; using pinned fallback"
```

Under `pinned` fallback, planning assigns the planned role on its pinned default,
announces the fallback, flags the step **unrouted** in the plan, and runs this
command to append an `UNROUTED` verdict to the dispatch log. It makes no provider
call. `--format json` prints the record. `--no-log` suppresses the append, so
don't use it for required fallback records. See the contract's dispatcher policy.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | routed or override (route) / proceed (gate) / ready or off (check) / recorded (unrouted) |
| 2 | usage or provider/probe error (stderr explains) |
| 3 | escalate — confidence too low, or `--expect` mismatch below `--trust` |
| 4 | blocked — gate statement holds |
| 5 | no credentials (check) |
| 6 | invalid config.json (check) |

## Outcome log

`route`, `gate`, and `unrouted` append one JSON line unless `--no-log`; `check`
never logs. The log is `./.sectorfive/dispatch-log.jsonl` when run from a working
repo that has `.sectorfive/`. Use it to recalibrate thresholds and criteria.

## Troubleshooting

- `TYPESAFE_API_KEY is not set` → export it or add the gitignored plugin-root `.env`.
- `check` exits 5 → set the selected provider's credentials and re-check.
- `check` exits 6 → re-run the init interview to confirm a valid config.
- `all providers failed` → check keys, then network, then `--timeout`.
- `provider answer missing ...` → provider changed shape; inspect with
  `--format json` and report.
