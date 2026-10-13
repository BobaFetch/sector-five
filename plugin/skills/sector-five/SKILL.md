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

## Templates

Durable plan, cross-domain request, and completion report templates are in the
canonical contract under Handoff protocol and Completion ritual. Plans live in
`.sectorfive/plans/`, requests in `.sectorfive/requests/`.

## Models

Role contracts are model-agnostic (any frontier model may execute them); each
role pins a tested default per the contract's Model policy.

## Dispatcher (decision models)

A Jev-first / Clef-compatible dispatcher assists routing and gates; it never
replaces agent judgment. Run it from the plugin directory:

- Route a task: `python3 tools/dispatch.py route --task "..."`.
  Exit 0 prints the chosen role + tier; exit 3 means confidence is below
  threshold — escalate to the user instead of misrouting.
- Yes/no gate: `python3 tools/dispatch.py gate --state "..." --statement "..."`.
  Exit 0 proceeds; exit 4 blocks until planning rules.
- Keys come from `TYPESAFE_API_KEY` (Jev) and `CLOUDFLARE_ACCOUNT_ID` /
  `CLOUDFLARE_AUTH_TOKEN` (Clef fallback), via env or a gitignored `.env`
  at the plugin root. See `docs/runbook.md` in the plugin directory for exit codes and tuning.
