# Build prompt 03: the Research agent (`agents/research`), v3

- **SUPERSEDED 2026-11-11 by [prompt 05](05-research-agent-implementation-prompt.md)** (the complete implementation prompt: everything inside `agents/research/`, Research built before Backbone with local validators, adapters in the agent, live bake-off and runs with a spend sentence, test front end integration). Keep this file for its scope detail only.
- **Task id:** `prompt-03`. **Status:** ready, not started. Rewritten 2026-11-11 by the planning chat from [doc 13 v3](../architecture/13-research-agent-design.md). v1 (waves, 4/15/40) and v2 (tier budgets 6/12/20) are superseded.
- **Do not start until** (a) Backbone step 1 is on `main` (doc 12 §10; doc 13 §18 gives the reason), or the owner says to start earlier; and (b) the owner gives the go. Prompt 04 (Compass change requests CR-1 to CR-3) is independent; this task must not wait for it (it derives and flags what is missing).
- **No live calls in this task.** Live search or model spend needs an explicit yes in chat that names the batch, the allowance and the expected cost. Section 8 lists the live batches; none runs here.

## 0. Rules that apply first
- Follow `prompts/00-global-rules.md`. The first action is to make sure its text exists in the repo as `docs/implementation/GLOBAL_RULES.md` (create it from that text if missing, as commit `docs: add global build rules`) and that the repo's `CLAUDE.md` points to it.
- Test runs use `LLM_MODE=replay`. Never set `LLM_MODE=live`. Never read or print `.env`, a key or a token.
- One test suite at a time, in the foreground, and only when memory allows.
- Use `python3` or `uv run python`, never a bare `python`, in any hook or script you add.

## 1. Read first
1. `docs/architecture-v3/13-research-agent-design.md` (v3). The full specification for this task.
2. `docs/architecture-v3/notes/compass-as-built-2026-11-10.md` (what Compass really outputs; §8b are binding corrections from the Compass author).
3. The three real inputs, read-only: `docs/compass/research-inputs/quick-yoga-studio-bengaluru.json`, `standard-produce-marketplace-nashik.json`, `thorough-freight-fleet-india.json`, and their `README.md`. Use `consumer_views.research` as the contract; use `brief` only to look up tiers and ids until CR-2 ships.
4. `docs/architecture-v3/12-four-agent-pipeline.md` §4.2, §5, §8, §10.
5. `docs/architecture-v3/05-change-management-and-edge-cases.md`: §1 (I-1, I-2, I-6, I-11, I-12), §5 (scope ledger statuses), §8 Research rows.
6. `docs/architecture-v3/07-quality-evals-and-models.md`: §2 (validator framework), §6 (model classes and the cross-vendor rule).
7. `docs/architecture-v3/00-index-and-decisions.md`: AD-33, AD-34, AD-38, AD-39, AD-41 to AD-52.

If `docs/architecture-v3/` is missing from the repo, copy the `architecture/` and `notes/` folders there first (doc 09 §1). Do not edit that copy, except to add a note.

## 2. Git and baseline
- Baseline per global rules §A.1: `git status`, note the branch and hash, create the safety tag `pre/research-<yyyymmdd-hhmm>`. Commit any uncommitted changes that are not yours as `chore(wip): ...` first.
- Branch `feat/research-agent`, started from `main`. If the Brief schema with `research_requests` or the consumer views exists only on `feat/compass-ai-interview`, start from that branch's latest commit and say so in the report. **Do not merge the Compass branch.**
- Commits: small and atomic, Conventional Commits, footers `Task: prompt-03`, `Phase: <n>`, `Refs: AD-39..AD-52`. Commit at least every 15 to 20 minutes, and before any risky step. Tags `research/agent-p<n>` after each green phase.
- Remote: if a GitHub remote exists, push the task branch and tags only, never `main`. If none exists, keep committing locally and say so.

## 3. Goal
Build `agents/research` so that, from an approved Brief's research view, it runs the foreground, slot-driven, parallel-worker loop of doc 13 §5 and produces a validated `research.json` (v3, doc 13 §9) and a resumable event stream (doc 13 §10). Every item is backed by a verbatim quote that code found in the stored page text. Test all of it offline with fakes and cassette-style fixtures. Make no live calls.

## 4. Scope: in
1. **`agents/research/`** (package `mde_research`, the AD-33 layout):
   - **Intake:** `ResearchInput` builder (RV-13 hash check; tiers per value; deduped roles and features; `geo_uncertain`; own-brand and declined terms; `input_quality`) and the **research load** (doc 13 §3.3: weights and thresholds in a data file, not code; the tier and `guidance.size_band/depth/search_budget/max_requests` are ignored). Works with and without CR-1 to CR-3 fields.
   - **Run state machine:** foreground run, ceilings by load class (doc 13 §7: searches, pages, time, rounds, lanes), per-call timeouts and retries (timeouts and 0-token calls count as failures), **Skip**, **cancel a topic**, resume after reload, one run per program, delta re-run keyed by Compass `_section_hashes`.
   - **Planner** (RSCH-1): research brief and targets with typed **slot schemas** (data files per job, doc 13 §4); the **worthiness gate** and **query anatomy** (RV-14, RV-06); merging by entity; priority ranking; rebuilding from facts when requests are template or thin.
   - **Workers**, one per target, in parallel with isolated context (doc 13 §5.2): query variants (literal with `exact_match`, descriptive, local-language), parallel search, **reciprocal rank fusion** and URL canonicalization, source-class ranking and the authority registry, triage, passage selection (BM25 windows), typed extraction per `kind` (RSCH-2, no tools), quote check (RV-02, RV-03), slot-fill update.
   - **Coverage audit and rounds** (doc 13 §5.3): required-slot coverage, follow-ups only for unfilled slots and discovered entities (`domain_restrict` to the entity's domain), distinct queries only, at most 2 follow-ups per target, `not_found`, evidence-aware stopping (covered; a round fills under 10% of remaining required slots; ceilings; Skip).
   - **Checker** (RSCH-4) for `impact: high` items only, batched; corroboration and source-class rules (RV-07, RV-08).
   - **Assembly** (RSCH-3 + RV-05): code builds the indexes `by_feature`, `by_entity`, `by_flow`, `by_integration`, the feature matrix, `suggestions`, `confirm`, `defaults_proposed`, `gaps`, `conflicts` and the lane **digests**; one Luna call selects and orders and writes `use_note`s; it writes no new facts. RV-16 checks every `applies_to` id against the Brief.
   - **Events:** `research.event.v1` emitter (doc 13 §10), stored, replayable, SSE with `Last-Event-ID`; labels from templates plus Compass `shown_to_user_as`, never model-written at run time; RV-17.
   - **Validators** RV-01 to RV-17, registered in the Backbone validator framework.
   - **Call specs** RSCH-1 (plan), RSCH-1b (query writer), RSCH-1c (gap analyst), RSCH-2 (extract), RSCH-3 (assemble), RSCH-4 (check): prompt templates (`.jinja`), strict JSON schemas, examples and mock outputs. Extraction and assembly get **no tools**. Model roles through the gateway: writer `gpt-6-luna`; checker a second model (config; mock in tests).
   - **Authority registry** as a data file (`data/authority_registry.yaml`, India seed); slot schemas and load weights as data files.
   - Evidence store in PostgreSQL only (table names your choice; follow the repo's migration convention); atomic writers for `research.json` and `research_evidence.json`.
   - `create_app()` and `router` with the endpoints of doc 13 §10, including the events stream, skip and target cancel.
   - Tests, the agent's README, and a copy of this prompt in `docs/build-prompts/`.
2. **`packages/contracts`:** `research.v3.schema.json` and `research.event.v1.schema.json` (shared: Spec and the front ends read them).
3. **`packages/llm-gateway`:** `SearchPort` adapters for **Tavily** `POST /search` and **Serper** (Google results). **Brave is deferred** (owner provides two accounts only; do not build the Brave adapter, but keep the port generic so it can be added). All under the same ledger, budget and live-mode rules as LLM calls. Only the gateway imports provider SDKs or HTTP clients. They normalize to `SearchHit {url, title, snippet, content?, published?, rank, provider_score}`, support `domain_prefer`, `domain_restrict`, `exact_match`, `time_range`, country and language where the provider has them, and report which locale parameters were actually applied (`locale: weak` otherwise).
4. **`packages/webfetch`** (name to confirm): the `FetchPort` implementation with **trafilatura** main-text extraction, the **PDF text layer**, SSRF rules (private, loopback and link-local refused, also after redirects), robots.txt, redirect limit, size caps, timeouts, content hash, the User-Agent rule (doc 13 R13). JavaScript-only pages are skipped and marked `no_content` (RS-19). No headless browser in this task.
5. Import-linter contracts, CI paths and uv workspace members updated.

Suggested phases for the tags: (1) schemas, data files, validators and `ResearchInput` with the load score, offline; (2) ports, fakes, planner, gate, one worker end to end; (3) parallel workers, rounds, coverage audit, assembly and digests; (4) adapters, `FetchPort`, API and events, recorded as cassettes; (5) offline evals on the three real inputs and the report.

## 5. Scope: out
- No changes to Compass, Spec, Verify or the front end except the shared schemas. (CR-1 to CR-3 are prompt 04.)
- No front-end UI (the contracts and API are built; the test UI renders them in a separate task). No live calls. No `.env`. No provider accounts. No keys in files.
- Do not choose the primary provider: build the two adapters; config selects; the bake-off decides (doc 13 §8). Default config: Tavily.
- Do not use the model vendor's built-in web search or any answer engine on the evidence path. Do not use the Apple iTunes Search API, Google Play scrapers or Jina Reader. No SearXNG, Crawl4AI, headless browser or reference packs (Phase 2).
- Do not merge `feat/compass-ai-interview` or any other branch into `main`.

## 6. Verify before you code
- **Provider facts.** Open the official references for Tavily Search and Serper. Doc 13 §17 lists what was verified on 2026-11-11 and what was not (Serper entirely; prices; Brave's language list; Tavily plan limits). Record the URL, the date and what you found in the report and the repo's research log.
- **If a provider has no country or language parameter** for a query, the adapter says so; the query log marks it `locale: weak`.
- **If the Brief has no research view or no `research_requests`,** stop and report. If trust flags are missing, derive and flag; do not stop.
- **Check the three real inputs against doc 13 §6.6** (the paper dry-run): load scores about 17, 31 to 41 and 95; target counts and mapping as described. Report where they do not hold.

## 7. Acceptance (all offline)
- Each of RV-01 to RV-17 has a passing test and a failing test.
- Scenario tests `SB-RS-01` to `SB-RS-34`, one per RS row in doc 13 §12.
- Property tests (doc 13 §18): no item without a quote; no tier other than `default` or `open`; **no query without a target, an anchor, a place and an intent word reaches an adapter**; ceilings never exceeded; any provider failure gives `partial` or `failed`, never an exception to the orchestrator; a run for a different Brief hash is refused; **a covered target issues no further queries; a follow-up never repeats an earlier query**; every event is stored and replayable from `Last-Event-ID`.
- **The three real inputs** run end to end on fakes: Quick's template requests are detected and rebuilt; own-brand terms are never queried; load classes are focused, standard and deep as in doc 13 §3.3 **without reading the tier**; Thorough's 20 requests merge to about 18 targets; the number of searches never exceeds 20, 40 and 80; the output indexes (`by_feature`, `by_entity`, `by_flow`) reference real Brief ids only.
- Determinism: replaying the same cassettes gives the same `research.json` hash twice in a row.
- Cost accounting: every provider call logged with its cost; the run stops at a ceiling and records `budget_stop`. Time box, Skip and topic cancel tested.
- Fail-soft: with the search port down, the run ends `partial` or `failed`, and the orchestrator receives a normal event.
- One run per program at a time. The suite passes with `LLM_MODE=replay`.
- The doc 13 §14 gate: every row that can be checked offline passes; live rows are marked `pending pilot`.

## 8. Live work (not in this task; each needs an explicit yes)
- **Bake-off** (doc 13 §8): the real research requests of the three Compass exports on Tavily and Serper (about 40 searches), capped at USD 0.75. Measures local-source share, on-topic share, discovered-entity hit rate, page-text availability, latency. Records cassettes.
- **First pilot:** the Quick input (focused load, ceiling 20 searches, cap USD 0.30), then Standard, only after the bake-off.
If the work reaches either, stop and ask in the report.

## 9. Report (end of task)
- Branch, tags, `git log --oneline` for the task, working-tree status.
- Exact revert commands: last commit; whole task (`git revert -m 1 <merge-hash>`); return to a tag without losing history (global rules §A.10).
- Tests run, with counts, and which RV and RS ids pass.
- Provider facts checked (URL and date), and anything not verified.
- Deviations from doc 13, with reasons. DECISIONS.md entries and ADRs written.
- Where the paper dry-run (doc 13 §6.6) and the load thresholds did not hold on the real inputs.
- Open questions for the owner, limited to facts only the owner can supply.
