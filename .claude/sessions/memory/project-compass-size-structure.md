---
name: project-compass-size-structure
description: Compass size-aware Brief (ADR 0017): structure sections, size bands per tier, batch H replay, and the scoped live-testing authorization used for it
metadata:
  type: project
---

Compass Brief now carries a drafted, user-approved structure (modules, screens, flows, entities) and the three tiers mean app size (small 8-15, medium 20-30, large 40-60+ screens; bands are guidance, never padded). Plan: `docs/implementation/COMPASS-SIZE-PLAN-2026-11-10.md`, ADR `docs/adr/0017-brief-structure-sections-and-size-bands.md`, decisions D-113 to D-123, progress section "Compass size" in `docs/implementation/PROGRESS.md`. Branch `feat/compass-speed-v3`, phases S0 to S9 committed; S8 (prefetch) deliberately not built.

**Why:** the owner wanted the Brief detailed enough for a downstream AI to build a 10-60 screen app, with research depth scaling by app size, and the final Brief logically correct and realistic.

**How to apply:** `COMPASS_STRUCTURE=on|off` switches it (on in the live product policy); cheap live calibration is `uv run python -m mde_compass.live_structure --replay <delhi|pune|hospital> --band <b>` (about USD 0.01-0.03 a run, `--mock`/`--dry-run` free). Live spend after the 2026-11-10 verification program was about USD 1.07 of the USD 3.00 ceiling. The owner's own browser test stays theirs.

**Scope of the live authorization:** in this task (2026-11-10) the owner said live calls were allowed for testing within the USD 3.00 budget ("do not worry about the restriction"), so I set `LLM_MODE=live` inline for those commands only (D-119, D-123). See [[feedback-owner-tests-with-live-llm]] and [[feedback-compass-speed-over-cost]].