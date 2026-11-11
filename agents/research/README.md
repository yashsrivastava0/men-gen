# Research agent (`mde_research_agent`)

The second agent of the Context Engine, now also a **standalone app**: one process, one address (`http://127.0.0.1:8120/`), its own front end (`apps/research-console/`), live models and the live web only. It does not call Compass and is not inside the Compass console. It reads approved Compass exports from a folder (`docs/compass/research-inputs/`, found automatically), searches the web, reads pages, extracts facts that **code** verifies against the page text, has a second model check every fact, and writes one playbook: `research.json` (`research.v5`), indexed by the Brief's ids, with evidence levels, norms (how common, how recent), suggestions, a confirm list, gaps, conflicts, lane quality and one digest per Spec lane; plus an evidence file (pages, quotes, offsets) and a readable Markdown report.

Design: `docs/architecture-v3/13-research-agent-design.md` (v5) and `14-research-proof-plan.md`. Build prompts: `docs/build-prompts/`. HTTP contract: `docs/api/research-agent.md`. Package page: `docs/packages/research-agent.md`. Runbook (how the owner tests it): `docs/runbooks/research-agent.md`. Decisions: ADR 0018 and its addendum, `docs/implementation/DECISIONS.md`.

The import name is `mde_research_agent` (distribution `mde-research-agent`) because `packages/research` already owns `mde_research` (D-126). That older package is untouched and not wired in here.

## How a run goes

1. **Read** an approved Compass format-2 export (the three hashes must agree) and compute the research **load** from its content (features, rule facts, apps, sensitive data, references). Focused below 22, standard 22 to 57, deep from 58 (a 3-point margin below each boundary). The Compass tier is never an input.
2. **Plan** topics across the jobs (features, structure, flows, data, services, design, screens, local, and **trust** when the Brief shows health data, minors, identity checks, several regions or sensitive data, or payments together with two-sided money, and always for deep apps) from data files and **capability signals** found by code. A tiny app with no signal plans only two topics (light mode).
3. **Research**, one worker per topic in parallel: queries (anchor + place + intent), search through `SearchPort` (Tavily or Serper, with failover and a bounded second opinion), triage, read through our own `FetchPort` (tables and lists kept; soft 404s and walls skipped), extract typed items with **verbatim quotes that code verifies**, require every number in the quote (else widen to the sentence or reject), read dates and region from code, **check every item** with a second model (supported, partial, not supported, unsupported fields nulled), coverage audit, gap analyst follow-ups.
4. **Assemble** once: items with evidence level, independence by content, region and entity fit; norms; the main store (about 40,000 tokens) and an **appendix** (nothing is deleted to fit); digests; conflicts; gaps; lane quality and builder-need coverage. Twenty-two validators (RV-01..23, RV-11 reserved) run before the result is committed.
5. **Stream** `research.event.v1` events over resumable SSE the whole time; the front end shows the plan, the feed, the counters and the cost.

Plain code controls the flow; every model call is one structured, schema-validated call through the shared gateway with a versioned spec, a mock handler (kept for the call contract; the app composition cannot reach it) and a deterministic fallback that never invents content. No agent loops. Web content is untrusted data: no tools on any call that reads it.

## Run the app

```
uv run python scripts/research_app.py start     # detached, prints the address
uv run python scripts/research_app.py status | logs | stop | restart
just research-dev                               # the same server in the foreground
```

The app always runs live. Without a search key it still starts and its Preflight explains; every start-run action is refused with the names of what to set. Money: USD 5.00 in total, tripwire 4.50, one budget-period ledger file each, restartable from the Budget page. See the runbook.

## Run the tests (offline)

```
uv run pytest agents/research -m "not integration and not live" -q     # the offline suite
uv run pytest apps/research-console -q                                 # the front end's helper and static tests
uv run python agents/research/tests/e2e/fake_app.py --port 8140        # the TEST-ONLY fake app for browser checks (offline doubles)
```

The test doubles (fake search and fetch, the in-memory store, a scripted gateway) live only in `tests/support/`; the app's composition root cannot select one (`tests/api/test_app.py` checks it).

## Layout

| Path | What |
| --- | --- |
| `src/mde_research_agent/core/` | pure code: load score, query anatomy, fusion, passages, quote normalisation and verification, **numbers and dates (RV-18)**, URL safety, source classes and independence, prevalence and recency, norm keys, slots, hashing |
| `src/mde_research_agent/catalog/`, `data/` | the data files (jobs and slot schemas incl. `trust`, ceilings and the three caps, load rules, **signals**, authority registry, intent words, template patterns, quality thresholds, estimates, eval tasks, provider prices) and their validating loader |
| `src/mde_research_agent/intake/`, `plan/`, `inputs/` | export reader and trust flags; target plan builder and signals; inputs discovery and the zero-cost preview |
| `src/mde_research_agent/calls/` | the model calls (plan, queries, gap analyst, extract per job, assemble, check v2, bake-off judge, eval probe and judge): specs, mocks, fallbacks, `templates/*.jinja` |
| `src/mde_research_agent/engine/` | director, worker, **in-topic checking**, budget, limiters, verification, spend booking |
| `src/mde_research_agent/assemble/`, `validators/`, `events/` | output assembly, the main store and appendix, quality, RV-01..23, event writer and reducer |
| `src/mde_research_agent/report.py`, `review.py`, `metrics.py`, `spotcheck.py` | the Markdown report, paged review and sources, honest metrics, the spot-check |
| `src/mde_research_agent/adapters/` | Tavily and Serper, failover, fetch, robots, caching, recording and replay |
| `src/mde_research_agent/spend/` | the spend record, budget periods and the budget control |
| `src/mde_research_agent/store/`, `api/` | file and PostgreSQL stores (one contract suite), FastAPI app, security middleware and SSE |
| `src/mde_research_agent/eval/`, `tools.py` | the evaluation harness (planted errors, consumer probe) and the bake-off tool |
| `contracts/` | generated JSON Schemas (`research.v5`, `research.event.v1`) with a drift test |
| `tests/` | unit, scenario, validator, determinism, generality, contract, store, API and CLI tests; `support/` (doubles), `e2e/` (the fake app), `fixtures/` |
| `.runtime/` | the app's data (git-ignored): runs, spend record, uploads, spot-checks, logs |

`rsch.plan`, `rsch.write_queries`, `rsch.gap_analyst`, `rsch.extract.<job>` for the nine jobs, `rsch.assemble`, `rsch.check`, `rsch.bakeoff_judge`, `rsch.eval_probe`, `rsch.eval_judge`. Routes: writers use `fast` and `deep`; the **`standard` route is reserved for the checker** (D-127), flagged `checker_vendor: same`. Untrusted page text travels as one `text` field with no tools and no URL, action or tool fields in any output schema. The gateway's cap on untrusted text is raised to 120,000 characters (a checker batch is longer than the 20,000 default).

## Configuration (environment, never code)

`TAVILY_API_KEY`, `SERPER_API_KEY` (booleans only through `keys_present()`), `RESEARCH_SEARCH_PROVIDER` (`auto`, `tavily`, `serper`), `RESEARCH_LIVE_BUDGET_USD` (5.00), `RESEARCH_TRIPWIRE_USD` (4.50), `RESEARCH_DATA_DIR`, `RESEARCH_INPUTS_DIR`, `RESEARCH_UI_DIR`, `RESEARCH_STORE` (`file` or `postgres`), `RESEARCH_MAX_CONCURRENT_RUNS` (1), `RESEARCH_STALE_AFTER_S` (600), `RESEARCH_DEV_TOKEN`, `MDE_ENV_FILE`. The gateway cap is raised by the app to at least USD 6.50 (see the runbook); the repo ceiling stays 8.00 (D-125).

## Validators

RV-01..RV-23 (RV-11 reserved) in `validators/registry.py`, one pass test and one fail test each: schema, quotes, sources, tier, no new facts, query hygiene, corroboration, support, caps, coverage, budget ceilings, the Brief binding, query anatomy, slots, `applies_to`, the event allowlist, **numbers in the quote (RV-18), independence by content (RV-19), region and entity fit (RV-20), every item checked (RV-21), the store and appendix (RV-22), recomputed evidence, lane quality and builder-need coverage (RV-23)**. RV-04 and RV-13 refuse a commit; a violation of RV-05, 09, 18 to 23 is a bug in assembly: the run still commits but as `partial`.
