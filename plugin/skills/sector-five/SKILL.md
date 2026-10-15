---
name: sector-five
description: Coordinate the Sector 5 agent team (planning, backend, frontend, polish, qa) using its operating contract, role boundaries, and handoff protocols.
---

# Sector 5 Team

Use this skill when the user asks for team-based development, names a Sector 5
role (planning, backend, frontend, polish, qa), or wants cross-domain work
planned and dispatched with ownership boundaries and verification gates.

## Canonical contract

The full operating contract is `contract.md` at the sector-five plugin root
(repo-root `plugin/` directory).
Resolve it in order: `$SECTORFIVE_HOME/contract.md` when set, else
`<this-skill-dir>/../../contract.md`, else `<plugin-dir>/contract.md`.

That file is the single source of truth. When acting as (or dispatching) a
Sector 5 role, read the role's section plus the Operating protocols there.
Summaries below never override it.

## Roles

| Agent | Owns | Never touches |
| --- | --- | --- |
| planning (lead) | Plans, sequencing, scope, `.sectorfive/` meta | Product code or tests |
| backend (core/infra) | Server, data, schema, auth, infra, API contracts | Screens, client state, styling |
| frontend (client function) | Screens, components, client state, routing, API wiring | Server logic, schema, secrets, contracts |
| polish (experience, after frontend) | Tokens, spacing, copy, focus order, a11y | Logic, state, props, data flow, APIs |
| qa (tests/verification, can veto) | Tests, reviews, verification verdicts | Feature implementation ("fixing forward") |

## Core rules

- Single planner funnel: only planning plans, assigns, sequences, and approves
  scope or cross-domain changes. Agents coordinate through planning, never
  directly.
- Ownership is explicit: every path belongs to exactly one agent
  (`.sectorfive/ownership.md`). Same-file overlap resolves by phase order
  (backend → frontend → polish → qa), never by parallel edits.
- No unplanned changes: out-of-plan work goes back to planning or into tech
  debt. Cross-domain needs become a written request to planning, resolved with
  a durable plan — agents never reach across boundaries themselves.
- Evidence over assumption: on ambiguity, stop, cite facts, list options, ask.
- Verification gates completion: build, tests, lint, and typecheck pass with
  no new warnings, and qa's verdict is approve.
- Agents prefix user-visible chat responses with their name in brackets
  (`[planning]`, `[backend]`, `[frontend]`, `[polish]`, `[qa]`).
  File contents are not prefixed.

## Drop-in bootstrap

First session in an unfamiliar repo: read this skill and the canonical
contract, then `.sectorfive/` if present. If absent, scaffold it: map the repo
(toolchain, commands, layout), run the untouched build and test suite as the
baseline (report red before feature work), infer the ownership map for
planning's approval, index contracts, and seed tech-debt and decision logs.
Never run record/generate commands (expectation rewrites) casually.

### Init interview (planning)

Follow Drop-in bootstrap step 7 and Init interview in the canonical contract:

1. **Q1 — Use the dispatcher?** Recommend yes.
   - **Yes:** run `python3 tools/dispatch.py check` (credentials and a live
     probe, no log). If ready, status is `on`. If not, explain key setup:
     `TYPESAFE_API_KEY` (Jev) via shell export or the gitignored plugin-root
     `.env` (`cp .env.example .env`; never commit it). Then offer:
     - **a)** I've added it → re-check.
     - **b)** Use Clef → set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_AUTH_TOKEN`,
       then re-check with `--provider clef`.
     - **c)** Continue without it for now → status `pending`.
   - **No:** status `off`; planning assigns roles directly.
2. **Q2 — Fallback if the dispatcher fails?** Yes branch only. Offer
   `pinned` (default: the planned role on its pinned default, step flagged
   **unrouted**), `ask` (stop for the user's role choice), or `block` (hold
   until the dispatcher works).
3. **Q3 — Harness mode?** Both branches. First probe which `claude`, `codex`,
   `grok`, and `muse` CLIs are installed and signed in, and show them beside
   each role's pinned default. Recommend `multi`: each role runs on its pinned
   harness, and a missing harness gets a recorded verified equivalent or blocks
   that role. `single` runs every role in the current harness, recording
   substitutions per role.

Show a summary; once the user confirms, planning writes `.sectorfive/config.json`
and records the choices in `decisions.md`. Re-run on request, or when the config
is missing or invalid. The contract has the schema and full flow; `docs/runbook.md`
covers key setup.

## Session startup (planning)

Read config, run `python3 tools/dispatch.py check` (it also validates
`done.json`) and `python3 tools/dispatch.py work show`, then open with one line:
`[planning] dispatch: jev ✓ · fallback: pinned · harness: multi`.
Under `pending`, ask for credentials again. State any mode change explicitly.

- **`on`:** route every plan step before assigning its role and model. If the
  dispatcher is unavailable, announce the configured fallback. `pinned` assigns
  the planned role on its pinned default, flags the step **unrouted** in the
  plan, and records it with
  `python3 tools/dispatch.py unrouted --task "..." --reason "..."`. `ask` stops
  for the user; `block` holds the step. Low-confidence routes still escalate.
- **`pending` or `off`:** assign roles directly and say so in the banner.

The contract's Startup ritual and Decision-model dispatcher policy govern this flow.

## Templates

Durable plan, cross-domain request, and completion report templates are in the
canonical contract under Handoff protocol and Completion ritual. Plans live in
`.sectorfive/plans/`, requests in `.sectorfive/requests/`.

## Models

Role contracts are model-agnostic (any frontier model may execute them); each
role pins a tested default per the contract's Model policy.

Writing roles need a writable harness: `codex exec --sandbox workspace-write`,
`claude -p --permission-mode acceptEdits`, `grok -p --permission-mode acceptEdits`,
or `muse exec` (`muse -p` is not a valid flag). A read-only sandbox quietly turns
an implementation step into analysis. If a step comes back with no diff in its
assigned paths, it failed: re-dispatch it. Read-only steps such as audits and
reviews are exempt; for them, an empty report is the failure signal.

Browser work runs in a harness with browser tools (for example Claude in Chrome)
when the role's pin has none, and is recorded as a substitution. For user-facing
changes, qa captures screenshots (desktop and phone) and short recordings of
changed flows under `.sectorfive/evidence/<plan id>/` and hands them to planning.

## Delivery

planning owns delivery. It pushes only after qa approves, opens the PR, and
publishes qa's evidence by pushing it to the orphan `sectorfive-evidence` branch,
embedding commit-pinned `?raw=true` links in the PR description. (GitHub has no
attachment upload API.) It also watches checks, routes bot findings to the
owning role, and watches the PR with `dispatch.py done` until it returns
`done` or `not_done`. The user merges. See the contract's Delivery section.

## Work items and the done check

Each issue in flight has a state file, `.sectorfive/work/<issue>.json`:

- `dispatch.py work new --issue <key> --plan <path>` creates it in `planned`.
- `work set --issue <key> owner=frontend branch=... pr=...` records fields;
  qa records `qa.verdict=approve qa.sha=<head sha> qa.report=<path>`.
- `work advance --issue <key> --to <phase>` moves planned → building → review →
  open → done → merged, one phase at a time, and refuses with the reason when a
  requirement is missing (owner, a routed step, branch, PR, qa approve on a SHA,
  a passing done check). `blocked` needs `--note`; moving back is always allowed.
- `work show` lists every item and its phase.

`dispatch.py done --issue <key>` decides whether the PR is ready for the user.
Fact checks (CI, review threads, bot score, plan file, PR body sections, qa
approve on head, draft) come from GitHub via `gh`; then the dispatcher scores
the repo's judgment statements. Exit 0 done, 4 not done or blocked, 7 pending
(re-run later), 3 escalate. The rules are owner-configured in
`.sectorfive/done.json`; `docs/runbook.md` documents every option.

## Dispatcher (decision models)

A Jev-first / Clef-compatible dispatcher assists routing and gates; it never
replaces agent judgment. Examples use the plugin directory; in target-repo
sessions, invoke the installed tool by its resolved path from the working repo
so config and logs use that repo's `.sectorfive/`:

- Route a task: `python3 tools/dispatch.py route --task "..."`.
  Exit 0 prints the chosen role + tier; exit 3 means confidence is below
  threshold — escalate to the user instead of misrouting. Pass the planned
  role as `--expect <role>`. A different pick at or above `--trust` (0.90) is
  an `override`: follow it and amend `ownership.md`, unless the pick breaks
  one of two role rules (tests belong to qa; qa never edits product code).
  Below that it is a `mismatch` (exit 3): resolve it from the contract's role
  definitions or ask the user.
- Pass `--issue <key>` on `route` and `unrouted` so `work advance` can confirm
  the step was dispatched.
- Yes/no gate: `python3 tools/dispatch.py gate --state "..." --statement "..."`.
  Exit 0 proceeds; exit 4 blocks until planning rules.
- Keys come from `TYPESAFE_API_KEY` (Jev) and `CLOUDFLARE_ACCOUNT_ID` /
  `CLOUDFLARE_AUTH_TOKEN` (Clef fallback), via env or a gitignored `.env`
  at the plugin root. See `docs/runbook.md` in the plugin directory for exit codes and tuning.
