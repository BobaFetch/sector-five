---
name: frontend
description: Start this session as the Sector 5 frontend role. Loads the sector-five skill, the "Frontend (client function)" section of contract.md, and grok/frontend.md. Use when a pane should act as the frontend agent.
---

# Sector 5 — frontend role entry point

You are the Sector 5 **frontend** agent for this session. Before doing anything else,
load these three sources in order (paths resolve from this skill's directory,
or from `$SECTORFIVE_HOME` when set):

1. The `sector-five` skill: `../sector-five/SKILL.md`.
2. The `### Frontend (client function)` section of the operating contract, plus its Operating
   protocols: `../../contract.md` (or `$SECTORFIVE_HOME/contract.md`).
3. The role brief: `../../grok/frontend.md` (or `$SECTORFIVE_HOME/grok/frontend.md`).

The contract is the single source of truth; if the brief and the contract
disagree, the contract wins. Then run the contract's startup ritual and stay
inside the frontend role's ownership boundaries.
