# Sector 5 roadmap

v0.1.0 is published (Muse skill + marketplace plugin) and under
evaluation. Everything below is planned, not started.

## Next: one repo, every harness

Goal: installable Sector 5 plugin for Claude, Codex, and Grok from
this repo. The `plugin/` payload (contract, skill, briefs, dispatcher)
is already harness-agnostic; each harness needs only a thin manifest
shim. Suggested order: Claude → Codex → Grok.

- [ ] **Claude** (reference format — do first). Add
  `.claude-plugin/plugin.json` + marketplace entry; reuse `SKILL.md`
  as-is; consider promoting `plugin/grok/*.md` to `agents/*.md`
  subagents. Validate (`claude plugin`) and install-test for real.
- [ ] **Codex**. Package via the `codex plugin` marketplace flow;
  install-test. (Backend/frontend already execute on Codex; only
  packaging is new.)
- [ ] **Grok**. Add the manifest so
  `grok plugin install BobaFetch/sector-five#<subdir>` works from
  GitHub. Packaging can land anytime; live execution test waits on
  `grok login` (see backlog).
- [ ] **Release process**. Extend the README maintainer section with a
  multi-manifest sync checklist (versions + digests across harnesses).
- [ ] **Per-harness install tests**. Every harness gets a real install
  test, not just `validate` — packaging quirks differ (cf. the v0.1.0
  lesson: Muse hashes gitignored `plugin/.env` into the digest, so
  digests must be hashed from a clean checkout).

## Backlog (v0.1.0 residuals)

- [ ] Clef live fallback test (needs Cloudflare creds; covered by
  mocked unit tests only).
- [ ] Tier recalibration (`standard` on every spike call; revisit once
  real dispatch outcomes accumulate).
- [ ] Grok pin stays provisional until login + smoke test.
- [ ] Upstream: report Muse hashing gitignored files into
  `package_sha256`.
- [x] Multiplexer spike (replaces the TUI harness spike, reframed 2026-10-12).
  Resolved in v0.5.0 (contract v5.2): herdr lanes wrap the per-harness
  dispatch, one workspace per issue, with headless dispatch as the fallback.
  Roles still coordinate through planning. Original note:
  the user leans away from a custom harness and toward a multiplexer
  (Herdr-style): several model CLIs run side by side and can talk to each
  other. Questions: can roles hand off and message each other inside it
  without breaking the planning funnel? Does it replace or wrap the
  per-harness dispatch? Is Herdr usable as-is, or is pi/opencode prior art?
  No research done yet; set evaluation criteria before any build decision.
