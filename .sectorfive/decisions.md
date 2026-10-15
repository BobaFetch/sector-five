# Decisions

## 2026-10-12: scaffold .sectorfive/ in the package repo
Bootstrapped so multi-harness work follows the contract. Lives at the repo root, outside `plugin/`, so the Muse digest is unaffected.

## 2026-10-12: ignore Jev tier for Plan 001
The user chose this: every role runs its pinned default model. The tier is logged but not acted on until the tier criteria are recalibrated (ROADMAP backlog).

## 2026-10-12: no Jev calibration data from this repo
This repo is packaging only, with no real backend or frontend, so it is not representative. Jev routing and tier calibration should be refined from a live working repo. The 6 Plan 001 route calls were removed from the log. Here planning assigns roles directly. Any Jev call in this repo uses `--no-log`, and its results are not used to change role definitions or thresholds.

## 2026-10-12: Plan 002 approved and runs before Plan 001
The user approved the init flow with fallbackMode defaulting to `pinned` (assign the pinned default, flag the step unrouted). Plan 002 goes first because live-repo testing needs these safeguards before Codex/Grok packaging.

## 2026-10-12 (late): model availability changed since pins were set
Observed: `grok models` shows a signed-in account with default `grok-4.7` (also 4.7-build-fast, 4.6, 4.5), so the grok-4.6 pin is stale and grok is no longer blocked on login. `muse model-profile show muse-wonder-1.0` resolves (catalog has it, not smoke-tested). `claude -p --model opus` → claude-opus-5-5. The existing pins came from "reply OK" smoke tests: they show each model runs, not which is best. No capability comparison exists yet.

## 2026-10-13: smoke pass over Muse and Grok candidates
All three replied `OK` to `Reply with exactly: OK` via headless harnesses: `muse exec --model muse-wonder-1.0` (default effort high), `muse exec --model muse-spark-1.3` (default effort high), `grok --single --model grok-4.7` (grok.com login, 4.7 default; 4.6/4.5 also listed). grok-4.7 supersedes the stale grok-4.6 equivalent. No pin changes: Wonder needs one supervised task cycle per the swap rule before any role pins it.

## 2026-10-13: qa pinned to muse-wonder-1.0
Two supervised QA cycles passed, both with independent verification and no invented evidence: (1) docs-only review of the equivalents update, verdict approve; (2) review of the 409-line dispatcher diff, verdict approve with three genuine non-blocking defects (pending-banner masking probe failure, float() probe-answer coercion, empty roles passing validation). Contract qa pin changed to `muse/muse-wonder-1.0 @ high` with `claude/opus @ default` as fallback. Other pins unchanged.
