# 11. Discovery agent: detailed design (v1, 2026-11-09)

**Agent name: Compass** (package `mde_compass`; replaces the older "Discover"). Build prompt: [`prompts/01-compass-build-prompt.md`](../prompts/01-compass-build-prompt.md).

Status: design approved by the owner on 2026-11-09. It refines Stage 1 in [doc 03](03-context-engine.md) and replaces the older Discover design (final spec rev 3, section 3) wherever they differ. The built Discover code is a starting point, not a constraint: the owner allowed a complete change. Decisions: AD-27 to AD-29 in [doc 00](00-index-and-decisions.md).

> **As built (2026-11-10).** Compass has since changed in the product: a tier chosen first (Quick, Standard, Thorough) that also sets app size and research depth, batch forms, an intent-level **structure** (modules, screens, flows, entities) approved at Gate 1, a `research` consumer view with `guidance`, and a full-output export (format 2). The current facts, three real runs and my review are in [notes/compass-as-built-2026-11-10.md](../notes/compass-as-built-2026-11-10.md). Where this doc and that note differ on what the product does, the note wins; this doc stays the design intent. Decision: AD-44.

> **Revision 2 (2026-11-09, evening).** Owner testing found the questions hard-coded, generic and vague. The cause was a design flaw: a fixed slot catalog chose *which* topics to ask and the model only reworded them. The fix is in **section 17** (AD-29 revised, AD-30 to AD-32): the catalog becomes a *Needs Registry*, an LLM plans project-specific question batches and follow-ups, and every question and answer is kept as an *Interview Memory* of facts from which the Brief is compiled. Where sections 3 to 9 conflict with section 17, section 17 wins. Build prompt: [`prompts/02-compass-ai-interview-prompt.md`](../prompts/02-compass-ai-interview-prompt.md).

## 1. The one rule that shapes everything
**Every field in the Brief exists because a downstream agent needs it, and every downstream need has a field.** Nothing is collected "just in case", and nothing a later agent needs is left for it to guess. Discovery serves Research, Blueprint, Design, Screens, Verify, Change control and (later) the Front-end, Backend and QA engines.

Second rule: **ask only what the user must decide.** Everything else is inferred, proposed and corrected, or assumed and shown for approval. A correction is cheaper for a user than a blank question.

What Discovery is not: it does not search the web (it may read a page the user points to), design anything, or choose technology (the stack is fixed).

## 2. What each downstream agent needs from it
| Consumer | Decisions it must make | What it needs from the Brief |
|---|---|---|
| **Research** | Which searches to run, in which region and language, for which competitors and patterns | Precise category and sub-category, business model, geography and languages, audience segments, reference and competitor names, feature intents (the job each feature does), local services in play, the user's own vocabulary and synonyms, and an explicit list of **unknowns to resolve** |
| **Blueprint** | Roles, journeys, screens, data, rules, API shape | Apps and their purposes, roles with goals and rough permissions, features with priority, any rules or numbers the user already gave, existing systems or API specs, scale and launch context, success measures |
| **Design** | Look, tone, density, patterns | Brand assets or style signals, tone words, audience traits (age, tech comfort, languages), devices and locales (including right-to-left), accessibility needs, visual references |
| **Screens / Verify** | Copy tone, coverage, ground truth | Vocabulary, audience, the full scope ledger, "must not have" list; the Brief is what the critic compares the spec against |
| **Change control** | What is in, later, out | The Scope Ledger, priorities, the assumption register |
| **Front-end / Backend / QA** | Integrations, constraints, acceptance | Integration preferences (real later or mock), offline and low-end device needs, success measures |

## 3. Adapting to most situations: the Situation Profile
In the first turn Discovery builds a small profile from what the user said and brought (inferred, never interrogated). It decides which parts of the conversation run, how deep, and in which voice.
| Dimension | Values | Effect |
|---|---|---|
| Input richness | nothing / one sentence / rich description / documents | Start path: inspire, expand, or read first |
| Clarity of vision | vague / knows features / knows everything | Strategy: suggest options / confirm and fill gaps / validate and stress-test |
| Expertise | non-technical / product / technical | Vocabulary and depth; never jargon for non-technical users |
| Size and stakes | tiny / startup / enterprise | Depth profile (Express, Standard, Large) and question budget |
| Reference-driven | "like X" present or not | Run the reference path (what to borrow, what to avoid) |
| Collaboration | one person / several stakeholders | Attribute answers, assign decision owners, detect conflicts |
| Language and mode | languages, voice or typing | Chat language, voice echo-back, separate app locales |
| Urgency | "just build it" or thorough | Minimum Viable Brief or full pass |
The result is the **Discovery Plan**: an ordered list of conversation modules (Understand, Playback, Gaps, Propose, Research seeding, Probes) with a question budget.

**Minimum Viable Brief (the "just build it" path).** Even the fastest path must give Research and Blueprint what they cannot guess: what it is, for whom, where (country, city, languages), and which apps. Those few answers are asked; everything else is assumed and listed.

## 4. The pipeline (twelve steps)
| # | Step | Who | What happens |
|---|---|---|---|
| 0 | Welcome | code | Canned greeting, four starts: describe your idea, upload your documents, start from an app you like, just build it. Voice button and language choice are visible |
| 1 | Intake | code | Parse every file and page the user points to into text, page images and transcripts; chunk by structure; record coverage |
| 2 | Read | model (DISC-1, parallel) | Extract typed statements with source pointers. Types: requirement, constraint, business context, goal, user or role, rule or number, glossary term, example or scenario, open issue ("TBD"), preference, exclusion |
| 3 | Merge | model (DISC-2) | Deduplicate, find contradictions, report coverage ("read 100%; 187 statements; 31 need confirming; 4 conflict") |
| 4 | Understand the situation | model (DISC-3) + code | Build the Situation Profile and a **Domain Hypothesis**: category and sub-category, business-model candidates, likely roles and features grouped by library module, hard parts (tier B and C), candidate glossary. Everything is labelled a hypothesis |
| 5 | Playback | card | "Here is what I understood": idea, apps, who uses it, main features, and what is unclear. The user corrects in place. This is the cheapest high-value interaction |
| 6 | Gap analysis | code | Compare slots with the consumer needs (section 5); score each gap; build the question queue (section 6.5) |
| 7 | Interview | model (DISC-4, 5, 6) + code | Serve one card at a time; absorb free text; answer questions; update slots; recompute the queue after every answer |
| 8 | Propose, then correct | model (DISC-6) | Roles, features, integrations and vocabulary arrive as pre-filled checklists to adjust, not blank questions |
| 9 | Seed research | model (DISC-9) + code | Write `research_requests` (section 8) and show them to the user as "What we will look up for you", editable |
| 10 | Handoff probes | model canaries (DISC-10) | A weak Research planner, Blueprint planner and Design planner each try to start from the draft Brief; anything they cannot do is a gap (section 9) |
| 11 | Assumption sweep | code | Group assumptions by impact; prepare the structured review (section 7) |
| 12 | Compile and G1 | model (DISC-8) + code | Write `brief.json`, the readable Brief, the readiness report; open Gate 1 |
Later stages may reopen one slot with a single targeted question (typed back-edge, at most 2 loops), never a new interview.

## 5. The slot catalog (what we need to know, by consumer)
Levels: **must** (cannot leave Discovery empty; may be assumed with approval), **should** (ask if the budget allows, else assume), **may** (infer and list). "Filled by": U = user answer, D = from documents, I = inferred by the model with a basis, P = pattern default from the module library.
| Group | Slot | Level | Consumers | Filled by |
|---|---|---|---|---|
| Idea | `idea.summary` (one line + pitch) | must | all | U, D |
| | `idea.category`, `idea.subcategory` | must | Research, Blueprint | I, confirmed in playback |
| | `idea.business_model` | must where money flows | Research, Blueprint | U (generated options), P |
| | `idea.problem_value`, `idea.differentiator` | should | Research, Design | U, D, I |
| Context | `context.geo` (country, city, region) | must | Research, Blueprint, Design | U, D |
| | `context.scale_launch` (pilot size, timeline) | should | Blueprint, Front-end, QA | U, P |
| | `goals.primary`, `goals.success_measures` | should | Blueprint, QA | U, D, I |
| Audience | `audience.segments` (who, in plain words) | must | Research, Design, Screens | U, D |
| | `audience.traits` (age, tech comfort, languages, accessibility) | should | Design, Screens | U (chips), I |
| | `audience.usage_context` (on the go, low network, shared device) | may | Design, Front-end | I, P |
| Apps and roles | `apps.list` (purpose, surface) | must | Blueprint, Design | I, U |
| | `roles.list` (name in user's words, goal, rough permissions) | must | Blueprint | I, U (checklist) |
| Scope | `scope.features` (status, intent, priority, size, tier) | must | Blueprint, Research, Change control | D, I, U (checklist) |
| | `scope.priority` (MVP boundary) | should | Blueprint | U |
| | `scope.must_not` | may | Verify, Change control | U, D |
| | `scope.references` ("like X": what to borrow, what to avoid) | should | Research, Design | U |
| Experience | `brand.assets`, `brand.name` | should | Design | U (upload, link) |
| | `look.tone_words`, `look.visual_references`, `look.density_mode` | should | Design | U (chips) |
| | `locales` (chat, app languages, right-to-left) | must | Design, Screens | U, I |
| | `devices.profile` | should | Design, Front-end | I, P |
| Access | `auth.methods`, `auth.account_model`, `onboarding.approvals` | must / should | Blueprint | U (generated options), P |
| Data | `integrations.wanted` (payments, maps, OTP, push, email, KYC...; real later or mock) | should | Blueprint, Backend | U (checklist), P |
| | `data.existing_systems` (APIs, databases, ERP; uploads) | should | Blueprint, Backend | U, D |
| Limits | `constraints.*` (offline, performance, accessibility, must-use services) | may | Blueprint, Front-end | I, P |
| Words | `glossary.terms` (preferred term, synonyms, avoid) | should | Research, Blueprint, Screens | D, U, I |
| Unknowns | `unknowns.research_wanted` | generated | Research | I |
| Enterprise | org profile, approvers, design system, API catalog | should in Large | all | D, U |
**No-ask list:** stack and framework choices, hex colors, database or hosting, legal wording, anything not tied to a consumer decision, anything that can be inferred with high confidence and low impact.

## 6. The question system
### 6.1 Fixed purpose, fixed shape, generated content
> *Superseded in part by section 17: the shape, basis labels and validation stay; the choice of which questions to ask is now made by an AI planner, not a fixed list.*
Hard-coded questions per category bind users into our categories and ask irrelevant things; free-form AI questions invent options. The middle path:
| Fixed (data) | Generated (per project) | Guardrails (code) |
|---|---|---|
| The slot catalog; the card frames (shape and behavior); allowed answer types; safety rules; a plain deterministic fallback card per slot | Question wording in the user's own words and language; the three options; the "why we ask" line; the recommended option and its reason | Validated before display: see 6.2 |
Every generated option carries a **basis label** the user can see: *from your document*, *typical for apps like this*, *our suggestion*, or *your words*. No invented statistics, no "most people choose X" without a source.

### 6.2 Card validation (code, before anything is shown)
Exactly three distinct, concrete options (or a checklist); each option maps to a valid value for its slot; none relies on a tier C capability; none adds a feature outside the module library unless flagged `custom`; no brand names unless the user used them; neutral wording (no leading adjectives, no marketing language); question at most about 20 words, options at most 8 words; language matches the user; one recommended option with a reason; a custom input, "You decide" and "Skip for now" always present. If validation fails twice the deterministic fallback card is used.

### 6.3 What the user sees
```
 Question 3 of about 7                                   [ Skip ]   [ You decide ]
 Who can order on your app?
 Why we ask: this decides which screens the first version needs.

  ( ) Anyone, no sign-in needed to browse            Our suggestion: most delivery apps let people look first
  ( ) Only people who have signed up
  ( ) Customers and restaurant staff in the same app
  [ Something else: type it, or tap the mic                                          ]

  [ Back ]                                                              [ Continue ]
```
Rules for the experience: show an honest "question n of about N" and update N rather than hide it (respondents dislike not knowing how many exchanges remain, and repetitive probes [S: Google Research chatbot-survey study]); at most one follow-up per answer; never re-ask a confirmed slot; typed or spoken answers can fill several slots at once and the matching cards disappear; "You decide" collapses the card into a one-line *Assumed: X (change)*; a live Brief panel beside the chat shows rows appearing and turning from assumed to confirmed; open-ended questions are used sparingly because they cost more effort, especially on a phone [S: CHI 2025 probing-chatbot study].

### 6.4 Card types
| Type | Use | Example |
|---|---|---|
| Choose one | Three options + custom + you decide | Business model, login method |
| Pick many | Checklist, sensible items pre-ticked, "add your own" | Roles, features, integrations |
| Priority | Drag chips into Must / Nice / Later | Forcing a real MVP when "everything is a must" |
| Confirm and correct | Editable rows summarizing what was understood | Playback, vocabulary |
| Look signals | Mood words and examples, not full designs (Design stage makes the real directions) | Tone words, "apps whose look you like" |
| Quick form | A few related fields together | Name, logo, languages, city |
| Upload or link | Files, brand kit, API spec, screenshots, a page to read | Documents and assets |
| Open prompt | One free-form question; voice welcome; used in help mode | "Tell me about your business in a few lines" |

### 6.5 Choosing the next question (value of information)
> *Revision 2: these scores (impact, inferability, risk, effort) are now **inputs to the AI Question Planner** (section 17), which generates the questions; code no longer picks frames from a queue.*
For each gap code scores **impact** (how many downstream decisions depend on it and how costly a wrong guess is), **inferability** (how well it can be inferred from context or patterns; the model proposes a value and a confidence), **risk** (payments, accounts, anything hard to undo) and **effort** (tap, text, upload).
| Impact | Inferability | Outcome |
|---|---|---|
| High | Low | **Ask** (a card) |
| High | High | **Propose and confirm** (in playback or a pre-filled checklist) |
| Low | Any | **Assume**, list in the assumption review |
| Risky | Any | **Ask**, and mark must-confirm |
Order follows dependency: category and geography, apps and roles, features, access, experience, integrations, limits. Related slots are batched into one card or form. Budgets: Express at most 5 questions, Standard about 7 (never more than 10), Large per module. The queue is recomputed after every answer, so an answer that settles three slots removes three questions. The structured-elicitation choice follows 2026 evidence that models under-ask when several things are unclear and that more reasoning does not fix it [V: ClarifyCodeBench], so *what* must be known is decided by the catalog and code, and the model only helps with language and inference.

### 6.6 Free text, custom answers and voice
A typed or spoken answer is parsed for every slot it touches; the user sees chips such as *I picked up: Pune · Hindi and English · restaurants as partners* and can remove any. If the text is ambiguous the engine asks a small either/or, not a new open question. A custom answer to a choice card is stored in the user's own words plus a normalized candidate; if it implies a new feature or category it becomes a `custom` item that is flagged and confirmed. Voice input is echoed back for names and numbers. Chat follows the user's language; app locales are asked separately.

### 6.7 Question frames, not scripts
> *Revision 2: frames are only style guides, few-shot examples and the last-resort fallback. They are never the source of the questions.*
A frame fixes the purpose, shape and guardrails; the content comes from the situation. The same frame in two domains:
| Frame: "How will it make money?" | Food marketplace | Clinic booking app |
|---|---|---|
| Option 1 (our suggestion) | A small commission on each order | Patients pay per appointment |
| Option 2 | Delivery fees paid by customers | Clinics pay a monthly fee to be listed |
| Option 3 | A monthly plan for restaurants | Free for patients, clinics pay per booking |
| Always | Something else + You decide + Skip | same |
Frames in use (about twelve): idea in your words; confirm the kind of app; business model; who uses it and where; which apps; roles; features (checklist); priorities (forced ranking when needed); apps you like and what to borrow; brand and tone; login and accounts; integrations and existing systems; scale and launch; limits (offline, devices, accessibility). The builder implements frames as data, so adding a frame never requires new code paths.

## 7. Assumptions: propose, then approve
Every assumption is an object: claim, reason, basis, confidence, **impact** (what downstream depends on it), toggle. During the chat, an assumption appears as a one-line *Assumed* chip. At Gate 1 they are shown grouped by theme (Business, Users, Features, Look, Access, Integrations) as a "We assumed" review: **low-impact** ones can be accepted in bulk with one tap; **high-impact** ones must each be looked at (confirm or change), because silence never approves. A short note beside each says what changes if the user edits it. We track how often assumptions are changed later and use it to improve defaults.

## 8. Research seeding: serving the next agent
Discovery writes the starting plan for Research so Research does not guess what matters. `research_requests[]` (each one specific, or it fails the probe):
```yaml
- id: RR-03
  topic: "Typical feature set of multi-restaurant delivery apps for small Indian cities"
  question: "Which features do comparable apps treat as must-have vs advanced?"
  decision_unlocked: Blueprint scope (MVP boundary), Scope Ledger suggestions
  geography: India, tier-2 cities (user named Pune)
  source_languages: [en, hi]
  terms: ["food delivery", "restaurant ordering", "online ordering"]   # from the user's vocabulary plus synonyms
  references: ["<apps the user named>"]
  prefer_sources: [app store listings, official help pages, public feature lists]
  avoid: ["B2B catering", "cloud kitchens"]                           # from the user's exclusions
  depth: standard    priority: high    shown_to_user_as: "What similar apps include"
- id: RR-07
  topic: "Payment and SMS providers commonly used in the region"
  decision_unlocked: Integration Activation Checklist, Blueprint integrations
  geography: India     depth: quick     priority: medium
```
Also `search_hints`: disambiguated names, synonyms, local competitors, languages. Before Gate 1 the user sees **What we will look up for you** as removable and addable chips ("also check how X handles refunds"). This gives the user control and makes research visibly relevant.

## 9. Handoff probes: the Brief is tested by its consumers
Same principle as the spec's sufficiency probe (AD-27). Before Gate 1 three weak canary planners try to start from the draft Brief:
| Canary | Task | Fails when |
|---|---|---|
| Research planner | Write at least 8 concrete search queries with region, entity and terms | It lacks a geography, an entity, terms or a reason |
| Blueprint planner | List roles, main journeys and the first-release screens | It cannot say who does what, or features lack intent |
| Design planner | Name a tone and a direction for brand and palette | Audience, tone or brand signals are missing |
Every blocking question becomes a gap: fixed by an assumption (shown for approval) or one card. Gate 1 opens only when all three pass.

## 10. The output: the Brief (the frame)
One canonical `brief.json`, rendered as a readable page, plus **consumer views** derived by code (Research view, Design view, Blueprint view) so each agent receives only what it needs.
```yaml
meta:        {program_id, depth_profile, situation_profile, locales[], created_at}
idea:        {summary, pitch, problem_value, category, subcategory, business_model, differentiator}
context:     {geo, scale_launch, goals[], success_measures[]}
audience:    {segments[], traits, usage_context}
apps:        [{id, name, purpose, surface, audience, roles[]}]
roles:       [{id, name_in_user_words, goal, rough_permissions[]}]
scope:       [{id FTR-###, name, intent, apps[], module, status in|later|out|unsupported|needs-engineering,
               priority must|nice|later, source, size{}, capability_tier A|B|C}]
references:  [{label, borrow[], avoid[]}]
experience:  {brand{assets[],name}, tone_words[], visual_references[], density_mode, locales[], devices, accessibility}
access:      {auth_methods[], account_model, onboarding_approvals}
integrations:[{name, preference real_later|mock|undecided, notes}]
data:        {existing_systems[], sample_data}
constraints: {offline, performance, must_use[], must_not[]}
glossary:    [{term, synonyms[], avoid[]}]
assumptions: [{id, claim, reason, basis, impact, confidence, status}]
open_questions: [{id, text, blocking, owner}]
research_requests: [...]        # section 8
assets:      [{id, kind, summary, pointer, consumers[]}]   # API specs go to Blueprint, brand files to Design, competitor docs to Research
sources:     [{id SRC-###, kind, name, hash, coverage}]
declined:    [{what, reason, alternative}]
readiness:   {research: pass|gaps[], blueprint: ..., design: ..., verify: ...}
```
Every item also carries `tier` (Locked / Default / Open), `provenance`, `confidence` and `status`. **Gate 1 page (the frame):** one-line idea; readiness chips (*Ready for Research, Blueprint, Design*); then cards, each editable: Your idea · Who it is for · Apps and roles · Features (in / later / out and priority) · Look and tone · Access · Integrations and data · Limits · Words we will use · We assumed · Still unknown · What we will look up · Sources read.

## 11. What Discovery stores
| Store | Contents | Passed on? |
|---|---|---|
| Event log | Every message, tap, upload, card shown, with times | No (audit and replay only) |
| Sources | Original files (hash), parsed text and pages, extraction results with pointers, coverage | Pointers and summaries only |
| Claims | Typed statements with source, confidence, status (used, duplicate, conflicting, ignored) | Via the Brief |
| Slot table | Value, provenance, state (empty, assumed, volunteered, confirmed, disputed), confidence, history for undo | Yes (the Brief) |
| Scope Ledger | Features and their status | Yes |
| Assumption register | Claim, reason, impact, decision | Yes |
| Question ledger | Cards asked, answers, skips, why each was asked (scores) | No (improves the system) |
| Situation Profile and plan | Dimensions and chosen modules | Summary only |
| Research requests, glossary | As above | Yes |
| Brief versions | Draft each time slots change; approved baseline `B-G1` | Yes |

## 12. Prompting approach (so prompts handle situations instead of scripting them)
1. **Separate jobs.** Understand calls never ask questions. Ask calls never infer values. Compile calls never invent.
2. **Evidence first.** Every extracted statement points to its source; no source means `assumed`.
3. **Principles, not scripts.** Each prompt gives role, policy, schema and two or three diverse canonical examples, not a long list of edge cases.
4. **Plain language.** No jargon for non-technical users; use the user's words and the glossary.
5. **Neutral and bounded.** Options are mutually exclusive and concrete, never leading, never add unrequested scope, never invent numbers.
6. **Fail safe.** When unsure, ask or assume-and-flag; never decide silently.
7. **Small context.** Calls receive the situation profile and the Brief so far, not the whole chat.
8. **Structured outputs everywhere**, validated; one repair, then the deterministic fallback.
9. **Control stays in code.** The model never chooses what to do next; it fills schemas.

## 13. Discovery-specific edge cases (to merge into the catalog)
| ID | Case | Rule |
|---|---|---|
| DC-01 | Custom text contradicts the chosen option | Ask a small either/or; latest explicit statement wins |
| DC-02 | Everything is "must have" | Priority card: "If you launched with only five features, which?" |
| DC-03 | Two references pull in opposite directions | Capture both; ask what to borrow from each |
| DC-04 | Same feature in different words | Merge through the glossary and show the merge |
| DC-05 | Hybrid or unclear category | Compose from several modules; ask which is primary |
| DC-06 | Chat language differs from app language | Ask separately; never assume |
| DC-07 | Voice mis-hears names or numbers | Echo back and confirm names, numbers, places |
| DC-08 | User wants to change a Locked answer after seeing downstream effects | Reopen that slot with an impact preview |
| DC-09 | User asks for a technology choice | Explain the fixed stack in a line; no choice offered |
| DC-10 | Request beyond capability (tier B or C) | Say so right away with the alternative, not at G2 |
| DC-11 | Five or more apps requested | Propose the main two now, the rest as later phases |
| DC-12 | User names a competitor as their own business | Confirm before treating it as a reference |
| DC-13 | User leaves mid-way | Draft saved; resume with a recap; nothing is finalized as assumed |
| DC-14 | A research request comes back with a major surprise | A single targeted question via the typed back-edge, not a new interview |

## 14. Acceptance gate for this agent (initial targets [J], measured in SpecBench-Mobile)
| Measure | Target |
|---|---|
| Requirement recall and precision on PRD-led cases | at least 90% each |
| Locked items without evidence | 0 |
| Questions per Brief | by tier (as built 2026-11-10): Quick about 7 (cap 10), Standard about 12 (cap 15), Thorough 25 or more (cap 30); superseded earlier "median at most 7" |
| Questions that change a downstream decision | at least 85% (no wasted question) |
| Assumptions unchanged at G1 | at least 80% |
| Handoff probes (Research, Blueprint, Design) | all pass before G1 opens (**as built, G1 opens on a degraded Brief**: the Standard run was approved with the design probe false; see the as-built note §8.4) |
| Generated cards passing validation (or using the fallback) | 100%; zero options with false "typical" claims in sampled review |
| Hidden-instruction tests | 100% ignored |
| Time to Brief (interactive) | Express about 3 min, Standard about 10 min |
| Non-universal questions containing project-specific terms | at least 90% |
| Cross-idea diversity (same needs, five different ideas) | question texts differ materially (calibrated overlap threshold) |
| Repeated or already-answered questions | 0 |
| Follow-ups on vague answers to high-impact needs | at least 90% fire; none on specific answers |
| Fact coverage (every fact used in the Brief or marked `not_used`) | 100% |
| Provenance quotes verified against the original text | 100%; unverified quotes never Locked |
| Planted early fact present in the Brief after 25 turns | yes |
| Fallback cards in live runs | 0 |

## 15. Reuse and replace (hypothesis for Step 0 to confirm)
**Likely reuse:** event idempotency, slot state machine, intent router, safety and redaction, language handling, card schema, gate mechanics, scenario test harness. **Replace:** archetype-bound question bank (becomes frames); pre-rendered theme cards in Discovery (the Design stage makes directions); the slot list (becomes the consumer-driven catalog). **Add:** document intake, Situation Profile and Domain Hypothesis, value-of-information selection, propose-then-correct checklists, glossary, assumption register, research seeding, handoff probes, readiness report, live Brief panel data.

## 16. Build order inside this agent (for the later builder prompt)
(a) Brief schema and slot catalog as data; (b) document intake; (c) extraction and merge; (d) Situation Profile and Domain Hypothesis; (e) question engine: frames, generation, validation, value-of-information queue; (f) free text, multi-slot absorption, voice echo; (g) assumption register and review data; (h) research seeding and handoff probes; (i) Brief compile, consumer views, readiness; (j) scenario and benchmark tests for the cases in docs 05 and 11. Each sub-step has its own tests; the agent is done when section 14 passes.

## 17. Revision 2: AI-planned interview, follow-ups and interview memory (2026-11-09)
**Updated role.** Compass is an **AI interviewer**. It listens, reads the documents, and plans the questions *this particular project* needs, in themed batches. It asks follow-ups where answers are thin, remembers everything as facts with exact quotes, and compiles one Brief for the agents below it. It is not a form with a fixed question list.

**What changed and why.** Before: the slot catalog decided the topics, a queue picked frames, the model reworded one card at a time, answers fed slots only. Result: generic questions, no follow-ups, details lost. Now: the catalog is a **Needs Registry** (what downstream agents need, each with an `answer_spec` saying what a sufficient answer contains); an LLM **Question Planner** generates the questions; code validates and governs.

**The loop.**
1. Intake and read → facts → needs status (satisfied, partial, missing, assumed).
2. `plan_questions` (initial) returns the Domain Hypothesis and a first batch of 5 to 7 questions in 2 to 3 themed groups: grounding questions if domain confidence is low; 2 to 3 domain-specific mechanics only this idea has; the consumer-driven basics phrased in the project's own terms; at least one **expert blind-spot** question. Rich documents mean fewer questions (confirm and fill gaps only).
3. The user reads the playback card while the batch is prepared and audited (`audit_questions`: specificity, leading, jargon, duplicates, need mapping; code validators too).
4. Each answer is turned into facts and judged for **completeness against the answer_spec**. A thin answer to a high-impact need triggers one targeted follow-up (depth cap 2 per need; follow-ups at most 40% of base questions). New topics go to a backlog; contradictions get an immediate either/or.
5. After each batch, a gap audit and `replan`: add, drop, never repeat. Stop when every must-need is satisfied or visibly assumed and the value of more questions is low, when the budget is reached, or when the user says enough.
6. Adaptive budgets: **Quick** up to 5, **Standard** up to 12 with follow-ups, **Thorough** up to 25; **Ask me more** deepens at any time. Compass is the agent that asks the most; it stops early on fatigue (many skips).

**Interview Memory (how the AI keeps full knowledge).** Stored: every question (with the options shown and what it serves), every answer (versioned), and a **fact ledger**: atomic normalized statements with type, topic tags, consumers, confidence, status and a **verified verbatim quote** (unverified quotes can never support a Locked item). Slots become a view over facts. Each AI call gets a bounded working context (project card, fact digest, needs table, asked list, last four Q&A, the task); the **Brief compiler gets the entire fact ledger**. A **fact coverage check** requires every fact to appear in the Brief or be marked `not_used` with a reason. Older facts are superseded, never deleted; editing an earlier answer re-derives its facts and triggers a replan; a pivot ("actually it's for clinics") is confirmed and handled as a replan.

**Brief additions.** A `domain_facts` section (typed facts with provenance and consumers) so project-specific details that fit no fixed field still reach Research, Blueprint and Design; a Brief-page section **"Details we captured"**; consumer views include the facts tagged for them and never the transcript.

**Honest failure handling.** Mock mode shows a banner and labels its questions `[mock]`. A fallback card (when the planner fails twice) is flagged, counted, and treated as a bug in live runs.

**Edge cases.** Twenty named scenarios `SB-QP-01..20` are listed in the build prompt (one-line idea, rich PRD, unknown domain, monologue answers, vague answers, "I don't know", contradictions, pivots, off-topic, declined categories, languages, voice, duplicate or generic planner output, planner failure, skipping everything, answer edits, late documents, latency, exhausted budget, the planted-fact test).

## Evidence used here
ClarifyCodeBench (models under-ask; reasoning does not fix it) [V]; LLM interviewer studies show context-aware questioning comparable to human error rates, mostly with simulated stakeholders [S: LLMREI arXiv 2507.02564; iReDev arXiv 2507.13081]; closed questions plus chatbot follow-ups gave richer answers, and respondents disliked uncertainty about the number of exchanges and repetitive probes [S: Google Research]; open-ended questions are more demanding, especially on mobile [S: CHI 2025 probing-chatbot study]; competitor planning modes ask clarifying questions about audience, features, design and technical needs, then build strictly from an approved plan [S: Lovable and Replit documentation]. Full links: [doc 10](10-research-log.md).
