# Research agent (`mde_research_agent`)

The second agent of the Context Engine. After the user approves the Brief (Gate 1) it searches the web, reads pages, extracts facts that **code** verifies against the page text, and hands Spec one playbook: `research.json` (`research.v4`), indexed by the Brief's ids, with norms (how common, how recent), suggestions, a confirm list, gaps, conflicts and one digest per Spec lane; plus `research_evidence.json` (pages, quotes, offsets). Design: `docs/architecture-v3/13-research-agent-design.md`. Build prompt: `docs/build-prompts/05-research-agent-implementation-prompt.md`. HTTP contract: `docs/api/research-agent.md`. Package page: `docs/packages/research-agent.md`. Runbook: `docs/runbooks/research-agent.md`. Decision: ADR 0018.

The import name is `mde_research_agent` (distribution `mde-research-agent`) because `packages/research` already owns `mde_research` (D-126). That older package is untouched and not wired in here.

## How a run goes

1. **Read** an approved Compass format-2 export (the three hashes must agree) and compute the research **load** from its content (features, rule facts, apps, sensitive data, references). Focused below 25, standard 25 to 60, deep above 60. The tier is never an input.
2. **Plan** topics across eight jobs (features, structure, flows, data, services, design, screens, local) from data files, with a model proposal checked by code.
3. **Research**, one worker per topic in parallel: queries (anchor + place + intent), search through `SearchPort` (Tavily or Serper), triage, read through our own `FetchPort`, extract typed items with **verbatim quotes that code verifies**, coverage audit, gap analyst follow-ups, checker.
4. **Assemble** once: items, norms with prevalence and recency, digests, conflicts, gaps. Sixteen validators (RV-01..17, RV-11 reserved) run before the result is committed.
5. **Stream** `research.event.v1` events over resumable SSE the whole time.

Plain code controls the flow; every model call is one structured, schema-validated call through the shared gateway with a versioned spec, a mock handler and a deterministic fallback. No agent loops. Web content is untrusted data: no tools on any call that reads it.

## Run it offline

```
uv run pytest agents/research -m "not integration and not live" -q     # the offline suite (about 1,100 tests)
uv run python -m mde_research_agent.run --export <export.json> --mock --out /tmp/research-out   # headless, fake search and fetch
just research-dev-memory                                               # dev server on 8120, no database
just research-dev                                                      # dev server on 8120 with the private Postgres (just db-up, just research-migrate)
```

The dev server allows the test console's origins through CORS. Auth is the same dev header set as Compass (`Authorization: Bearer dev-token`, `X-Tenant-Id`, `X-User-Id`).

## The test console

`apps/web/console/agents/research/` holds the Research screens. Run Compass (`uv run python -m mde_compass --memory --port 8110`) and Research (`just research-dev-memory`), open `http://127.0.0.1:8110/ui/`, and either finish an interview (Gate 1 starts the research by itself and the Research tab shows "Researching your idea", then "What we found") or use **Load a saved Compass export** on the start screen. A banner says when the server is in mock mode. A Research address other than the default needs `MDE_RESEARCH_ORIGIN` on the Compass server (the console's CSP names it).

## Live use (the owner only)

Live runs need the owner's switches in the environment (`LLM_MODE=live`, `ALLOW_LIVE_LLM=1`, a search key, and `LLM_BUDGET_USD` up to the 8.00 ceiling). The scripts refuse to start without them and never set them. See `docs/runbooks/research-agent.md`. The bake-off script compares the two search providers on about 40 generic queries; the batch script runs the three example exports and a synthetic Brief and writes `agents/research/examples/<name>/` plus a one-page summary under `docs/research-agent/examples/`.

## Layout

| Path | What |
| --- | --- |
| `src/mde_research_agent/core/` | pure code: load score, query anatomy, RRF fusion, BM25 passages, quote normalisation and verification, URL safety, source classes, prevalence and recency, norm keys, slots, hashing |
| `src/mde_research_agent/catalog/`, `data/` | the data files (jobs and slot schemas, ceilings, load rules, authority registry, intent words, template patterns, provider prices, default provider) and their validating loader |
| `src/mde_research_agent/intake/`, `plan/` | export reader and trust flags; target plan builder |
| `src/mde_research_agent/calls/` | the model calls (plan, queries, gap analyst, extract per job, assemble, check, bake-off judge): specs, mocks, fallbacks, `templates/*.jinja` |
| `src/mde_research_agent/engine/` | director, worker, budget, limiters, verification, spend booking |
| `src/mde_research_agent/assemble/`, `validators/`, `events/` | output assembly, RV-01..17, event writer and reducer |
| `src/mde_research_agent/adapters/` | Tavily and Serper, fetch, robots, caching, recording and replay |
| `src/mde_research_agent/store/`, `api/` | in-memory and PostgreSQL stores (one contract suite), FastAPI app and SSE |
| `contracts/` | generated JSON Schemas (`research.v4`, `research.event.v1`) with a drift test |
| `tests/` | unit, scenario (RS-01..34), validator, determinism, generality, contract, store, API and CLI tests; `fixtures/` (extracted exports, synthetic Briefs, pages) |
| `examples/` | outputs of live runs (empty until the owner runs them) |
