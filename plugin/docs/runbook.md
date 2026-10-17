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

## Work items

One state file per issue at `.sectorfive/work/<issue>.json`. Issue keys are
letters, digits, `.`, `_` and `-` (for example `ENG-123`).

```sh
python3 tools/dispatch.py work new --issue ENG-123 --plan .sectorfive/plans/eng-123.md
python3 tools/dispatch.py work set --issue ENG-123 owner=frontend branch=fix/eng-123-x
python3 tools/dispatch.py route --task "..." --plan .sectorfive/plans/eng-123.md \
  --expect frontend --issue ENG-123
python3 tools/dispatch.py work advance --issue ENG-123 --to building
python3 tools/dispatch.py work show
```

Fields `work set` accepts:

| Field | Value |
| --- | --- |
| `owner` | `backend`, `frontend`, `polish` or `qa` |
| `branch`, `plan` | text |
| `pr` | PR number |
| `qa.verdict` | `approve` or `veto` |
| `qa.sha` | the head SHA qa reviewed |
| `qa.report` | path to qa's report (used as judgment evidence) |
| `ci.preexisting` | comma-separated check names proven to fail on the base too |

An empty value (`pr=`) clears a field. `work advance --to <phase>` refuses
(exit 4) and prints each missing requirement. The requirements per phase are
in the contract's Work items section. `work show --format json` prints the
raw state.

## Done check

```sh
python3 tools/dispatch.py done --issue ENG-123          # PR from the work item
python3 tools/dispatch.py done --issue ENG-123 --pr 740 # explicit PR
python3 tools/dispatch.py done --issue ENG-123 --facts facts.json  # no gh
```

The check reads the PR with the `gh` CLI from the working repo: checks,
review threads, comments and reviews, body, draft state, the head commit's
file tree, and the diff when a judgment uses it. `--facts` takes the same
data as a JSON file, for hosts other than GitHub or for replaying a decision.

| Verdict | Exit | Meaning |
| --- | --- | --- |
| `done` | 0 | every fact passed and no judgment blocked |
| `not_done` | 4 | a fact check failed (judgments are skipped) |
| `blocked` | 4 | a judgment scored at or above its threshold |
| `pending` | 7 | a check is still running or the review bot hasn't scored; re-run later |
| `escalate` | 3 | judgments couldn't run (dispatcher unavailable) and `onUnavailable` is `escalate` |

The result is logged as a `done` line and stored on the work item, so
`work advance --to done` can require it.

### `done.json` reference

The repo owner writes `.sectorfive/done.json`. Everything in it is optional
except `version`, and only the checks listed under `checks` run. Without the
file, the defaults are:

```json
{ "version": 1, "checks": { "ci": {}, "reviewThreads": {}, "qa": {}, "draft": {}, "mergeable": {} } }
```

A fuller example:

```json
{
  "version": 1,
  "checks": {
    "ci": {
      "required": ["build", "test"],
      "ignore": ["preview-deploy"],
      "allowPreexisting": true,
      "docsOnly": [".plans/**", "*.md", "**/*.md"]
    },
    "reviewThreads": { "ignoreOutdated": false },
    "botScore": {
      "author": "greptile",
      "pattern": "greptile_confidence_score:(\\d+)",
      "min": 5,
      "commitPattern": "Last reviewed commit:.*?/commit/([0-9a-f]{7,40})"
    },
    "mergeable": { "maxBehind": 50 },
    "planFile": { "glob": ".plans/done/*{issue}*.md" },
    "prBody": { "sections": ["Summary", "Resolves", "Plan", "Verification"] },
    "qa": {},
    "draft": {}
  },
  "judgments": [
    {
      "id": "done-criteria",
      "statement": "The diff leaves at least one of the plan's done criteria unmet.",
      "threshold": 0.5,
      "evidence": ["plan", "pr", "diff"]
    },
    {
      "id": "untested-behavior",
      "statement": "A behavior change in this diff has no test covering it.",
      "threshold": 0.6,
      "evidence": ["diff"]
    }
  ],
  "judgment": { "onUnavailable": "escalate", "maxStateChars": 16000 }
}
```

**Checks**

| Check | Options | Passes when |
| --- | --- | --- |
| `ci` | `required` (check names that must be present), `ignore` (names to skip), `allowPreexisting` (bool, default false), `docsOnly` (path globs) | every reported check finished green, and every `required` check is present. With `allowPreexisting`, failures named in the work item's `ci.preexisting` are excused. With `docsOnly`, when the head has no checks and every commit above the newest code commit only touched matching paths, that code commit's checks count (the output names it). No checks yet or any still running → pending. |
| `reviewThreads` | `ignoreOutdated` (bool, default false) | no unresolved review threads |
| `botScore` | `author` (substring of the bot's login), `pattern` (regex with one capture group for the score), `min` (number), all required; `commitPattern` (regex capturing the SHA the bot says it reviewed) | the bot's latest score is at least `min` and is for the head commit. The reviewed commit comes from `commitPattern`, else from the review's own commit, else from comparing the comment's last edit with the head commit's time. No score yet, or a score for an older commit → pending. |
| `mergeable` | `maxBehind` (integer, optional) | GitHub reports no conflicts with the base, and the head is at most `maxBehind` commits behind it when that is set. Still computing → pending. Set `maxBehind` when CI must have run against a recent base; leave it out in busy repos where every branch is always behind. |
| `planFile` | `glob` (required; `{issue}` is replaced by the key), `caseInsensitive` (bool, default true) | a file on the PR's head commit matches |
| `prBody` | `sections` (required list) | each name starts a line of the body, as a heading (`## Plan`), bold (`**Plan**`) or plain text (`Resolves ENG-123`) |
| `qa` | none | the work item has qa `approve` with `qa.sha` equal to the PR head |
| `draft` | none | the PR is not a draft |

**Judgments** (up to 64), asked only when every check passes:

| Key | Meaning |
| --- | --- |
| `id` | unique lowercase slug, shown in output and logs |
| `statement` | a statement of a *problem*. The dispatcher scores how likely it holds (`noul`), so a high score blocks |
| `threshold` | block at or above this score, in (0, 1]; default 0.5 |
| `evidence` | what the dispatcher sees: any of `plan`, `pr`, `qa`, `reviews`, `diff`; default all |

Phrase statements as objections ("…leaves a done criterion unmet"), not as
approvals. Each distinct evidence set is one dispatcher request.
`maxStateChars` (default 16000) caps that request's state. The plan, PR, qa
report and review comments come first, then a list of every changed file.
The diff fills what's left, with each file trimmed to a fair share so files
late in the diff, often the tests, are never cut off.

`judgment.onUnavailable` is `escalate` (default; exit 3) or `facts_only`
(judgments are skipped and recorded as skipped, and the fact verdict stands).

Validation is strict. Unknown keys, wrong types, a pattern without a capture
group or duplicate judgment ids all fail `check` and `done` with exit 6, so a
typo can't quietly weaken the gate. Run `dispatch.py check` after editing.

**Calibration.** Thresholds are starting points. Every `done` line records
each judgment's score. Compare them with what happened later (review findings
after `done`, follow-up fixes, reopened issues) and adjust per repo.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | routed or override (route) / proceed (gate) / ready or off (check) / recorded (unrouted) / done (done) / ok (work) |
| 2 | usage or provider/probe error (stderr explains) |
| 3 | escalate — confidence too low, `--expect` mismatch below `--trust`, or done judgments unavailable |
| 4 | blocked — gate statement holds / not done or blocked (done) / advance refused (work) |
| 5 | no credentials (check) |
| 6 | invalid config.json or done.json (check, done) |
| 7 | pending — checks running or bot score not posted (done) |

## Outcome log

`route`, `gate`, `unrouted` and `done` append one JSON line unless `--no-log`,
and `work advance --to merged` appends the `closeout` line. `check` never logs. The log is `./.sectorfive/dispatch-log.jsonl` when run from a working
repo that has `.sectorfive/`. Use it to recalibrate thresholds and criteria.

## Troubleshooting

- `TYPESAFE_API_KEY is not set` → export it or add the gitignored plugin-root `.env`.
- `check` exits 5 → set the selected provider's credentials and re-check.
- `check` exits 6 → stderr names the file. For `config.json`, re-run the init
  interview. For `done.json`, fix the option it names.
- `done` stays `pending` → a check never finishes or the bot never scores. Name
  the check in `ci.required`/`ignore`, or hand the watch to the user. A head
  with no CI at all after a docs-only push needs `ci.docsOnly`.
- `botScore` says the score is for an older commit → the bot hasn't reviewed
  the latest push yet; wait for it instead of reusing the old score.
- `all providers failed` → check keys, then network, then `--timeout`.
- `provider answer missing ...` → provider changed shape; inspect with
  `--format json` and report.
