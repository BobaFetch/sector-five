# Sector 5

A five-role agent team with an explicit operating contract: **planning** leads
and dispatches; **backend**, **frontend**, **polish**, and **qa** build in
phase order with ownership boundaries and verification gates. A Jev-first /
Clef-compatible decision dispatcher assists routing and gates.

Start here: [`plugin/contract.md`](plugin/contract.md) is the single source of truth.

## Layout

| Path | Purpose |
| --- | --- |
| `plugin/contract.md` | Operating contract (roles, protocols, templates, model policy) |
| `plugin/skills/sector-five/SKILL.md` | Muse skill entry point |
| `plugin/grok/` | Grok agent briefs (`planning`, `backend`, `frontend`, `polish`, `qa`) |
| `plugin/tools/dispatch.py` | Decision-model dispatcher (stdlib-only Python 3) |
| `plugin/docs/runbook.md` | Dispatcher usage, exit codes, tuning |
| `plugin/docs/decisions.md` | Package decision log (pins, providers) |

## Install

Pick one:

```sh
# Muse skill (this checkout)
muse skills install ./plugin/skills/sector-five

# Muse plugin via marketplace (once pushed)
muse plugins marketplace add sector-five https://github.com/BobaFetch/sector-five.git
muse plugins install sector-five@sector-five

# Claude plugin (this checkout). Adds the marketplace, then installs.
claude plugin install sector-five --marketplace .

# Claude plugin via marketplace (once pushed)
claude plugin marketplace add BobaFetch/sector-five
claude plugin install sector-five@sector-five

# Grok briefs: point --agent at plugin/grok/<name>.md, or copy the
# directory into your Grok agents location.

# Manual: clone and set SECTORFIVE_HOME to the checkout's `plugin/` directory.
```

Inside Claude Code, the one-step install is `/plugin install sector-five --marketplace BobaFetch/sector-five`.

Contract resolution (no absolute paths anywhere): `$SECTORFIVE_HOME`, else
relative to the installed skill/brief, else `./plugin/contract.md`.

## Roles

| Role | Owns | Pinned default |
| --- | --- | --- |
| planning | plans, sequencing, scope, `.sectorfive/` meta | claude/opus |
| backend | server, data, schema, auth, infra, API contracts | codex/gpt-6.1-sol @ high |
| frontend | screens, components, client state, routing, wiring | codex/gpt-6.1-sol @ high |
| polish | tokens, spacing, copy, focus order, a11y (after frontend) | claude/sonnet |
| qa | tests, reviews, verification verdicts (can veto) | claude/opus |

Contracts are model-agnostic; pins are tested defaults (see Model policy in
`plugin/contract.md`, evidence in `plugin/docs/decisions.md`).

Legacy name map (pre-v3 personal names → generic): Y'shtola → planning,
Lightning → backend, Aerith → frontend, Tifa → polish, Fina → qa.

## Dispatcher

Set `TYPESAFE_API_KEY` for Jev, or `CLOUDFLARE_ACCOUNT_ID` and
`CLOUDFLARE_AUTH_TOKEN` for Clef. Shell exports work:

```sh
export TYPESAFE_API_KEY="<Jev key>"
# Or use Clef:
export CLOUDFLARE_ACCOUNT_ID="<Cloudflare account ID>"
export CLOUDFLARE_AUTH_TOKEN="<Cloudflare token>"
```

Or copy the example and add your values to the gitignored `plugin/.env`
(never commit it). Then verify with `check`:

```sh
cp plugin/.env.example plugin/.env
python3 plugin/tools/dispatch.py check
python3 plugin/tools/dispatch.py route --task "Add cursor pagination to /orders"
python3 plugin/tools/dispatch.py gate --state "<diff summary>" \
  --statement "This change is dangerous"
```

Exit 0 means ready/off, routed, proceed, unrouted recorded, or done; 3
escalates (low confidence); 4 blocks (gate holds, not done, or a refused
phase change); 7 means pending (CI still running); 2 is a usage or
provider/probe error; 5 means no credentials and 6 an invalid `config.json`
or `done.json`.
Full reference: [`plugin/docs/runbook.md`](plugin/docs/runbook.md).

## Work items and the done check

Each issue in flight gets a state file, `.sectorfive/work/<issue>.json`. Its
phases go planned → building → review → open → done → merged, and
`dispatch.py work advance` refuses a phase until its requirements hold: an
owner, a routed step, a branch, a PR, qa's approve on the head SHA, and a
passing done check.

`dispatch.py done --issue <key>` decides whether a PR is ready for you to
merge, in two layers:

- **Fact checks** are read from GitHub with `gh`: CI, review threads, a review
  bot's score (only counted for the latest commit), mergeability, a plan file,
  PR body sections, qa's approve, draft state.
- **Judgments** are statements the dispatcher scores against the plan, PR and
  diff. They can block a PR but never pass a failed fact.

Each repo's owner configures both layers in `.sectorfive/done.json`. Without
it, the defaults are CI green, threads resolved, qa approve on head, not a
draft, and no conflicts with the base. Every option is in the
[runbook's `done.json` reference](plugin/docs/runbook.md#donejson-reference).

## Update / rollback

Claude reads the plugin straight from a local checkout, so pull and restart.
For a marketplace install, run `claude plugin marketplace update sector-five`.

Muse caches a snapshot, and `muse plugins update` can keep serving the old
one. Refresh the marketplace, then reinstall:

```sh
muse plugins marketplace update sector-five
muse plugins remove sector-five
muse plugins install sector-five@sector-five
```

Rollback: check out the previous tag. Tags are `vX.Y.Z`.

## Target-repo bootstrap

In a working repo, the team scaffolds `.sectorfive/` on first contact
(repo map, ownership map, contracts index, baseline, tech debt, decisions,
plans, requests, work items, dispatch log) per the contract's drop-in
bootstrap. The repo owner can add `.sectorfive/done.json` with the repo's done
rules at any time.

The first session runs an init interview covering dispatcher use, fallback
behavior (`pinned` by default), and harness mode (`multi` or `single`).
Confirmed choices go into `.sectorfive/config.json`. See the
[contract's Init interview](plugin/contract.md#init-interview).

Each session, planning checks readiness and opens with a banner such as
`[planning] dispatch: jev ✓ · fallback: pinned · harness: multi`. It shows the
provider and its state (`✓` ready, `✗` failed, `pending`, or `off`), the
fallback mode, and the harness mode.

## Maintainer: releasing

`marketplace.json` pins an integrity digest over `plugin/`. Any change under
`plugin/` invalidates it — recompute before tagging:

```sh
(cd plugin && python3 -B -m unittest discover -s tests)  # must be green
# Bump the version in plugin/.claude-plugin/plugin.json,
# plugin/.muse-plugin/plugin.json, .claude-plugin/marketplace.json and
# marketplace.json, then commit any plugin/ changes. Hash a CLEAN
# checkout: plugin/.env is hashed but never published, so hashing the
# worktree poisons the digest and marketplace installs fail integrity.
scripts/plugin-digest.sh            # prints the digest Muse verifies
# (clean clone + throwaway HOME; reads `installed.package_sha256`, NOT
# `manifest_sha256` -- pinning the manifest hash fails marketplace installs)
# Write the digest into marketplace.json, commit, then confirm with
# `scripts/plugin-digest.sh --check` before tagging and pushing.
```

Sanity check: re-hashing the previous tag the same way must reproduce its
published digest. Files outside `plugin/` (README, ROADMAP) don't affect the
digest. After pushing, refresh installs as described in Update / rollback.
