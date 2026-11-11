---
name: run-live-eval
description: Run a live LLM batch (L0 smoke, L1 Discover, L2 Research, L3 end to end, L4 safety) within the USD 6.00 hard cap, recording cassettes. Lead or live-runner only; budget checks come first.
---

# Run a live batch

Only the lead or `live-runner` may do this, one batch at a time. Subagents never run live. Spend is real money against a hard cap of **USD 6.00 total** (effective cap USD 5.70). Never read `.env` or print any key. The full procedure, the tripwires and the recovery steps are in [docs/runbooks/live-llm-budget.md](../../../docs/runbooks/live-llm-budget.md); failure modes are in [docs/runbooks/troubleshooting.md](../../../docs/runbooks/troubleshooting.md).

1. **Budget first.** Run `just budget`. Note spent and the effective cap. **Above 80%: stop** unless the batch is a must-run item. At 95% the guard refuses everything anyway. If it reports a ledger integrity problem, stop: that is a reconciliation job for the user, not a batch.
2. **Check the plan** (brief 9.4): L0 smoke <= USD 0.30 (done); L1 Discover <= 1.20; L2 Research <= 1.00; L3 end to end <= 2.00; L4 safety and probes <= 0.50; reserve >= 1.00. Order: L1, L2, L3, L4. Calibrate from L0 and shrink allowances, never the cap.
3. **Live mode.** Set `LLM_MODE=live` in the environment of the process that runs the batch. `MDE_FORBID_LIVE` must be unset (only the pytest autouse fixture sets it for non-live tests).
4. **Run one batch**: `just live-eval L1` (then L2, L3, L4; `just live-smoke` is L0). The runner refuses, in plain words, unless live mode, the tripwire and the headroom under the effective cap allow it; it stops at the batch allowance and records every live response as a sanitized cassette in `tests/cassettes/` (key = sha256 of spec id, version, model, rendered messages, tools, effort, schema hash). If the model id is unavailable, fail loudly, log it in `DECISIONS.md` and continue in mock or replay; never fall back to a costlier model.
5. **After each batch, before the next:** run `just budget`; write the new total and the batch result in `docs/implementation/PROGRESS.md`; read the raw result (`evals/reports/live-<batch>.json`, gitignored) and the log lines for fallbacks, failed calls and `incomplete` responses, and fix them offline; run `uv run pytest tests/security -q` (the secret scan covers cassettes); verify the new cassettes replay offline (`LLM_MODE=replay`).
6. Record the batch in `docs/evals/reports/` (summary only) and in the final `docs/implementation/REPORT.md`; remind the user in the report to set a USD 6 spend limit on the OpenAI project as an external backstop.
