# evals/

**Job:** measure quality honestly. Datasets, graders, runners and the in-process harness the tests share; raw outputs go to `evals/reports/` (gitignored), summaries to `docs/evals/reports/`. Plan, metrics, live batches and known product bugs: [docs/evals/eval-plan.md](../docs/evals/eval-plan.md).

**Layout**
- `harness/`: `stack.py` (the real product in one process; scripted provider over the mock handlers; network guard), `driver.py` (`Driver`, `FlowDriver`, `HttpDriver` over `create_app` and an ASGI transport), `journeys.py`, `checks.py`, `observe.py`, `persona.py`, `briefs.py`, `catalog.py`, `shims.py` (BUG-1 shim; remove when fixed).
- `datasets/`: `personas/` (10, incl. Hindi and Hinglish), `briefs/<archetype>/` (5, with generated `expected.yaml` and `APP_SPEC.md.golden`), `injections/` (9), `situations/` (the catalog as data, generated).
- `graders/`: `metrics.py` (cards per session, assumption rate, first-pass rate, proposals, screens, tokens per slice, cost, latency, research additions), `injection.py`, `sufficiency.py`.
- `probes/sufficiency/`: the context sufficiency probe (its call spec, templates and offline reader; not part of the product catalog).
- `runners/`: `mock_batch.py` (offline, writes a report), `make_goldens.py`, `make_situations.py`, the live batch module (entry point of `just live-eval`: refuses without `LLM_MODE=live` and a budget that allows it; lead only).

**Metrics (spec section 12):** cards asked per Guided session (target 7, cap 10), assumption rate, first-pass validator rate, valid proposals accepted vs dropped, context sufficiency probe, screens per app, tokens per slice, cost and latency per session, injection pass rate, research additions accepted. Report where Luna struggles and what changed.

**Run:** offline by default: `uv run python -m evals.runners.mock_batch`. Live eval is `just live-eval`, only by the lead or `live-runner`, after `just budget` (skill `run-live-eval`; stop at 80% spend unless must-run). Agents other than the lead never run it live.

**Conventions:** deterministic graders; never print keys (compare, report labels); every live response recorded as a cassette; goldens are mock-mode snapshots (regenerate with `make_goldens`, review the diff); markdown only under `docs/`.

**Owner:** `test-engineer`.
