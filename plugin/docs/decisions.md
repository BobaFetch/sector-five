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
