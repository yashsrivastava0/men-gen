# research-agent

Package `mde_research_agent` (`agents/research/`, distribution `mde-research-agent`). The second agent of the Context Engine: after Gate 1 it turns the approved Brief into `research.json` (`research.v5`), a quote-verified, id-indexed playbook for the Spec lanes. Not the older `mde_research` (`packages/research`, see [research](research.md)), which is untouched (D-126). Design: [architecture-v3/13](../architecture-v3/13-research-agent-design.md). Contract: [api/research-agent](../api/research-agent.md). Run it: [runbooks/research-agent](../runbooks/research-agent.md). Decision: [ADR 0018](../adr/0018-research-agent-architecture.md).

## Boundaries

Imports only `mde_core`, `mde_contracts`, `mde_llm_gateway` and third-party libraries (`httpx`, `trafilatura`, `pypdf`, `lxml`, `jsonschema`, `regex`, FastAPI, SQLAlchemy). Never another agent, an app, or the older research package; enforced by `just layers` (contract `research-agent-isolated`) and by a scan in `tests/test_generality.py`. Only the gateway imports `openai`. It is the only part of the repository that fetches web pages, through its own `FetchPort` (SSRF checks on every hop, robots, size caps).

## Interface

```python
from mde_research_agent.api import create_app          # FastAPI app: /v1/research (see api/research-agent.md)
from mde_research_agent.compose import build_service   # ResearchService wired to a gateway, search, fetch and store
from mde_research_agent.run import run_export          # headless: export in, research.json out
```

`ResearchService.start_run / snapshot / events / skip / cancel_target / result / review / reject_item`. One active run per program (partial unique index). A committed result is immutable and hashed; rejections are an overlay.

## Model calls (14 specs, all with mock handler, fallback and must-not tests)

`rsch.plan`, `rsch.write_queries`, `rsch.gap_analyst`, `rsch.extract.<job>` for the eight jobs (features, structure, flows, data, services, design, screens, local), `rsch.assemble`, `rsch.check`, `rsch.bakeoff_judge` (the live bake-off only). Routes: writers use `fast` and `deep`; the **`standard` route is reserved for the checker** (D-127), flagged `checker_vendor: same`. Untrusted page text travels as one `text` field with no tools and no URL, action or tool fields in any output schema. Spend is booked in the shared ledger under `rsch.` ids; search cost is booked through the guard as `rsch.search.<provider>`.

## Data files (`src/mde_research_agent/data/`)

`load.yaml` (load score rules and class limits), `ceilings.yaml` (soft target and hard limit per class), `jobs/*.yaml` (targets, questions, slot schemas with declarative fill rules, extraction focus), `plan.yaml`, `lanes.yaml`, `authority/<country>.yaml` (official domains), `countries.yaml`, `languages.yaml`, `intent_words.yaml` (per language, `reviewed: false` until native review), `template_patterns.yaml`, `forbidden_patterns.yaml`, `source_classes.yaml`, `prevalence.yaml`, `use_notes.yaml`, `labels/`, `provider_prices.yaml`, `providers.yaml` (the measured default search provider). `catalog/loader.py` validates cross references at load and returns a frozen `Catalog` with a `catalog_hash`. Nothing is hard-coded to one company or app type (tested with every fixture and three synthetic Briefs).

## Configuration (environment, never code)

`TAVILY_API_KEY`, `SERPER_API_KEY` (booleans only through `keys_present()`), `RESEARCH_SEARCH_PROVIDER` (`auto`, `tavily`, `serper`, `fake`), `RESEARCH_LIVE_BUDGET_USD` (4.00, Research as a whole), `RESEARCH_RUN_CAP_USD` (2.50 per run; a `research_run` scope kind in the gateway's budget), `RESEARCH_DEV_TOKEN`, `RESEARCH_CORS_ORIGINS`, `MDE_ENV_FILE`. The repo-wide cap is `LLM_BUDGET_USD` (ceiling 8.00, D-125).

## Store

In-memory and PostgreSQL behind one Protocol with a shared contract suite (ADR 0007). Tables (migration chain `research_alembic_version`): `rsch_runs`, `rsch_events`, `rsch_targets`, `rsch_results`, `rsch_decisions`, `rsch_spend`, evidence and shared search and page caches. Migrations are append-only.

## Validators

RV-01..RV-17 (RV-11 reserved) in `validators/registry.py`, one pass test and one fail test each. They cover the schema (RV-01), quotes (RV-02), sources (RV-03), the tier never being an input (RV-04), no new facts in assembly (RV-05), query hygiene (RV-06), corroboration (RV-07), support (RV-08), caps (RV-09), coverage (RV-10), budget ceilings (RV-12), the Brief binding (RV-13), query anatomy (RV-14), slots (RV-15), `applies_to` (RV-16) and the event allowlist (RV-17). Two refuse a commit (`must_refuse`): RV-04 and RV-13. The others run on every run and must report nothing on every fixture in the offline suite.

## Tests

`uv run pytest agents/research -m "not integration and not live"` (offline; scenarios RS-01..34, determinism with random delays, generality, contract and CLI tests); `just research-test-int` for the PostgreSQL store. Replay of a recorded run gives the same content hash.

## v5 additions (standalone app)

- **App:** `python -m mde_research_agent` serves the API and `apps/research-console/` on 127.0.0.1:8120; `scripts/research_app.py` starts it detached. Live only (see D-132); the composition is `mde_research_agent.compose.compose_app`.
- **Inputs, preview, queue:** `inputs/` (discovery by content hash, preview with no model or web call), one run at a time by default with a queue, the file store (`store/files.py`) or PostgreSQL.
- **Money:** `spend/` (period ledgers, `spend-record.json`, the budget control).
- **Truth layer:** `engine/checking.py`, `core/numbers.py`, `engine/verify.py`, evidence and independence in `assemble/items.py`, RV-18..23.
- **Reading a result:** `review.py`, `report.py`, `metrics.py`, `spotcheck.py`, endpoints in `api/results_routes.py`.
- **Operations:** `adapters/search/failover.py`, `tools.py` (bake-off), `eval/` (planted errors, consumer probe).
- Configuration, data files and the runbook: `agents/research/README.md`, `docs/runbooks/research-agent.md`.

## Page budget, region scope and number repair (2026-11-11)

A page is one the run READ: an unreadable page is refunded, a spare replaces it, and a run that runs out of pages is `partial` with a `pages_exhausted` warning (D-144). Each topic template has a region scope (`city`, `country`, `none`) in `data/jobs/*.yaml` (D-146). Double-encoded page text is repaired exactly (D-145). An unverified figure is dropped, not the item, for the kinds in `data/figure_drop.yaml` only (D-149). `python -m mde_research_agent.compare OLD NEW` is the quality gate for any discovery change (D-143).
