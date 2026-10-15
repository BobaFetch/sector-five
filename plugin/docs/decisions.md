# Sector 5 repo decisions

Decision log for the distributed package itself (not to be confused with a
working repo's `.sectorfive/decisions.md`).

## 2026-10-12 — pinned (harness, model, effort) defaults

Smoke tests (trivial "reply OK" prompts, live this date):

| Probe | Result |
| --- | --- |
| `claude -p ... --model sonnet` | OK |
| `claude -p ... --model opus` | OK |
| `codex exec ...` (default `gpt-6.1-sol`, effort `high` from `~/.codex/config.toml`) | OK (needed unsandboxed run for app-server init) |
| `muse exec ...` (`muse-spark-1.3`, default effort high) | OK (needed unsandboxed run for session lock) |
| `grok models` | lists `grok-4.6` (default), `grok-4.5` |
| `grok -p ...` | not run — harness reports "Not signed in" (`grok login --device-code` or `XAI_API_KEY` required, user's terminal) |

Pins: planning → claude/opus; backend → codex/gpt-6.1-sol @ high;
frontend → codex/gpt-6.1-sol @ high; polish → claude/sonnet (confirm vision
input on first supervised cycle); qa → claude/opus. Equivalents:
muse/muse-spark-1.3 @ high (verified); grok/grok-4.6 @ default (provisional).

## 2026-10-12 — Jev-first dispatcher

Jev is the default provider because the maintainer holds a key and local
Clef-flash resource fit is unproven. Clef-hosted is the drop-in fallback
(same System One shape). Revisit after the local Clef-flash probe.

## 2026-10-12 — dispatcher spike results (Jev, live)

Six `route` calls + two `gate` calls via `tools/dispatch.py`:

| Task | Role (conf) | Tier (conf) | Difficulty | Verdict |
| --- | --- | --- | --- | --- |
| cursor pagination on /orders | backend (1.00) | standard (0.84) | 1.45 | ROUTED |
| search form w/ states | frontend (1.00) | standard (0.96) | 1.07 | ROUTED |
| microcopy + focus order, no behavior | polish (0.99) | standard (0.94) | 1.03 | ROUTED |
| e2e tests + scope review | qa (1.00) | standard (0.22) | 1.73 | ROUTED |
| migration backfill slugs | backend (1.00) | standard (0.82) | 1.56 | ROUTED |
| "Make the app better" | polish (0.31) | standard (0.06) | 1.70 | ESCALATE (exit 3) |

Gates: drop-table state → BLOCKED (noul 0.98, exit 4); typo-fix state →
PROCEED (noul 0.02, exit 0). `--provider clef` without credentials →
clean exit 2. Logging to `.sectorfive/dispatch-log.jsonl` verified (one
JSON line per call, no key material). Clef live fallback untested (no
Cloudflare credentials); Jev→Clef chain is covered by mocked unit tests.

Follow-up: tier always returned `standard` in the spike; recalibrate tier
criteria once real dispatch outcomes accumulate.

## 2026-10-13 — routing recalibration from the first live repo

Replayed the first live repo's 23 `route` calls through Jev (`--no-log`), each labeled with
the role that owned the step. Role question only, so confidences differ a little
from the logged runs.

| Variant | Correct | Correct and ≥ 0.60 |
| --- | --- | --- |
| v0.1 criteria (baseline) | 19/23 | 18/23 |
| ownership-first instruction | 18/23 | 15/23 (pulled polish/verify steps toward frontend; rejected) |
| v0.1 criteria + step framing | 19/23 | 18/23 |
| refined criteria | 18/23 | 18/23 |
| refined criteria + step framing (shipped) | 19/23 | 19/23 |

The gain is within noise on 23 cases. One confident misroute remained in every
variant (a regression-test step → backend at 0.6–0.9). Wording
can't close it, so `route --expect <planned role>` now makes any disagreement
escalate (`mismatch`, exit 3), and the log records `expected_role`. That turns
future runs into labeled calibration data. Revisit once a live repo has about 50
`expected_role` rows.
