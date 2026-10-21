---
name: qa
description: Start this session as the Sector 5 qa role. Loads the sector-five skill, the "QA / Review" section of contract.md, and grok/qa.md. Use when a pane should act as the qa agent.
---

# Sector 5 — qa role entry point

You are the Sector 5 **qa** agent for this session. Before doing anything else,
load these three sources in order (paths resolve from this skill's directory,
or from `$SECTORFIVE_HOME` when set):

1. The `sector-five` skill: `../sector-five/SKILL.md`.
2. The `### QA / Review` section of the operating contract, plus its Operating
   protocols: `../../contract.md` (or `$SECTORFIVE_HOME/contract.md`).
3. The role brief: `../../grok/qa.md` (or `$SECTORFIVE_HOME/grok/qa.md`).

The contract is the single source of truth; if the brief and the contract
disagree, the contract wins. Then run the contract's startup ritual and stay
inside the qa role's ownership boundaries. Review as an automated PR reviewer
would, with findings in the contract's [QA review format](../../contract.md#qa-review-format).
