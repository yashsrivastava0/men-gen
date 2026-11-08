# research

Package `mde_research` (`packages/research`) plus the four call specs it owns in `mde_prompts`: `S1.web_search`, `C6.plan_research`, `C7.extract`, `C8.synthesize_research`. Owner: `research-engineer`. Spec: [product spec section 4](../product/context-engine-spec.md); brief 7.7, 7.10 and 8.3.

Step 3 of the Context Engine, inside PLANNING phase `research`, after Gate 1, with the approved Brief as input. It is a **bounded, sequential pipeline, not an agent loop**: plain code decides what runs, every LLM call is single and schema-validated (S1 is free text and the only call with a tool), and the result is one Research Note.

## Using it

```python
from mde_research import DefaultResearchEngine

engine = DefaultResearchEngine(gateway)                 # a GatewayClient; never mde_llm_gateway
note = await engine.run(request, run_context)           # ResearchNote, never raises for research failures
report = await engine.run_with_report(request, run_context)   # note + queries, injection events, fallbacks, cost
```

* `request` is `mde_contracts.engines.ResearchRequest`; `run_context` is `RunContext` (cancellation token, progress emitter, `run_id`). **The flow must set `run_id`**: it becomes the `research:<run_id>` budget scope that carries the per-run cap (USD 0.30).
* The only exception that leaves the engine is `mde_core.errors.CallCancelled` (the flow handles it). Everything else becomes a note with `status` `partial` or `unavailable`.
* Persistence is the flow's job. `run_with_report` exists so the flow can write injection attempts to the event log (spec section 9: "noted in the event log").
* `is_stale(note, now, max_age_days=14)` tells the flow when to refresh a note on resume.

## The pipeline

```text
policy.research.enabled? ----no----> unavailable("policy")           (zero gateway calls)
        |
  budget left? ---------------no---> unavailable("budget")           (zero gateway calls)
        |
  C6 plan   one call: jobs R1..R6, up to max_queries_per_job generic queries each (the depth cap
        |      spread over the web jobs that apply, 2 to 8), trimmed to depth cap + budget
        |      (code re-validates: jobs allowed, query privacy, private data, dedupe, trim)
        v
  for each query, in order (concurrency 1 by default):
     S1 web_search ---> clean + screen (code) ---> C7 extract (no tools, untrusted input)
        |                  strip hidden text/markup, number links, drop unknown urls,          |
        |                  injection tripwire, domain allow/block, size cap                    v
        |                                                   validate facts (code): <=240 chars, no urls,
        |                                                   no instruction look-alikes, source must be
        |                                                   one the search returned, no long quotes
  R6 internal sources (Enterprise): InternalSourcePort.fetch -> facts, no web
        |
  cross-check (code): merge near-duplicates, union sources, verify, number F-001..
        |
  C8 synthesize ---> (Deep: gaps -> <= 4 gap-fill queries, each only while the C8 re-run stays
        |             affordable -> C8 again on all facts, only if the round found something new)
        |
  validate + number (code): suggestions cite real facts, verified inherited, RA-001.. ids,
        |                   urls only from facts, free text cleaned, applies_to normalised
        v
  ResearchNote (status ok | partial | unavailable)
```

Cancellation is checked before **every** call and between steps. Progress goes out through `ctx.progress("research", step, label, done, total)` with plain-language labels from `data/progress_labels.yaml` ("Planning the research", "Looking at similar apps", "Checking integrations", "Reading what we found", "Putting the notes together"; English, Hindi and Hinglish).

### Jobs

| Job | Runs when | Notes |
| --- | --- | --- |
| R1 market scan | always | two lead queries (common features, UX patterns), then one query per feature of the Brief alternating with wider angles (monetization, retention, accessibility and localization, permission prompts, slow networks, user complaints) |
| R2 reference apps | the request carries `references` and the policy allows names in queries | patterns only, never brand assets; four angles per app (features, user feedback, pricing, sign-up flow), every app before any app's second angle |
| R3 brand | the user gave or confirmed a website (`brand_url`), the policy allows names, the site passes the domain policy | search is pinned to that site (`allowed_domains=[host]`); four queries (colors and fonts, about and mission, logo use, store listing) |
| R4 compliance pointers | the Brief has compliance flags | pointers with links, not legal advice; eight angles per flagged area (requirements, store disclosure, permission screen, deletion, encryption, third-party sharing, checklist, enforcement), every area before any area's second angle |
| R5 integration notes | the Brief implies a service (payments, push, maps, sign-in, messaging ...) | SDK availability, Expo compatibility, caveats; eight angles per service (SDK and Expo, pricing and limits, config plugin, privacy and retention, known issues, alternatives, development build, keys), sign-in and payments first |
| R6 internal sources | the policy lists connectors | `InternalSourcePort` only, `NullInternalSource` stub, never the web |

`brand_url` and `references` are not slots in v1. They travel on the request (`ResearchRequest.references` and `ResearchRequest.brand_url`, added in D-047; the flow fills them from Discover's state) and, for the website only, from a link typed into the `brand.name` or `brand.assets` card.

### Depth dial

| Depth | Search-call ceiling | Reachable | Extras |
| --- | --- | --- | --- |
| Light (Guided) | 6 | 6 | |
| Standard | 12 | 12 | |
| Deep (Enterprise) | 20 | 20 + up to 4 gap-fill | one gap-fill round, then C8 again; plus R6 |

The flow sets `ResearchRequest.depth` (from `policy.research.depth`, or a tier override); the engine uses the request's value. The ceilings are reachable (D-078; before it a plan was capped at two queries per job, ten searches in all): how many queries one job may carry depends on the depth, see below. Trimming keeps every job's first query before any job's second one, every second before any third, in priority order R1, R4, R5, R2, R3. `RESEARCH_CONCURRENCY` (`ResearchConfig.concurrency`, default 1) bounds parallel searches with a semaphore; results are merged in plan order, so a note does not depend on timing.

**Queries per job.** One function, `mde_prompts.calls.c6_plan_research.plan.queries_per_job(depth_cap, active_web_jobs)`, gives the number: `clamp(ceil(depth_cap / active_web_jobs), 2, 8)` (bounds in `queries_per_job` of `fixtures/query_templates.yaml`). The active web jobs are the R1 to R5 jobs `_jobs_available` returns for this run (R6 reads documents and is not counted). The engine calls it once, passes the result to C6 as `max_queries_per_job` and validates the plan against the same number; the deterministic planner (the C6 mock and the fallback) only reads `C6Input.max_queries_per_job`, so the two cannot drift. Light is pinned at two per job (`fixed_up_to_cap` in the same file): a small Guided brief with two jobs still searches four times, not six. The depth cap still cuts the plan's total, so a larger per-job number never means more searches than the ceiling.

| Active web jobs | Light (6) | Standard (12) | Deep (20) |
| --- | --- | --- | --- |
| 5 (R1 to R5) | 2 | 3 | 4 |
| 3 (R1, R4, R5: the policy bars company names, so no R2 or R3) | 2 | 4 | 7 |
| 2 (R1, R5) | 2 | 6 | 8 |
| 1 | 2 | 8 | 8 |

With five jobs Standard is five firsts, five seconds, then a third for R1 and R4; Deep is four queries for every job. Without company names Deep is R1 7, R4 7, R5 6. A rerun of selected jobs uses the number its full run had (the jobs the Brief and the policy allow), so a refresh costs no more than the original research did. The planner has at least eight distinct templates for R1, R4 and R5 and four for R2 and R3, so one flagged area or one service is enough to fill the number.

## Budget and cost

The gateway enforces the caps (session USD 0.50, research run USD 0.30 via the extra scope `research:<run_id>`, global 95%); the engine plans inside them so it fails soft instead of hitting a wall.

* Remaining budget = the tightest of the policy's `research_budget_usd` minus this run's spend, the session cap minus session spend, and the global effective cap minus spent and reserved (read from `GatewayClient.budget_status()`; our own cost tally covers a gateway that cannot answer). The 80% and 95% tripwires mean zero.
* `max_search_calls = min(depth cap, (remaining - 5,500) // 13,000)` is passed to C6 together with `remaining_budget_micros` and `max_queries_per_job`; the deterministic plan applies the same arithmetic.
* The ceilings fit the research cap. At the planning costs (13,000 per query, 5,500 for the plan and the first synthesis) Light is 83,500, Standard 161,500 and Deep 20 queries 265,500 micro-dollars of the 300,000 cap (D-078; the gateway caps are not raised). The 34,500 left at Deep pays for two gap-fill searches.
* **Gap-fill keeps room for its re-synthesis.** Before each gap search the engine needs the cost of a query plus `synthesis_cost_micros` (4,000); when that is not left, the search and the rest of the round are skipped (the note is `partial`, reason `budget`, and the open questions stay in `gaps`). The second C8 call therefore cannot be refused by the budget a gap search used up. A round that finds nothing new (all searches skipped, failed or empty) makes no second C8 call.
* Not enough for one search plus planning and synthesis: `unavailable("budget")` before any call. `BudgetExceeded` on the planning call or the first search: the same. Later: stop searching, still synthesise what was found, `partial` with reason `budget`.
* Numbers from the L0 live smoke (2026-11-08): one `web_search` call is about 8,700 input tokens (search content counts as input), about USD 0.011 and 4 to 5 seconds, with many `url_citation` annotations (27 for one query); low reasoning effort used 32 to 56 reasoning tokens. Planning values in `ResearchConfig`: search 12,000 micros, extraction 1,000, planning 1,500, synthesis 4,000. A Light run is therefore at most about USD 0.08, Standard about 0.16, Deep about 0.27 with its 20 searches (all under the USD 0.30 cap; Deep leaves room for about two gap-fill searches). Those figures are estimates until the L2 research batch.

## Safety rules (spec 4.4)

Web text is data, never instructions. Layers, each independent of the model:

1. **Clean** (`safety.clean_web_text`): zero-width and bidi characters out, full-width forms folded (NFKC), scripts, styles, comments and tags removed, markdown links to known sources turned into numbered markers (`[1]`, `[2]`), every other url removed, size capped (12,000 characters).
2. **Screen** (`safety.detect_injection`): a tripwire of 16 instruction look-alike patterns (override, system-prompt and key requests, fetch-this-url, role markers, chat templates, fake JSON, scripts, "note to AI"), run on the text with known links already numbered and again on the cleaned text. A result that trips it is dropped **before any model reads it**, and the attempt is recorded (`InjectionEvent`: job, query index, stage, pattern names, never the text). Patterns are tuned to stay quiet on ordinary product and developer documentation.
3. **C7 has no tools** and sees the text only inside the gateway's `<untrusted_content>` block (`untrusted_inputs=("search_text",)`). `injection_suspected` drops all facts of that result.
4. **Validate facts in code**: at most 240 characters, no urls, no markup, no JSON look-alikes or instructions (an instruction or JSON statement distrusts the whole result), the `source_url` must be one the search returned (after the domain policy), no verbatim run of 25 or more words from the source, at most six facts per result.
5. **C8 sees facts as untrusted** too (`untrusted_inputs=("facts",)`, second-order defence). Its output is validated: fact ids must exist, `verified` is inherited from the facts, urls must come from facts, free text is cleaned and checked, `applies_to` is normalised, hex colors and fonts are format-checked, the style direction must be a real theme.
6. **Links the content asks for are never followed**: the engine fetches nothing; it only sees what the hosted search tool returned.
7. **Domain policy**: `allowed_domains` (when set, only those) and `blocked_domains` apply to the search call, to citations and to every stored source; subdomains match, look-alikes do not.
8. **Query privacy**: queries come from generic terms (kind of app, region, feature and service types, the sensitive area). With `allow_company_name_in_queries=false` the brand, reference and organisation names and the website never appear in a query (the planner does not even see them: the brief handed to C6 and C8 is scrubbed, R2 and R3 are not planned), and every query, whoever wrote it, is checked again (word-wise, separator-insensitive, and inside words for names of six or more letters). Emails, phone numbers and key-like strings in a query reject it.
9. **Internal documents never go to a search provider**: R6 uses `InternalSourcePort`; its facts get the same screening and an `internal://` source.
10. **Copyright**: facts are short paraphrases with url and retrieval date; nothing longer is stored.

The tripwire is not a guarantee (OWASP: foolproof prevention does not exist). Layers 3 to 5 hold even if a payload gets past it, which is what the injection suite assumes: it feeds eleven payloads through the screen and five through a model that obeys.

## Cross-check (code)

* Every fact has at least one source with a url (public http(s), no credentials, no private hosts) and a `retrieved_at` date from the engine's clock.
* Near-identical statements of one job (word overlap 0.75 or more) merge and their sources are unioned: that is how a claim gets two sources.
* `verified` is true for a low-impact fact with one source and for a high-impact fact with sources on **two independent registrable domains** (`a.example.com` and `b.example.com` are not independent; a built-in list of multi-part suffixes handles `co.uk` and friends). Other high-impact facts stay `verified=False` ("unverified") with confidence `low`.
* A suggestion is `verified` only if all its facts are, and an unverified suggestion is never `default_included`.
* Ids are deterministic: `F-001` in job order then first-seen order, `RA-001` in C8 order; on a rerun existing suggestion ids are reused by (kind, title) and new ones continue the numbering, so Gate 2 exclusions stay valid.
* Suggestions are capped (12), deduplicated by title, and never repeat a feature the Brief already has.

### What the Spec Builder reads: `applies_to`

`applies_to` is `scope.features:<pack feature id>` for a feature to turn on (the id exists in the archetype's Domain Pack), `scope.features` when the id is unknown, a spec section (`design`, `screens`, `navigation`, `data`, `api`, `roles`, `integrations`, `behavior`), a slot id such as `auth.sso`, or `null`.

## Change handling

`ResearchRequest.previous_note` with `rerun_jobs` re-runs only the listed jobs (spec 3.6 dependency map) and merges: facts of other jobs keep their ids, the listed jobs' facts are replaced (new ids continue the numbering), C8 re-synthesises on the merged facts, the version is bumped and queries and cost add up. An empty list returns the previous note untouched; a rerun that finds nothing keeps the previous note marked `partial`. Without `rerun_jobs`, a previous note only keeps suggestion ids stable.

## Failure modes

The engine never blocks the Plan phase and never shows a raw error.

| What happens | Result |
| --- | --- |
| research off by policy | `unavailable`, reason `policy`, no calls |
| budget out, tripwire, or `BudgetExceeded` on the first call | `unavailable`, reason `budget` |
| budget runs out mid-run | `partial`, reason `budget`, what was found is kept |
| a search or extraction fails (provider, circuit open, bad output) | that query is skipped (`s1_skip_query`, `c7_no_facts`); `partial`, reason `failed` |
| C6 fails or plans nothing usable | deterministic plan from the Brief (`c6_deterministic_plan`) |
| C8 fails | deterministic suggestions from verified facts, never included by default (`c8_deterministic_suggestions`); `partial` |
| every query failed or nothing usable came back | `unavailable`, reason `failed` |
| mock mode and no canned answer matched | `unavailable`, reason `mock_no_fixtures` |
| a result looked like an injection | that result is dropped and logged; the run continues |
| cancellation | `CallCancelled` propagates |

Each spec's `fallback` name is registered in `mde_research.engine.FALLBACKS`; the engine refuses specs whose fallback is not registered. `ResearchNote.unavailable_reason` is also set on `partial` notes (the cause of the gap); consumers should switch on `status`.

## Mock behaviour (offline, no key)

Every spec ships a deterministic `mock_handler`, so the whole step runs offline:

* **S1** returns a canned answer chosen by job and query keywords (`fixtures/search.yaml`): five archetypes with a "features" and a "UX patterns" entry each, gap-fill entries for Deep, one entry per wider R1 angle, R2 (a default and three angles), R3 (the user's own site, a default and three angles), five R4 topics and seven R4 angles, seven R5 services and seven R5 angles, a default per job. The later queries of a plan need two keyword hits so they beat the topic entries, and an entry with a `subject_pattern` echoes the part of the query it captures (a feature, an app, a sensitive area, a service), so a Standard or Deep run gets mostly distinct facts instead of one canned answer many times. Two or three citations on reserved example domains (`example.com`, `.org`, `.net`, path `mock-source`), inline markdown links like a hosted answer, offsets included. With `allowed_domains` the sources are re-pointed at the allowed domains so policy filtering still passes.
* **C6** is the deterministic plan (`c6_plan_research/plan.py`, templates in `fixtures/query_templates.yaml`), also the registered fallback. It fills the per-job number from distinct templates and never repeats a query.
* **C7** behaves like a careful model: up to three statements from the cleaned text, a claim returned once per source marker it carries, kind and impact from the job and a few words, refuses instruction-like text and flags it.
* **C8** returns a canned draft per archetype (`fixtures/drafts.yaml`): feature suggestions with real Domain Pack ids (checked by the tests), patterns, common compliance and integration suggestions, pointers per flag, a brand finding parsed from R3 facts, integration notes, and on Deep one gap.

A suggestion is emitted only when a fact supports it, so the draft always cites real fact ids.

## Call specs

| Call | Route / effort | Max out | Tools | Untrusted | Fallback |
| --- | --- | --- | --- | --- | --- |
| `S1.web_search` | standard / low | 1500 | `web_search` (1 call, low context) | none (its output is untrusted downstream) | `s1_skip_query` |
| `C6.plan_research` | standard / low | 3000 | none | none | `c6_deterministic_plan` |
| `C7.extract` | fast / low | 1200 | none | `search_text` | `c7_no_facts` |
| `C8.synthesize_research` | standard / medium | 2500 | none | `facts` | `c8_deterministic_suggestions` |

C6 is `version=2` (D-078: the prompt states `max_queries_per_job`, the output allowance grew for 20 or more queries); the others are `version=1`. A cassette recorded for C6 version 1 would be stale (none exist yet; the L2 batch records version 2). C8 is a large-tier call in the spec; it stays on the standard route with medium effort until the evals say otherwise.

## Modules

| Module | Job |
| --- | --- |
| `engine` | `DefaultResearchEngine`, `ResearchReport`, `FALLBACKS`, `ResearchSpecs` |
| `context` | `build_brief_context` (Brief + slots to `BriefContext`), `build_privacy`, `scrub_context` |
| `safety` | cleaning, injection tripwire, source selection, statement and query checks |
| `privacy` | `QueryPrivacy` (forbidden names, scrub, clean) |
| `domains` | registrable domains, allow and block rules, safe source urls |
| `crosscheck` | merge, verify, number facts |
| `synthesis` | validate C8's draft into suggestions, pointers, brand, integrations; the C8 fallback; note assembly |
| `internal` | `NullInternalSource` (R6 stub) |
| `staleness` | `is_stale` |
| `config`, `progress` | `ResearchConfig`; progress labels |

## Tests

```powershell
uv run pytest packages/research packages/prompts/tests/test_calls_s1.py packages/prompts/tests/test_calls_c6.py packages/prompts/tests/test_calls_c7.py packages/prompts/tests/test_calls_c8.py -q
uv run pytest packages/research --cov=packages/research/src --cov-report=term-missing -q
uv run pyright packages/research ; uv run ruff check packages/research packages/prompts ; uv run lint-imports
```

`packages/research/tests/research_helpers.py` (re-exported by `helpers.py`) holds `DirectMockGateway` (runs the mock handlers, no gateway import), `ScriptedGateway` (queued outputs or errors per call id, records every `CallContext`) and request builders. The injection suite, the privacy property tests (hypothesis over names and briefs), the cancellation-at-every-call tests and the five-archetype offline run (loopback-only socket guard) are in `test_engine_*.py`. `test_engine_scale.py` covers D-078: the ceilings are reached exactly (12 and 20) with five web jobs and with only R1, R4 and R5, Light stays at 6 or fewer in every combination, the first-query-first order holds for every cap, every planned query is distinct and passes the engine's own privacy and clean checks, and the gap-fill reserve (`CappedGateway` refuses a call that would pass its cap, the way the real gateway does). The shared rule has its own table test in `packages/prompts/tests/test_calls_c6.py`.

## Contract changes: applied and still open

Applied (D-047): `ResearchRequest.references` and `ResearchRequest.brand_url` (without them R2 and R3 cannot run), and `mde-prompts` declares `mde-content`.

Still open, additive and optional (the engine runs without them):

* `C8Input.available_features: list[OptionLite] = []` (pack features not yet in the Brief) and `SuggestionDraft.feature_id` / `ResearchSuggestion.feature_id`: today a live C8 cannot know which pack feature ids exist, so `scope.features:<id>` is reliable only in mock mode (D-048).
* `ExtractedFact.also_stated_by: list[str] = []`: lets C7 report a claim once with several sources instead of once per source.
