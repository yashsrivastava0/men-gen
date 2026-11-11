# 13. The Research agent (design v5)

- **Status:** v5 written 2026-11-11, after the builder's offline build (branch `feat/research-agent`, 26 commits, no live run yet) and a read-only review of the built code. Replaces v4 (earlier the same day). No code and no live calls in this workspace. **Where the built code and this doc differ, §20 says what the code does and what v5 changes.**
- **Status words used below:** **Built** = implemented offline by the builder and checked in its code; **Proposed** = designed here, not built; **Proven (PG-n)** = shown to work by gate n in [doc 14](14-research-proof-plan.md). **Nothing is Proven yet** (there is no live run). The owner's rule: nothing is approved until it is shown to work.
- **What changed from v4 (owner, 2026-11-11: "detailed, correct, not hallucinated; adapt by size and kind of app; privacy and security light; not much on regional language; a little cost and latency; decide cache, batch and parallel"):** (1) **Sync to the build** (§20). (2) **Size and detail without silent loss** (AD-55 rewritten): a loop cap, a main store with an appendix, packs in Spec (§7.2). (3) **Truth layer** (AD-56): every item checked inside its topic, numbers must be in the quote, no attribute the page does not state, dates from the page, independence by content, region fit, an evidence ladder and use rules for Spec (§5.2, §9.4, §9.5). (4) **Category adaptation** (AD-57): capability signals, a light `trust` job, light mode for tiny apps, a class margin, a reshaped design job (§3.3, §4). (5) **Speed and cost** (AD-58): foreground parallel, never batch; provider failover; cache paused (§7.3, §7.4). (6) **Proof plan** (AD-59, doc 14). (7) **A standalone, live-only test app with its own front end** (AD-60, §10.1). Privacy and security stay baseline for Research's own data (AD-54); regional-language care is cut down to the `local` job.
- **Scope:** agent 2 of 4 in the Context Engine (doc 12 §4.2). Where this doc and docs 02, 03, 05, 12 differ on Research, this doc wins.
- **Compass facts used:** [notes/compass-as-built-2026-11-10.md](../notes/compass-as-built-2026-11-10.md) (the `research` view, `research_requests`, the author's corrections §8b) and the three real exports (`/Users/coder/Desktop/project/men-gen/docs/compass/research-inputs/`).
- **Readings of your words (tell me if wrong):** "runs in the frontend" = foreground and visible; "character search" = targeted, entity-level search; "ChatGPT, Luna, a third provider" = writer model, checker model, one search provider.
- **Evidence labels:** [V] opened in an official or primary source in this pass; [S] secondary (search summaries, blogs, forums); [J] architect judgment, to be measured. Sources: [doc 10](10-research-log.md) rows R-53 to R-64. Facts about the built code carry a file reference and were read in the code on 2026-11-11.
- **Earlier versions:** v1 and v2 (2026-11-10 and 11), v3 (load, slots, events), v4 (organized by what Spec decides); the changes are listed in §18.

## 0. The design on one page
1. **One agent** (`agents/research`), foreground, visible, starting at **Gate 1 approval**. It enriches and never decides (Default or Open tier, never Locked; no scope status; no Spec Graph writes).
2. **It decides its own depth.** From the Brief's content it computes a **research load** (focused, standard, deep). Compass's tier is not an input. The load sets ceilings, not targets: a target stops as soon as its slots are filled. A 3-point margin keeps a borderline app in the larger class, and a tiny app gets a **light mode** (§4.2).
3. **Orchestrator-worker, code-controlled.** Code is the orchestrator (no free-running LLM agent loop). A planner turns the Brief into **targets**, each with a typed **slot schema** (what Spec needs). **Capability signals** computed from the Brief (payments, scheduling, tracking, sensitive data and so on) switch slot packs on and off (§4.1). One **worker per target** runs in parallel with its own context: write queries, search, triage, read, extract, **verify and check**.
4. **Rounds with evidence-aware stopping.** Round 1 covers every target (discovery first). A coverage audit finds unfilled required slots and newly discovered entities; round 2 (and at most a round 3) goes only there, with new distinct queries. Stop when required slots are filled, when a round adds little, or at the ceilings.
5. **Queries are specific by construction:** anchor (named entity or domain noun) + place + intent word; variants merged by rank fusion; round 2 can restrict to a discovered entity's own domain. A local-language variant runs only in the `local` job.
6. **Organized by what Spec must decide** (eight jobs: features, structure, flows, data, services, design, screens, local, plus the conditional `trust` job). Each is searched for **the category norm** (how apps of this kind usually do it, how common, how recent) **plus the idea's own details**.
7. **Truth layer (AD-56).** Every fact has a verbatim quote that code finds on the page; every number in a claim must be in its quote; no attribute the page does not state; dates come from the page; **every item is checked by a second model call inside its topic**; independence is judged by content, not only by domain; pages about another country or entity are flagged; each item gets an **evidence ladder** value. Synthesis selects and orders; it writes no new facts.
8. **Output `research.v5` is a playbook for Spec:** items (merged findings) indexed by the Brief's ids, each with `evidence`, prevalence read as "mentioned in n of N", recency, `applies_to`, tier and sources; `norms`; `suggestions` (never scope); a `confirm` list; **`lane_quality` and `bn_coverage`** so Spec knows how far to trust each lane; a **main store plus an appendix** (nothing is deleted for size); digests as an index; and a code-rendered `research_report.md` for people.
9. **Live events** (`research.event.v1`) stream plan, queries, sources, facts and progress to the UI; the same contract serves the test front end and the future full one.
10. **Providers:** writer Luna; checker Luna on its own route (same vendor, flagged); Tavily and Serper behind `SearchPort` (a primary and a failover after the bake-off); own `FetchPort` (trafilatura + PDF text layer, public URLs only).
11. **Parallel, never batch, inside the run** (a person is waiting). Batch calls are for offline evaluation only. The search cache is kept as built and not extended (AD-58).
12. **Three caps, nothing dropped silently (AD-55):** the loop stops by evidence; the main store has a token limit and the rest goes to the appendix; Spec chooses between the whole store and per-call packs by measured size (§7.2).
13. **Nothing is approved until shown to work** (doc 14): live gates, an owner spot-check, a checker test with planted errors, and a with-and-without-research comparison.
14. **Fails soft** (partial, skipped) and never blocks Spec.

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
- **Outputs:** `research.json` (v5; read by Spec; reaches the front-end engine only inside the spec), `research_evidence.json` (internal) and `research_report.md` (rendered by code on demand from both; for people).
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
The approved Brief's **research view** (idea, geography, languages, audience, references, feature intents, glossary, `research_requests`, `search_hints`, declined, module and entity names, `domain_facts`, `guidance.focus`), Compass trust flags (CR-1 to CR-3, §15), builder needs BN-01..14, Stack Kit facts, the authority registry (data file), and the run controls (Skip; a missing key or budget also skips the run). **Never:** the transcript (AD-20), uploaded documents as text, other agents' outputs, contact details of you or your staff.
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
`load_score = deduped features + facts of kind rule, compliance, integration_detail or nfr + 3 × apps + 5 × sensitive-data flags + 2 × named references` (**Built**, `data/load.yaml`). Classes: **focused** below 25, **standard** 25 to 60, **deep** above 60 [J]. **As computed by the built code:** Quick yoga **17** (focused), Standard produce **27** (standard), Thorough freight **83** (deep). (v4 estimated 31 to 41 and 95 by hand; the builder's dedupe is stricter. Only 17 is unit-tested; 27 and 83 come from the builder's report. A comment in `load.yaml` still says 17, 30 and 90.) A "Quick" interview with a regulated, integration-heavy idea scores higher; a "Thorough" interview about a simple idea scores lower. The class is shown in `input_quality` and in the first live event.
- **Class margin (Proposed, RC-8).** Standard scores 27, two points above the boundary, so a small dedupe change would flip it to focused and halve its ceilings. A score within **3 points below** a boundary uses the larger class (`class_margin: 3` in `data/load.yaml`: 22 and 57). The hard ceilings still bound cost. A continuous interpolation of ceilings was considered and left as an option: it would change the built numbers without evidence.
- **Light mode (Proposed, RC-8, §4.2)** applies to tiny apps (a score below about 12 with no signal).

## 4. Jobs: research organized by what Spec must decide
**Principle (owner, 2026-11-11).** To build an app of a given kind, Spec decides its features, its structure (screens, navigation, roles), its flows, its data and rules, its integrations, its design, the content of each screen, and its mock data. Research brings, for each of those decisions, **how it is usually done, how common each choice is, and what is current**, from several sources, so Spec starts from a grounded norm instead of a guess, and then adds what is special about *this* idea from the Brief's facts. Research serves the downstream engines first; the front-end engine benefits through a better spec.
Eight jobs plus one conditional job (`trust`, §4.1). Every target carries a `spec_need` and a `lane`, and typed slots (minimums are starting points; more verified detail is better). **Built:** the eight jobs and their slot schemas are data files (`data/jobs/*.yaml`); the table shows the v5 values.
| Job | Spec decision it feeds | Finds | Required slots (starting minimums) | Lane |
|---|---|---|---|---|
| **features** | Scope (BLUE-1) | Comparable and local apps; the feature set by commonness (core, common, advanced, emerging); recent additions; pricing models | 3+ competitors each with 5+ features (level); norms for 8+ features with prevalence | blueprint |
| **structure** | Screens, navigation, roles (BLUE-2) | The usual screen inventory per app and role; navigation patterns and when they are used; the usual roles and what each does; module grouping | 8+ typical screens with purpose and the role that uses them; 1+ navigation pattern; roles with 3+ typical tasks | blueprint |
| **flows** | Journeys (BLUE-2, BLUE-3) | Usual journeys per role with steps, alternates and exceptions; onboarding; empty, failed and offline cases | per main journey: 4+ steps, 1+ alternate, 1+ exception | blueprint, screens |
| **data** | Entities, lifecycles, rules (BLUE-3) | Usual entities, fields (type, required, values, validation), relations, statuses and transitions, business rules and formulas, KPIs; regulatory rules as information | per main entity: 6+ fields and states where a lifecycle exists; rules with value, unit and source | blueprint |
| **services** | Contract and integrations (BLUE-4, BN-10) | Providers by need (payments, OTP and messaging, maps, telematics, identity, government portals): auth, endpoints or events, sandbox, limits, fees, regional fit, current status | per need: 2+ providers with auth, events and sandbox | integrations |
| **design** | Look and feel (DSGN) | **Checkable facts first:** platform guidelines (Apple HIG, Material) as official sources; accessibility guidance; script, font and text-direction needs when the locale is not Latin. **Optional, only when a page states them in text:** category conventions (layout, onboarding, components, colour and tone mood, typography and density, iconography, motion) and recent design trends | **required:** 3+ platform-guideline conventions and 2+ accessibility items (official), a script need when the locale is not Latin; **optional:** 3+ category conventions across 2+ aspects. (Built today: 6+ conventions across 4+ aspects are required and the guidelines are optional, §20) | design |
| **screens** | Per-screen content (SCRN) | For each usual screen pattern: sections, key components, actions, states (loading, empty, error, success), validations, microcopy conventions. Mostly from help centres, product documentation and onboarding pages, which do describe screens in text | patterns for the 3+ most important screens (5+ for deep loads, per module cluster); `not_found` is an accepted answer | screens |
| **local** | Mock data and formats | Formats and ID patterns with validation patterns and examples; local terms and units; realistic names, price ranges, edge cases | formats where unusual; 10+ realistic examples for the main entities | mock |
| **trust** (conditional, §4.1) | Non-functional requirements, permissions, consent and delete-account screens, payment integration (BLUE, SCRN) | Data-protection law that applies in each country of the geography (information); store privacy disclosure (Apple App Privacy details, Google Play Data safety); sign-in and session norms; payment-security scope; consent and data-subject-rights patterns; retention and residency; breach-notice timelines; role and audit norms | all optional (`required: false`) except the store disclosure when the job runs; kinds `compliance_note`, `rule`, `service_option`; always `legal-adjacent` and `confirm` | blueprint (NFR), integrations, screens, verify |
- Regulations and compliance are information inside `data` (rules) and `services` (portals), flagged `legal-adjacent`.
- **Targets come from the union** of the Spec-need areas that apply to this kind of app, Compass `research_requests`, `guidance.focus` and high-impact `domain_facts` (Standard's `focus` lacks topics its requests cover; `focus.compliance` mixes enum values and sentences, so it is a hint).
- **Two layers per job:** the **category norm** (how apps of this kind usually do it, from several comparable sources) and the **idea-specific details** (what this Brief's unusual facts need, for example an e-way bill for a freight app).
- **Norms, not just facts.** For features, screens, flows, entities, integrations and design conventions, code computes **prevalence** (seen in n of N distinct sources or competitors) and a level: `core` (most), `common`, `advanced`, `emerging` (recent and rising). Fewer than 3 sources: `limited_evidence`.
- **Latest.** Features, design and services targets prefer sources from the last 24 months (a time range on the query; `published_on` and `age_months` on every item; older than 24 months flagged `stale`); `trend` items record what changed recently. Regulations and provider facts use the newest official source.

### 4.1 Capability signals and the `trust` job (AD-57; Proposed, RC-8)
**Why.** The built planner finds integration needs from single English words (`data/need_keywords.yaml`: "id", "buy", "pay", "sign" and so on) and has four `when:` conditions, one of them used (§20). A word such as "id" or "buy" triggers searches the app does not need, and health data, minors and several regions are not detected at all. **Decision:** replace the keyword needs with **capability signals**, computed by code from feature intents, integration facts, rule and compliance facts, roles, apps, geography and the sensitive-data flags. Each signal has **strong words** (one is enough) and **weak words** (at least two, from different features or facts), kept in data. A signal is a generic capability, never a category name or a brand, so the same signal gives the same slot pack in a clinic, a shop or a fleet app (nothing is hard-coded to one app type).
Starting signals [J] (the trust triggers are `health_data`, `minors`, `identity_kyc`, `multi_region`, `sensitive_data` and `payments` with `two_sided`): `payments`, `messaging`, `maps_location`, `tracking`, `identity_kyc`, `government_documents` (a tax and rules topic, not a trust trigger), `accounting`, `scheduling`, `two_sided` (buyers and sellers, riders and drivers), `media_upload`, `health_data`, `minors`, `multi_region`, `non_latin_locale`, `many_roles` (5 or more), `multi_app` (2 or more), `sensitive_data` (the Compass flags). A signal turns on slot packs and seed topics in the job files through `when:` (for example `payments` adds payment-provider options, settlement and refund rules, and tax and invoice formats). The builder tunes the word lists; the mechanism is the decision.
**The `trust` job.** It researches, as information and never as legal advice (`legal-adjacent`, `confirm`): the **data-protection law that applies in each country of the geography** (at most 3 countries), **store privacy disclosure** (Apple App Privacy details, Google Play Data safety), **sign-in and session norms** for the category (OTP, MFA, device binding), **payment-security scope** (card-data rules, gateway tokenization, regulator rules for the local rails), **consent and data-subject-rights patterns** (consent, access, deletion), **retention and residency**, **breach-notice timelines**, and **role and audit norms**. Output kinds are the existing `compliance_note`, `rule` and `service_option`. Official sources come first (the authority registry); high-impact items need an official source or two independent organizations (R8).
**When it runs (owner, 2026-11-11: "baseline for small apps; for big apps, and in some cases medium apps, search it"):** always for the **deep** class; for **standard** or **focused** when `health_data`, `minors`, `identity_kyc`, `multi_region` or `sensitive_data` fires, or when `payments` fires **together with** `two_sided` (money moving between parties: payouts, commissions, escrow). **`payments` alone does not trigger it:** a payment gateway carries the card-data burden, the `services` job already researches the provider, and Spec's module library covers the standard checkout rules. The paper test showed why: every one of the six fixtures except the tiny converter has a payments integration, so a payments trigger would switch the job on for every small commercial app and defeat "baseline for small apps". **A content trigger overrides size:** a small clinic app needs it, and a large app with none of the signals still gets it because it is deep. The owner may change this rule (OI-21). The job has low priority (45) and optional slots, so it never crowds out the core jobs, and `not_found` is accepted. Spec uses it for non-functional requirements, the permission model, consent and delete-account screens and the payment integration; Verify uses it for checks. Research's own handling of data stays baseline (AD-54, R12).
**Regional language.** Not a focus (owner, 2026-11-11): the local-language query variant runs only in the `local` job and only when the region's language is not English; there is no native-review gate (OI-15 downgraded). Quote checks still work in any script (NFKC).

### 4.2 Light mode for tiny apps (Proposed, RC-8)
**Why.** In the built code a one-feature app with no place still plans 13 candidate topics (features 2, structure 3, flows 3, design 4, screens 1), trims them to 10 and allows a round-1 pool of 22 searches (`plan/build.py`, `engine/director.py`); Compass's own small `search_budget` is ignored by design. For such an app most of the output would be generic filler. **Rule:** when the load score is below about 12, no signal fires, and there is no named reference, no integration or rule fact and no place, the plan keeps only the **feature norms** topic and the **screen inventory** topic (2 variants each, soft ceiling 8 searches, about 90 s). The run commits `complete` with `profile.light: true`, and every skipped topic is a visible gap `not_worthwhile`. Spec then uses its module library. The threshold is [J]; the live Quick run (load 17) shows whether even a small real app gains enough from the full set.

## 5. The run
Trigger: the orchestrator sees `gate1.approved` (Brief version and hash); one run per program. Agents never import each other.

### 5.1 Steps
| # | ID | Who | What |
|---|---|---|---|
| 0 | RSCH-0 | code | **Intake.** Verify Brief hash = approved hash (RV-13). Build `ResearchInput`: dedupe, tiers, `input_quality`, **load score and class**, own-brand terms, declined topics. Emit `run.started`. |
| 1 | RSCH-1 | Luna | **Plan.** A research brief (idea, places, languages, constraints) and **targets**: job, entity, region, languages, question, `decision_unlocked`, links (RR and BN ids, Brief ids), priority, slot schema. Template or thin requests are rebuilt from facts. Emit `plan.ready` (user-facing target labels). |
| 2 | RV-14 | code | **Worthiness gate** (§6.1): drop targets whose answer would not change a spec decision or that the model already knows. Merge targets by entity. Rank. Emit the final plan. |
| 3 | W-1..W-n | **one worker per target, in parallel, isolated context** | See §5.2. Round 1 for every target, pipelined (no global barrier). Each worker also **verifies and checks** its items (§5.2 steps 6 and 7) before the audit. |
| 4 | RSCH-1c | code, then Luna | **Coverage audit** (§5.3): filled required slots per target; entities discovered. The gap analyst (Luna) proposes follow-up queries **only** for unfilled required slots and for discovered entities worth deepening. |
| 5 | W-1..W-m | workers | **Round 2** (and a round 3 at most for standard and deep loads), same pipeline, restricted to the gaps. |
| 6 | RSCH-3 + RV-05 | code, then Luna (no tools) | **Assemble.** Code builds the items (atoms merged by norm key), norms, the `evidence` ladder, `lane_quality`, `bn_coverage`, the main store and appendix split (§7.2), and the digests. Ordering inside a lane is done by code; **one batched Luna call per lane writes only the optional `use_note`s**, guarded so that no number or name appears that is not in the item (RV-05, enforced). It writes no new facts and **may not drop or shorten a verified item** (RV-09). Code renders `research_report.md` on demand. |
| 7 | commit | code | Write `research.json` and `research_evidence.json` (the report is rendered from both); emit `run.finished` with stage timings and cost by stage. |

### 5.2 The per-target worker (a fixed pipeline, not a free agent)
1. **Write queries** (Luna, small; sees only its target, slot schema, anchors and trust flags): 2 or 3 **variants** in parallel: *literal* (the named entity in quotes with `exact_match`), *descriptive* (anchor + place + intent), *local-language* when the region's language is not English.
2. **Search** all variants in parallel through `SearchPort`; **merge by reciprocal rank fusion** and dedupe by canonical URL; apply source-class ranking and the authority registry.
3. **Triage** (code score; one batched model call only when more than about 12 candidates): choose the top 3 to 5 pages by snippet relevance, source class and domain diversity (at most 2 per domain).
4. **Read:** get page text (provider content, else `FetchPort`); **select passages** around the slot terms (BM25 windows), not blind truncation.
5. **Extract** (Luna, small, no tools), in parallel per page, into the slot-typed record: a paraphrase of at most 40 words, a verbatim quote of at most 25 words, **everything the page supports** (as many distinct, specific items as it states: every feature, screen section and state, field, step, exception, provider capability, limit, fee and number; when a page yields items from most of its windows, the remaining windows are read too, up to `max_windows_per_page`, default 6 [J]) and **only the attributes the passage states** (anything else is null: the built prompt asks for "every field with its type and allowed values", which invites invented types, §20). The extractor also returns `about_region` and `about_entity` as the page states them.
6. **Verify (code):** the quote must be found on the stored page (RV-02; one re-extraction for the failed keys, then dropped); **every number, amount, percentage and date in the claim and in numeric data must occur in the quote** (RV-18: code may widen the quote to its sentence, else the item is rejected `number_not_in_quote`); **dates come from the page metadata or are parsed by code from the quote, never taken from the model**; a claim over 40 words is cut only at a sentence end, else rejected; region and entity fit (RV-20); source in the fetch log (RV-03). Emit `fact.found`.
7. **Check (second model call, Proposed, RC-2):** all verified items of the topic, in batches of 15, **in parallel with other topics and before the coverage audit**. The checker sees the claim, the typed data, the passage window (about 150 words around the quote) and the page title and domain, and returns `supported`, `partial` or `not_supported` plus the **unsupported field paths**. Code nulls unsupported fields, drops `not_supported` items and marks `partial` items `unverified`. A failed call leaves the item `unchecked` and `unverified`, never trusted. Update slot fill. Emit `fact.verified` or `fact.rejected`.
Workers do not see each other's raw pages (context isolation); they return only verified, checked, typed items.
**Built today (§20):** steps 1 to 6 without the number, date, fit and field-support checks; the checker runs once after all rounds, in sequential batches of 15, on high-impact items only, and sees only the claim and the quote.

### 5.3 Coverage audit and stopping (evidence-aware)
- A required slot is **filled** by at least one verified **and checked** item; for `impact: high` it needs an official source or two independent organizations (by content, RV-19), or it is flagged `single-source`. **A checker rejection removes the item before the audit, so the slot is open again and a follow-up can run** (RS-38); today the checker runs after the last round and cannot reopen anything.
- A target is **covered** when all required slots are filled. Covered targets stop immediately and spend no more budget.
- **Follow-ups** target only unfilled required slots, with queries that differ from earlier ones (no repeats); at most 2 follow-ups per target. After that the target is `not_found` (a gap with the attempted queries). This is the guard against endless searching for sources that do not exist [V, Anthropic].
- **Entity deepening:** a competitor, provider or authority found in round 1 that matches a required slot gets one query restricted to its own domain (`domain_restrict`), to read its official pages.
- **Stop** when every target is covered or `not_found`, **or** a round fills fewer than 10% of the remaining required slots (**Built**, `min_round_gain`), **or** a ceiling is hit (searches, pages, time box), **or** Skip is pressed. On a ceiling the run finishes extraction **and the checks** of pages already read and commits `partial`. **The time limit covers the whole run, including checks and assembly** (the built code stops only new searches and pages, so a late serial checker can overrun it, §20).

## 6. Query strategy
### 6.1 Worthiness gate: search what Spec needs grounded
A target is searched if it feeds a Spec decision (`spec_need`) **and** at least one holds: it needs **current or regional grounding** (providers, fees, rules, trends); a **norm needs evidence of how common it is** (features, screens, flows, entities, design conventions); it names an **entity** (from the Brief or found in round 1); it involves a number or an integration's specifics. Not searched, answered from model defaults and flagged `assumed`: pure trivia that never changes a decision (a currency symbol, common date formats). **Norms for the Spec-critical areas are always worth grounding, even where the model already knows them**, because they must be sourced, current and quantified.

### 6.2 Query anatomy (code-checked, RV-06 and RV-14)
Every query has an **anchor** (named entity from references and glossary, a discovered entity, or a domain noun in your own words), a **place** (from Locked geography, or country plus every stated city when `geo_uncertain`), and an **intent word** (features, fees, rules, API, sandbox, limits). **Category-only queries are rejected.** Queries are short first (about 4 to 12 words) and narrowed in later rounds [V, Anthropic]. At most 25 words.

### 6.3 Variants and fusion
Per target, 2 or 3 variants run in parallel (literal, descriptive; a local-language variant only in the `local` job, §4.1). **Built:** in round 1 the variants shrink from 3 to 2 to 1 for the lowest-ranked topics when the search pool (70% of the hard search ceiling) is small. Results are merged by **reciprocal rank fusion** (code), so a page that several variants return rises. This improves recall without more model calls, and it is the cheap form of query decomposition.

### 6.4 By job
- **features:** discovery first (2 variants: who the comparable and local apps are; what they offer), then deepen on discovered apps (their feature, pricing and help pages; `domain_restrict`). Time range of 24 months for "latest". Compute prevalence across at least 3 apps.
- **structure and screens:** from the same competitor pages, app-store listings and product tours (public pages), plus UX articles for the category; extract screen inventories, navigation patterns and per-screen sections and states. Platform guidelines (Apple HIG, Material) are official sources for patterns.
- **flows:** help-centre and onboarding pages, how-it-works pages, process descriptions; steps, alternates, exceptions.
- **data:** domain explainers, official forms and documents (for fields and validations), provider and regulator pages (for rules and formulas); official domains from the authority registry preferred for rules; one-year time range for fees and rules; two sources for numbers; rules flagged `legal-adjacent`.
- **services:** discovery of providers by need, then `domain_restrict` to each provider's documentation (auth, endpoints or webhooks, sandbox, limits, fees, current status). The Stack Kit wins on Expo compatibility.
- **design (v5):** **required:** the platform guidelines (Apple HIG, Material; official, restricted to their domains) and accessibility guidance, plus script, font and text-direction needs when the locale is not Latin. Category conventions (layout, onboarding, components, colour mood, typography, density, iconography, motion) are **optional** and recorded only when a page states them in text (design-system pages, case studies, help centres), flagged as conventions with lower confidence and `seen_in` counts; recent trends from the last 24 months. Web text rarely states taste, so a low yield here is expected and measured, not treated as a failure (doc 14 decision table).
- **references and exact entities:** quoted phrase with `exact_match`. Zero hits means `not_found`: no claims, and Spec treats the name as yours. A collision with another company means `ambiguous` with candidates, confirmed at G2. Never substitute a similarly named real company.
- **local:** an English query, plus one in the region's language when it is not English (the only job with a local-language variant; not a focus, §4.1); Hinglish (`hi-Latn`) is searched in English with transliterated terms (Compass sends `hi` for it today: flag a mismatch, never guess).
- **trust (conditional, §4.1):** the regulator's and the store's own pages first (authority registry: the data-protection authority, Apple and Google store policy pages, payment-network and central-bank pages), then provider documentation for tokenization and sign-in; one-year time range; official sources only for laws and fees; every item `legal-adjacent` and on the `confirm` list.
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
**v5 paper test (2026-11-11; signals read by hand from the features and integrations of the six fixtures; the builder's signal code decides):**
| Brief | Class | Signals (by reading) | `trust` | Light |
|---|---|---|---|---|
| Quick yoga, Bengaluru | focused (17) | payments, scheduling | off | no |
| Standard produce, Nashik | standard (27) | payments, two_sided, messaging, maps_location | **on** (payments with two_sided) | no |
| Thorough freight, India | deep (83) | payments, tracking, government_documents, many_roles | **on** (deep) | no |
| Coffee loyalty, Nairobi | focused | payments, maps_location | off | no |
| Dental clinic, Dubai | focused | payments, scheduling, health_data (patient records, insurance claims), non_latin_locale | **on** (health data) | no |
| Unit converter | focused (about 1) | none | off | **yes** |
Findings that changed the design: the load must come from content, not from the number of requests (those were set by the tier); targets need the union of sources; own-brand names occur in two of three runs. **v5 note:** the builder computed the loads as 17, 27 and 83, and a tiny one-feature app would plan 10 topics today, which light mode replaces with 2 (§4.2).

## 7. Budgets, the three caps, time and cache
**Owner direction (2026-11-11): result first; the numbers are starting values the builder tunes; money is a configurable budget. Owner decision (2026-11-11, later): USD 5.00 in total for the proof work, restartable, recorded and never exceeded (doc 14 §10); the earlier USD 4.00 allowance and the USD 8.00 ceiling with 2.37 spent are superseded for this work.** Hard ceilings are never exceeded for searches and pages (RV-12); in v5 the **time limit also covers checks and assembly** (today they ignore it, §20).

### 7.1 Ceilings as built (`data/ceilings.yaml`) and v5 expectations
| | **focused** | **standard** | **deep** |
|---|---|---|---|
| Research load (with the 3-point margin) | below 25 (with the margin: below 22) | 25 to 60 (22 to 56) | above 60 (57 and up) |
| Real inputs | Quick yoga 17 | Standard produce 27 | Thorough freight 83 |
| Searches, soft / hard | 20 / 32 | 40 / 64 | 80 / 120 |
| Pages read, soft / hard | 24 / 40 | 60 / 96 | 140 / 210 |
| Time, soft / hard | 180 s / 330 s | 360 s / 600 s | 600 s / 960 s |
| Topics, soft (hard is not used) | 10 (14) | 16 (22) | 26 (34) |
| Rounds, soft / hard | 2 / 3 | 3 / 3 | 3 / 4 |
| Follow-ups per topic, soft / hard | 2 / 3 | 2 / 3 | 2 / 3 |
| Parallel lanes: search and fetch / extraction and queries | 6 / 8 | 8 / 12 | 10 / 16 |
| Round-1 search pool | 70% of the hard ceiling, shared out by topic rank (3, 2 or 1 variants) | same | same |
| Cost per run, builder's dry-run estimate (USD) | 0.28 | 0.57 | 1.15 |
| Added by check-all (my estimate [J]) | +0.05 to 0.10 | +0.10 to 0.20 | +0.15 to 0.30 |
| Time added by check-all, inside topics, parallel [J] | +5 to 10 s | about +10 s | +10 to 20 s |
**Replaced in v5:** `research_tokens` and `slice_tokens` (drop thresholds) by `store_tokens` and `chunk_items` (§7.2). **Dead settings** to use or remove: `hard.targets`, `hard.lanes_*`, `triage_model_above` (the built code never reads them).

### 7.2 The three caps (AD-55 rewritten; Proposed until PG-5 and PG-6)
The owner's concern: "put a cap on the size, because it will go on the loop, or it will be over context, but nothing must be missed." These are two risks in two places, so there are three caps:
| Cap | Where | Rule | Status |
|---|---|---|---|
| **1. Loop cap** | Research, while searching | A topic stops when its required slots are filled; a round stops when it fills under 10% of what is left; each topic has at most 2 or 3 follow-ups and then `not_found`; the ceilings of §7.1 are the safety net | **Built** (`min_round_gain: 0.10`, follow-ups, ceilings) |
| **2. Store cap** | Research, at assembly | The **main store** (items, norms, gaps, confirm, suggestions and defaults, in the compact view of §9.3) is limited to `store_tokens` (default **40,000** estimated tokens, the same for every class [J]). Findings above the limit get `store: appendix` and a reason. They are **kept**, listed in the report, and left out of lane slices. **What stays, in order:** linked to a Brief id (not `NEW`) and high impact; flagged `conflict` or `needs-confirm`; evidence (`official`, `corroborated`, `single_source`, `unverified`); **prevalence is only a tie-break**; the best two of every kind always stay | **Proposed** (RC-1). Today: silent deletion, lowest prevalence first, which removes niche rules first (§20) |
| **3. Pack cap** | Spec, per call | If the main store is at or under the **routing threshold** (default 40k, equal to `store_tokens`; to be set from the model's real window and the PG-5 and PG-6 results) the **whole main store** goes to every Spec call as a stable, cached prefix, with no selection that could miss a record. Otherwise Spec builds a **pack** per call from a Need Card (a data file in Spec: Brief ids, record kinds, hops, a token budget): the records that apply to the call's Brief ids, **plus one hop** along `applies_to`, plus the lane essentials, plus a title-only index of the rest. A pack that is too large is **split into more calls, never truncated** | **Proposed**; the Spec design decides the details, Research guarantees the fields (§9.3) |
Why cap the store at all if Spec can read 40k tokens whole: so that "whole store" stays possible for every app. The appendix holds the long tail (limited evidence, not tied to the Brief, normal impact), which Spec can still reach through a pack and which the report shows to people. **Expected sizes [J, measured in PG-3]:** main store about 10k to 15k tokens for focused, 25k to 40k for standard, at the 40k limit for deep with an appendix. **Checked in the built code:** it has no pack function and no routing; slices are id lists plus digests, and the digests (up to 16k to 44k characters per lane) sit outside the token estimate (§20).

### 7.3 Time, parallelism and the batch decision (AD-58)
- **Foreground = parallel pipeline, never batch.** A person is waiting, so every call runs now: topics in parallel (bounded by the lanes and by the gateway's per-route limits), pages extracted in parallel per page, checks in parallel per topic. A **batch API** (cheaper, but results come back in minutes to hours) is used only where nobody waits: the bake-off judge, the consumer probe and the planted-error test (doc 14), and only if the gateway offers one.
- **Time model [J, measured in the first live runs]:** plan 15 to 25 s; round 1 about 50 to 90 s (focused) up to about 4 min (deep); audit and follow-up planning 15 s; round 2 about 45 s to 2 min; assembly 20 s. Per-call timeouts: search 8 s, page fetch 12 s, model 45 to 120 s, bounded retries; **a timeout or a 0-token call is a counted failure** (the Compass undercount lesson).
- **Where the time goes in v5:** the checks move inside the topics and run in parallel. Today they are one serial pass of batches of 15 after all rounds, which for a deep run is ten or more calls in a row and ignores the run clock. Stage timings are recorded (RC-7) and read before any further machinery is added. **Progressive release** (publish a job's slice as soon as it is done) stays Phase 2 with the trigger "deep p50 above 8 minutes".
- **Rate limits.** The built semaphores are per run; the gateway limits are per route (concurrency 16 / 8 / 4, requests per minute 600 / 240 / 120) and per process. Simultaneous runs share the gateway limits but not the search limits; a **global limiter and a queue** are P2 (one active run per program is enforced).
- **Early stop:** a covered topic stops; a round under 10% ends the run; passage windows keep prompts short; a small fast model does extraction; static prompt prefixes are cached where the gateway supports it (AD-26).

### 7.4 Cache (paused; AD-58)
Kept exactly as built: an **in-process search-result cache** (key: normalized query tokens, country, language, domains, exact flag, recency, content flag and result count; 14 days, 2 days for fee and price queries; 2,000 entries; successes only; shared by all runs in the process; not persisted) and a per-run page cache. The gateway has its own small model-call cache. **Not added:** a persistent page cache, a cross-run model cache, per-tenant isolation. Reason: nothing yet shows that repeat traffic matters, and the cache holds raw provider text in memory. Revisit when real traffic shows repeated regional topics. Every item already carries `retrieved_on`, so freshness is visible.

## 8. Sources, services and providers
**Models by role.** Writer **Luna** (`gpt-6-luna`): plan, queries, extraction, gap analysis and use notes. **Checker** (**Built** as RSCH-4 on the gateway's `standard` route, which Research reserves for it, so a different model is a one-line route change): **Luna on its own route today, so `checker_vendor: same` is recorded.** v5 checks **every item** (today only high-impact items, and only claim and quote) and shows the checker the claim, typed data and passage window (§5.2 step 7). Independence from the writer is weaker while both are the same vendor, so **code checks stay primary** (quote on the page, numbers in the quote, independence by content, official first), and the **planted-error test** (doc 14) measures whether the checker really catches errors; only if it does not, a second vendor becomes a requirement (OI-23).
**Search provider (AD-52, AD-58).** The built adapters are Tavily and Serper behind `SearchPort`. `choose_provider` takes `RESEARCH_SEARCH_PROVIDER` or the first provider that has a key (default Tavily, **unmeasured: the bake-off has not run**). There is **no failover and no second opinion** today (§20). v5 (Proposed, RC-10): after the bake-off, one **primary**; the other is the **failover** on provider errors and a **second opinion** for topics that return fewer than 3 usable results (at most 15% of searches). Brave stays a documented candidate and is not built.
| Candidate | Why | Notes |
|---|---|---|
| **Tavily** (default until measured) | Search plus page text in one call; `country`, `language` + `filter_by_language`, `include_domains` with prefer or restrict, `exact_match`, `time_range` [V]; 1,000 free credits per month [S, official credits page excerpt] | `country` is a lowercase name and only for `general` [V]; price per credit about USD 0.008 [S] |
| **Brave** (deferred; not in the first bake-off) | `country`, `search_lang`, `freshness`, `extra_snippets`, `goggles` [V]; USD 5 monthly credit, USD 5 per 1,000 requests [S] | Snippets only, so it needs `FetchPort`; supported `search_lang` values unverified |
| **Serper** (Google results) | `gl` country and `hl` language parameters; 2,500 free queries [S]; Google's index may serve Indian local queries better | **Serper has no public API reference page:** the builder could read only its home page and third-party reports (`docs/research-agent/provider-facts.md`), so every parameter claim is [S] until the bake-off. Links and snippets only, so it needs `FetchPort` |
**Page reading: our own `FetchPort` (Built).** trafilatura main text (server-rendered HTML only), the PDF text layer (60 pages, no OCR), SSRF rules, robots, redirect and size limits (400k characters per page). Tavily's own page text of 300 characters or more is used without a wall check (a cookie or login wall could slip through; P2); every Serper page goes through our fetcher. JavaScript-only pages and scanned PDFs become `no_content`, and no rendering or Tavily-extract fallback exists. **Fallbacks (P2, by the decision table):** Tavily extract or a rendered fetch (Crawl4AI) if fetch success on selected pages is below 70%. A high-impact claim is never extracted from a snippet alone (RS-19).
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

## 9. The output: `research.json` (v5), a playbook for Spec
### 9.1 Principles
Spec reads it directly to produce the Blueprint, structure, Design and Screens. It is a **playbook per Spec lane**, **indexed by the Brief's own ids**, with each choice's **commonness and recency**, typed fields, enumerations, no markdown inside fields, and a short `label` per item for rendering. An **item** is a merged finding: atoms about the same thing (same job, kind and normalized key) become one item with several sources (**Built**, `assemble/items.py`); different rule values stay separate items (a conflict is information). Every item: `id` (`RI-###`), `job`, `spec_need`, `kind`, `label` (at most 8 words), `claim` (at most 40 words), `use_note` (at most 15 words; the kind's default when absent), `slot`, `impact`, `confidence`, **`evidence`** (`official`, `corroborated`, `single_source`, `unverified`; §9.4), `tier` (`default` or `open`, never `locked`), `flags`, `applies_to[]`, `sources[]` (page id, domain, `source_class`, URL, retrieved and published dates), `independent_domains`, `prevalence`, `recency`, `request_ids`, `target_id`, `store` (`main` or `appendix`), `tokens_est`, `rank`, `source_language`, `about_region`, and a `data` object by `kind`.
**Quotes stay in `research_evidence.json`** (as built, and kept): the evidence file maps every atom to its item (`item_id`), and the rendered report and the review views show the best quote next to each claim. Putting quotes in every item would add about a third to the store for no gain to Spec. Verify's drift check and the owner's spot-check read the quotes from the evidence. (v4 of this doc said each item carries its quote and a `passage_ref`; the build followed prompt 05, where quotes are internal, and v5 keeps that.)
`applies_to` entries: `{kind: "FTR|ROL|APP|MOD|ENT|FLW|INT|NEW", id: "<Brief id or integration key or null>", relation: "expects|constrains|refines|alternative|suggests"}`. `NEW` means "not in the Brief"; Spec decides.

### 9.2 Shape
```json
{
  "schema": "research.v5",
  "program_id": "<id>", "brief_hash": "<hash>", "status": "complete|partial|failed|skipped",
  "profile": {"load_score": 0, "load_class": "focused|standard|deep", "rounds": 0, "light": false,
              "signals": ["payments"], "store_tokens": 0, "appendix_items": 0, "digest_tokens": 0},
  "input_quality": {"requests": {"trusted": 0, "template": 0, "thin": 0}, "plan_source": "from_request|rebuilt|mixed",
                    "degraded": false, "failing_probes": [], "structure_source": "model|mixed|fallback", "geo_uncertain": false},
  "budget": {"searches_used": 0, "pages_read": 0, "seconds": 0, "usd": "0.0000", "failures": 0},
  "coverage": {"targets": 0, "covered": 0, "not_found": 0, "slots_required": 0, "slots_filled": 0},
  "lane_quality": {"blueprint": "strong|partial|thin|none", "integrations": "...", "design": "...", "screens": "...", "mock": "...", "verify": "..."},
  "bn_coverage": {"BN-02": "researched|single_source_only|defaulted_only|gap"},
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
  "slices": {"blueprint": {"item_ids": [], "chunks": [], "digest": "<index text>"}, "integrations": {}, "design": {}, "screens": {}, "mock": {}, "verify": {}},
  "claims_used": [],
  "validation": {"errors": 0, "warnings": 0, "by_rule": {}},
  "dropped": {"quote_not_found": 0, "number_not_in_quote": 0, "not_supported": 0, "unsupported_field": 0, "forbidden_pattern": 0, "claim_too_long": 0, "invalid": 0},
  "flagged": {"partial_support": 0, "unchecked": 0, "other_region": 0, "other_entity": 0, "duplicate_source": 0, "number_widened": 0}
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
- **Attributes are optional and sourced (AD-56).** In `data`, only what the passage states is filled: a field's `type`, `required` or `values` are `null` unless the text says them, and a step or state the page does not list is not added. Spec decides the rest. Ordered lists (`steps`, `transitions`) are **never concatenated across sources**: the richest source's list is kept and the others are noted as variants (the built merge unions every list, so steps from two pages are joined, §20).
- **`norms[]` are computed by code** from the items (**Built**; keys are reconciled by pure code: normalized stems, nested sets or Jaccard 0.6; no model; the model only proposes each item's `norm_key`). **How to read them (v5):** `seen_in` of `of` means **mentioned in n of N** comparable pages. It is not market share and not "has the feature": for features N is the number of distinct comparable apps with a feature list; for screens, flows, entities and design it is the number of distinct organizations that contributed any item of that kind. A page that does not list a feature does not prove the app lacks it, so `core` means "likely expected", never "universal". Levels: `core` (share 0.6 or more), `common` (0.3 or more), `advanced`, `emerging` (recent, rising, seen in at most 2) and `limited_evidence` (fewer than 3 sources). **Integration norms (v5 fix, RC-6):** one norm per (need, provider), merged across items, counting only **independent third-party mentions** (comparison pages) in `seen_in`; the provider's own documentation is `evidence: official` for facts about itself and does not count toward prevalence (today there is one norm per item, so a provider with several items appears several times, and its own site counts). Items flagged `other_region` or `other_entity` are excluded from norms. Independence in norms uses the same content-overlap rule as `independent_domains` (**built for items, not yet for norms**). `matches_brief` is a fuzzy match to the Brief's ids. Unmatched norms go to `suggestions` (features, screens, fields, states, validations): always `undecided`, never scope (AD-42).
- **`confirm[]`** lists numbers and legal-adjacent facts the owner should confirm at G2 or G3. **`claims_used`** is filled later by Spec for Verify's drift check.
- **Slices (one per Spec lane)** (**Built** as `{item_ids, digest}`): `blueprint` (features, structure, flows, data, rules), `integrations` (services), `design` (design conventions, script needs, formats), `screens` (screen patterns, flow exceptions, validations), `mock` (data hints, formats; the built `local` job feeds this lane), `verify` (confirm list, defaults, compliance). **v5: a slice lists every main-store item of its lane** (today the id list is cut at 66, 116 or 183 ids, §20), ordered by code (norm level, impact, Brief link, job priority); a lane too large for one call is split into `chunks[]` by module cluster or topic (`chunk_items`, default 120), **never cut**. The **digest** is a code-rendered **index and quick-read table** (top norms with prevalence, the screen inventory, entity field tables, the provider comparison). It never replaces the items, it is **not counted in `store_tokens`**, and **when the whole store is passed to Spec the digests are left out** (they repeat the items).
- **Depth ladder (what Spec gets by load class):** **focused** gives a pattern sheet (core and common features with prevalence, 8 to 12 typical screens and one navigation pattern, the main entity's fields and states, 3 to 5 service facts, 6 design conventions, formats and sample-data hints); **standard** adds a competitor matrix (4 to 6), flows with exceptions, entity patterns and lifecycles for the main records, service options with contracts, screen patterns for the key screens, a rules list; **deep** adds playbooks per module cluster (Compass modules grouped to at most 8 clusters), integration notes per external system, role workflow exceptions, a compliance register, realistic data by entity, and design trends. Anything not covered is in `gaps`; Spec then uses module defaults flagged `assumed`. In v5 a **light** run gives only the feature norms and the screen inventory, and the **trust** job adds the non-functional facts of §4.1 when it runs.
- **`research_evidence.json` (internal, Built):** every page read with its **full text** (so quotes can be re-verified), the queries with their results, the atoms (claim, quote, status, verdict, `item_id`), the targets and the failures; the service adds the validation results and all events. **Gaps (v5):** no offsets are stored for quotes (re-verify by normalizing the page text); no API serves the evidence or the validation results today (RC-5 serves evidence-derived views); no retention or purge exists (P2: keep passage windows and a content hash instead of full text in production). Never read by Spec.

### 9.3 What Spec needs from the file to choose between the whole store and packs (Proposed, RC-1, RC-6)
- **Compact view.** A deterministic projection of the file (`GET .../latest?view=compact`, not a second stored file): the items, norms, gaps, confirm, suggestions and defaults, with **sources in one table** (`sources{}` keyed by id; items hold ids) and `use_note` only when it differs from the kind's default. This is what `store_tokens` measures and what Spec reads. The built item repeats its source objects in every item (§20).
- **Per-record fields:** `tokens_est`, `rank` (the rule of §7.2), `store`, `applies_to` (Brief ids, `NEW` allowed), `evidence`, and the lane.
- **Fields on the file:** `profile.store_tokens`, `profile.appendix_items`, `profile.digest_tokens`, so Spec can route without reading the file.
- **A deterministic `pack(need_card)` function** (pure code; no embeddings, no model) belongs to the Spec build. Research guarantees that every main-store item has a Brief link or `NEW`, so a selection by `applies_to` cannot miss a record that applies. **Selection is by structure, not by similarity,** and Spec never asks a model to fetch more (no agent loop).

### 9.4 Evidence ladder, lane quality and builder-need coverage (Proposed, RC-6)
All computed by code from the checked items. Spec and Verify read them instead of re-deriving trust.
| Field | Value | Rule |
|---|---|---|
| `evidence` (per item) | `official` | at least one source of class `official` or `help` that is the origin of the fact (a provider's own documentation for its own API, a regulator for a rule), and the check said `supported` |
| | `corroborated` | at least two independent organizations (content-independent, RV-19), none of class forum, blog or seo, and `supported` |
| | `single_source` | one source, `supported` |
| | `unverified` | the check said `partial` or did not run (`unchecked`), or the item is flagged `other_region`, `other_entity` or `number_widened` and has no second source |
| `lane_quality` (per lane) | `strong` | 80% or more of the lane's required slots filled and 60% or more of its items `official` or `corroborated` [J] |
| | `partial` | 50% or more of the required slots filled |
| | `thin` | some items, under 50% of the slots |
| | `none` | no item |
| `bn_coverage` (BN-02, 03, 04, 05, 07, 08, 09, 10) | `researched` | at least one `official` or `corroborated` item maps to the need (`data/lanes.yaml`) |
| | `single_source_only` | only `single_source` items |
| | `defaulted_only` | no item; Spec proposes the module default (doc 12 §6) |
| | `gap` | the need had topics and all were `not_found` or not run |

### 9.5 Use rules for Spec and Verify (R21 to R27, §11)
Spec may turn a finding into a **Default** only when its `evidence` is `official` or `corroborated`; a `single_source` finding becomes an **Open** suggestion or goes on the `confirm` list; `unverified` findings never enter a Spec decision. Numbers, fees, limits and legal facts are always on `confirm`. If a lane's quality is `thin` or `none`, Spec writes "assumed" from its own module library and never invents a source, number or provider. Spec records the finding ids behind every research-derived value (`claims_used`), and Verify's drift check compares them with the quotes. This is what keeps a thin or wrong finding from becoming a confident statement downstream.

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
**HTTP** (`create_app()`): `POST /research/runs` (program id, Brief hash), `GET /research/runs/{id}` (status), `GET /research/runs/{id}/events` (SSE, resumable), `POST /research/runs/{id}/skip`, `POST /research/runs/{id}/targets/{target_id}/cancel`, `GET /research/programs/{id}/latest` (`research.json`), `GET /research/programs/{id}/review` (items with links and dates), `POST /research/runs/{id}/items/{item_id}/reject`. **Shared contracts:** `agents/research/contracts/research.v5.schema.json` (built as `research.v4.schema.json` until RC-6 lands) and `research.event.v1.schema.json`; the evidence schema stays in `agents/research/`. **Store:** PostgreSQL only.
**v5 additions (Proposed, RC-5, RC-6).** `GET /research/runs/{id}/report` (Markdown rendered by code from `research.json` and the evidence); `GET /research/runs/{id}/spot-check?n=60&seed=<int>` (a stratified sample as a table with a blank verdict column, doc 14 §4); `GET /research/programs/{id}/latest?view=compact` (§9.3); `GET /research/programs/{id}/decisions` (the overlay of rejections and toggles; **the built decision D-130 mentions `latest?apply_decisions=1`, which does not exist**, §20). Events and snapshot: `plan.ready` carries `slots_required` per target and in total (the coverage bar is exact); `fact.found`, `fact.verified` and `fact.rejected` carry a stable `fact_id`; the snapshot keeps a `facts` list instead of only the last 60 events, so a reload shows every "checked" line; `run.finished` carries stage timings and cost by stage.
**Findings page ("What we found"):** a playbook view per Spec lane, a norms view (prevalence and recency), and tabs per job; each item shows the label, claim, source domain, date, link, confidence and flags; suggestions are toggles, **off by default**; you can reject any item. Spec starts when the run commits; toggles and rejections are collected at **G2** as change sets (R7). G2 records the `research.json` hash (I-2). A later Brief version re-runs only affected jobs, keyed by Compass `_section_hashes` (scope and apps: landscape and references; integrations and data: services and rules; context and experience: local).
**v5:** each item also shows its best quote, its source link and its `evidence` badge (served from the evidence file, which no API serves today); the page offers "Download report (.md)" and "Download spot-check sheet". Today the page shows digests in a text block and offers only "Download research.json" and a raw-JSON toggle (§20).

### 10.1 The standalone test app and front end (AD-60; Proposed)
**Owner, 2026-11-11:** Research has its **own front end and its own server**, not integrated with Compass or any upstream engine (the built hand-over inside the Compass console stays frozen and is not extended); it **picks its inputs up automatically** from the docs folder; there is **no mock model logic in the app**; **the only money limit is the USD 5.00 total** (restartable and recorded, "there is no limit of it" otherwise); the owner tests small, medium and big inputs from the front end, and the app is left running.
- **One process, one port, same origin** (`python -m mde_research_agent`, default `127.0.0.1:8120`): the API and a static front end (`agents/research/ui/`, no build step). Test doubles live only in the test tree; the app has no `--memory`, no mock banner and no replay mode. If provider keys are missing the app still starts but refuses runs and says exactly what to set.
- **Inputs:** every Compass format-2 export in the inputs folder (default `docs/compass/research-inputs/`, read-only; override `RESEARCH_INPUTS_DIR`) is listed with its **computed** size (small, medium, big, or tiny), a **zero-cost preview** (research load and class, signals, trust job on or off and why, planned topics, estimated searches, pages, minutes and cost, trust warnings) and a Run button. New files appear without a restart; invalid files are listed with the reason; uploads are optional and kept elsewhere.
- **Runs:** one at a time by default (a queue shows the position); a run copies its input at the start; runs persist across restarts (a file-backed store by default, PostgreSQL optional; both pass the same store contract tests); a run cut off by a restart shows as interrupted with a Re-run button.
- **Screens:** inputs; run (live plan, feed, counters, cost so far, Skip, topic cancel, warnings); results (playbook by lane and by job, norms, confirm list, gaps and conflicts, appendix, quality, sources, rendered report, raw JSON, filters, an item view with quote and source); history; **spot-check** (60 items, six verdict buttons, saved automatically, a summary against doc 14); budget (spend record, restart with a confirmation); preflight; a short test guide; tools (provider bake-off).
- **Safety and speed:** every string that came from a web page is escaped, links are http or https only, a strict content-security policy applies; lists are paged on the server and virtualized in the browser; live events are applied in batches.
- **Money:** no per-run cap and no call-count cap; the total is USD 5.00 with a tripwire at 4.50 (the run in progress finishes, nothing new starts) and a hard stop at 5.00 (the run commits `partial`); every live action appends to a spend record.

## 11. Rules
- **R1. Enriches, never decides.** No scope status, approval, or change to a value you or a document set (AD-42).
- **R2. Tier is Default or Open.** Never Locked (I-6).
- **R3. No claim without a quote,** found by code in stored page text (RV-02).
- **R4. Synthesis selects and annotates; it writes no new facts** (RV-05).
- **R5. Pages are data.** Extraction and synthesis have no tools; output schemas have no URL, action or tool fields (I-11).
- **R6. Locale.** Each query carries the target's country and language (§6.4).
- **R7. Late findings never change the spec on their own.** They appear as G2 toggles or become change requests in the lane they belong to (doc 05 §3), never lane L1.
- **R8. High-impact claims** (prices, fees, rules that change behavior, legal or payment facts, formats that change validation) need an **official source** or two **independent organizations** (independence by content, RV-19), or are flagged `single-source` and shown `unverified`.
- **R9. Official sources rank first in a conflict;** forums, blogs and SEO pages cannot support high-impact claims.
- **R10. Stack facts:** the Stack Kit wins.
- **R11. Legal, tax and payment facts are information, not advice** (`legal-adjacent`). Regional compliance packs stay deferred (AD-24).
- **R12. Privacy and security are baseline for Research's own handling** (owner, 2026-11-11; AD-54). Never log or print API keys; public web only; pages are data (no tools). No name or personal-data filtering, no per-program cache isolation, no per-program switch beyond Skip. **What the app must do about privacy and security is researched by the light `trust` job** (§4.1, AD-57) for deep apps and whenever a signal fires.
- **R13. Web hygiene (basic).** Public URLs only (skip private and local addresses); respect robots.txt; no logins or paywall bypass; a User-Agent that names the product.
- **R14. No bulk copying.** Page text is internal evidence; each item holds one finding as a paraphrase of at most 40 words, with the link and dates; the verbatim quote (at most 25 words) is kept in the evidence file. Detail comes from **many specific items and structured `data`**, not from long copied text (AD-55).
- **R15. Brand facts** come from your own domain or a document you gave, read as a seed source; they are not inferred from a competitor's site. Your own brand name alone is not searched.
- **R16. Named references** are patterns and public feature lists; no logos, images or copy.
- **R17. Missing research never makes a builder need `missing`:** it falls back to a module default marked `defaulted`.
- **R18. Trust follows the tier, not approval.** Default-tier seeds give `assumption_derived` queries; results never raise a tier.
- **R19. Stop by evidence.** A target stops when its required slots are filled; follow-ups are distinct and capped; `not_found` is a valid answer and is never retried indefinitely.
- **R20. Declined topics are not researched** (Compass `declined`), and nothing is searched about games or other declined categories.
- **R21. Use rules.** Spec may turn a finding into a Default only when its `evidence` is `official` or `corroborated`; `single_source` becomes an Open suggestion or goes on `confirm`; `unverified` never enters a Spec decision (§9.5).
- **R22. Numbers and legal facts are always on `confirm`** and shown at G2, whatever their evidence.
- **R23. Cite and check drift.** Spec records the finding ids behind every research-derived value (`claims_used`); Verify's drift check compares them with the quotes in the evidence.
- **R24. Thin lanes mean "assumed".** If a lane's `lane_quality` is `thin` or `none`, Spec writes "assumed" from its own module library and never invents a source, number, provider or quote.
- **R25. Prevalence is a rough signal.** It reads "mentioned in n of N comparable pages", not market share; `core` means likely expected; fewer than 3 sources is `limited_evidence`.
- **R26. No silent loss.** A finding is never deleted for size: it moves to the appendix with a reason and stays in the report; every drop has a counted reason.
- **R27. Research contributes no attribute the page does not state.** Types, required flags, enumerations, steps and dates come from the passage (or the page metadata) or are null; the model's own knowledge is Spec's job, not Research's.

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
| RS-10 | Findings after a Brief change | Delta re-run by section hash; R7. **Not built in the first release** (builder, 2026-11-11): a new Brief version re-runs everything, or the old research is reused and marked "older Brief"; build the delta when Gate-2 edits first force it |
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
| RS-24 | Duplicate queries or entities | Normalize, dedupe, reuse the in-process search cache (§7.4) |
| RS-25 | A source needed for a required slot does not exist on the web (a new niche idea) | After 2 distinct follow-ups: `not_found`; Spec uses a module default flagged `assumed` |
| RS-26 | Two sources give different values (fees, thresholds) | Keep both with dates and authority; flag `conflict`; put in `confirm[]` |
| RS-27 | Pages that are SEO listicles or AI-generated | Source class `blog` or `seo`; cannot support high-impact claims |
| RS-28 | A paywalled or login-only page | `page.skipped: paywall`; never bypassed |
| RS-29 | The user cancels a topic mid-run | `target.cancelled`; no more spend on it; recorded as a gap with reason `user_cancelled` |
| RS-30 | A round fills almost nothing (the search provider returns junk for a locale) | Evidence-aware stop (R19); warn in events; the bake-off metrics should have caught it |
| RS-31 | The Brief mixes several regions (six cities) | Query the country and distinct regional specifics once; do not run one search per city |
| RS-32 | Non-English source with English paraphrase | Quote kept in the source language; paraphrase in English; `source_language` recorded (**built** on evidence pages only; v5 puts it on items, RC-6) |
| RS-33 | The research view is missing or stale (hash mismatch) | RV-13 refuses the run and emits `run.failed` with the reason |
| RS-34 | A model call or search times out repeatedly | Count as failures in `budget.failures`; event `warning`; finish with `partial` |
| RS-35 | A number, amount or date in the claim is not in its quote | RV-18: widen the quote to its sentence if the passage has it; else the item is rejected `number_not_in_quote` |
| RS-36 | The page is about another country or another company (a US fee page for an Indian app; a namesake) | `other_region` or `other_entity`: no corroboration, no prevalence, never Default; kept (appendix-eligible) and shown in the report |
| RS-37 | The same text on several domains (syndication, copied listicles) | Counted as one source (RV-19) |
| RS-38 | The checker rejects the only item that filled a required slot | The slot is open again before the audit and a follow-up runs; never a silent hole |
| RS-39 | A tiny app with nothing worth searching | Light mode (§4.2): at most about 8 searches, status `complete`, `profile.light`, skipped topics are gaps `not_worthwhile`; Spec uses defaults |
| RS-40 | The primary search provider errors or is rate-limited | Wait as the provider says; then fail over to the other provider for that topic; event `warning`; if both fail the topic is `provider_down` and the run is `partial` |
| RS-41 | JavaScript-only page, scanned PDF, wall text | `page.skipped` with a reason; use the provider's page text when it passes the wall check; never extract a high-impact claim from a snippet alone; a rendered fetch is P2 |
| RS-42 | The main store would exceed `store_tokens` | The lowest-ranked findings move to the appendix with a reason (§7.2); counted; nothing deleted; if the protected findings alone exceed the limit, a warning and the store grows |
| RS-43 | Two processes, or a restart, share one database | Single process assumed (no lease): startup recovery may mark another process's live run `interrupted` (§20); a lease is P2; until then run one Research process |
| RS-44 | The extractor adds a type, required flag or enumeration the passage does not state | Nulled by the field-support check, or the item is dropped as thin; counted `unsupported_field` |
| RS-45 | The checker call fails or returns no verdict for an item | The item stays `unverified` with the flag `unchecked`; counted; never trusted as verified and never dropped silently |
| RS-46 | A claim over the word limit | Cut only at a sentence end; else rejected `claim_too_long`; never clipped mid-sentence |
| RS-47 | A run stops producing events (a hung call, a killed process) and blocks its program | A stale-run expiry marks it `failed: stalled` with a terminal event after a period with no events (default 10 minutes), so the program can start a new run (§20) |
Stale items are flagged by RV-07: older than 2 years for prices and fees, older than 5 years for general facts [J].

## 13. Validators (RV-01 to RV-23; RV-11 reserved)
Run on the Backbone's validator framework (doc 07 §2).
**As built:** only RV-04 (item tiers are `default` or `open`) and RV-13 refuse a commit; every other validator records its result in `evidence.validation`, which no API serves, and RV-09's size check is recorded, not enforced (§20). **v5:** the results are summarized in `research.json.validation` and shown in the report and the console, and the truth rules (RV-02, RV-03, RV-18, RV-21) act **before** an item enters the output; RV-22 is enforced.
| ID | Check | On failure |
|---|---|---|
| RV-01 | `research.v5` schema (built: `research.v4`), strict; `data` matches `kind` | One repair; else commit valid items as `partial` |
| RV-02 | Each quote is a verbatim substring of the stored page after normalization | Item dropped |
| RV-03 | Source URL and date match the fetch log | Item dropped |
| RV-04 | Every tier is `default` or `open` | Commit refused (bug) |
| RV-05 | `claim`, `use_note` and digests add no number, currency, date, name or URL not in the cited items | Note removed or item dropped |
| RV-06 | Query hygiene (basic): no URL or key-like string; no own-brand term alone; at most 25 words | Dropped before sending |
| RV-07 | Corroboration: an official source or two independent organizations for high-impact items; stale dates; conflicts; forum, blog and SEO limits | Flags set |
| RV-08 | Support check on **every** item: `supported` required for high-impact; `partial` makes the item `unverified`; `not_supported` drops; unsupported fields are nulled; no verdict means `unchecked` | Dropped, nulled or downgraded |
| RV-09 | **Completeness:** every verified atom is in an item (main store or appendix) or merged into one; slices contain every main-store item of their lane (split into `chunks[]`, never cut); the report lists every item; **no item is deleted for size** | Missing items restored; a mismatch is a bug and the commit is marked `partial` |
| RV-10 | Coverage: every target and request appears in items or gaps | Added to gaps with a reason |
| RV-11 | Reserved (personal-data filtering removed, AD-54) | none |
| RV-12 | Budget: searches, pages and seconds within the ceilings | Run stops (`budget_stop`) |
| RV-13 | Binding: `brief_hash` equals the approved Brief hash; trust flags present or recomputed | Run refused |
| RV-14 | Anatomy and worthiness: every query has a target with `decision_unlocked`, an anchor, a place and an intent word; category-only queries rejected | Query dropped |
| RV-15 | Slot consistency: `coverage` counts match the filled slots; a target marked covered has all required slots verified | Recomputed; mismatch is a bug |
| RV-16 | Every `applies_to` id exists in the approved Brief (or is `NEW`) | Item re-tagged `NEW` or dropped |
| RV-17 | Events contain no page text or quotes; labels come from templates | Event dropped |
| RV-18 | Number grounding: every number, amount, percentage and date in `claim` and numeric `data` occurs in the item's quote (normalized) | Quote widened to its sentence, else the item is rejected |
| RV-19 | Independence by content: near-duplicate passages (5-word shingle overlap 0.8 or more) count as one source in `independent_domains`, corroboration and norms | Counted once; flag `duplicate_source` |
| RV-20 | Region and entity fit: `about_region` and `about_entity` agree with the plan's place and the topic's anchor, or the item carries `other_region` or `other_entity` and is excluded from corroboration, norms and Default | Flag set; item kept as `unverified` |
| RV-21 | Check coverage: every verified item has a verdict or the `unchecked` flag; no unsupported field remains in `data`; checks finished before the audit | Item flagged; a mismatch is a bug |
| RV-22 | Store limit and appendix: main-store tokens are at most `store_tokens` unless the protected findings alone exceed it; every appendix item has a reason; the count is conserved (verified atoms equal items plus merged) | Items moved to the appendix; a mismatch is a bug and the commit is marked `partial` |
| RV-23 | `evidence`, `lane_quality` and `bn_coverage` recomputed from the items equal the stored values | Recomputed; a mismatch is a bug |

## 14. Evaluation and gates
**The proof plan is [doc 14](14-research-proof-plan.md):** gates PG-1 to PG-7, a decision table, the owner's spot-check, the planted-error test of the checker, the with-and-without-research comparison and the live protocol. This section keeps the metric definitions, **corrected where the built metrics measure nothing**.
- **Eval set R-EVAL v3:** the three real Compass exports first (Thorough re-recorded after the Compass fixes), then nine more Briefs (India four, one Hinglish; UAE two; Kenya two; UK or US one) and two adversarial ones (a document with embedded instructions; a request naming a private person). Regional-language and native-review checks are **optional, not gates** (owner, 2026-11-11).
- **Replay** (cassettes) on every change; **live** only after your explicit yes. Scenario IDs `SB-RS-01` to `SB-RS-47`.
| Metric | Definition | Target |
|---|---|---|
| Extraction quote pass rate | quotes found on the first try, over all extractions (also report it after the retry) | 90% or more live [J]; below it, the decision table switches to citation by sentence id |
| Committed items re-verify | every committed item's quote is found again on the stored page | 100%; a regression guard, true by construction, **not** a quality measure (the built "quote check" metric is exactly this) |
| Number grounding | numbers in claims found in their quotes | 100% of committed items; rejections under 3% of extractions |
| Checker verdicts | share `partial` and `not_supported` | `not_supported` under 10%; report both |
| Checker on planted errors | catch rate on seeded corruptions; false-reject rate on clean items | 90% or more; 5% or less |
| High-impact items | `official` or `corroborated`, or flagged `single-source` | 100% flagged correctly (the built metric re-applies the producing rule, so it is a regression guard only) |
| Owner spot-check, 60 items | supported by the quote; true, current and relevant; fabricated | 95% or more; 90% or more; none |
| Required slots filled | filled over required, on the real inputs | 70% or more [J]; design and screens reported separately |
| Searches that produced a used item | contributing over all | 60% or more [J] |
| Norms backed by 3 or more sources | share of norms | 70% or more [J] |
| Dated feature, design and service items under 24 months | share of dated | 70% or more [J] |
| Fetch success on selected pages | fetched with text over selected | 70% or more [J] |
| Injection tests (RS-06) | ignored | 100% |
| Query hygiene and anatomy | | 100%; zero category-only queries |
| Targets covered or `not_found` | none silently dropped | 100% |
| Time and cost | against §7.1 | soft time met in 90% of runs; cost within 1.5 times the estimate |
| Events complete; resume loses nothing | | 100% |
| Determinism | same cassettes, same hash | 100% |
| Lift over no research (consumer probe) | assumed items and invented facts, with and without research | fewer assumed on researched needs; invented not higher (PG-5) |
| Items used or consciously rejected downstream | | 70% or more [J], once Spec exists |

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
- **v5 risks.** (1) **Nothing is measured on the real web**: every v5 number is [J] until PG-2 and PG-3. (2) **The checker and the writer are the same vendor**: correlated blind spots; the planted-error test decides (OI-23). (3) **Prevalence is a rough signal** from a handful of pages; read it as "mentioned in n of N" (R25). (4) **The design and screens jobs will have low yield on the web**; they stay as a measured bonus (decision table). (5) **Three real runs is a tiny sample**: thresholds catch gross failures, not fine differences. (6) **Check-all adds calls**: about USD 0.1 to 0.3 and some seconds per run [J], measured in PG-3. (7) **The 40k store limit is a guess**: set from real sizes and PG-6. (8) **A single process is assumed**: no lease, global limiter or queue (RS-43). (9) **The shared in-process cache holds raw provider text in memory** (2,000 entries). (10) **A page's own date is often missing**, so recency is partly unknown; unknown is shown, never guessed.

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
- **Order (owner, 2026-11-11): Research is built now, before Backbone, with an internal validator registry that Backbone can adopt later.** **Build prompts:** [`prompts/05-research-agent-implementation-prompt.md`](../prompts/05-research-agent-implementation-prompt.md) (built offline; prompt 03 is superseded) and [`prompts/06-research-agent-v5-changes.md`](../prompts/06-research-agent-v5-changes.md) (complete version: the budget restart, the standalone live-only app with its own front end, and the v5 changes RC-1 to RC-10; ready, not started). **Compass change prompt:** [`prompts/04-compass-research-contract-changes.md`](../prompts/04-compass-research-contract-changes.md) (not routed yet, OI-19).
- **In the build:** the run state machine, load score, planner, worker pipeline, coverage audit, `SearchPort` adapters (Tavily and Serper; Brave deferred), `FetchPort` (trafilatura, PDF text layer, SSRF), validators RV-01 to RV-17, the event stream and API, the authority registry data file, tests on the three real inputs. **Not in the build:** Compass or Spec changes, the front-end UI (the contract is built, the test UI renders it), live calls, `.env`, provider accounts, merging the Compass branch.
- **Acceptance (prompt 05, met offline; the v5 acceptance is in prompt 06 and doc 14):** §14 gate offline; each RV with pass and fail tests; scenarios RS-01 to RS-34; property tests (no item without a quote; no tier other than `default` or `open`; no query without a target, anchor, place and intent word reaches an adapter; ceilings never exceeded; any provider failure gives `partial` or `failed`, never an exception to the orchestrator; a run for a different Brief hash is refused; a covered target issues no further queries; a follow-up never repeats an earlier query; every event is stored and replayable); determinism.
- **v4 to v5:** sync to the build (§20); three caps and the appendix instead of silent deletion (AD-55); the truth layer: check every item inside its topic, numbers in the quote, no unstated attributes, dates from the page, independence by content, region fit, an evidence ladder and use rules (AD-56); capability signals, the light `trust` job, light mode, a class margin and a reshaped design job (AD-57); parallel and not batch, provider failover, cache paused (AD-58); the proof plan (AD-59, doc 14); `research.v5` with `evidence`, `lane_quality`, `bn_coverage`, `store`, the appendix and a rendered report; RV-18 to RV-23, R21 to R27, RS-35 to RS-47.
- **v3 to v4:** research organized by what Spec must decide (eight jobs incl. structure, flows, design and screens), norms with prevalence and recency, playbook slices per lane, wider worthiness gate, `research.v4`; privacy and security reduced to baseline (RV-11 reserved, no PII or name filtering, shared cache allowed, light web hygiene).
- **v2 to v3:** the tier is no longer an input; the load score and ceilings replace 6, 12, 20 (+8); the single pass becomes an iterative slot-driven loop with parallel isolated workers, variants and rank fusion, coverage audit and evidence-aware stopping; output indexed by Brief ids with suggestions, `confirm`, slices and digests (`research.v3`); the event contract; the source and service policy and `FetchPort` in scope; Serper added to the bake-off; RS-25 to RS-34, RV-15 to RV-17, R19 and R20.

## 19. Open items
| ID | Item | My default |
|---|---|---|
| OI-11 | Provider accounts and the bake-off spend | Keys are in the builder's environment; the bake-off script estimates about USD 0.40 and refuses to start without `LLM_MODE=live`. You run it (doc 14 §10) |
| OI-14 | Primary provider | Set by the bake-off (not run yet); Tavily until then; the other becomes the failover (RC-10) |
| OI-15 | Native reviewers for local items | **Downgraded (owner, 2026-11-11): optional, not a gate.** Arrange later if a region matters |
| OI-16 | Build order | Done: Research was built before Backbone (owner) |
| OI-17 | My readings (header) | Foreground; entity-level search; writer, checker, one search provider |
| OI-18 | Authority registry seed (India first) | I draft it as a data file in the build; you or a reviewer confirm |
| OI-19 | Who gives the Compass builder prompt 04 | You decide |
| OI-20 | Load score weights and class thresholds | Start as in §3.3; retune after the R-EVAL runs |
| OI-21 | Trust job triggers (§4.1): content signals override size | As written; you may switch to a pure size rule |
| OI-22 | `store_tokens` and the Spec routing threshold (§7.2) | 40k for both; set from real sizes and PG-5 and PG-6 |
| OI-23 | A second checker vendor (model id and prices) | Only if the planted-error test shows a catch rate under 90%; you supply id and prices |
| OI-24 | Budget for the proof plan (doc 14 §10) | **Decided (owner, 2026-11-11): USD 5.00 in total, restartable, recorded, never exceeded;** staged allowances A to D plus a reserve (estimate 3.35, allowances 4.65); the synthetic run only if money is left |

## 20. As built, checked in the code (2026-11-11)
The builder's report was compared with the code (read-only; nothing was run). **Rule:** where the code is the contract (existing behavior that v5 keeps), v5 says so; where v5 changes it, the last column names the change. Paths are under `agents/research/src/mde_research_agent/` unless they start with `apps/` or `docs/`.
| # | Area | As built | Where | v5 action |
|---|---|---|---|---|
| 1 | Output size | Items plus norms are cut to `hard.research_tokens` (16k, 28k, 44k estimated tokens) by dropping the lowest-ranked 10% at a time (prevalence level first, then impact, then single-source; the best two of every kind stay); counted in `dropped.size_cap`; digests, gaps and suggestions sit outside the estimate | `assemble/output.py` 274-316, `data/ceilings.yaml` | RC-1: appendix, never drop |
| 2 | Slices | `{item_ids, digest}`; ids cut at 66, 116 or 183; digests capped at 16k, 28k or 44k characters per lane | `assemble/output.py` 329-332, `assemble/digest.py` | RC-1: every main-store item; chunks |
| 3 | Items | Atoms merged into items by job, kind and norm key; the richest atom's claim is kept; `data` lists are unioned, so ordered lists (steps) from two pages are joined; items hold no quote, no `source_language` and no `passage_ref`; source objects repeat in every item | `assemble/items.py` | RC-3 (no joining of ordered data), RC-6 (compact view, `source_language`) |
| 4 | Norm keys | Pure code (normalized stems; nested sets or Jaccard 0.6); no model | `core/norm_keys.py` | measure over-merging and under-merging in PG-3 |
| 5 | Prevalence | `of` is the number of comparable apps (features) or organizations (others); integrations get one norm per item and the provider's own site counts; no syndication check in norms | `assemble/norms.py`, `core/prevalence.py` | RC-6, RC-4 |
| 6 | Verification | Strict payload, forbidden patterns (any URL drops an item), quote found on the full stored page (12 or more characters, 2 or more words, ellipsis fragments), one re-extraction for failed keys; claims clipped at 40 words; **the model's `published_on` wins over the page date**; no number, region, entity or field check | `engine/verify.py`, `core/quotes.py`, `engine/worker.py` 545-654 | RC-3, RC-4 |
| 7 | Independence | One per organization plus 5-word shingle overlap of 0.8 on page text (items only) | `core/sources.py` 107-157 | RC-4: also in norms |
| 8 | Checker | High-impact atoms only (the kinds `rule`, `compliance_note`, `service_option`, `local_format` are forced high); batches of 15, **in sequence, once after all rounds**; sees `id`, claim and quote; `not_supported` rejects with no re-search; `partial` only lowers confidence; a failed call sets `unchecked` and tier `open`; the hard time ceiling ignores checks and assembly | `engine/director.py` 482-523, `calls/check.py` | RC-2 |
| 9 | Use notes | One call per run; input per lane is id, kind, label, claim, level, impact and flags of the first 2x-limit items; the `novel_tokens` guard is weak (a number passes if the digit occurs anywhere in the item text; the first word is not checked; lowercase words are ignored); RV-05 only warns | `assemble/output.py`, `core/novelty.py` | RC-3 (tighten), ordering by code |
| 10 | Planner | Topics from templates over the Brief; four `when:` conditions, one used (`non_latin_locale`); needs from single English words; no detection of health data, minors or several regions; no security or privacy topic (one optional `compliance` slot); soft topics 10, 16, 26; `hard.targets` unused | `plan/*.py`, `data/need_keywords.yaml`, `catalog/models.py` 34 | RC-8 |
| 11 | Tiny app | No light mode: the tiny fixture gives 13 candidates, 10 topics and a round-1 pool of 22 searches (read from the templates, not run) | `plan/build.py`, `engine/director.py` 207-224 | RC-8 |
| 12 | Design job | Requires 6 or more conventions across 4 or more aspects; platform guidelines and accessibility are optional | `data/jobs/design.yaml` | RC-8 |
| 13 | Concurrency | All topics start together; `lanes_search` sizes the search and fetch semaphores and `lanes_extract` the model semaphore, per run; gateway limits per route (16, 8, 4 concurrent; 600, 240, 120 per minute); search timeouts retry twice, a 429 waits up to 20 s, four times | `engine/director.py` 342, `engine/context.py`, `config.py` | measure; global limiter is P2 |
| 14 | Record and replay | Three cassette kinds (model calls by rendered-prompt hash; searches by request hash; pages by URL hash); replay is all-or-nothing and raises on a miss; replaying the web with live models is not a mode | `adapters/recording.py`, `live_runs.py` | RC-7 |
| 15 | Cache | Search results only; in process; shared across runs; 14 days, 2 days for fee words; 2,000 entries; raw text in memory; pages cached per run | `adapters/search/caching.py` | paused (AD-58) |
| 16 | Evidence | Full page text for every page (400k characters per page; Tavily text uncapped); atoms with quotes and no offsets; no API serves it; no retention | `assemble/output.py` 437-527, `store/` | RC-5; P2 |
| 17 | Run lifecycle | No heartbeat, lease or reaper; startup recovery marks every queued, running or committing row `interrupted` for all tenants, with no terminal event and no resume; a killed process that is never restarted blocks its program through the unique index; no queue and no global run limit | `api/app.py` 234, `store/postgres.py` 255-263, `service.py` 126 | RC-10 (stale-run expiry, terminal event); P2 (lease, queue) |
| 18 | Report and console | No Markdown report; the console shows digests in a text block and offers only "Download research.json" and a raw-JSON toggle; quotes and evidence are not shown | `apps/web/console/agents/research/research.js` | RC-5 |
| 19 | Evaluation | Bake-off metrics exist (local share, on-topic judge, distinct domains, page text, latency, cost); the run metrics for quote check and high-impact flags are near-tautological; no accuracy measure, consumer probe, with-and-without comparison or spot-check | `live_bakeoff.py`, `metrics.py` | RC-7, RC-9; doc 14 |
| 20 | Providers | `choose_provider`: the environment variable, else the default in `providers.yaml` (Tavily) if its key exists, else the other; no failover; auth or quota errors mark a topic `provider_down` | `config.py` 72-83 | RC-10 |
| 21 | Validators | Only RV-04 (item tiers) and RV-13 refuse a commit; the others record to `evidence.validation`, which no API serves; the RV-09 size check is recorded, not enforced; "the tier is never an input" is a test, not RV-04 | `validators/registry.py` | RC-5, RC-6 |
| 22 | Docs against code | `latest?apply_decisions=1` (D-130) does not exist; the package page lists tables that do not exist; `/config` hard-codes the model name; unused settings (`triage_model_above`, `hard.targets`, `hard.lanes_*`, `conflict_unresolved`, `run.writer_model`, `run.checker_model`, `claims_used`); `local` feeds the lane `mock` (the report says `verify`) | `api/app.py`, `docs/` | RC-6 (overlay endpoint), clean-up in prompt 06 |
| 23 | Report claims the code does not match | "A quote is never repaired by a model": the code re-extracts failed keys once; "evidence has offsets": none are stored | `engine/worker.py` 545, `assemble/output.py` 485 | note only |
**Accepted builder decisions (D-125 to D-131):** the live-spend ceiling is USD 8.00 (the cap in force stays 6.00 until `LLM_BUDGET_USD` is set; superseded for the proof work by the owner's USD 5.00 total, doc 14 §10); package `mde_research_agent`; checker on the `standard` route; Markdown reports live under `docs/research-agent/`; the Compass CSP names the Research origin through `MDE_RESEARCH_ORIGIN`; where the design docs disagreed, the builder chose a shared cache, soft and hard ceilings, Skip commits `skipped`, rejections as an overlay, digests capped separately and flat extraction atoms (merged into items at assembly); the console registry has `extras`.
**What was built and held up well:** plain-code control and the per-topic pipeline; typed slots and a coverage audit; the quote check with Unicode folding; rank fusion; per-run and total budgets through the gateway; resumable events; deterministic ids and hashes; web cassettes kept apart from model cassettes; the anti-hard-coding scan; the console with a fake-server contract test.
