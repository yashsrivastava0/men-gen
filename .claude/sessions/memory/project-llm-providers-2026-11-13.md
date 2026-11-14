---
name: llm-providers-2026-11-13
description: "Model-vendor independence (CLAUDE.md rule 14, D-171/D-172), Research agent tested on Claude, branch feat/llm-providers, how to switch, results and money"
metadata:
  node_type: memory
  type: project
---

Branch `feat/llm-providers` (from `feat/research-depth`, tag `pre/llm-providers-20261113-2128`). The owner asked: every agent/engine must be independent of the model vendor, switching happens in the gateway; test the Research agent on Claude (OpenAI account had no credits); cap "USD 5 for the testing LLM"; save outputs in the docs folder; give an in-depth report with an opinion.

**State (2026-11-13):** gateway has `AnthropicMessagesProvider`, `RoutingProvider`, `vendor` + optional `effort_floor` on each `pricing.yaml` row, `MODEL_ROUTES` as JSON / `fast=a,standard=b,deep=c` / one model id. Switch in the LAUNCH environment, never `.env`: `MODEL_ROUTES=claude-haiku-5-5` (needs `ANTHROPIC_API_KEY`) or `MODEL_ROUTES=gpt-6-luna`. Report: `docs/research-agent/claude-provider-test-report-2026-11-13.md`; saved outputs `docs/research-agent/outputs/claude-2026-11-13/`.

**Findings worth remembering:** Claude's structured output refuses >16 nullable fields and too-large grammars (handled inside the provider); Haiku 5.5 must run at least at medium effort (`effort_floor: medium`) or it over-fills fields (unverified 31% to 11%); Sonnet 5.5 as checker failed the planted-error bar (85%, misses swapped providers); Small 19/19 slots, Medium 78%, Big 90%; consumer probe not passed (invented 0 to 23, research thin on features/screens).

**Money:** Research period restarted with USD 4.95 total; 4.66 booked (model about 3.0, search 1.69), tripwire reached: the owner (or whoever runs next) must restart the budget on the Budget page. Saved search provider restored to Tavily.

**Open:** cross-vendor checker and Claude-vs-OpenAI on the same day when OpenAI credits return; Medium/Big on Tavily; Dakar/Pune/hospital/Central America; Compass still hardcodes the OpenAI model (rule 14 debt). See [[research-depth-2026-11-13]], [[live-mode-is-one-switch]].