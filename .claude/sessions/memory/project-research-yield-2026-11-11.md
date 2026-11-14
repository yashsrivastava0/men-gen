---
name: research-yield-2026-11-11
description: "Research agent yield and cost work (branch feat/research-yield, D-142..D-153): what changed, live results, provider decision, how to compare runs"
metadata:
  type: project
---

Branch `feat/research-yield` (tag `pre/research-yield-*`), pushed to origin 2026-11-11 with every other branch and tag; main on origin is deliberately the older tip. Start page for a fresh session: `docs/implementation/CURRENT_STATE.md`. Owner goal: spend less only if the research result stays equal or better. Report for the owner: `docs/research-agent/yield-and-cost-report-2026-11-11.md`; decisions D-142..D-153.

Found: the 40-page ceiling starved topics (refused pages counted as read), double-encoded page text made rupee prices fail the number check, the city was on every query, quoted searches were sent as one whole phrase. All fixed (page budget, `repair_mojibake`, `scope` per template in `data/jobs`, `quote_phrase`, relaxed retry, figure drop for provider items).

**Why:** Small run 2 of 11 slots at USD 0.25; after the fixes 2 to 8 of 10 slots (Tavily 8 and 6 at about 0.30, Serper 8, 3, 5, 2 at 0.04 to 0.08). Decision: Tavily stays primary (owner's rule: Serper only if at least as many slots). `settings.json` primary_provider is `tavily`; it applies only after `scripts/research_app.py restart`.

**How to apply:** compare runs with `uv run python -m mde_research_agent.compare OLD NEW` but one run per side cannot pass or fail the gate (same code, two runs fail each other); use several runs. Live runs go through the app API (`POST /api/runs {input_id}`, headers dev-token, tenant demo) and book into `agents/research/.runtime/spend-record.json`; USD 1.10 of 5.00 booked on 2026-11-11. Medium and Big not run with the new code. See [[research-v5-state]] and [[live-mode-is-one-switch]].