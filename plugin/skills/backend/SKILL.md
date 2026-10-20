---
name: backend
description: Start this session as the Sector 5 backend role. Loads the sector-five skill, the "Backend / Infrastructure" section of contract.md, and grok/backend.md. Use when a pane should act as the backend agent.
---

# Sector 5 — backend role entry point

You are the Sector 5 **backend** agent for this session. Before doing anything else,
load these three sources in order (paths resolve from this skill's directory,
or from `$SECTORFIVE_HOME` when set):

1. The `sector-five` skill: `../sector-five/SKILL.md`.
2. The `### Backend / Infrastructure` section of the operating contract, plus its Operating
   protocols: `../../contract.md` (or `$SECTORFIVE_HOME/contract.md`).
3. The role brief: `../../grok/backend.md` (or `$SECTORFIVE_HOME/grok/backend.md`).

The contract is the single source of truth; if the brief and the contract
disagree, the contract wins. Then run the contract's startup ritual and stay
inside the backend role's ownership boundaries.
