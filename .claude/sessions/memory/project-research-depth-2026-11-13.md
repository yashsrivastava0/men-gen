---
name: research-depth-2026-11-13
description: "Research agent overhaul on branch feat/research-depth (D-156..D-166+): owner's credit/money rules, what is merged, the eval commands, what is still open"
metadata:
  node_type: memory
  type: project
  originSessionId: 06263737-88e0-4720-9acb-a6e4fc286dea
  modified: 2026-11-12T23:37:15.576Z
---

Branch `feat/research-depth` (tag `pre/research-depth-20261112-2220`), pushed to origin. Owner's prompt 11 v3 approved the overhaul (D-154). Plan: `~/.claude/plans/pasted-content-id-fb80-research-agent-velvety-token.md`; decisions D-156..D-166 in `docs/implementation/DECISIONS.md`; work list in `PROGRESS.md` "Research depth".

**Owner numbers (2026-11-13):** "Tavily 93 / 1,000" = credits USED this cycle (Tavily's own `GET /usage` confirmed: plan Researcher, 93 of 1,000, all searches; the call is free); "Serper 2,389" = credits REMAINING (no balance endpoint, counted from responses). Task live-spend cap "USD 5 to 6": new spend stays under the 8.00 repo ceiling (shared ledger 2.37 + Research 1.10 booked), so the app is started with `RESEARCH_LIVE_BUDGET_USD=5.50 RESEARCH_TRIPWIRE_USD=5.00` (settings, not the Budget page; never touch `.env`).

**Why:** the owner wants research that is correct and build-grade for Spec and the front-end engine, each size tested on its own input, and a global credit trace in the console.

**How to apply:** live eval commands (app must be stopped, `LLM_MODE=live` plus the budget env): `uv run python -m mde_research_agent.eval planted|probe|reextract --run RUN_ID|--run-dir DIR`; run folders are in `agents/research/.runtime/runs/` (git-ignored, keep them). Credits: `uv run python scripts/research_app.py credits show|set`. Planted-error baseline on run F (v3 checker): caught 98.9%, 0 climbs of planted errors. Subagent worktrees may start from `main`: tell them to `git merge --ff-only feat/research-depth` first. See [[research-v5-state]], [[research-yield-2026-11-11]], [[live-mode-is-one-switch]].

**State at end of session (2026-11-13):** branch pushed, tag `research/depth-20261113`, offline suite 2,245 passed. **The OpenAI account ran out of credits (HTTP 429 "You have no credits remaining")**, so the Medium run on the final code, the consumer probe and the Porto run are still to do (about USD 1.7). Booked 4.57 of 5.50. Tavily is the saved default (Serper is the failover and second engine). As-built note: `docs/research-agent/depth-overhaul-as-built-2026-11-13.md`; decisions up to D-170.