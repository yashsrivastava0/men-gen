---
name: feedback-owner-tests-with-live-llm
description: "When the owner says \"run it so I can test it\" they mean the LIVE model, never mock; the owner starts it themselves with the ! prefix"
metadata:
  node_type: memory
  type: feedback
  originSessionId: b1c70918-007b-426a-a4d3-6fd76d81c6be
  modified: 2026-11-09T18:30:52.244Z
---

When the owner asks to run Compass for testing, they always test with the live LLM (no mock). Do not start a mock server for them. Prepare everything that is not a live call (stop old servers, `uv run python scripts/dev_db.py up` with `MDE_PG_BIN` set, `just compass-migrate`, read-only `uv run mde budget`), then start it with `LLM_MODE=live uv run python -m mde_compass --port 8139` (see see the runbook `docs/runbooks/live-llm-budget.md` and `docs/runbooks/compass.md`).

**Why:** the owner said (2026-11-10) "when I test I will be using live LLM calls, no mock. That is a major thing." Mock questions are canned and prove nothing about quality or speed.

**How to apply:** default to the live instructions. Remind them the Compass ceiling is USD 3.00 total (check `uv run mde budget` first) and that mock mode exists only for offline tests. See [[feedback-compass-speed-over-cost]] and [[project-compass-v2-plan]].

**Update 2026-11-10:** the owner said they give me full authority to run everything and they will only click in the page. Run all preparation (db up, migrate, budget check, port check, tests), then start the server in live mode myself. See [[live-mode-is-one-switch]].

**Update 2026-11-10 (size task):** for that task the owner explicitly allowed me to run live calls for testing within the USD 3.00 budget; scope and spend are in [[project-compass-size-structure]]. It was task-scoped.