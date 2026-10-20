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

## 2026-10-13 — routing recalibration from yonaka (first live repo)

Replayed the 23 yonaka `route` calls through Jev (`--no-log`), each labeled with
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
variant ("sign-out redirect regression tests" → backend at 0.6–0.9). Wording
can't close it, so `route --expect <planned role>` now makes any disagreement
escalate (`mismatch`, exit 3), and the log records `expected_role`. That turns
future runs into labeled calibration data. Revisit once yonaka has about 50
`expected_role` rows.

## 2026-10-15 — work items and the done check (v0.4.0, contract v5)

yonaka ran up to 9 implementers in parallel. The parallelism held, but the
handoffs between phases didn't:

- 9 PRs shipped with no plan file until one was added later.
- qa approved before CI finished.
- Branches touching the same files were caught late.
- Nobody watched the PRs after opening them.
- The dispatch log stopped growing even though about 19 PRs were opened.

The repo asked for queues (one per phase).

Chosen for now: one state file per issue, with phases advanced only by
`dispatch.py work advance` when each phase's requirements hold. This needs no
runtime support (agents can't wait on events, so a real queue would mean
polling). The file keeps fixed fields so it can later become a queue entry.
Real queues, and possibly a kanban/issue board, wait for a UI wrapper once the
roles have settled.

"Done" is a separate check, `dispatch.py done`, with two layers:

- Fact checks are read from GitHub and decided by code.
- Judgments are scored by the dispatcher. They run only after every fact
  passes, and they can block but never pass.

This keeps a confident model from calling a PR done while its CI is red. Each
repo's owner configures both layers in `.sectorfive/done.json`, which is
validated strictly.

Live smoke test on two open yonaka PRs (read-only, `--no-log`):

- The fact checks matched what was on GitHub (a draft, a missing Greptile
  score, no recorded qa approve).
- A first judgment run blocked on "untested behavior". The 65k-char diff had
  been cut at 16k, before the test files, so Jev never saw them.
- The state now lists every changed file and gives each file a fair share of
  the diff. With that, the same PR scored 0.43 and 0.41 (both pass).
- These scores are close to their thresholds, so the defaults are starting
  points. Calibrate them from logged `done` lines against later review
  findings.
- One of the PRs reported a single CI check where its neighbor reported 22.
  That led to the `ci.required` option.

## 2026-10-16 — done check fixes from yonaka D22 (v0.4.1, contract v5.1)

yonaka's merge-readiness session (D22) used "open + Greptile 5/5 + CI green +
no conflicts" as its merge gate. It showed three gaps in the v0.4.0 done check:

- **A score after a push.** A Greptile 5/5 dropped to 4/5 after a push, but
  `botScore` took the latest score whatever commit it was for. It now counts a
  score only for the head commit. Greptile's summary names the reviewed commit,
  so `commitPattern` matches it exactly; otherwise the review's commit or the
  comment's last-edit time is used.
- **CI against an old base.** CI tests the merge with the base, so a branch
  went red twice after main moved. A new `mergeable` check, on by default,
  fails on conflicts. `maxBehind` is opt-in, because in a repo merging dozens
  of PRs a day every branch is always behind.
- **Docs-only commits.** Four markdown-only commits got no Actions runs, which
  would have left `ci` pending forever. `ci.docsOnly` lets the newest code
  commit's checks stand in, and the output names that commit.

Live read-only run (`--no-log`) on the 4 open yonaka PRs: 2 done, and 2 not
done on Greptile 3/5 and 4/5 posted for their current commit. A stacked PR
whose base branch was deleted after merging returned a 404 on the base
comparison; that comparison is now reported as unavailable instead of crashing.

Also added to the contract: two failures in a row on the same job need a
diagnosis before a third rerun, and test doubles must match the current code.

## 2026-10-20 — herdr lanes and Whitby-derived lane rules (v0.5.0, contract v5.2)

Compared Sector 5 with Whitby, a teammate's directive set for running many
issues at once. Whitby got more done because its lanes are long-lived
interactive sessions running in parallel, one issue each. Sector 5 ran every
step as a one-shot headless call, so lanes lost context between steps and
planning waited on each call. Whitby also spends more model usage, because it
pins top-tier models for every lane.

Decisions (yonaka):

- Inside herdr, one workspace per issue with a long-lived lane per role. The
  harness, model and effort still come from the role pins and the dispatcher's
  tier choice. Whitby's model pins and fast mode are not adopted.
- herdr is preferred, not required: outside herdr, headless dispatch is
  unchanged and the banner says `lanes: headless`.
- The issue's delivering lane (the last writing lane) pushes, opens and watches
  its PR. planning keeps `work advance`; qa's approve still gates the push.
  This removes PR watching from planning, which limited how many issues it
  could run at once.
- planning fans out prompts without `--wait` and fans in with `agent list` /
  `agent wait`. With `--wait` on every prompt, planning would block on one lane
  at a time.
- Lane rules taken from Whitby, kept generic: approval hygiene, built-in tools
  over scripts, `gh`, targeted tests only, check off finished issue
  requirements, no tracker issues without instruction, design system first,
  plain external text. In-role decisions come from evidence instead of asking
  planning.

Not changed: the `sectorfive-evidence` orphan branch. Some repos ban committed
screenshots; that needs its own decision. (Superseded 2026-10-20, see below:
"local evidence replaces the evidence branch".)

## 2026-10-20 — tier recalibration follow-up closed (jev hardening, contract v5.3)

Closes the spike follow-up ("tier always returned `standard`… recalibrate tier
criteria once real dispatch outcomes accumulate"). This repo has no route
history yet, and the only real dispatch data is from a private client repo
that cannot be brought in. Decision: the `TIERS` criteria stay unchanged.
v5.3 adds `dispatch.py outcome` and `dispatch.py report`, so the evidence now
accumulates here. Re-run the recalibration once this repo's
`dispatch-log.jsonl` holds at least 50 `outcome` lines.

Known follow-up (fix before the next recalibration): for tierless configs,
`report` falls back to the raw `tier` pick when `resolved_tier` is null, so
those route lines are filed under the pick rather than a resolved tier.

## 2026-10-20 — local evidence replaces the evidence branch (contract v5.5)

Supersedes the "Not changed: the `sectorfive-evidence` orphan branch" note in
the herdr lanes (contract v5.2) entry above. qa's
screenshots and recordings stay in `.sectorfive/evidence/<plan id>/`, ignored
through `.git/info/exclude`, and PRs list the file paths only. Branch mode is
removed rather than kept as a legacy option: it existed only as contract and
doc text (no dispatcher code), so removing it is the smaller change, and it
pushed screenshots to the remote, which some host repos ban.
`config.json` takes an optional `evidence` key; omit it or use `"local"`, and
any other value (including `null`) is invalid config (exit 6). Existing remote
evidence branches are left alone; deleting one is a dangerous action and the
repo owner's call.

## 2026-10-20 — shared state and locked writes for parallel lanes (contract v5.6)

`work set` and other `work` commands failed from a worktree because each
checkout had its own `.sectorfive/`. Worktrees now export `SECTORFIVE_STATE`
pointing at the main checkout's `.sectorfive/`. An empty or missing value is a
hard error (exit 2) rather than a fallback to the current checkout, since that
silent fallback is the bug being fixed. Work-item writes and log appends take
an `fcntl.flock` on a sidecar `.lock` file (work item, then log), and work
items are replaced atomically; without the lock, 8 parallel writers lost 95 of
200 updates. Locking is POSIX only and not reliable on network filesystems;
Windows is out of scope. Per-issue planner lanes are contract text only; lane
tooling waits for the orchestrator-hooks change.
