# 18. Spec engine: the final architecture (v2)

- **Status:** **final architecture v2.1, 2026-11-14.** The owner asked for it to be finalized ("do the task fastly ... do everything"), so it is **folded into docs 00, 02, 03, 06, 07, 09, 12, 15, 16 and 17 (amendment banners and notes; where they differ, this document wins) and builder prompt 12 is now v3, ready to paste** (§10). Decisions **AD-83 to AD-91**, validator **V-45** and open items **OI-40 to OI-43** are registered as Proposed until their gates in [doc 16](16-spec-proof-plan.md) pass (nothing here is Proven). **v1 was reviewed independently the same day (25 findings, three of them blockers) and a second pass found 11 smaller problems; this version resolves them all (§11).**
- **Amended later on 2026-11-14 by [doc 19](19-spec-big-apps-bench-and-result-first.md) (owner: "result is the main thing ... the big one is more important"; AD-92 to AD-95): doc 19 wins where it differs.** In short: the **Verify agent is built now for deep apps** (§3.5 and OI-40 are superseded), three directions also for deep, un-fuse below 90%, the model writes every list-visible mock record, cost is a hard cap only, and the Spec Bench (a standalone mock front end) is part of the build. Everything else here stands.
- **Why it exists:** the owner asked (2026-11-14, voice) for the *final* architecture before anything is built: cost focus on small and medium **without hurting the result** ("Result is the main thing"); **free tools only**, and only where they help Spec; **no validator agent between Spec and the front end** for small and medium (an agent only for big apps, **not built now**); a simple **self-check with basic repair**; a **static** output the front end can use in any order (parallel, one by one, or all at once); and **no dependence on one model or provider** (a Haiku-class model for now).
- **How to read the numbers:** every figure is **[J]**, my estimate, until the proof gates ([doc 16](16-spec-proof-plan.md)) measure it; the model behind the estimates is in the appendix. Prices are the recorded Haiku-class prices (USD 0.10 in, 0.50 out per million tokens, read by the builder in D-171, **not checked by me**). Web facts are in [the research note](../notes/spec-external-tools-research-2026-11-14.md); they come mostly from secondary sources, so every tool gets a one-line smoke test in S0 before anything depends on it.

## 0. The answer on one page

**Spec in one line:** a compiler. It takes the approved idea (Compass) and the market facts (Research) and writes a **static folder of files** (the *pack*) that tells the front-end engine what the app is, who uses it, what data and rules it has, what every screen must communicate and do, the words, the colour and type tokens and the mock data. It never says how a screen looks.

```
  Compass Brief ──┐
                  ├─► INTAKE ─► PLAN ─┬─► MODULES ──────┐
  Research facts ─┘   (code)  (model) ├─► INTEGRATIONS ─┤
                                      ├─► JOURNEYS ─────┼─► JOIN ─► FREEZE ─► GLOSSARY ─┬─► SCREENS + words ─┐
                                      └─► DESIGN ───────┘   (code)  (code)  (model)     ├─► HERO RECORDS     ├─► ASSEMBLE ─► PACK ─► G2 ─► FRONT END
                                                                                        ├─► BEHAVIOURS       │
                                                                                        └─► LOCALES ─────────┘
  model calls: plan, modules, integrations, journeys, design, glossary translation, screens, hero records, behaviours, locales (the rest is code)
  PACK = static files · G2 = you read the summary and approve · FRONT END = reads in any order
  every step ends in a self-check done by code (§3); no agent stands between the pack and the front end
```

**G2 stays.** It is *your* approval page (summary first, the risky items ranked), a human step, not a latency Spec adds. Spec's output goes to you and, the moment you approve, to the front end. **G1.5** (the phase split) stays too: it is always shown for big apps and whenever a deferral is proposed.

**Your idea, my answer.** You said the output should go straight to the front end and a validator agent should exist only for big apps. **I agree, for small and medium.** Three reasons: (1) while every route is the same Haiku-class model, a second model reading the first one's work shares its blind spots, so it adds a wait but little independence (my judgment [J]; the proof-only arm of PS-7 measures it); (2) what a check can really prove (a link that does not resolve, a wrong type, an uncovered entity, a file over budget, a number that is not in the quote) **code proves better, in milliseconds**; (3) *meaning* is checked by you at G2, by the front end's own questions (`raise_decision`), and at G3 on the preview. **Every validator stays; they run as code (§3).** What we give up, honestly, is in §3.4.

**What changes after your message** (everything else in docs 15 to 17 stays):
1. **No agent between Spec and the front end** for compact and standard; Spec checks itself in code (all validators) and repairs simply (§3). The agent (Verify) is **reserved for big apps and not built now**.
2. **Fewer, fuller calls** for small and medium, sized by the model's output limit (§1).
3. **Code does more:** colour and type tokens, the `empty`, `busy`, `extreme`, `broken`, `offline` mock modes, schemas, block and asset lists, the summary tables (§5). **The model still writes what decides how the app feels:** hero records, directions, screen cards, the words.
4. **Free tools only, all running locally**; the only network call in Spec's run is the LLM gateway (§6).
5. **One portable recipe for every model:** one JSON object, checked locally, one targeted re-ask, then a safe default (§4).
6. **A static pack every screen can be built from alone** (§2).

| Estimate [J], like for like | Before (docs 15 and 17) | After, writer can output 32k tokens or more per call | After, writer limited to 8k per call |
|---|---|---|---|
| **Small (compact)**, a yoga-studio class app | 15 to 20 calls, 5 to 7 min, about USD 0.06 | **about 14 calls (12 to 16), about 4.3 min at 4 workers, about USD 0.05** | about 25 calls, about 3.9 min at 4 workers, about USD 0.06 |
| **Medium (standard)**, the produce marketplace | about 80 calls, 8 to 11 min at 6 workers, about USD 0.27 | **about 38 calls (34 to 46), about 6.8 min at 6 workers (5.6 at 8), about USD 0.16** | about 70 calls, about 5.9 min at 8 workers, about USD 0.17 |
| Big (deep), 60 screens in one app, for reference | about 129 calls, about 14 min at 8 workers, about USD 0.53 | about 100 calls, about 12 min at 8 workers, about USD 0.42, **no independent check until Verify is built** | about the same |

**Plain truth about the gain.** Small and medium cost **cents** already (at ten times these prices: about 0.5 and 1.6 dollars). Small apps were already lean, so they gain little (about 15 to 40% faster, about a sixth cheaper). **Medium gains most:** about half the calls (fewer places to fail), about 40% cheaper, about 15 to 40% faster, and no serial wait at the end. The writer's output limit is **not yet recorded** (OI-31); S0 records it and every number is recomputed from the profile. The free wins do not touch the result; the switches might, so each has a risk, a default and a test, and §5 also lists what is never cut.

## 1. The pipeline for small and medium

### 1.1 Who does what

| Code does (free, instant, same every time) | The model writes (judgment and language only) |
|---|---|
| Read Compass and Research, trust grades, readiness and drift flags | The plan: merges and splits, the release proposal, modules, roles and access skeleton, entity owners, personas |
| The system set (sign-in, profile, settings, help, deletion...), standard states per screen type, standard scenarios | Per module: entities with fields, rules, lifecycles, the **custom** operations, user stories |
| CRUD operations, JSON Schemas, OpenAPI 3.1 skeleton, auth matrix, error taxonomy | Integrations and events: the provider-neutral flow, interruptions, pending timeouts, webhooks |
| **Tokens:** palette ramps, dark scheme, semantic and state colours, data-visualisation palette, elevation, motion durations and easings, type scale and script fallbacks, contrast checked (§5) | Direction (three for standard, one for compact): mood, voice, ambition, motion personality, shape and density, type personality, the seed colour and axes, non-data asset needs |
| **Mock modes** `empty`, `busy`, `extreme`, `broken`, `offline`, demo accounts, seeded recipes; the **hero skeleton** (ids per entity, lifecycle state and relation) | **Hero records** (content for the skeleton: names, descriptions, quantities, prices) and image briefs |
| Routes, entry and exit graph, needs list, shared blocks (exact), data-bound asset needs, the glossary, pilot picks | Journeys: emotion, moments that matter, unhappy paths (the steps come from Compass flows) |
| Locale formats and plural forms (CLDR), documented example phone numbers, copy-key namespaces | Screen cards (MUST, USUALLY, COULD with signature moments) **and** their source-language words |
| Task cards, build plan, INDEX, manifest, hashes, prefix bundle, budgets | App-specific global behaviours; locale translations (flagged `needs_native_review`) |
| **All validators**, the builder-needs rule, the coverage matrix, the G2 tables and the confirm list ranked by money, legal, identity | One short plain-language paragraph for the G2 summary |

### 1.2 Steps, calls, routes

| # | Step | Kind | Route | Compact calls | Standard calls | Notes |
|---|---|---|---|---|---|---|
| 1 | Intake | code | | 0 | 0 | `SpecInput`, trust, readiness, drift |
| 2 | **Plan** (`SPEC-PLAN`) | model | `deep` | 1 (also writes the product, the integrations and the main journey) | 1 (plan, skeleton, personas) | the one step with no safe default (§3.3) |
| 3 | **Modules** (`PROD-MODULE`) | model, parallel | `standard` | in 2 | 5 (one per module) | entities, rules, lifecycles, custom operations, stories |
| 4 | **Integrations** (`PROD-INTEG`) | model, parallel | `standard` | in 2 | 1 | one record per integration need from the plan: flow, interruptions, pending timeouts, events |
| 5 | **Journeys** (`PROD-JOURNEYS`) | model, parallel | `standard` | in 2 | 2 (about 4 flows per call) | against the planned inventory ids |
| 6 | **Design** (`DSGN`) | model, parallel | `standard` | 1 (beside the fused plan) | 1 (three directions, after the plan) | direction, voice, seed colour and axes, unbound asset needs; no signature moments (they need the frozen journeys: step 10) |
| 7 | Join + gate 2 | code | | | | §3.1 |
| 8 | **Freeze** | event | | | | roles, entities, operations, inventory, navigation, tokens, direction, personas, stories, journeys, integrations, **glossary** and **hero skeleton** become read-only |
| 9 | **Glossary translation** (`GLOSS-L`) | model | `fast` | 1 | 1 | right after the Freeze, before Screens: one call puts the glossary (at most 0.7k tokens) into the source language and every extra language; if it fails after its re-ask the Brief's own terms are used and the locale calls are marked `degraded` |
| 10 | **Screens** (`SCRN`) | model, parallel | `fast` or `standard` | 4 (3 to 4 per call) | 9 | cards, **source copy** and COULD entries (with the signature moments) in one call |
| 11 | **Hero records** (`MOCK-HERO`) | model, parallel with 10 | `standard` | 1 | 2 | fills the skeleton; never mints ids |
| 12 | **Behaviours** (`BEHAV`) | model, parallel with 10 | `standard` | 1 | 1 | only the app-specific part; the rest is library |
| 13 | **Locales** (`COPY-L`) | model, streamed behind 10 | `fast` | 2 | about 10 | at most about 300 strings per call; Indic scripts are token-heavy (doc 15 assumed 1 call for Kannada; I count 2) |
| 14 | Assemble + gate 3 (`spec check`) | code | | | | commit, render, shard, slice, task cards, prefix bundle, manifest, hashes |
| 15 | **G2 summary** (`G2-SUMMARY`) | model, parallel with 14 | `fast` | 1 | 1 | tables from code, one paragraph from the model; a template stands in if it fails |
| 16 | Re-asks | model | same route | about 2 | about 4 | **batched per call kind** (§3.2) |
| | **Total [J]** | | | **about 14 (12 to 16)** | **about 38 (34 to 46)** | |

**Fusion rule (model-independent).** Two jobs share one call only if **all three limits** of the writer profile hold: (1) the **estimated output of the fused call is at most half of `max_output_tokens`**; (2) the call input stays under `min(0.5 × effective window, input limit, long-prompt threshold)` (doc 15 §8.3); (3) **the fused schema compiles within the profile's limits** (`max_nullable_fields`, today 16 from D-172; depth; enum size), checked offline in PS-0. Otherwise the call splits back into the doc 15 and doc 17 calls. Items per call: `clamp(floor(0.5 × max_output_tokens ÷ average item tokens), 1, cap for the kind)` (caps: 4 cards, 300 strings, about 5 entities). A stronger **writer profile** gets fewer, fuller calls and a weaker one more, smaller calls, with **no change to the content**. If the first-try pass rate of a call kind falls below **85% of nodes [J]** (PS-5) on the proof corpus, the profile un-fuses that kind (configuration).

### 1.3 Time and cost [J]

| | Compact: yoga class app, 16 screens, 2 languages | Standard: produce marketplace, 4 apps, 31 screens, 3 languages |
|---|---|---|
| Writer output 32k or more: calls; tokens in, out; cost | 14; 0.13M, 0.07M; **USD 0.05** | 38; 0.41M, 0.23M; **USD 0.16** |
| Minutes at 1 / 2 / 4 / 8 workers | 9.8 / 5.6 / **4.3** / 4.0 | 30 / 16 / 9.0 / 5.6 (6 workers: **6.8**; 12 workers: 4.3) |
| Writer output 8k: calls; tokens in, out; cost | 25; 0.20M, 0.075M; USD 0.06 | 70; 0.59M, 0.23M; USD 0.17 |
| Minutes at 1 / 2 / 4 / 8 workers | 11.8 / 6.2 / 3.9 / 2.9 | 35 / 18 / 9.9 / 5.9 |

Time follows total output tokens divided by the workers, so fusing saves calls and cost more than minutes; **the minutes saved come from removing the serial tail** (the structure critic about 40 s, the final critic about 45 s, canary calls that spill past the last screen about 20 s at 8 workers and about 1.8 min at 2, and the summary, now parallel: **about 2 to 3.5 minutes**). A Gate 3 re-ask wave, when one is needed (§3.3), adds about 1 to 2 minutes in standard and about 1 in compact; it is an exception line, not in the table. Concurrency is still computed from provider limits (doc 15 §10.3); the table assumes the workers are available. Assumptions are in the appendix.

## 2. What Spec hands over: the static pack

### 2.1 What is in it, by size (the content model of doc 17, leaned)

| Piece | Compact | Standard | Deep (for reference) |
|---|---|---|---|
| `AGENTS.md` (one page: how to read, three recipes), `INDEX.md`, `prefix.bundle.md`, `MANIFEST.json` | yes | yes | yes |
| One-file `APP_SPEC.md` | only for a consumer that reads about 80k or more | no | no |
| global: product, roles and access, navigation and `routes.json`, data model, rules, behaviours, state and sync, errors, platform, integrations (when the app has any), direction and motion intents, tokens (DTCG), glossary, copy per language | yes | yes | yes, plus compliance |
| contract: OpenAPI 3.1, schemas, hero fixtures, auth matrix, events | operations and schemas | plus pagination | plus idempotency, webhooks |
| mock: named modes (§5) | `empty`, `typical`, `broken`, `offline` | plus `busy` | all six, realtime scripts |
| screens: card (MUST, USUALLY) plus its closure; `.could.md` | cards; COULD on key screens only (at most 5) | cards; COULD at most 12 | cards; COULD at most 30, with a basis |
| stories / journeys / personas | about 10 to 20 / the main one / a line per role | about 30 to 60 / every primary role / a paragraph | 100 and more / plus cross-app / plus context |
| needs, assets, blocks | small | full | full, with sourcing rules |
| build plan and task cards | yes | yes | plus waves and pilot picks |
| `evidence/` (self-check report, provenance; not for the front end) | yes | yes | yes |

**Size does not shrink much.** The content is the result, so a pack is about the size doc 17 estimated (about 60 to 70k tokens compact, about 150 to 240k standard); the saving is in calls and waiting, not in what the front end receives. `whole_pack_tokens` comes from the consumer profile (doc 15 §4.8): **the design-target consumer reads about 22k at once, so even a small pack ships as a tree for it**; the one-file form exists for stronger consumers.

### 2.2 Guarantees (`spec check` verifies each one)

| # | Guarantee | Why it makes order irrelevant |
|---|---|---|
| G-1 | **Immutable and content-addressed.** A graph version never changes; a change is a new version; the same graph renders the same bytes (V-34). (Two runs from the same Brief may write different graphs; both pass the same checks.) | nobody reads a file that moves while they read |
| G-2 | **Self-contained slices.** A screen file holds its card and its closure: bound fields and lifecycle states, operations and their errors, access rows, copy, **the bodies of the blocks it uses**, **the route targets of its navigation effects** (route name, typed parameters, guard) and token names. A task bundle lists a block body once (the packager dedupes at read time). Slices grow by about 0.3 to 0.8k (a typical slice about 3.3 to 3.8k), so **a task carries two to three slices, not about three** (8k prefix, 3k shards, 2k module and the 4k front-end allowance leave 11k); the cap stays 5k and V-26 checks it; an over-cap slice caused by a card's size is repaired (§3.3), it does not kill the run | any screen can be built alone, in parallel or later |
| G-3 | **One shared prefix**, identical for every worker (index, global rules, direction and motion intents, token **names** (the values stay in `tokens.dtcg.json`), a delight block of at most 0.4k collected from the COULD entries, the glossary of at most 0.7k) | the same inputs for every worker |
| G-4 | **No build-order words.** Nothing says "build this after that". Sequences that are product content (wizard steps, journey stages, GIVEN, WHEN, THEN) are fine. Navigation is declared as entries and exits; waves and ownership live only in the build plan | build one by one, in parallel, or all at once |
| G-5 | **One owner per shared thing** (entity, block, token, route, glossary term) with a stable id; **copy keys are namespaced by screen id, minted by code**; shared strings live in global copy with one owner | parallel calls cannot collide |
| G-6 | **Every id used to build resolves** inside the slice or the prefix; informational ids (entry points, related screens) may point elsewhere and are marked `info`. A worker may still look up at most `lookups_before_raise` files (doc 15 §6.3) | a task needs no discovery pass |
| G-7 | **Honest tiers.** Locked, Default, Open, `assumed` are marked on every fact | the front end knows what it may change |
| G-8 | **Self-describing.** The manifest lists every file with its size; `AGENTS.md` tells any reader how to start in one page | no hidden knowledge |

### 2.3 Three ways to consume (recipes in `AGENTS.md`)
- **All at once:** read `APP_SPEC.md`. **Only a consumer that reads about 80k or more can do this**; the weakest configured consumer cannot, and gets the tree.
- **One by one:** read `prefix.bundle.md` once, then the next screen's card and closure (G-2 makes that enough).
- **In parallel:** every worker gets a task card whose reads stay within the task budget (about 28k including the front end's own files); the prefix is identical; file ownership is disjoint; a worker that finds a gap writes a `raise_decision` instead of guessing.

**What Spec cannot guarantee:** visual consistency beyond tokens, direction, glossary and shared blocks comes from the **front end's own design-language step** (pilot-first, doc 17 §8). Parallel generation without a pilot gives a less consistent look; Spec says so in `AGENTS.md` rather than promising otherwise.

**The validator is code, not an agent.** `spec check <pack>` (the validators of `spec-core`) runs in seconds, costs nothing and can be run by anyone: Spec before it hands over, the front end's orchestrator before it fans out, a developer in CI. A later Verify agent would call the same code.

## 3. Self-check and basic repair (replaces the validator agent for small and medium)

### 3.1 Three gates, all code, **every validator stays**

| Gate | When | What runs | On failure |
|---|---|---|---|
| **1, per call** | after every call | that call's own checks: schema and closed vocabularies (V-01, V-24), ids and edges (V-02, V-03, V-32), bindings and actions (V-06, V-07, V-30), mandatory states (V-13), keys (V-31), provenance and grounding (V-17, V-33, V-36, **V-45**), shapes and COULD rules (V-38, V-42), fixtures (V-15) | the ladder (§3.2) |
| **2, join** | Product, Integrations, Journeys and Design are done | roles, lifecycles, contract, vocabulary (V-08 to V-11), tokens and contrast (V-12), capability tiers (V-16), scope ledger, release and inventory (V-19, V-28, V-29), every integration need has a record, every `in` feature has a story (V-40) | the ladder, one re-ask to the owning call |
| **3, pack** (`spec check`) | assemble | **the whole suite, V-01 to V-45 (V-18 is superseded by V-26), on the finished pack,** including what exists only now: reachability and dead ends (V-04), flows (V-05), copy in every locale (V-14), accessibility labels (V-22), allowed capabilities (V-23), no secrets (V-20), determinism (V-21, V-34), budgets (V-26), closure (V-27), global behaviours (V-35), blocks (V-39), stories on every screen (V-40), mock coverage (V-41), assets and motion (V-43), journeys (V-44), mobile-fit (V-37), form-word lint (V-25) | by class (§3.3) |

**The builder-needs rule (doc 15 §9.5, kept):** every need BN-01 to BN-17 is `present`, `default` (library, flagged) or `assumed` (flagged). A need that is `missing` ends the run `partial` with a plain list and **never reaches G2**. The **coverage matrix** (no entity, role or flow without a screen; no `in` feature without a story and a screen) is computed at Gate 3 and shown at G2. V-37 is a warning; its error cases (hover, right-click and tooltip intents, a gross breach left unresolved) block G2 as in doc 17 §5.6.

**V-45, new: grounding, narrow on purpose.** In a Locked node or a research-derived Default node: (1) every **numeral with its unit** occurs in the cited quote or item (digits, separators and Indic numerals normalised; number words, lakh and crore scaling and derived arithmetic are **not** checked and are listed as `unchecked` for G2); (2) provider and brand names come from **closed lists** (the dossier list and the Brief's own tokens); (3) typed rules with the same subject and the same condition but different values are flagged. **V-45 runs flag-only until PS-7 shows at most 5% false positives; then its downgrade of the node to Open `assumed` is switched on** (a configuration of the checker). A flag is shown at G2. (V-17 checks that evidence exists, not what it says: a slip from 4% to 5% citing the right quote would pass it; V-36 covers research items only.)

### 3.2 The repair ladder (logical and basic)

| Step | What | Limits |
|---|---|---|
| **R-code** | A mechanical fix that cannot change meaning (the list below) | single pass; **every change is logged and counted on the G2 page** |
| **R-ask** | **One** targeted re-ask of only the failed items: the item, the validator's message word for word, the schema fragment, the allowed values; "return only these items". **Batched per call kind**: all failed items of one kind in one wave go in as few calls as the item limit allows (so the number of re-asks does not grow with fusion) | **one re-ask per node in the whole run**, same route |
| **R-flag** | Use the library default or Compass's own value, or drop the optional item; mark the node `assumed` or `degraded`; list it on the G2 page; a human decision becomes a `needs_decision` entry | the last step; **a pack is never delivered with an unmarked hole** |

**The code fixes (the whole list):** (1) sort keys, normalise line endings and order (bytes); (2) mint a **missing** id (a duplicate id is never fixed by code: R-ask); (3) instantiate a missing standard state, behaviour, scenario or system screen from the library (provenance `library`, tier Default); (4) map an unknown enum, shape or intent through an **exact-synonym table**, else R-ask; (5) drop an unknown optional field (logged); (6) downgrade a Locked claim without evidence to Default (logged); plus the packager's re-shard of an over-budget task at a module boundary (a generated file).
**Not fixed by code:** a binding the role may not see (R-ask with the access rows; if it still fails, the binding is dropped and listed at G2 as "this role cannot see X"); a missing copy key (R-ask; else the region is marked `copy_unspecified`, so the front end writes its own words, and listed); coverage gaps, contradictory rules, an operation without an error model, a story without acceptance, a missing journey stage.
**An invented fact is removed, never "repaired" into a Locked one** (V-45, FN-07).

### 3.3 Classes of failure, end states, and one banner

**By what the failing validator reads:** (a) a **model-written node** (card, story, journey, hero content, copy, behaviour, integration) goes down the ladder, including at Gate 3 (one re-ask of its owning batch if it has not been re-asked yet, otherwise R-flag); **an over-cap slice caused by a card's size is also class (a)**: the packager first moves bulky detail to `screens/<id>.detail.md`, then one re-ask shortens the card, and only a budget that cannot be met after both is a Spec bug; (b) a **gap in a frozen node** (a card needs an operation Product never wrote) gets one **additive** re-ask to the owning Product call (the `needs_decision` loop of doc 15 §8.1, kept; it adds a node, never changes one), then is listed at G2; (c) a **generated file** (closure, bytes, routes, needs, blocks) that fails is a **Spec bug and fails the build loudly**; it becomes a regression fixture. **A Gate 3 re-ask wave** (when one is needed) re-assembles, re-runs `spec check` and re-translates changed copy keys: about +1 to 2 minutes in standard and +1 in compact [J]. **V-37 error cases left after their one re-ask** become a `blocked` entry on the G2 page (the owner waives with a recorded reason, `mobile_fit_waiver`, or sends the screen back for a split); the rest of the pack is unaffected.
**End states.** Every step has a library, Compass or code-filled default (hero content from seeded pools, a card from its screen-type template) **except the Plan**: if the Plan call is still invalid after its one re-ask, the run ends **`partial`, resumable**, with a plain message (and a fallback route is tried first if one is configured). The run also ends `partial` when the money budget is spent (the owner's one spend control).
**One banner, no stop.** If more than **15% of nodes [J]** needed a re-ask for one step (PS-5's own target), the run report and G2 carry a **`weak_route` banner** ("this route needed many repairs for step X; consider a stronger route for it"). Nothing stops a run for quality; the number is defined on nodes, the unit PS-5 uses.

### 3.4 What is re-homed, and what we give up

**Re-homed (nothing dropped):** the validators V-01 to V-45 (Gate 3), the builder-needs rule, the coverage matrix, the research-drift check (V-36), the ranked confirm list, the freedom schema and form-word lint (V-24, V-25), the route fingerprint and byte checks. The freedom probe stays in the proof (PS-4).

**Given up:**

| A second model could catch | What catches it now |
|---|---|
| a wrong business rule or a missing requirement (meaning) | **you at G2** (summary first, confirm list ranked by money, legal and identity, what differs from the Brief), Research's quote-backed facts, provenance, **V-45** (numerals with units, closed name lists, same-subject conflicts; flag-only until measured); **the rest escapes to you** |
| a screen that makes no sense for its user | journeys and the coverage matrix (code), the front end's own questions (`raise_decision`), **G3 on the preview** |
| a voice or direction that is off | the direction page at G2 and G3; direction is Open guidance, never binding |
| subtle over-binding in free text (Verify's freedom checks, doc 15 §9.1) | the schema and the lint catch the words; the **freedom probe** and the owner reading 5 cards in PS-4 catch the rest |
| correlated mistakes (the same model writes and checks) | nothing, and **I judge [J]** a same-model critic would not catch them either |
| anything that still escapes | the proof measures it **before** we trust the setup: **PS-7 becomes the planted-defect test of the self-check with two sets of defects, 22 structural and 8 meaning** (a changed Locked number, two contradictory rules, a user-stated feature missing from the inventory, a role seeing what the Brief forbids, a journey without its payment-failure path, a story that contradicts a rule, a Default that contradicts an official Research fact, an `assumed` item shown as Locked); catch rates are reported **separately** and **with and without a proof-only critic arm**, which shows exactly what the removed agent would have caught. **The bars of doc 16 (90% overall, 80% per kind) apply to the 22 structural kinds only; the meaning set has no bar**, because code cannot catch a story that contradicts a rule: that gap is exactly what the test shows the owner. PS-6 keeps the consumer probe (the old canary's method), in the proof and on a sample of real runs, never in the delivery path |

If the proof shows too many meaning defects escape, the answer is not a hidden switch: it is **Verify for that size, exactly as already designed in doc 15 §9**, and it is your decision.

### 3.5 Big apps: what is true today
Verify (the agent of doc 15 §9) stays the design for `deep` and is **not built now**. The pack format does not change when it arrives: it reads the pack and `evidence/self-check.json`, returns structured patches, Spec applies them (at most 2 LLM rounds, doc 15 §9.3). **Until it exists,** a deep pack is checked by the same self-check, **the mandatory G1.5 and G2**, and the front end's **pilot-first** step (3 to 5 pilot screens are built before the fan-out, so a gap comes back as a `raise_decision` first); `MANIFEST.json` and the G2 page say **`checked_by: self_check` and "no independent critic"**. My recommendation (OI-40): use deep packs internally or in beta until Verify exists.

## 4. How Spec uses LLMs: one recipe for any model

### 4.1 The rule
Spec's code and prompts **never name a model or a provider**. Calls name a **route class** (`fast`, `standard`, `deep`, rule 14); the **gateway** maps a route to a model, picks the strictest structured-output mode the provider has, translates the schema dialect, counts tokens, places cache markers and sets effort and temperature. Today all three routes may map to one Haiku-class model; switching a route is a gateway configuration change. **The content hash excludes route and model identity**, and a different front-end consumer only means re-packaging (code, zero model cost). Two routes may write different content; both must pass the same checks (validated, not compared).

### 4.2 One call, step by step

```
 code assembles the input (only what this job needs, under the input cap) ─► gateway (route class)
   ─► text ─► strip reasoning blocks and code fences ─► take the first JSON object that parses and fits the call's top-level shape
          (truncated? parse the main array element by element and keep the finished items)
          ─► schema check ─► item checks (closed vocabularies, ids, provenance and grounding, sizes)
                ├─ all good ─► result cache ─► next
                └─ some items bad ─► R-code ─► else ONE re-ask for those items (batched per call kind) ─► else R-flag
   truncated:               redo only the unfinished items in smaller batches (a fused call splits back into its parts)
   refused or filtered:     fallback route if configured, else R-flag
   rate limit or no credit: back off, circuit breaker, checkpoint; the run ends `partial` and resumes
```

**Prompt layout, static first** (works under every provider's caching style, and nothing depends on caching): (1) rules, closed vocabularies, the schema written out in short text form and one or two examples (identical for every call of this kind); (2) the app's frozen-facts summary and **glossary** (identical for every call of this run); (3) this call's slice; (4) the closing instruction "Return ONE JSON object that matches the schema, no other text" (**the word JSON must appear**, a Qwen-style requirement). Instructions are in English; the user's content stays in its language.

**Result-cache key:** (tenant, step, input hash, **route fingerprint**, prompt version, schema version, library version). The route fingerprint is an opaque hash the gateway computes from what the route class resolves to (model, provider, parameters); Spec never sees the names. After a route change nothing is served from the old route's cache, **PS-9 bypasses the cache entirely** (a harness argument, not a runtime flag), and `produced_with` records the fingerprint.

### 4.3 Why validated JSON and not "schema mode only" (dated facts from the research, never in Spec's code)

| Provider family | What the sources say | Consequence |
|---|---|---|
| DeepSeek | JSON mode documented and it does not enforce a shape; strict tool-calling is beta; `json_schema` support contested | local validation is the contract |
| Qwen (Alibaba Model Studio) | `json_object` and `json_schema` documented, model coverage varies; `json_object` needs the word json in the prompt | the word JSON in every prompt |
| Kimi | `json_schema` with a `strict` flag and its own schema dialect; reliability varies by model | flat, small schemas |
| MiniMax, GLM | no official docs found; one host lists JSON mode only | assume none |
| OpenRouter | per endpoint (386 of 469 model IDs on 2026-10-08) | the gateway picks the mode per route |
| The large American providers | schema modes exist (confirm per profile, OI-31) | use them when the profile says so; still validate |

Caching also differs (OpenAI automatic prefix, Anthropic explicit, Gemini implicit and explicit, DeepSeek automatic disk, Qwen implicit and explicit); a stable leading prefix works under all of them.

### 4.4 Portable by construction
Flat, closed records in arrays instead of open maps (ADR 0014); at most `max_nullable_fields` optional or nullable fields per call (a **profile value**, 16 today from D-172); shallow nesting; small enums; an offline compile check runs every call schema, fused or not, against every configured profile's limits (PS-0). **No over-filling:** a weak model fills fields the source does not state (D-172), so unknown stays null or `assumed`, and every filled field needs provenance (user, library, policy, research id). Code does counting, ordering, id minting, arithmetic and sizing; retrieval is by id, never by recall. **Effort and temperature are the gateway's**, never in a call spec. Token counts come from the profile's counter or a conservative factor. **Credits and limits are normal events:** the run ends `partial` and resumes from its checkpoint (Research could not, D-167; Spec must).

## 5. Cost and time: free wins and switches

**A. Free wins (no risk to the result)**

| # | What we do | Saves |
|---|---|---|
| A1 | No Verify agent for compact and standard; all validators run as code (§3) | 3 to 13 calls (the summary stays) and about 2 to 3.5 min of serial tail |
| A2 | The canary leaves the delivery path and becomes a proof and sampling tool | one call per task card |
| A3 | Plan, skeleton and personas in one call; Modules, Integrations, Journeys and Design in parallel | calls and waiting |
| A4 | **Tokens by code** (below): contrast is computed, dark mode is derived, no guessed hex values | fewer retries; a better result |
| A5 | `empty`, `broken`, `offline` are modes of the front end's mock adapter, made by code (they are behaviours, not content) | calls; no content lost |
| A6 | Locale batches streamed behind Screens; the G2 summary parallel with assemble | about 30 to 60 s |
| A7 | Result cache keyed on the route fingerprint; prefix-first prompts; re-asks batched per call kind | a resume or double click costs nothing; provider caching where it exists; no whole-call retries |

**B. Switches (they save cents and no wall-clock time; each has a risk, a default, and a test that decides)**

| # | What | Default | Risk | Saves | Decided by |
|---|---|---|---|---|---|
| B1 | Fused calls (fusion rule, §1.2) | on where the profile allows | a weak model fails more on big calls | about 25 to 30% of the calls | the 85% un-fuse rule; PS-5 per call kind |
| B2 | Source copy written inside the Screens call, with the glossary in the prefix | on | voice drift across parallel calls | 1 to 2 calls | PS-10: the owner reads 5 screens |
| B3 | `busy` and `extreme` made by code from the hero records (the hero call writes **12 to 20 records for each primary entity**, 3 to 5 for the rest; compact 8 to 12) | on | lists that look repeated | 2 to 4 calls | PS-10: **the owner compares code-made and model-written lists on 5 previews** |
| B4 | Data-bound asset needs made by code from media fields and hero briefs; **unbound needs (brand mark, app icon, splash, empty-state art, clips, audio and haptic cues) are written by the Design call** | on | a missed need | 1 call per module | PS-10 |
| B5 | Block names made by code (detection is exact either way, doc 17 §2.5) | on | none | 1 call per app | |

Together B1 to B5 save about USD 0.04 to 0.06 of a 0.16 run. **Not an optimization, kept as designed:** three directions for standard apps (doc 17 §5.7, the G2 "Look" view; one automatic direction for compact), because they are the anti-generic mechanism and cost under a cent.

**Mock modes (A5, B3).** The six names stay the vocabulary the front end's mock adapter understands. At the Freeze, **code mints a hero skeleton**: for every entity as many records as it has lifecycle states or the count of its class, whichever is larger (12 to 20 for a primary entity, 3 to 5 for the others; compact 8 to 12), with the relations between them assigned by code and stable ids that are the same in every app (cross-app coherence). The hero calls fill the content and never mint ids; **a value that appears in two entities (an amount, a person's name) is derived by code from the parent record, never written twice, and related entities go in the same call**; cards cite hero records by entity and state and the packager resolves them at assemble; **V-41** checks that every lifecycle state has a record. Demo accounts (one per role, built from hero records) and seeded recipes are made by code.

| Profile | Made by | Cost | Compact | Standard | Deep |
|---|---|---|---|---|---|
| `empty` | code (zero rows, first-run states) | 0 | yes | yes | yes |
| `typical` | **the model** (hero records, curated and realistic, with image briefs); code adds volume from seeded pools | 1 to 2 calls | yes | yes | yes |
| `busy` | code (seeded draws from the hero records, dates and quantities varied, relations kept) | 0 | | yes | yes |
| `broken` | code (error taxonomy to failure injection per operation) | 0 | yes | yes | yes |
| `offline` | code (a behaviour mode: cached data, queued actions) | 0 | yes | yes | yes |
| `extreme` | code (very long text, emoji, right-to-left, large numbers, zero, one and many, from the allow-listed naughty-strings categories and locale pools, §6) | 0 | | on request | yes |

Small apps ship **two data profiles (`empty`, `typical`) and two behaviour modes (`broken`, `offline`)**; the model wrote one. Locale realism is checked by code (names and places in the app's script above a configured share, currency and number formats per locale); **no real-looking ID number or random phone number is ever generated** (§6).

**Tokens by code (A4).** The model picks the mood, voice, ambition, shape and density language and **axes from closed sets**, a **type personality** (a closed set), and, if the user gave no brand colour, a **seed colour**. Code then builds, for each direction: the palette ramps and dark scheme from the seed with colour science (HCT); **semantic colours (success, warning, error, info) and state roles** (pressed, focused, disabled, selected); a **data-visualisation palette** (distinct hues, contrast against surfaces checked); **elevation levels**; **motion durations and named easings** from the motion personality; the **type scale** and, per script, a **fallback family from a dated data table of free fonts** (Devanagari, Kannada, Arabic...), as Default tokens the front end may override. It computes the real contrast of every text-on-surface pair and **asserts** 4.5:1 for body text (a brand colour the user gave stays Locked; the text on it is derived to pass, and G2 says so). Three directions must differ on at least two closed axes, and by at least 60 degrees of seed hue when no brand colour is Locked (thresholds [J]; doc 07 §7 only asks that the directions differ on the defined axes). Only token **names** go into the shared prefix; the values stay in `tokens.dtcg.json`. Signature moments are COULD entries written by the Screens call; code collects the entries of the primary journeys into a **delight block of at most 0.4k** in the prefix, so every worker still knows how bold the app should be (doc 17 §5.2 kept). Tokens are DTCG; spring and blur stay the front end's `ext.*`.

**The glossary.** Code builds it at the Freeze from entity, role and status names and the key verbs (at most 0.7k tokens), with the voice line from Design; `GLOSS-L` runs first and puts it into the **source language and every extra language** in one call; it leads the prefix of every Screens and Locale call (so nine parallel calls say Order and Booking the same way). If `GLOSS-L` fails after its re-ask, the Brief's own terms are used and the locale calls are marked `degraded`.

**Never cut:** the Freeze (Screens never start before the facts are frozen); provenance and the Locked, Default, Open tiers; the library-instantiated states and behaviours (free); hero records and image briefs (the preview must look real); the direction and motion intents in the shared prefix; three directions for standard; COULD on key screens, where a basis exists; G1.5 and G2; honest flags; the content hash. **Also not taken:** truncating a pack to fit; sharing result caches across tenants; one giant call for the whole app; writing every locale inside the Screens call (only the source language is written there); choosing a route by price alone.

## 6. Free tools: only free, only for Spec, only local

**Policy (AD-89).** Only free, open-source or free-tier tools. **No network call in Spec's run path except the LLM gateway**; the gateway sends call content to the configured LLM provider, and nothing else leaves, so no other third party sees a user's app and no other key exists to leak. Every tool is pinned to a version, its licence recorded, and it gets a one-line **smoke test in S0** (the web results are secondary sources). Free LLM tiers exist **only in the proof configuration** (a separate route map used by the proof harness; no runtime flag) and **see only the four synthetic inputs**; the nine exports go to a free tier only after the owner confirms they hold no personal data (OI-43), because one free tier says it may use content to improve its products.

| Group | Tool | Job in Spec | Rule and caveat from the research |
|---|---|---|---|
| **Use now** (local libraries) | **Faker** (Python, seeded) | volume and filler (names, addresses, dates) for `busy` and fixtures; its locales page lists `en_IN`, `hi_IN` and **`mr_IN`** (person, address, date_time, phone_number) and no `kn_IN` | **Kannada:** a curated pool, or the third-party `indic-faker` (claimed, vet first; OI-42). A missing provider silently falls back to English, so the script check (§7, FN-09) guards it. **Do not use** its `ssn`, `bank` providers or random phone numbers |
| | **Big List of Naughty Strings** (MIT, pinned commit) | the `extreme` mode | **allow-list of categories:** Reserved Strings, Numeric Strings, Special Characters, Unicode Symbols, Quotation Marks, Two-Byte Characters, Emoji, Right-To-Left Strings, Zalgo Text, Scunthorpe Problem. **Excluded:** every injection category (script, SQL, server code, command, XXE, interpolation, file inclusion, jinja2), Known CVEs, terminal escape codes, iOS vulnerabilities, trick and special Unicode, MSDOS and Windows filenames, IRC, human injection. A test fails if the pinned file's headings change |
| | **Babel (CLDR)** | number, currency and date formats, plural forms per locale (Indian digit grouping) | S0 test: `format_decimal(1234567.891, locale="en_IN")` gives Indian grouping (Babel's page shows no en_IN example) |
| | **phonenumbers** | validation and formats only; in mock data **phone numbers appear only masked** (+91 98••• ••210), and demo accounts sign in by a demo code or an email at the reserved `example.com` domain | a library's documented example number is not promised to be unassigned, and a generator's number can belong to a real subscriber |
| | **python-stdnum** | used to **reject**: mock data shows ID numbers only masked ("•••• 1234"); **any ID-like value that passes `validate()` fails the fixture** | it checks structure and checksum, so a valid-looking number passes: that is the danger; it has no validator for vehicle plates, UPI ids or account numbers, so a **deny-list** also fails any fixture with a digit run of 9 or more, a plate-like value or an unmasked `name@bank` id; licence LGPL-2.1+ to record |
| | **coloraide** | tonal ramps, dark scheme, real contrast ratios (HCT) | its contrast page lists WCAG 2.1 and L*; APCA not confirmed; **we assert the WCAG 4.5:1 ratio**; the tone rule is only a proxy |
| | **jsonschema, Pydantic, openapi-spec-validator** | schema and OpenAPI validation | PyPI lists OpenAPI 2.0, 3.0 and 3.1 (confirm in S0); the **run path is Python-only** (Spectral, Redocly and promptfoo need Node) |
| | script fallback fonts (Noto families, SIL Open Font Licence) | the per-script fallback entries of the type tokens, from a dated data table | **not searched**: S0 verifies the licence and script coverage of each family and records source and date |
| | pycountry, zoneinfo, python-holidays | codes, time zones, holidays for realism | not searched; pycountry is LGPL; confirm versions and licences in S0 |
| | a tolerant JSON parser (own code, or a vetted small library) | the parse step of §4.2 | builder to decide |
| **Proof only** | **promptfoo** (open source, needs Node 22 or newer) | the provider-switch test and prompt regression | its CI exit-code behaviour is described two ways: verify the installed version |
| | free LLM tiers (Gemini, Groq, Cerebras, Mistral, OpenRouter `:free`, others) | format and schema compliance across providers | low limits (for example 20 requests a minute, 50 a day): useless for throughput; synthetic data only |
| **Later, optional** | **data.gov.in** (GODL-India) snapshot | real commodity names and price ranges for produce apps | an API key is reportedly needed and no rate-limit page was found: snapshot, never a run-time call |
| | **Open Food Facts** | grocery product names | ODbL and CC BY-SA images: attribution and share-alike; skip the images |
| | Synthea; Mockaroo; IFSC and PIN data | health records; pool authoring; formats only | US defaults; limits conflict; terms unclear |
| | Google Cloud Translation (500,000 free characters a month), IndicTrans2 | an optional translation port | **outside AD-89** (app text would go to a third party and needs an account): only with the owner's yes; an LLM costs about USD 0.01 to 0.02 per language at our volume |
| **Not Spec's** (the front end's) | Expo MCP and Skills, component-library MCPs, Context7, Unsplash, Pexels, DiceBear, Lottie, Maestro, MSW, Reanimated, Style Dictionary, Figma MCP | | Spec states **needs** and records what exists in the capability sheet; `AGENTS.md` may name the usual tools as a recommendation, never a requirement |

## 7. Edge cases and issues (the ones that decide the design; the full catalogue is 148 cases in docs 15 and 17, this adds 36 `SPC-FN`, 184 in all)

| ID | Situation | What Spec does |
|---|---|---|
| FN-01 | The model wraps the JSON in prose, reasoning text or code fences | strip them, take the first object that parses and fits; if none, one re-ask; else R-flag |
| FN-02 | Output truncated at the limit | keep the finished array items; redo the rest in smaller batches; a fused call splits back into its parts (the single Plan call too) |
| FN-03 | A fused call returns some valid items and some invalid | item-level repair; the valid items are kept |
| FN-04 | A re-ask fixes one item and breaks another | accept only the repaired items, re-validate the whole call; if worse, keep the original and flag; **one re-ask per node in the run**, so Gate 3 does not ask again |
| FN-05 | An R-code change touches many nodes | every change is logged and counted on the G2 page, never silent |
| FN-06 | More than 15% of nodes need a re-ask for one step | the `weak_route` banner on the report and G2; no stop |
| FN-07 | The model states a fact the Brief and Research do not support, or a number that is not in its quote | V-45 flags it at G2 (and, once switched on after PS-7, downgrades the node to Open `assumed` and logs it); the claim is never turned into a Locked fact |
| FN-08 | A model refuses or filters (health, minors) | fallback route if configured; else library default and `degraded` |
| FN-09 | Faker has no Kannada provider (or any provider) and falls back to English names | a script check on generated people and places (share in the app's script above a threshold) fails the fixture; use the curated pool |
| FN-10 | The naughty-strings list holds injection, exploit, crash and terminal-escape strings | only the allow-listed categories enter `extreme`; a test fails if the pinned file's headings change |
| FN-11 | A fixture needs an ID number or a phone number | IDs and phone numbers only masked; a value that passes `validate()`, a digit run of 9 or more, a plate-like value or an unmasked `name@bank` fails the fixture; demo accounts use a demo code or an `example.com` email |
| FN-12 | A hero record names a real brand or business | deny-list and `avoid` check; hero records are marked synthetic and use only business names the user gave |
| FN-13 | Hero records want image URLs | none: briefs only; sourcing and licences are the front end's |
| FN-14 | Currency or number format wrong for the locale (lakh grouping) | Babel-built formatter, tested in S0 |
| FN-15 | The front end builds screen B before screen A, which links to it | A's closure carries B's route target (name, typed parameters, guard); no build-order word exists (G-2, G-4) |
| FN-16 | Two parallel workers need the same shared block | each slice carries the block body (G-2); the block has one owner for its definition (G-5); a task bundle lists the body once |
| FN-17 | A consumer's window is too small for the one-file form | the tree; `AGENTS.md` says which form to use; the weakest consumer never gets the one-file form |
| FN-18 | The front-end model changes mid-project | re-package only (code); the content hash is unchanged |
| FN-19 | A worker finds a fact missing from a slice | `raise_decision`; the gap is logged as a **pack defect** and becomes a regression fixture (V-27 should have caught it) |
| FN-20 | Double click or two starts for one program | one active run per program; the same input hash joins it (doc 15 §8.4) |
| FN-21 | A provider has JSON mode but no schema mode | the prompt carries the schema in text and the word JSON; local validation is the contract |
| FN-22 | A provider's schema dialect is limited (nullable count, depth) | flat schemas; the offline compile check per profile (PS-0); the fusion rule consults it |
| FN-23 | Free-tier limits stall the provider-switch test | a small sequential fixture set; never used for throughput |
| FN-24 | A real Brief could reach a free tier | free routes exist only in the proof configuration; the nine exports go there only after the owner's confirmation (OI-43) |
| FN-25 | Credits run out mid-run | `partial` with checkpoints; resume |
| FN-26 | Different tokenizers change a slice's size | budgets use the profile's counter; content splits use the fixed reference tokenizer; an over-cap slice is reported, never hidden, and repaired: bulky detail moves to `.detail.md`, then one re-ask shortens the card |
| FN-27 | The user's brand colour fails contrast with white text | keep the colour for brand roles; derive the text-on-brand role to pass 4.5:1; say so at G2 |
| FN-28 | No brand colour | seed from the category and direction; the user may change the seed at G2 |
| FN-29 | The G2 summary call fails | a template summary from code; the paragraph is omitted, never invented |
| FN-30 | A small pack fits one file for a strong consumer and not for a weak one | both forms are produced per consumer profile; same content |
| FN-31 | An integration need from the plan has no record (payments with no interrupted-flow or pending-timeout entry) | Gate 2 fails the integration; one re-ask to `PROD-INTEG`; else a library flow template, `degraded`, on the confirm list |
| FN-32 | Two hero calls would disagree on a shared record or value | ids, counts and relations come from the skeleton; a value that appears in two entities is derived by code from the parent record; related entities go in one call |
| FN-33 | Nine parallel Screens calls mint the same copy key or word a term differently | keys are namespaced by screen id by code; the glossary (at most 0.7k, source and extra languages, `GLOSS-L` first) leads every prefix; if `GLOSS-L` fails the Brief's own terms are used and the locale calls are `degraded` |
| FN-34 | After a route change the same Brief hits the old route's cached output | the cache key carries the route fingerprint; PS-9 bypasses the cache |
| FN-35 | The Plan call is invalid after its re-ask | no default exists: the run ends `partial` and resumes (a fallback route first, if configured) |
| FN-36 | A card needs an operation Product never wrote | one additive re-ask to the owning Product call, then listed at G2 as `needs_decision`; the card keeps the action marked `unspecified` |

Each case gets a named fixture (`spc-fn-01` and so on) under the rule of doc 16 §6 (a link check fails the build if an ID has no fixture).

## 8. Honest limits and risks

| Risk | Handling |
|---|---|
| Nothing here is **Proven**; every number is an estimate | the gates of doc 16 measure; decisions stay Proposed until they pass |
| **The self-check cannot judge meaning** | §3.4: you at G2, the front end's questions, G3, V-45; the planted-defect test (with meaning defects and a proof-only critic arm) shows what escapes; if too much does, Verify for that size is your decision |
| **Fused calls with a weak model** may fail more often | the fusion rule has three limits; item-level repair; the 85% rule un-fuses a kind; PS-5 measures per call kind |
| **The writer's real limits are unknown** (output limit, nullable limit, speed) | S0 records them in the profile; the table shows two output limits; every number is recomputed |
| **The Haiku-class model's judgment on the plan** is unknown | PS-3 and PS-5 decide; the `deep` route can be flipped alone when a better model is configured; no design change |
| Code-made mock lists and assets may look thinner than model-written ones | switches B3 and B4; **the owner compares them on 5 previews in PS-10** |
| V-45 could flag correct facts (number words, transliteration, lakh and crore) | narrow definition; **flag-only until PS-7 shows at most 5% false positives** |
| Slices grow with block bodies and route targets | a task carries two to three slices; an over-cap card is repaired (detail file, one re-ask); PS-2 measures |
| Free tools are known mostly from third-party pages | pinned versions, smoke tests in S0, licences recorded; allow-lists and masking rules in §6 |
| Big apps run **without an independent check** for now | §3.5; G1.5, G2, pilot-first; OI-40 asks you to confirm "internal or beta only" |
| Prices are builder-reported | the estimate is computed from configured prices before a run; ten times the prices still gives about 0.5 and 1.6 dollars |
| Indic locales are token-heavy and machine-translated | batches follow the output limit; glossary first; every non-English locale is `needs_native_review` (OI-33) |
| Parallel building looks less consistent without a pilot | said plainly in `AGENTS.md` (§2.3); the pilot-first step is the front end's |
| The front end ignores the pack | recorded as deviations, never forbidden (doc 15 §2.2); the pilot-first step and G3 show it early |

## 9. Decisions to approve

| ID | Decision (plain words) | Amends |
|---|---|---|
| **AD-83** | **No agent between Spec and the front end for compact and standard.** Spec checks itself at three code gates (call, join, pack); **every validator V-01 to V-45 runs as code** and the builder-needs rule and coverage matrix are kept; the Verify agent is reserved for deep and **not built now**; the pack says `checked_by` honestly. Adds **V-45** (grounding, narrow, flag-only until measured) | AD-17, AD-18, AD-27, AD-35, AD-36, AD-37, AD-73 |
| **AD-84** | **Basic repair ladder:** a short list of mechanical code fixes (every change logged and shown at G2) → one targeted re-ask per node, batched per call kind → library default or drop, flagged. Classes: model-written nodes down the ladder, frozen-node gaps one additive re-ask, generated files fail the build. A `weak_route` banner at 15% of nodes, no quality stop; the Plan has no default and ends `partial` | doc 15 §9.3, §8.4 |
| **AD-85** | **The canary leaves the delivery path:** it runs in the proof and on sampled real runs. **PS-7 becomes the planted-defect test of the self-check** (22 structural and 8 meaning defects, with and without a proof-only critic arm). This **reverses on purpose** doc 15 §13's "canary and independent critic are never dropped for speed" for compact and standard | AD-17, AD-18, AD-27, AD-73, doc 15 §8.2, §9.4, §13 |
| **AD-86** | **Fewer, fuller calls for small and medium** by the fusion rule (three limits: output, input, schema); integrations and glossary translation get their own calls; counts and times shown for two output limits; B1 to B5 are switches decided by tests | doc 17 §10.5, §2.7 |
| **AD-87** | **Mock data:** code mints a hero skeleton at the Freeze (counts by class, relations by code); the model fills hero content (12 to 20 records per primary entity); duplicated values are derived by code; `empty`, `busy`, `extreme`, `broken`, `offline`, demo accounts and recipes are made by code; compact ships `empty`, `typical`, `broken`, `offline`; the owner compares code-made and model-written on 5 previews (PS-10) | AD-80, doc 17 §4.8, §10.5 |
| **AD-88** | **Tokens by code:** the model picks closed-set axes, a type personality and a seed colour; code builds palettes, semantic, state and data-viz colours, elevation, motion tokens, type scale and script fallbacks with contrast asserted; **three directions kept for standard**, one automatic for compact; unbound asset needs written by the Design call; a code-built glossary (at most 0.7k) and a delight block (at most 0.4k) lead every prefix, token names only | doc 15 §4.1, doc 17 §2.6, §2.7, §5 |
| **AD-89** | **Free-tools policy:** only free tools; no network call in the run path except the gateway; pinned, licensed, smoke-tested; allow-lists and masking for fixtures; free LLM tiers only in the proof configuration, synthetic inputs only; translation services outside the policy | new |
| **AD-90** | **Portable call contract:** one JSON object; strictest mode the gateway has; local validation; tolerant parse with array salvage; item-level re-ask; split on truncation; prefix-first prompts with the word JSON; flat schemas within profile limits; cache key with the route fingerprint | AD-76 |
| **AD-91** | **Static pack guarantees G-1 to G-8:** closures carry block bodies and route targets, copy keys are namespaced by code, honest about consistency and about the one-file form; three consumption recipes; `spec check` as code anyone can run | AD-69, AD-70 |

**Open items:** **OI-40** big apps run without an independent check until Verify exists (my recommendation: internal or beta only; your call). **OI-41** thresholds and switches (15% banner, 85% first-try pass, un-fuse rule, B1 to B5), tuned in PS-5 and PS-10. **OI-42** who writes and reviews the Kannada pool and the Marathi and other name and place pools and the common-strings catalogue (ties to OI-33). **OI-43** the terms of the free LLM tiers for proof, which to use, and whether the nine exports hold personal data. The writer's real limits go into **OI-31**.

## 10. What changed in the other documents, and the build order

**Amendments and reversals, stated openly (applied to the documents on 2026-11-14 as banners and notes; this document wins where they differ):**

| Where | What it said | Now (compact and standard) |
|---|---|---|
| **AD-27** (doc 00) | the Spec output is tested by a builder canary before its gate opens | the canary runs in the proof and on sampled runs, not in the delivery path |
| **Doc 15 §13** (Quality row; "Optimizations deliberately not taken") | canary and independent critic "none of these is dropped for speed or money"; "skipping the canary to save time" | **reversed on purpose**, on the owner's direction, for compact and standard only; kept in the proof |
| **AD-17, AD-18, AD-36** (doc 00) | a sufficiency probe with a weak canary; writer and critic from different vendors; the Verify streaming and speculation parts stand | amended for compact and standard: no delivery-path canary, critic or Verify streaming; the proof keeps them (the vendor constraint applies to the proof-only critic arm) |
| Doc 15 §8.1, §8.2, §9, §10, §16 | Verify streams beside Spec; canary per task card; call formula and numbers | Verify reserved for deep; self-check in Spec; the steps, calls and numbers of §1 |
| Doc 15 §4.8, §6.1 | whole-pack mode by window | the rule stays; "all at once" is only for consumers that read about 80k or more |
| Doc 15 §5.4, §5.7 (closure) | block bodies once per task; closure without route targets | the closure carries block bodies and route targets (a task bundle dedupes) |
| Doc 15 §8.1 step 5 | a gap raises `needs_decision` to Product | one additive re-ask per node, then listed at G2 |
| Doc 15 SPC-MD-13, SPC-OP-02 | "the critic still runs"; "one retry on a stronger route" | stale: replaced by the ladder (§3.2) and FN-rows |
| Doc 07 §7 row 6 | stage-6 gate: canary implementability at least 85%, critic recall at least 90% | measured in the proof (PS-6, PS-7), not at run time; V-45 added |
| Doc 12 §4.4; doc 09 step 8; doc 02 VRFY-1 to VRFY-3 | the Verify and package agent: critic, canary, repair | reserved for deep, not built; VRFY-4 (the summary) stays as `G2-SUMMARY` inside Spec |
| Doc 16 PS-0, PS-5, PS-6, PS-7, PS-9, §6, §7, §8, §10 | critic and `critic_vendor`; 148 cases | smoke tests and compile check in PS-0; per call kind pass rate in PS-5; PS-6 in the proof; PS-7 as above; cache bypass in PS-9; 184 cases; `critic_vendor` only for the proof arm |
| Doc 17 §4.8, §7, §10.5, §2.5, §2.7 | mock by size, depth by size, order of steps, block and asset naming | §1, §5 (hero skeleton, glossary, B1 to B5); the G2 "Look" view is unchanged for standard |
| Doc 17 §5.2, §5.3 (signature moments in the direction file, in the prefix) | written by the Design call into the prefix | written with the COULD entries by the Screens call; code collects the entries of the primary journeys into a delight block of at most 0.4k in the prefix |
| **Every other delivery-path Verify, canary or critic statement** in docs 00 to 17 (for example doc 15 §1.1 diagram, §4.1 Verify row, §7.1 "proven by critic or canary", §8.6, §8.7, §11.2 route rows, §14 worked examples, §15 risk row, §16.1 rows, the L2 change row; doc 05 change step 8; the Research `verify` view) | as written | **superseded by §3 for compact and standard** |

**Done on 2026-11-14:** (1) docs 15, 16 and 17 carry an amendment banner and a note at each section in the table; (2) doc 00: AD-83 to AD-91, OI-40 to OI-43, doc index row 18, version v3.9, and V-45 in doc 07 §2; (3) banners on docs 02, 03, 06, 07, 09 and 12; (4) **prompt 12 is v3**: S5 is "self-check and report" (Verify not built, interface reserved), the fusion rule and call catalogue, hero skeleton, glossary, mock and token derivation by code, the free-tool list with S0 smoke tests, the portable call harness, `spec check`, the new non-negotiables and DO-NOTs; (5) STATE, INDEX, README, log. The paragraphs inside docs 15 to 17 that the banners supersede are not rewritten line by line; a later tidy-up can do that.

**Builder phases (revised, simpler):** **S0** shared packages, Compass reader, fixtures (the nine exports and four synthetic inputs), consumer and provider profiles **with the writer's recorded limits**, the free-tool smoke tests, the portable call harness · **S1** deterministic derivation (system set, states, contract, tokens, mock modes, hero skeleton, glossary), packager, slicer, build plan, libraries v1, `spec check`; **zero model cost** · **S2** Plan, Modules, Integrations, Journeys · **S3** Design · **S4** Screens, Hero records, Behaviours, Glossary and Locales · **S5** self-check and report (three gates, ladder, banner) · **S6** a small viewer for you. Verify (agent): not built. The proof order of doc 16 stays: zero-model gates first.

**What happens next:** the owner pastes [prompt 12 v3](../prompts/12-spec-agent-build-prompt.md) to the builder; it starts at S0 and S1 (zero model cost) and reports against doc 16's gates. Anything the owner wants changed is a change request to this document first.

## 11. What the independent review changed (v1 to v2)

Three blockers: the repair guard counted calls while PS-5 counts nodes, so it would have stopped runs that met the proof target (now a node-based banner, no stop); Verify's deterministic duties were not re-homed (now every validator runs at Gate 3, plus the builder-needs rule and V-45); G-2 to G-6 did not hold as written (closures now carry block bodies and route targets, copy keys are namespaced, G-3 and G-4 are honest). Also: repair classes per gate and a short mechanical code-fix list (binding narrowing and copy-key invention removed from code); grounding checks (V-45); meaning-defect kinds in PS-7; hero skeleton by code (so hero calls need not precede Screens and cannot disagree); integrations and glossary calls added; three directions kept for standard; tokens expanded to the roles doc 17 requires; B-switches with an owner A/B instead of "none"; two output-limit scenarios and like-for-like baselines; the route fingerprint in the cache key; free-tier privacy (four synthetic inputs, no runtime flag); the Faker finding corrected (Marathi exists, Kannada does not); the naughty-strings allow-list; the stdnum rule reversed; explicit reversals and the stale rows. **A second pass (the reviewer re-checked v2) found 11 smaller problems, all resolved here:** an over-cap slice is repaired instead of killing the run, and a task carries two to three slices; V-45 is narrow and flag-only until PS-7 measures false positives; the Gate 3 re-ask wave and the V-37 end state are defined; hero skeleton counts by class and duplicated values derived by code; the glossary runs first, is capped and covers the source language; token names only in the prefix plus a delight block; phones masked and a deny-list for other IDs; integrations in the pack table; compact and 8k numbers recomputed from one model; the missing amendments (AD-17, AD-18, AD-36, signature moments, a catch-all).

## Appendix. The model behind the estimates [J]

Per-call latency = 8 s + output tokens ÷ 150 per second; a call starts when its inputs exist; in standard the Design, Modules, Integrations and Journeys calls run in parallel after the Plan (in compact Design runs beside the fused Plan); the glossary translation runs right after the Freeze and Screens wait for it; Hero and Behaviours also start after the Freeze; locale batches start when their screens are done; one re-ask wave before assemble; the G2 summary runs beside assemble. Sizes in thousands of tokens (input, output) for writer output 32k or more:

| Call kind | Compact | Standard |
|---|---|---|
| Plan (compact: plan plus product) | 1 × (12, 10) | 1 × (14, 6) |
| Modules | in the plan | 5 × (14, 6) |
| Integrations | in the plan | 1 × (8, 3) |
| Journeys | in the plan | 2 × (10, 3) |
| Design | 1 × (8, 3) | 1 × (10, 4) |
| Screens (with copy) | 4 × (14, 7) | 9 × (16, 7) |
| Hero records | 1 × (8, 5) | 2 × (10, 7) |
| Behaviours | 1 × (10, 3) | 1 × (12, 4) |
| Glossary translation | 1 × (3, 2) | 1 × (3, 2) |
| Locales | 2 × (7, 9) | 10 × (8, 9) |
| Re-asks | 2 × (6, 2) | 4 × (6, 2) |
| G2 summary | 1 × (6, 1) | 1 × (8, 1) |

For writer output 8k the per-call output is at most 4k, so cards go 2 per call, modules and hero batches shrink, locale batches fall to about 4k each, and the counts rise as the table in §0 shows (25 and 70; the 8k lists carry the same total output as the 32k lists, about 0.075M and 0.23M tokens). The "before" figures are doc 15 §14 (15 and 52 calls) plus doc 17's additions (doc 15 §8.2: about 28 more calls for the Nashik example), costs grown by the same tenth to a quarter doc 15 §10.4 gives, and times grown likewise.

**Sources:** [external tools research note](../notes/spec-external-tools-research-2026-11-14.md). Related: [doc 15](15-spec-agent-design.md), [doc 16](16-spec-proof-plan.md), [doc 17](17-spec-content-model.md), [prompt 12 (v2, on hold)](../prompts/12-spec-agent-build-prompt.md).
