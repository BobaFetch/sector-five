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

Exit 0 means ready/off, routed, proceed, or unrouted recorded; 3 escalates
(low confidence); 4 blocks (gate holds); 2 is a usage or provider/probe error;
5 means no credentials and 6 invalid config (`check` only).
Full reference: [`plugin/docs/runbook.md`](plugin/docs/runbook.md).

## Update / rollback

Pull and re-run your install command, or `muse plugins marketplace update
sector-five`. Rollback: check out the previous tag. Tags are `vX.Y.Z`.

## Target-repo bootstrap

In a working repo, the team scaffolds `.sectorfive/` on first contact
(repo map, ownership map, contracts index, baseline, tech debt, decisions,
plans, requests, dispatch log) per the contract's drop-in bootstrap.

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
S=$(mktemp -d)
git clone -q . "$S/repo"
# Install into a throwaway HOME so your real muse install (the one your
# working repos use) is never replaced or removed.
HOME="$S/home" XDG_CONFIG_HOME="$S/home/.config" \
  XDG_DATA_HOME="$S/home/.local/share" XDG_CACHE_HOME="$S/home/.cache" \
  muse plugins install "$S/repo/plugin" --json | grep package_sha256
rm -rf "$S"
# Write the digest into marketplace.json, commit, then tag and push.
```

Sanity check: re-hashing the previous tag the same way must reproduce its
published digest. Files outside `plugin/` (README, ROADMAP) don't affect the
digest. After pushing, refresh installs with
`claude plugin marketplace update sector-five` and
`muse plugins update sector-five`.
