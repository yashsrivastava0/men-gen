# 13. The Research agent (design v4)

- **Status:** v4 written 2026-11-11 for the builder (implementation not started). Replaces v3, v2 (earlier the same day) and v1 (2026-11-10). No code and no live calls.
- **What changed from v3 (owner, 2026-11-11):** (1) **Research is organized around what Spec must decide**: features, structure (screens, navigation, roles), flows, data and rules, integrations, design, per-screen content, and mock data. For each it brings **how it is usually done, how common each choice is, and what is current**, with several sources, so Spec starts from a grounded norm (AD-53). (2) **Design conventions and typical screens are researched again** (v3 dropped look norms). (3) **Privacy and security are baseline only** (AD-54): the name and PII filtering, per-program cache isolation and heavy SSRF machinery are removed; the cache may be shared. The worthiness gate is widened: Spec-critical norms are always worth grounding. v3's load-based depth, slot-driven parallel workers, quote check, events and API stay.
- **What changed from v2 (owner, 2026-11-11):** (1) **Depth is derived from what the user said, not from the tier**: Research is never told Quick, Standard or Thorough; it computes its own *research load* from the Brief. (2) **Six searches is too few** as a hard cap; budgets are now adaptive ceilings with coverage-based stopping. (3) Research is an **iterative, slot-driven loop** with parallel per-target workers (what the good deep-research systems do, §1), not one pass. (4) The output is built **for the Spec agent**: indexed by the Brief's own ids, slot-typed, with ready-made digests. (5) A **live event stream** for the front end (test UI now, full UI later). (6) A **source and service policy** (what free and paid services to use, and which not). Decisions AD-49 to AD-52. v2's foreground run, worthiness gate, quote check and trust rules stay.
- **Scope:** agent 2 of 4 in the Context Engine (doc 12 §4.2). Where this doc and docs 02, 03, 05, 12 differ on Research, this doc wins.
- **Compass facts used:** [notes/compass-as-built-2026-11-10.md](../notes/compass-as-built-2026-11-10.md) (the `research` view, `research_requests`, the author's corrections §8b) and the three real exports (`/Users/coder/Desktop/project/men-gen/docs/compass/research-inputs/`).
- **Readings of your words (tell me if wrong):** "runs in the frontend" = foreground and visible; "character search" = targeted, entity-level search; "ChatGPT, Luna, a third provider" = writer model, checker model, one search provider.
- **Evidence labels:** [V] opened in an official or primary source in this pass; [S] secondary (search summaries, blogs, forums); [J] architect judgment, to be measured. Sources: [doc 10](10-research-log.md) rows R-53 to R-64.

## 0. The design on one page
1. **One agent** (`agents/research`), foreground, visible, starting at **Gate 1 approval**. It enriches and never decides (Default or Open tier, never Locked; no scope status; no Spec Graph writes).
2. **It decides its own depth.** From the Brief's content it computes a **research load** (focused, standard, deep). Compass's tier is not an input. Load sets ceilings, not targets: a target stops as soon as its slots are filled.
3. **Orchestrator-worker, code-controlled.** Code is the orchestrator (no free-running LLM agent loop). A planner turns the Brief into **targets**, each with a typed **slot schema** (what Spec needs: fields, steps, providers, fees, rules). One **worker per target** runs in parallel with its own context: write queries, search, triage, read, extract, verify.
4. **Rounds with evidence-aware stopping.** Round 1 covers every target (discovery first). A coverage audit finds unfilled required slots and newly discovered entities; round 2 (and at most a round 3) goes only there, with new distinct queries. Stop when required slots are filled, when a round adds little, or at the ceilings (search count, pages, time).
5. **Queries are specific by construction:** anchor (named entity or domain noun) + place + intent word; several variants per target in parallel (literal, descriptive, local-language), merged by rank fusion; round 2 can restrict to a discovered entity's own domain to read its official pages.
6. **Research is organized by what Spec must decide** (eight jobs: features, structure, flows, data, services, design, screens, local). Each topic is a Spec decision area, searched for **the category norm** (how apps of this kind usually do it, how common each choice is, what is recent) **plus the idea's own details**. Only pure trivia that never changes a decision is skipped (worthiness gate).
7. **Every fact is quote-verified by code**; high-impact facts also need a second model and two independent domains. Synthesis selects; it writes no new facts.
8. **Output `research.v4` is a playbook for Spec:** items indexed by the Brief's ids (FTR, ROL, APP, MOD, ENT, FLW and integration keys), slot-typed, each with **prevalence** (seen in n of N sources, core, common, advanced or emerging) and **recency** (newest source date, trend), `applies_to`, tier, sources and flags; computed `norms` per area; `suggestions` beyond the Brief (never scope); a `confirm` list; and code-rendered digests per Spec lane (blueprint, integrations, design, screens, mock, verify).
9. **Live events** (`research.event.v1`) stream plan, queries, sources, facts and progress to the UI; the same contract serves the test front end and the future full one.
10. **Providers:** writer Luna; checker a second model; one search provider behind a port (Tavily or Serper, set by a bake-off); own `FetchPort` (trafilatura + PDF text layer, public URLs only). Free options are used where they fit (§8).
11. **Fails soft** (partial, skipped) and never blocks Spec.

## 1. Design basis: how deep-research systems work, and what we take
| System (evidence) | How it works | Lesson we take | What we do not copy |
|---|---|---|---|
| **Anthropic's multi-agent research** [V, primary post] | A lead agent plans and saves the plan; subagents search in parallel, each in its own context; a separate citation agent attaches sources. Effort scales with the query: simple fact-finding "1 agent with 3-10 tool calls", direct comparisons "2-4 subagents with 10-15 calls each", complex research "more than 10 subagents". Parallel subagents and tools cut time "by up to 90%" for complex queries. Start with short broad queries, then narrow. Agents use about 4× the tokens of chat, multi-agent about 15×. Failures: spawning 50 subagents for simple queries, endless searching for sources that do not exist, SEO farms outranking primary sources, duplicated work from vague tasks | Parallel workers with isolated contexts; explicit effort scaling; short-then-narrow queries; guardrails against endless search; source-quality ranking; detailed task briefs per worker; checkpoint and retry | An LLM lead that spawns agents freely (our code orchestrates); 15× token spend for every run |
| **LangChain Open Deep Research** [V, primary post] | Scope, then a condensed **research brief**, then a supervisor delegating to parallel researchers with isolated context, then **one-shot report writing**. Writing sections in parallel produced "disjoint" reports. Researchers return cleaned findings, not raw pages | Brief as north star; isolate context per sub-topic; parallelize research, not writing; return cleaned findings | Open-ended supervisor loop |
| **GPT Researcher** [S, README and write-ups] | A planner generates questions; one crawler agent per question runs in parallel; each source is summarized and source-tracked; a publisher aggregates | Per-question parallel workers with source tracking | Free-text report as output |
| **Gemini Deep Research** [S, third-party guides] | Collaborative planning: it returns a plan for you to refine before running; streaming progress with thought summaries; background execution | Show the plan first; stream progress; let the user steer | A free-form chat refinement loop |
| **"Don't Stop Early" (ACL industry 2026), ADORE, VeriTrace** [S, excerpts only] | Coverage-driven objectives; evidence-based sufficiency criteria; section-level coverage audits that trigger targeted follow-up retrieval; stop on coverage and stability, not a fixed iteration count; removing termination criteria hurt quality most | **Coverage audit and evidence-aware stopping**, which is exactly what typed slots give us | Long free-form reports |
Not checked in this pass: STORM (perspective-guided question asking) and OpenAI's deep-research internals. **Net design:** the structure of Anthropic and LangChain (parallel isolated workers, a brief, effort scaling), the coverage-driven loop of the 2026 papers made concrete by **typed slots**, Gemini's plan-first and streaming UX, and our own **quote verification**, which none of the above enforces in code.

## 2. Role and boundary
- **What it is:** a folder `agents/research/` (package `mde_research_agent`, AD-33). **Boundary test (AD-38):** T1 (the only agent with web access, which is untrusted input).
- **Not a step of Compass** (Compass talks to you; Research never does) **and not a lane of Spec** (Spec must never read the open web).
- **Outputs:** `research.json` (v3; read by Spec; reaches the front-end engine only inside the spec) and `research_evidence.json` (internal).
- **Never does:** decide scope, set a tier above Default, write the Spec Graph, hold the chat transcript, call another agent, search for a private individual, or research a topic Compass marked `declined`.

| Agent | Role | Gets from or gives to Research | Never |
|---|---|---|---|
| **Compass** | Interview, Brief, approved intent-level structure, research requests | Gives the approved Brief (research view + trust flags) | Searches the web; goes below intent level |
| **Research** | External facts: typed, quoted, Default tier, indexed by Brief ids | Returns `research.json` and live events | Sets scope; Locked; reaches the front-end directly |
| **Spec** (Product ‖ Design, Freeze, Screens) | Decides. Starts from Compass's structure and Research, refines, owns routes, navigation and per-screen detail (AD-44) | Reads its slice and digest | Reads the open web; marks suggestions `in` |
| **Verify and package** | Independent proof: BN matrix, "research-backed" ratio, **drift check** that spec facts still match research quotes | Reads `claims_used` and slices | Shares writer context or vendor |
| **Front-end engine** | Builds the Expo app from slices of `APP_SPEC.md`, the single output; research arrives as sourced Default facts inside it | None directly | Reads `research.json` or the web |
| **Backend and QA** (later) | Backend: Contract, services, rules. QA: acceptance and rules | None directly | Not designed yet |

## 3. Inputs, trust and the research load
### 3.1 Inputs
The approved Brief's **research view** (idea, geography, languages, audience, references, feature intents, glossary, `research_requests`, `search_hints`, declined, module and entity names, `domain_facts`, `guidance.focus`), Compass trust flags (CR-1 to CR-3, §15), builder needs BN-01..14, Stack Kit facts, the authority registry (data file), and the per-program setting (web research on or off). **Never:** the transcript (AD-20), uploaded documents as text, other agents' outputs, contact details of you or your staff.
**Not an input: the tier.** Compass's `size_band`, `depth`, `search_budget` and `max_requests` in `guidance` are ignored. They came from how many questions you chose to answer, not from what your app needs.

### 3.2 Trust rules (Compass author's corrections, 2026-11-11)
1. Read each value by its **tier** (Locked = your verified words; Default or assumed = an AI guess). "Approve all" is not review.
2. `fallback_count` is not a full failure counter (timeouts of about 60 s with 0 tokens are missed). Quick's requests are **template text** when they show the patterns in §6.5. Research recomputes failures from `spend.by_call` when present.
3. The structure is a **hint** (module names to cluster targets). Thorough's is mostly fallback; never anchor a query on a screen count or name, and never use structure counts in the load score.
4. `later` does not mean defer. Priority only ranks targets.
5. Roles and features may be duplicated (singular and plural; two names for one thing): code dedupes before building terms.
6. `open_questions` and assumptions are not searched; junk tokens ("FASTag", "pilot_small") are ignored.
7. Weak Locked values (for example the Thorough geography "Pune" from the quote "Data stays in India" while six cities are stated) set `geo_uncertain`: use the country plus every stated city.
8. **Your own brand name alone is not a search target** (Sunrise Yoga; TruckSetu when it is your company): its facts come from the Brief. A website or reference URL you gave is read as a seed source. Named *references* are searched; if a reference cannot be told from your brand it is `ambiguous`.

### 3.3 The research load (computed by code from the Brief; the tier is not used)
`load_score = deduped features + facts of kind rule, compliance, integration_detail or nfr + 3 × apps + 5 × sensitive-data flags + 2 × named references`. Classes: **focused** below 25, **standard** 25 to 60, **deep** above 60 [J, calibrated on the three real runs]. Real inputs: Quick yoga = 6 + 8 + 3 = **17 (focused)**; Standard produce = 9 + 10 + 12 (+ sensitive flags) = **31 to 41 (standard)**; Thorough freight = 30 + 35 + 18 + 10 + 2 = **95 (deep)**. A "Quick" interview with a regulated, integration-heavy idea scores higher; a "Thorough" interview about a simple idea scores lower. The class is shown in `input_quality` and in the first live event.

## 4. Jobs: research organized by what Spec must decide
**Principle (owner, 2026-11-11).** To build an app of a given kind, Spec decides its features, its structure (screens, navigation, roles), its flows, its data and rules, its integrations, its design, the content of each screen, and its mock data. Research brings, for each of those decisions, **how it is usually done, how common each choice is, and what is current**, from several sources, so Spec starts from a grounded norm instead of a guess, and then adds what is special about *this* idea from the Brief's facts. Research serves the downstream engines first; the front-end engine benefits through a better spec.
Eight jobs. Every target carries a `spec_need` and a `lane`, and typed slots (minimums are starting points; more verified detail is better):
| Job | Spec decision it feeds | Finds | Required slots (starting minimums) | Lane |
|---|---|---|---|---|
| **features** | Scope (BLUE-1) | Comparable and local apps; the feature set by commonness (core, common, advanced, emerging); recent additions; pricing models | 3+ competitors each with 5+ features (level); norms for 8+ features with prevalence | blueprint |
| **structure** | Screens, navigation, roles (BLUE-2) | The usual screen inventory per app and role; navigation patterns and when they are used; the usual roles and what each does; module grouping | 8+ typical screens with purpose and the role that uses them; 1+ navigation pattern; roles with 3+ typical tasks | blueprint |
| **flows** | Journeys (BLUE-2, BLUE-3) | Usual journeys per role with steps, alternates and exceptions; onboarding; empty, failed and offline cases | per main journey: 4+ steps, 1+ alternate, 1+ exception | blueprint, screens |
| **data** | Entities, lifecycles, rules (BLUE-3) | Usual entities, fields (type, required, values, validation), relations, statuses and transitions, business rules and formulas, KPIs; regulatory rules as information | per main entity: 6+ fields and states where a lifecycle exists; rules with value, unit and source | blueprint |
| **services** | Contract and integrations (BLUE-4, BN-10) | Providers by need (payments, OTP and messaging, maps, telematics, identity, government portals): auth, endpoints or events, sandbox, limits, fees, regional fit, current status | per need: 2+ providers with auth, events and sandbox | integrations |
| **design** | Look and feel (DSGN) | Category UI and UX conventions: layout and navigation patterns, onboarding, component patterns, colour and tone mood, typography and density, iconography, motion, accessibility; platform guidelines (Apple HIG, Material) as official sources; script and font needs; recent design trends in the category | 6+ `design_convention` items across 4+ aspects; a script need when the locale is not Latin | design |
| **screens** | Per-screen content (SCRN) | For each usual screen pattern: sections, key components, actions, states (loading, empty, error, success), validations, microcopy conventions | patterns for the 5+ most important screens (more for deep loads, per module cluster) | screens |
| **local** | Mock data and formats | Formats and ID patterns with validation patterns and examples; local terms and units; realistic names, price ranges, edge cases | formats where unusual; 10+ realistic examples for the main entities | mock |
- Regulations and compliance are information inside `data` (rules) and `services` (portals), flagged `legal-adjacent`.
- **Targets come from the union** of the Spec-need areas that apply to this kind of app, Compass `research_requests`, `guidance.focus` and high-impact `domain_facts` (Standard's `focus` lacks topics its requests cover; `focus.compliance` mixes enum values and sentences, so it is a hint).
- **Two layers per job:** the **category norm** (how apps of this kind usually do it, from several comparable sources) and the **idea-specific details** (what this Brief's unusual facts need, for example an e-way bill for a freight app).
- **Norms, not just facts.** For features, screens, flows, entities, integrations and design conventions, code computes **prevalence** (seen in n of N distinct sources or competitors) and a level: `core` (most), `common`, `advanced`, `emerging` (recent and rising). Fewer than 3 sources: `limited_evidence`.
- **Latest.** Features, design and services targets prefer sources from the last 24 months (a time range on the query; `published_on` and `age_months` on every item; older than 24 months flagged `stale`); `trend` items record what changed recently. Regulations and provider facts use the newest official source.

## 5. The run
Trigger: the orchestrator sees `gate1.approved` (Brief version and hash); one run per program. Agents never import each other.

### 5.1 Steps
| # | ID | Who | What |
|---|---|---|---|
| 0 | RSCH-0 | code | **Intake.** Verify Brief hash = approved hash (RV-13). Build `ResearchInput`: dedupe, tiers, `input_quality`, **load score and class**, own-brand terms, declined topics. Emit `run.started`. |
| 1 | RSCH-1 | Luna | **Plan.** A research brief (idea, places, languages, constraints) and **targets**: job, entity, region, languages, question, `decision_unlocked`, links (RR and BN ids, Brief ids), priority, slot schema. Template or thin requests are rebuilt from facts. Emit `plan.ready` (user-facing target labels). |
| 2 | RV-14 | code | **Worthiness gate** (§6.1): drop targets whose answer would not change a spec decision or that the model already knows. Merge targets by entity. Rank. Emit the final plan. |
| 3 | W-1..W-n | **one worker per target, in parallel, isolated context** | See §5.2. Round 1 for every target, pipelined (no global barrier). |
| 4 | RSCH-1c | code, then Luna | **Coverage audit** (§5.3): filled required slots per target; entities discovered. The gap analyst (Luna) proposes follow-up queries **only** for unfilled required slots and for discovered entities worth deepening. |
| 5 | W-1..W-m | workers | **Round 2** (and a round 3 at most for standard and deep loads), same pipeline, restricted to the gaps. |
| 6 | RSCH-3 + RV-05 | code + Luna (no tools) | **Assemble.** Code builds the indexes, the feature matrix, prevalence counts and digests. One Luna call (one-shot, as LangChain found parallel writing disjoint) selects and orders per Spec lane and writes `use_note`s. It writes no new facts. |
| 7 | commit | code | Write `research.json` and `research_evidence.json`; emit `run.finished`. |

### 5.2 The per-target worker (a fixed pipeline, not a free agent)
1. **Write queries** (Luna, small; sees only its target, slot schema, anchors and trust flags): 2 or 3 **variants** in parallel: *literal* (the named entity in quotes with `exact_match`), *descriptive* (anchor + place + intent), *local-language* when the region's language is not English.
2. **Search** all variants in parallel through `SearchPort`; **merge by reciprocal rank fusion** and dedupe by canonical URL; apply source-class ranking and the authority registry.
3. **Triage** (code score; one batched model call only when more than about 12 candidates): choose the top 3 to 5 pages by snippet relevance, source class and domain diversity (at most 2 per domain).
4. **Read:** get page text (provider content, else `FetchPort`); **select passages** around the slot terms (BM25 windows), not blind truncation.
5. **Extract** (Luna, small, no tools), in parallel per page, into the slot-typed record: paraphrase of at most 40 words and a verbatim quote of at most 25 words.
6. **Verify (code):** quote substring check (RV-02), source in the fetch log (RV-03). Update slot fill. Emit `fact.found` and `fact.verified`.
Workers do not see each other's raw pages (context isolation); they return only verified, typed items.

### 5.3 Coverage audit and stopping (evidence-aware)
- A required slot is **filled** by at least one verified item; for `impact: high` it needs two independent domains (or it is flagged `single-source`).
- A target is **covered** when all required slots are filled. Covered targets stop immediately and spend no more budget.
- **Follow-ups** target only unfilled required slots, with queries that differ from earlier ones (no repeats); at most 2 follow-ups per target. After that the target is `not_found` (a gap with the attempted queries). This is the guard against endless searching for sources that do not exist [V, Anthropic].
- **Entity deepening:** a competitor, provider or authority found in round 1 that matches a required slot gets one query restricted to its own domain (`domain_restrict`), to read its official pages.
- **Stop** when every target is covered or `not_found`, **or** a round fills fewer than 10% of the remaining required slots, **or** a ceiling is hit (searches, pages, time box), **or** Skip is pressed. On a ceiling the run finishes extraction of pages already read and commits `partial`.

## 6. Query strategy
### 6.1 Worthiness gate: search what Spec needs grounded
A target is searched if it feeds a Spec decision (`spec_need`) **and** at least one holds: it needs **current or regional grounding** (providers, fees, rules, trends); a **norm needs evidence of how common it is** (features, screens, flows, entities, design conventions); it names an **entity** (from the Brief or found in round 1); it involves a number or an integration's specifics. Not searched, answered from model defaults and flagged `assumed`: pure trivia that never changes a decision (a currency symbol, common date formats). **Norms for the Spec-critical areas are always worth grounding, even where the model already knows them**, because they must be sourced, current and quantified.

### 6.2 Query anatomy (code-checked, RV-06 and RV-14)
Every query has an **anchor** (named entity from references and glossary, a discovered entity, or a domain noun in your own words), a **place** (from Locked geography, or country plus every stated city when `geo_uncertain`), and an **intent word** (features, fees, rules, API, sandbox, limits). **Category-only queries are rejected.** Queries are short first (about 4 to 12 words) and narrowed in later rounds [V, Anthropic]. At most 25 words.

### 6.3 Variants and fusion
Per target, 2 or 3 variants run in parallel (literal, descriptive, local-language). Results are merged by **reciprocal rank fusion** (code), so a page that several variants return rises. This improves recall without more model calls, and it is the cheap form of query decomposition.

### 6.4 By job
- **features:** discovery first (2 variants: who the comparable and local apps are; what they offer), then deepen on discovered apps (their feature, pricing and help pages; `domain_restrict`). Time range of 24 months for "latest". Compute prevalence across at least 3 apps.
- **structure and screens:** from the same competitor pages, app-store listings and product tours (public pages), plus UX articles for the category; extract screen inventories, navigation patterns and per-screen sections and states. Platform guidelines (Apple HIG, Material) are official sources for patterns.
- **flows:** help-centre and onboarding pages, how-it-works pages, process descriptions; steps, alternates, exceptions.
- **data:** domain explainers, official forms and documents (for fields and validations), provider and regulator pages (for rules and formulas); official domains from the authority registry preferred for rules; one-year time range for fees and rules; two sources for numbers; rules flagged `legal-adjacent`.
- **services:** discovery of providers by need, then `domain_restrict` to each provider's documentation (auth, endpoints or webhooks, sandbox, limits, fees, current status). The Stack Kit wins on Expo compatibility.
- **design:** category design articles and case studies, platform guidelines, accessibility guidance, and public pages of the best-known apps in the category; conventions are flagged as conventions (lower confidence), with `seen_in` counts; recent trends from the last 24 months.
- **references and exact entities:** quoted phrase with `exact_match`. Zero hits means `not_found`: no claims, and Spec treats the name as yours. A collision with another company means `ambiguous` with candidates, confirmed at G2. Never substitute a similarly named real company.
- **local:** an English query plus one in the region's language when it is not English (Hindi, Marathi, Kannada, Arabic, Swahili); Hinglish (`hi-Latn`) is searched in English with transliterated terms (Compass sends `hi` for it today: flag a mismatch, never guess).
- **Discovery names from model memory** appear only in discovery queries and are labelled `entity_from_model`; they are never facts.
- **Seeds come from Locked facts first;** Default-tier values give `assumption_derived` queries, and results never raise a tier.

### 6.5 Template and thin requests (the real Quick run)
Detected when `quality.seed_source` is `template` (CR-1), the request `origin` is `template` (CR-3), a topic repeats a word ("app apps"), the same audience list appears in most requests, or `search_hints` are empty and no request names a regional entity. Action: `plan_source: rebuilt` from `focus`, features (deduped), roles, business model and `domain_facts`.

### 6.6 Dry run on the three real runs (paper test)
| Run | Load | Worthy targets (estimate) | Mapping |
|---|---|---|---|
| Quick yoga, Bengaluru | 17, focused | 6 to 8 | Template requests rebuilt: local booking apps and class-pack models (2 variants + deepen), payments (UPI, card), SMS and OTP rules, fitness-service rules; formats and look not searched; no named reference; own brand excluded |
| Standard produce, Nashik | 31 to 41, standard | 10 to 12 | 7 requests become 7 targets (fees and commission models, settlement, transport coordination, trade rules, accounts and staff access, messaging, rules); plus 3 from facts (FPO and buyer roles, delivery tracking, weekly-availability workflow) |
| Thorough freight, India | 95, deep | 16 to 22 | 20 requests merge to about 18 targets: 2 landscape, 1 exact reference (probably your own company, so `ambiguous` or `not_found`, no claims), 4 rules, 6 services (telematics, FASTag, payment gateway, messaging, e-way bill API, GST e-invoice API), 4 domain (truck register documents, hired-truck dispatch, POD, claims), 1 local (ID formats) |
Findings that changed the design: the load must come from content, not from the number of requests (those were set by the tier); targets need the union of sources; own-brand names occur in two of three runs.

## 7. Budgets: soft defaults, result first
**Owner direction (2026-11-11): focus on the result; the numbers below are starting defaults the builder tunes for completeness and quality, not hard limits, and money is a configurable budget (USD 4.00 approved for the first build and live tests, expected to be raised).** Time boxes are targets: if coverage is still improving the run may continue (the UI says "taking a bit longer"); Skip is always available. The coverage-based stop (R19) is what keeps runs from searching forever.

Compass's tier numbers (6, 12, 20 searches) are not used. **Is 6 enough? No.** For a focused app with 6 to 8 worthy targets, one search per target leaves nothing for discovery, deepening, retries or the second source that high-impact claims need; Anthropic's own scaling rules put even a simple fact-finding task at 3 to 10 tool calls and a comparison at 10 to 15 per sub-task [V]. Search is cheap (about USD 0.005 to 0.016); **time** is the limit, and parallelism handles time.
| | **focused** | **standard** | **deep** |
|---|---|---|---|
| Load score [J] | below 25 | 25 to 60 | above 60 |
| Rounds (maximum) | 2 | 3 | 3 |
| **Search ceiling** (all rounds) | 20 | 40 | 80 |
| Expected searches (coverage stop) | 10 to 14 | 20 to 30 | 40 to 60 |
| Pages extracted (ceiling) | 24 | 60 | 140 |
| Parallel lanes (search / extraction) | 6 / 8 | 8 / 12 | 10 / 16 |
| **Time box** [J] | 3 min | 6 min | 10 min |
| `research.json` size (tokens, soft) | 12k | 20k | 32k |
| Per-lane slice (tokens, soft) | 4k | 7k | 11k |
| Estimated cost [J] | USD 0.05 to 0.20 | 0.20 to 0.70 | 0.80 to 2.50 |
- **Per target:** round 1 uses 2 or 3 variants; follow-ups at most 2; a covered target stops at once.
- **Time (focused, pipelined) [J]:** plan 15 to 25 s; round 1 about 50 s; audit and follow-up planning 15 s; round 2 about 45 s; assemble 20 s: about 2.5 to 3 min. Per-call timeouts: search 8 s, page fetch 10 s, model 45 s, one retry; **a timeout or a 0-token call counts as a failure** and is shown in events (the Compass undercount lesson).
- **Cache:** per program, keyed by normalized query, country, language and a freshness bucket; never shared across programs (queries reveal ideas). Time to live: 14 days for general facts, 2 days for prices and fees [J].
- One run per program at a time; provider rate limits with backoff; transport errors retried up to three times.
- **Speed techniques kept simple:** parallel at every level; pipelined workers; batched triage and checker calls; passage windows to cut tokens; small fast model for extraction; early stop by coverage; static prompt prefixes cached (AD-26); no extra services for the first build.

## 8. Sources, services and providers
**Models by role.** Writer **Luna** (`gpt-6-luna`): plan, queries, extraction, assembly (planning and assembly at higher effort, extraction at low). **Checker**: a second model (ChatGPT-class now, Haiku 5.5 later) on `impact: high` items only; it sees claim and quote only. Independence is weaker while both are OpenAI, so **code checks are primary** (quote substring, two independent domains, official first).
**Search provider: one at a time behind `SearchPort`.** **Owner decision 2026-11-11: the owner provides accounts for two providers, so the bake-off compares Tavily and Serper (about 40 searches on the real requests of the three exports; needs your yes). Brave stays a documented candidate; its adapter is built later, only if wanted.**
| Candidate | Why | Notes |
|---|---|---|
| **Tavily** (default until measured) | Search plus page text in one call; `country`, `language` + `filter_by_language`, `include_domains` with prefer or restrict, `exact_match`, `time_range` [V]; 1,000 free credits per month [S, official credits page excerpt] | `country` is a lowercase name and only for `general` [V]; price per credit about USD 0.008 [S] |
| **Brave** (deferred; not in the first bake-off) | `country`, `search_lang`, `freshness`, `extra_snippets`, `goggles` [V]; USD 5 monthly credit, USD 5 per 1,000 requests [S] | Snippets only, so it needs `FetchPort`; supported `search_lang` values unverified |
| **Serper** (Google results) | `gl` country and `hl` language parameters; 2,500 free queries [S]; Google's index may serve Indian local queries better | Links and snippets only, so it needs `FetchPort`; official pricing page not confirmed |
**Page reading: our own `FetchPort`, in scope for the build.** Main-text extraction with **trafilatura** (free, Apache-2.0, server-rendered HTML only, no JavaScript) [S], plus the **PDF text layer** (regulators publish PDFs; matches Compass's text-layer rule), SSRF rules, robots, redirect and size limits. JavaScript-heavy pages: skipped and marked `no_content`; **Crawl4AI** (free, Apache-2.0, Playwright) [S] is a Phase 2 option.
**Free and other services, with a verdict:**
| Service | Verdict |
|---|---|
| Tavily free credits, Brave monthly credit, Serper free queries | Use for development and the bake-off; paid use decided by the bake-off |
| trafilatura, a PDF text extractor | Adopt (free) inside `FetchPort` |
| Crawl4AI (self-hosted) | Phase 2, only for JavaScript-heavy official pages |
| Jina Reader (URL to markdown; free allowance, limits unconfirmed) | Not default: a third party sees every URL you read |
| SearXNG (self-hosted metasearch) | Development fallback only: upstream engines block datacenter IPs and it adds operations work [S] |
| Apple iTunes Search API (free, no key, about 20 calls per minute) | **Not used:** its terms limit use to promoting store content [V]; app descriptions and fields are not documented |
| Google Play scrapers | **Not used:** no official API; terms risk |
| Wikipedia, Wikidata, OpenStreetMap Nominatim | Optional later for entity and place disambiguation (low value now) |
| Answer engines: OpenAI web search (about USD 10 per 1,000 [S]), Anthropic web search (USD 10 per 1,000 [V, doc 10]), Gemini grounding (about USD 14 per 1,000 queries [S]), Perplexity search (about USD 5 per 1,000 [S]) | **Not on the evidence path:** the model picks the queries and results stay inside its turn, so code cannot run lanes or check quotes |
| Google Custom Search | Out: closed to new customers (doc 10) |
Pre-researched, human-reviewed **reference packs** (India payments and messaging, GST and e-way bill basics, DPDP) are a Phase 2 optimization that removes repeated searches; they are data, never code.

## 9. The output: `research.json` (v4), a playbook for Spec
### 9.1 Principles
Spec reads it directly to produce the Blueprint, structure, Design and Screens. It is a **playbook per Spec lane**, **indexed by the Brief's own ids**, with each choice's **commonness and recency**, short typed fields, enumerations, no markdown inside fields, and a short `label` per item for rendering. Every item: `id` (`RI-###`), `job`, `spec_need`, `kind`, `label` (at most 8 words), `claim` (at most 40 words), `use_note` (at most 15 words), `slot`, `impact`, `confidence`, `tier` (`default` or `open`, never `locked`), `flags`, `applies_to[]`, `sources[]` (id, domain, `source_class`, URL, retrieved and published dates), `independent_domains`, `prevalence` (`{seen_in, of, level: core|common|advanced|emerging|limited_evidence}`), `recency` (`{newest_source, age_months, trend: stable|rising|declining|unknown}`), `request_ids`, `target_id`, and a `data` object by `kind`. Quotes stay in the internal evidence file.
`applies_to` entries: `{kind: "FTR|ROL|APP|MOD|ENT|FLW|INT|NEW", id: "<Brief id or integration key or null>", relation: "expects|constrains|refines|alternative|suggests"}`. `NEW` means "not in the Brief"; Spec decides.

### 9.2 Shape
```json
{
  "schema": "research.v4",
  "program_id": "<id>", "brief_hash": "<hash>", "status": "complete|partial|failed|skipped",
  "profile": {"load_score": 0, "load_class": "focused|standard|deep", "rounds": 0},
  "input_quality": {"requests": {"trusted": 0, "template": 0, "thin": 0}, "plan_source": "from_request|rebuilt|mixed",
                    "degraded": false, "failing_probes": [], "structure_source": "model|mixed|fallback", "geo_uncertain": false},
  "budget": {"searches_used": 0, "pages_read": 0, "seconds": 0, "usd": 0.0, "failures": 0},
  "coverage": {"targets": 0, "covered": 0, "not_found": 0, "slots_required": 0, "slots_filled": 0},
  "by_feature": {"FTR-001": ["RI-004"]}, "by_role": {"ROL-001": []}, "by_entity": {"ENT-002": []},
  "by_flow": {"FLW-003": []}, "by_integration": {"payments": []},
  "items": [],
  "norms": [{"area": "feature|screen|flow|entity|integration|design", "key": "<normalized key>", "label": "<text>",
             "prevalence": {"seen_in": 0, "of": 0, "level": "core"}, "recency": {"newest_source": "<date>", "trend": "rising"},
             "item_ids": [], "matches_brief": "<Brief id or null>"}],
  "suggestions": {"features": [], "screens": [], "fields": [], "states": [], "validations": []},
  "defaults_proposed": [{"need": "BN-10", "item_ids": ["RI-020"], "default": "<text>", "state": "defaulted"}],
  "confirm": [{"item_id": "RI-031", "why": "number or legal fact the owner must confirm"}],
  "gaps": [{"target_id": "T-07", "reason": "not_found|budget_stop|provider_down|not_run|not_worthwhile|conflict_unresolved|user_cancelled", "queries": []}],
  "conflicts": [],
  "slices": {"blueprint": {"item_ids": [], "digest": "<text>"}, "integrations": {}, "design": {}, "screens": {}, "mock": {}, "verify": {}},
  "claims_used": [],
  "dropped": {"quote_not_found": 0, "not_supported": 0, "size_cap": 0, "forbidden_pattern": 0}
}
```
Angle-bracket values are placeholders. **`data` by kind (closed list):**
| kind | `data` fields | Spec consumer |
|---|---|---|
| `competitor` | name, kind, region, url, platforms, pricing_model, features[{feature_key, level}], screens[{screen_key}] | blueprint (scope, structure) |
| `reference_check` | name, status (`found`, `not_found`, `ambiguous`), candidates, features, flows | blueprint (scope) |
| `screen_pattern` | screen_key, purpose, role, sections[], key_components[], actions[], states[{state, content}], validations[], microcopy_notes | blueprint (structure), screens |
| `navigation_pattern` | pattern (bottom_tabs, drawer, stack, dashboard_hub, wizard…), when_used, typical_items[] | blueprint (structure), design |
| `role_pattern` | role, typical_tasks[], typical_permissions[] | blueprint (roles) |
| `workflow` | name, role, steps[], alternates[], exceptions[], empty_error_offline_cases[] | blueprint (journeys), screens |
| `entity_pattern` | entity, fields[{name, type, required, values?, validation?}], relations[], kpis[] | blueprint (domain) |
| `lifecycle` | entity, states[], transitions[{from, to, trigger}] | blueprint (domain) |
| `rule` | statement, subject, value, unit, formula?, authority | blueprint (domain rules), screens |
| `service_option` | need, provider, region_fit, auth_method, endpoints_or_events[], sandbox, constraints[], fees, limits, integration_notes, current_status | integrations (contract), verify |
| `compliance_note` | topic, authority, summary, effective_date, `legal_adjacent: true` | blueprint (NFR), verify |
| `design_convention` | aspect (layout, navigation, onboarding, components, color_mood, typography, density, iconography, motion, accessibility), convention, examples[], applies_to_category | design |
| `script_need` | script, languages[], direction, font_notes, text_expansion | design |
| `local_format` | kind (currency, phone, address, date, number, id_format), pattern, example, validation | design, mock, screens |
| `data_hint` | kind (name_style, price_range, unit, edge_case, sample_record), range or examples | mock |
| `trend` | topic, what_changed, since, direction | blueprint, design |
- **`norms[]` are computed by code** from the items: each normalized key (feature, screen, flow, entity, integration, design aspect) gets `seen_in` n of N distinct sources or competitors, a level (`core` when seen in most, `common`, `advanced`, `emerging` when recent and rising, `limited_evidence` under 3 sources), `recency`, and `matches_brief` (fuzzy match to the Brief's ids). Unmatched norms go to `suggestions` (features, screens, fields, states, validations): always `undecided`, never scope (AD-42). The model assigns normalized keys; code reconciles them.
- **`confirm[]`** lists numbers and legal-adjacent facts the owner should confirm at G2 or G3. **`claims_used`** is filled later by Spec for Verify's drift check.
- **Slices (one per Spec lane):** `blueprint` (features, structure, flows, data, rules), `integrations` (services), `design` (design conventions, script needs, formats), `screens` (screen patterns, flows' exceptions, validations), `mock` (data hints, formats), `verify` (confirm list, defaults, compliance). Each is item ids plus a code-rendered **digest**: a compact, table-style text, easy for a model to read: for example the top norms with their prevalence and level, the typical screen inventory, the entity field tables, the provider comparison.
- **Depth ladder (what Spec gets by load class):** **focused** gives a pattern sheet (core and common features with prevalence, 8 to 12 typical screens and one navigation pattern, the main entity's fields and states, 3 to 5 service facts, 6 design conventions, formats and sample-data hints); **standard** adds a competitor matrix (4 to 6), flows with exceptions, entity patterns and lifecycles for the main records, service options with contracts, screen patterns for the key screens, a rules list; **deep** adds playbooks per module cluster (Compass modules grouped to at most 8 clusters), integration notes per external system, role workflow exceptions, a compliance register, realistic data by entity, and design trends. Anything not covered is in `gaps`; Spec then uses module defaults flagged `assumed`.
- **`research_evidence.json` (internal):** fetch log, stored page text, extractions, quote ids, support-check results, the full query log with target ids, the event log. Never read downstream.

## 10. Live events and API (for the test front end now, the full one later)
**Transport:** server-sent events with `Last-Event-ID` resume; events are stored (replay after a reload); the same contract for every front end. **User-facing `label` text is built by code from templates and the Brief's `shown_to_user_as` strings, localized; never written by a model at run time.** Events carry domains and titles, never page text or quotes, and no personal data (RV-17).
```json
{"id": "evt_0042", "seq": 42, "ts": "<utc>", "run_id": "<id>", "type": "query.issued",
 "target_id": "T-03", "round": 1, "phase": "search",
 "label": "Looking up payment providers in Bengaluru",
 "detail": {"query": "UPI payment gateway class pack subscription India", "variant": "descriptive", "lang": "en", "country": "in"},
 "counters": {"searches_used": 7, "searches_ceiling": 20, "pages_read": 9, "facts_verified": 5, "targets_done": 2, "targets_total": 8, "elapsed_s": 41, "eta_s": 70}}
```
| Event type | When | What the UI shows |
|---|---|---|
| `run.started` | Intake done | "Researching your idea", load class in plain words (focused, standard, deep), time box |
| `plan.ready` | After planning and the gate | The list of topics (targets) with user-facing labels; topics dropped by the gate shown as "we already know this" |
| `target.started`, `target.covered`, `target.not_found`, `target.cancelled` | Per target | Topic row status; **the user can cancel a topic** (`POST .../targets/{id}/cancel`) |
| `query.issued` | Each search | The query text, country, language |
| `search.results` | A search returned | Count and top domains |
| `page.reading`, `page.read`, `page.skipped` (with reason: `no_content`, `paywall`, `blocked`) | Per page (throttled) | Domain and title |
| `fact.found` | An extraction returned (unchecked) | A short preview marked "checking" |
| `fact.verified`, `fact.rejected` | After the quote check and the checker | Moves the item to "checked"; rejections counted, not shown |
| `entity.discovered` | A competitor, provider or authority found | "Found: <name>" chips |
| `round.completed` | End of a round | Coverage bar (slots filled of required) |
| `gap.detected` | After the audit | Which topics will get a second look |
| `warning` | Slow or failing provider, timeout, rate limit | Plain-language note, never an error dump |
| `run.finished`, `run.skipped`, `run.failed` | End | Counts and status; the findings page opens |
**HTTP** (`create_app()`): `POST /research/runs` (program id, Brief hash), `GET /research/runs/{id}` (status), `GET /research/runs/{id}/events` (SSE, resumable), `POST /research/runs/{id}/skip`, `POST /research/runs/{id}/targets/{target_id}/cancel`, `GET /research/programs/{id}/latest` (`research.json`), `GET /research/programs/{id}/review` (items with links and dates), `POST /research/runs/{id}/items/{item_id}/reject`. **Shared contracts:** `agents/research/contracts/research.v4.schema.json` and `research.event.v1.schema.json`; the evidence schema stays in `agents/research/`. **Store:** PostgreSQL only.
**Findings page ("What we found"):** a playbook view per Spec lane, a norms view (prevalence and recency), and tabs per job; each item shows the label, claim, source domain, date, link, confidence and flags; suggestions are toggles, **off by default**; you can reject any item. Spec starts when the run commits; toggles and rejections are collected at **G2** as change sets (R7). G2 records the `research.json` hash (I-2). A later Brief version re-runs only affected jobs, keyed by Compass `_section_hashes` (scope and apps: landscape and references; integrations and data: services and rules; context and experience: local).

## 11. Rules
- **R1. Enriches, never decides.** No scope status, approval, or change to a value you or a document set (AD-42).
- **R2. Tier is Default or Open.** Never Locked (I-6).
- **R3. No claim without a quote,** found by code in stored page text (RV-02).
- **R4. Synthesis selects and annotates; it writes no new facts** (RV-05).
- **R5. Pages are data.** Extraction and synthesis have no tools; output schemas have no URL, action or tool fields (I-11).
- **R6. Locale.** Each query carries the target's country and language (§6.4).
- **R7. Late findings never change the spec on their own.** They appear as G2 toggles or become change requests in the lane they belong to (doc 05 §3), never lane L1.
- **R8. High-impact claims** (prices, fees, rules that change behavior, legal or payment facts, formats that change validation) need two independent domains, or are flagged `single-source` and shown `unverified`.
- **R9. Official sources rank first in a conflict;** forums, blogs and SEO pages cannot support high-impact claims.
- **R10. Stack facts:** the Stack Kit wins.
- **R11. Legal, tax and payment facts are information, not advice** (`legal-adjacent`). Regional compliance packs stay deferred (AD-24).
- **R12. Privacy and security are baseline only** (owner, 2026-11-11; AD-54). Never log or print API keys; public web only; pages are data (no tools). No name or personal-data filtering, no per-program cache isolation (the cache may be shared across programs), no per-program switch beyond Skip.
- **R13. Web hygiene (basic).** Public URLs only (skip private and local addresses); respect robots.txt; no logins or paywall bypass; a User-Agent that names the product.
- **R14. No bulk copying.** Page text is internal evidence; output holds paraphrases of at most 40 words, links and dates.
- **R15. Brand facts** come from your own domain or a document you gave, read as a seed source; they are not inferred from a competitor's site. Your own brand name alone is not searched.
- **R16. Named references** are patterns and public feature lists; no logos, images or copy.
- **R17. Missing research never makes a builder need `missing`:** it falls back to a module default marked `defaulted`.
- **R18. Trust follows the tier, not approval.** Default-tier seeds give `assumption_derived` queries; results never raise a tier.
- **R19. Stop by evidence.** A target stops when its required slots are filled; follow-ups are distinct and capped; `not_found` is a valid answer and is never retried indefinitely.
- **R20. Declined topics are not researched** (Compass `declined`), and nothing is searched about games or other declined categories.

## 12. Failure and edge cases
| ID | Case | Rule |
|---|---|---|
| RS-01 | Search off, empty, or no budget | `skipped` or `failed`; Spec uses module defaults flagged `assumed`; Verify notes "less research-backed"; never blocks |
| RS-02 | Conflicting sources | Keep both with dates; official wins; else `open` and `unverified`, shown at G2 |
| RS-03 | Reference app with a huge feature set | Rank by prevalence; top N as suggestions; rest "also seen" |
| RS-04 | Stale SDK or stack facts | The Stack Kit wins; dates shown |
| RS-05 | Research suggests unrequested scope | `undecided` suggestions only |
| RS-06 | Injected instructions in a page | No tools; no action fields; directives ignored and counted |
| RS-07 | Provider down, rate-limited (429), quota | Retry transport errors three times with backoff; event `warning`; else `partial`, gap `provider_down`; never blocks |
| RS-08 | High-impact claim with one source | `single-source`, shown `unverified` |
| RS-09 | A query would carry a URL or a key-like string | RV-06 drops it (basic hygiene only; no personal-data filtering) |
| RS-10 | Findings after a Brief change | Delta re-run by section hash; R7 |
| RS-11 | Template or thin requests | §6.5; rebuilt from facts |
| RS-12 | Weak or conflicting geography | `geo_uncertain`; country plus every stated city |
| RS-13 | Own brand or unfindable named entity | `not_found`, no claims; never substitute a similarly named company |
| RS-14 | Name collision (company or locality, for example a neighbourhood name that exists in several cities) | `ambiguous` with candidates; the city is always in the query |
| RS-15 | Thin local-language web | English fallback; `source_language` recorded |
| RS-16 | Time box hit | Stop searching, finish extraction of pages read, commit `partial` |
| RS-17 | Skip pressed | `skipped`; evidence kept; no spend after the press |
| RS-18 | Tab closed or page reloaded | The run continues; the page resumes from stored events |
| RS-19 | No page text (null content, JavaScript-only page, scanned PDF) | `page.skipped` with reason; try `FetchPort`; else a gap; never extract high-impact claims from snippets alone |
| RS-20 | Research contradicts a Locked value | Locked wins; shown as `conflict`, never an edit |
| RS-21 | More targets than budget | Merge by entity; rank by priority and decision weight; unrun targets are visible gaps |
| RS-22 | Checker and writer from the same vendor | Code checks stay primary; log `checker_vendor` |
| RS-23 | Non-ASCII quote mismatch (Devanagari, Kannada, Arabic) | NFKC and zero-width removal before the substring test; one retry; else dropped |
| RS-24 | Duplicate queries or entities | Normalize, dedupe, reuse the per-program cache |
| RS-25 | A source needed for a required slot does not exist on the web (a new niche idea) | After 2 distinct follow-ups: `not_found`; Spec uses a module default flagged `assumed` |
| RS-26 | Two sources give different values (fees, thresholds) | Keep both with dates and authority; flag `conflict`; put in `confirm[]` |
| RS-27 | Pages that are SEO listicles or AI-generated | Source class `blog` or `seo`; cannot support high-impact claims |
| RS-28 | A paywalled or login-only page | `page.skipped: paywall`; never bypassed |
| RS-29 | The user cancels a topic mid-run | `target.cancelled`; no more spend on it; recorded as a gap with reason `user_cancelled` |
| RS-30 | A round fills almost nothing (the search provider returns junk for a locale) | Evidence-aware stop (R19); warn in events; the bake-off metrics should have caught it |
| RS-31 | The Brief mixes several regions (six cities) | Query the country and distinct regional specifics once; do not run one search per city |
| RS-32 | Non-English source with English paraphrase | Quote kept in the source language; paraphrase in English; `source_language` recorded |
| RS-33 | The research view is missing or stale (hash mismatch) | RV-13 refuses the run and emits `run.failed` with the reason |
| RS-34 | A model call or search times out repeatedly | Count as failures in `budget.failures`; event `warning`; finish with `partial` |
Stale items are flagged by RV-07: older than 2 years for prices and fees, older than 5 years for general facts [J].

## 13. Validators (RV-01 to RV-17; RV-11 reserved)
Run on the Backbone's validator framework (doc 07 §2).
| ID | Check | On failure |
|---|---|---|
| RV-01 | `research.v4` schema, strict; `data` matches `kind` | One repair; else commit valid items as `partial` |
| RV-02 | Each quote is a verbatim substring of the stored page after normalization | Item dropped |
| RV-03 | Source URL and date match the fetch log | Item dropped |
| RV-04 | Every tier is `default` or `open` | Commit refused (bug) |
| RV-05 | `claim`, `use_note` and digests add no number, currency, date, name or URL not in the cited items | Note removed or item dropped |
| RV-06 | Query hygiene (basic): no URL or key-like string; no own-brand term alone; at most 25 words | Dropped before sending |
| RV-07 | Corroboration: two independent domains for high-impact items; stale dates; conflicts; forum, blog and SEO limits | Flags set |
| RV-08 | Support check: `supported` required for high-impact; `partial` lowers confidence; `not_supported` drops | Dropped or downgraded |
| RV-09 | Caps: size, slices, items | Lowest priority dropped, counted |
| RV-10 | Coverage: every target and request appears in items or gaps | Added to gaps with a reason |
| RV-11 | Reserved (personal-data filtering removed, AD-54) | none |
| RV-12 | Budget: searches, pages and seconds within the ceilings | Run stops (`budget_stop`) |
| RV-13 | Binding: `brief_hash` equals the approved Brief hash; trust flags present or recomputed | Run refused |
| RV-14 | Anatomy and worthiness: every query has a target with `decision_unlocked`, an anchor, a place and an intent word; category-only queries rejected | Query dropped |
| RV-15 | Slot consistency: `coverage` counts match the filled slots; a target marked covered has all required slots verified | Recomputed; mismatch is a bug |
| RV-16 | Every `applies_to` id exists in the approved Brief (or is `NEW`) | Item re-tagged `NEW` or dropped |
| RV-17 | Events contain no page text or quotes; labels come from templates | Event dropped |

## 14. Evaluation and gates
- **Eval set R-EVAL v3:** the three real Compass exports first (Thorough re-recorded after the Compass fixes), then 9 more Briefs: India four (one Hinglish), UAE two (Arabic and English), Kenya two (English and Swahili, mobile money), UK or US one; plus two adversarial (a document with embedded instructions; a request naming a private person). Behavior is checked, not expected answers. **Spec-consumability check:** for each run, a weak model given only the digest of a Spec lane slice must be able to name the fields of the main entity, the steps of the main workflow, and the providers for payments, using only research items (measured once Spec exists; before that, a human reads the slices).
- **Replay** (cassettes) on every change; **live** only after your explicit yes. Scenario IDs `SB-RS-01` to `SB-RS-34`.
| Metric | Target |
|---|---|
| Quote check (RV-02), live and replay | 100% of items |
| Validators RV-01 to RV-17 | pass every run |
| High-impact items with two independent domains, or flagged | 100% |
| Injection tests (RS-06) | 100% ignored |
| Query hygiene and anatomy | 100%; zero category-only queries |
| Targets covered or `not_found` (none silently dropped) | 100% |
| Required slots filled | at least 70% [J] on the real inputs |
| Searches that produced at least one used item | at least 60% [J] (measures the worthiness gate and fusion) |
| Distinct queries per target (no repeats) | 100% |
| Native-reviewer check of local items | at least 9 of 10 per region [J] |
| Searches, pages and time within ceilings | 100% of runs |
| Time box met | at least 90% of runs [J] |
| Template requests detected on Quick-like inputs | 100% |
| Events: every query and page appears as an event; resume from `Last-Event-ID` loses nothing | 100% |
| Items used or consciously rejected downstream | at least 70% [J], once Spec exists |
| Determinism: same cassettes, same `research.json` hash | 100% |

## 15. Compass changes Research needs (prompt 04; not built)
- **CR-1 `quality` block in the research view:** `seed_source`, a **true failure count** including timeouts and 0-token calls, `structure_source`, `degraded`, probes (reconcile `probes.design` with `readiness.design`), whether the closing ranking set priorities.
- **CR-2 Tier tag** (`locked`, `default`, `open`) on values in the research view; still no quotes or history.
- **CR-3** `origin` (`model` or `template`) on each `research_request`; ISO country and language codes in the view.
- Research no longer uses `guidance.size_band`, `depth`, `search_budget` or `max_requests` (§3.1); Compass may keep them for its own use. Until CR-1 to CR-3 exist, Research derives what it can and flags the rest; it does not block.

## 16. Risks, Phase 2 and what is not verified
- **Latency on the critical path** (about 3, 6 and 10 minutes at the ceilings, usually less) is the price of foreground. Phase 2 switches, measured first: **progressive release** (publish each job's slice as it completes so Spec's Product lane can start early); plan at Brief draft (a model call, no web spend); reference packs; Crawl4AI for JavaScript pages.
- **Vendor quality for Indian local queries is unproven** until the bake-off; page text may be missing for PDFs and portals (RS-19).
- **Slot schemas may be too demanding** for niche ideas (RS-25); the 70% target and `not_found` handling are the safety valve; tune after the first runs.
- **Load score thresholds** are calibrated on three runs only [J]; retune on R-EVAL.
- **Checker independence** (RS-22); the **authority registry** needs a curated India seed (OI-18).
- **Not verified:** Luna's price and rate limits; Brave `search_lang` coverage for Marathi, Kannada, Swahili; Serper's official pricing page; Tavily plan limits; the full text of the 2026 deep-research papers and STORM; Gemini and OpenAI deep-research internals.

## 17. Provider and source evidence (2026-11-11; doc 10 R-53 to R-64)
| Item | Verified (primary page opened) | Not verified |
|---|---|---|
| Tavily | Search parameters (`country` only for `general`, `language`, `filter_by_language`, `include_domains` prefer or restrict, `exact_match`, `include_raw_content`, credit costs) | Price per credit; plan limits; local-language quality |
| Brave | `country`, `search_lang`, `count`, `freshness`, `extra_snippets`, `goggles`; an LLM Context endpoint exists | USD 5 per 1,000 and USD 5 monthly credit (search summary); language list; rate limits |
| Serper | None opened | `gl` and `hl` parameters, 2,500 free queries, pricing (third-party only) |
| Exa | `userLocation` country only, no language parameter | Rate limits |
| Anthropic multi-agent research post | Architecture, effort-scaling numbers, parallelism and cost lessons, failure modes | Whether the numbers transfer to our task |
| LangChain Open Deep Research | Brief, isolated researchers, one-shot writing, parallel writing failed | Numeric evaluation (none published) |
| Apple iTunes Search API | Parameters, about 20 calls per minute, terms limiting use to promotion | App-specific fields |
| trafilatura, Crawl4AI, Jina Reader, SearXNG, GPT Researcher, Gemini Deep Research, the 2026 papers | Search summaries only | Primary pages |

## 18. Build plan, prompts and what changed
- **Order (owner, 2026-11-11): Research is built now, before Backbone, with an internal validator registry that Backbone can adopt later.** **Build prompt:** [`prompts/05-research-agent-implementation-prompt.md`](../prompts/05-research-agent-implementation-prompt.md) (v4; prompt 03 is superseded). **Compass change prompt:** [`prompts/04-compass-research-contract-changes.md`](../prompts/04-compass-research-contract-changes.md).
- **In the build:** the run state machine, load score, planner, worker pipeline, coverage audit, `SearchPort` adapters (Tavily and Serper; Brave deferred), `FetchPort` (trafilatura, PDF text layer, SSRF), validators RV-01 to RV-17, the event stream and API, the authority registry data file, tests on the three real inputs. **Not in the build:** Compass or Spec changes, the front-end UI (the contract is built, the test UI renders it), live calls, `.env`, provider accounts, merging the Compass branch.
- **Acceptance:** §14 gate offline; each RV with pass and fail tests; scenarios RS-01 to RS-34; property tests (no item without a quote; no tier other than `default` or `open`; no query without a target, anchor, place and intent word reaches an adapter; ceilings never exceeded; any provider failure gives `partial` or `failed`, never an exception to the orchestrator; a run for a different Brief hash is refused; a covered target issues no further queries; a follow-up never repeats an earlier query; every event is stored and replayable); determinism.
- **v3 to v4:** research organized by what Spec must decide (eight jobs incl. structure, flows, design and screens), norms with prevalence and recency, playbook slices per lane, wider worthiness gate, `research.v4`; privacy and security reduced to baseline (RV-11 reserved, no PII or name filtering, shared cache allowed, light web hygiene).
- **v2 to v3:** the tier is no longer an input; the load score and ceilings replace 6, 12, 20 (+8); the single pass becomes an iterative slot-driven loop with parallel isolated workers, variants and rank fusion, coverage audit and evidence-aware stopping; output indexed by Brief ids with suggestions, `confirm`, slices and digests (`research.v3`); the event contract; the source and service policy and `FetchPort` in scope; Serper added to the bake-off; RS-25 to RS-34, RV-15 to RV-17, R19 and R20.

## 19. Open items
| ID | Item | My default |
|---|---|---|
| OI-11 | Provider accounts and the bake-off spend | You say yes to about 40 live searches (Tavily and Serper on the real requests) and create the two accounts; keys go in the environment. Brave deferred |
| OI-14 | Primary provider | Set by the bake-off; Tavily until then |
| OI-15 | Native reviewers for local items (Hindi and Marathi, Kannada, Arabic, Swahili) | You arrange 10 items each |
| OI-16 | Build order | Backbone first, then Research |
| OI-17 | My readings (header) | Foreground; entity-level search; writer, checker, one search provider |
| OI-18 | Authority registry seed (India first) | I draft it as a data file in the build; you or a reviewer confirm |
| OI-19 | Who gives the Compass builder prompt 04 | You decide |
| OI-20 | Load score weights and class thresholds | Start as in §3.3; retune after the R-EVAL runs |
