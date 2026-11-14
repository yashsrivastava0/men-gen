---
name: feedback-compass-speed-over-cost
description: Owner says speed of Compass responses beats cost; only hard limit is USD 3.00 total live spend
metadata:
  node_type: memory
  type: feedback
  originSessionId: b1c70918-007b-426a-a4d3-6fd76d81c6be
  modified: 2026-11-09T16:34:33.243Z
---

For the Compass agent, optimise for fast responses first; do not design around token cost (parallel/speculative calls, higher effort where it buys quality are fine). The only limit is the hard cap of USD 3.00 total Compass live spend (the existing `CompassBudget` ceiling, D-093, stops at 2.85). Do not add other cost limits.

**Why:** the owner's tests found question and option generation far too slow; they said "do not much worry about the cost, we will optimize it later" (2026-11-09).

**How to apply:** when speed and cost conflict, choose speed; keep the USD 3.00 ceiling. Roughly USD 0.55 was spent before the v2 work (check `uv run mde budget`).