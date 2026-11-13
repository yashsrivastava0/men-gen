# Compass as built (reference, 2026-11-10)

**What this is.** My condensed copy of the owner's "Compass agent: complete reference" (pasted 2026-11-11) plus what I found by reading the three real approved exports. It is the **current truth about what Compass does and outputs**. Where it differs from [doc 11](../architecture/11-discovery-agent-design.md), the built product wins on facts, and doc 11 stays the design intent.

**Sources.** The owner's pasted reference; the README and the three JSON exports in `/Users/coder/Desktop/project/men-gen/docs/compass/research-inputs/` (read on 2026-11-11, read-only, not copied here). Repo facts are as reported and as read from those files; I did not run the code. The Compass author later confirmed (2026-11-11) that they recomputed the Brief, section and baseline hashes for all three files and checked the research view against the code; all pass (`agents/compass/tests/test_research_inputs.py`). I did not recompute them; I only checked that `export.brief_hash`, `brief._hash` and `approved_baseline.hash` are equal in all three, and they are.

## 1. Where Compass sits (and one correction)
The owner's reference still lists the old six-step chain (Discover, Gate 1, Research, Build the spec, Gate 2, Hand-off). **Our current chain is the four agents of v3.2** (Compass, Research in the background, Spec, Verify and package). Compass is agent 1 and ends at **Gate 1**. Its end product is the approved Brief plus derived views in one JSON export (format 2).

## 2. What Compass does

### 2.1 Tier first
The person picks the tier on the first screen. The tier also sets app size and research depth.

| | Quick | Standard | Thorough |
|---|---|---|---|
| Interview target (hard cap, with follow-ups) | about 7 (10) | about 12 (15) | 25 or more (30) |
| Size band | small | medium | large (shown as "Enterprise") |
| Screens in the band | 8-15 | 20-30 | 40-60 and more |
| Research depth | light | standard | deep |
| Searches (`search_budget`) | up to 6 | up to 12 | up to 20 plus a gap-fill round |
| Research requests (`max_requests`) | up to 6 | up to 12 | up to 20 |
| Structure review at Gate 1 | summary only, never blocks | soft (a notice) | hard (every module accepted, or "Approve all") |

Band rules: guidance, never a minimum (a tiny idea in a big tier stays small). A big uploaded document can raise the band (25 or more verified claims gives medium, 60 or more gives large), never lower it. The person can re-plan for another band later; that redrafts the structure from the same answers. Band and source are in `brief.meta.interview` (`size_band`, `size_source`, `size_suggested`). Compass never switches the tier itself; it shows one notice with a one-tap switch.

### 2.2 Interview
A planner model asks **batch forms** (several questions, submitted once). Types: choose one, pick many, rank, free text, confirm, upload or link. The person can answer, skip, "You decide", or "Decide the rest". The interview ends with a ranked closing question ("for the first version, which of these matters most") that sets which features are must. Documents (pdf, docx, xlsx, pptx, csv, txt, md, eml, up to 15 MB) and links become facts.

### 2.3 Facts and tiers
Every answer becomes **facts**: statement, verbatim quote, type, origin (stated, tap, document, assumption), status (active, superseded, disputed). Newer supersedes older; nothing is deleted. Facts feed slots, slots feed Brief records. Every Brief value has a **tier** (Locked = the person's own verified words; Default = AI assumed or drafted; Open = unknown), a status, provenance with quotes, confidence and history.

List-merge rules (2.3.1 in the owner's text): facts join lists, never wipe them; existing elements keep their name and take the newer priority; roles and apps are matched strictly; an app named after its people also creates that role; a "role" that is really a list is split; "Name: description" cards become name plus goal; apps named only "app" or "portal" are dropped.

### 2.4 Brief build, Gate 1, editing
After the last answer: compile facts into slots and records; **draft the structure**; seed the research plan; run readiness probes (research, blueprint, design); list assumptions. Gate 1 page has **"Approve all and continue"** (accepts all pending assumptions and all proposed structure in one change set, then approves; stores version and hash). Every change is one change set with an exact inverse. A change after approval makes a new version; the approved one is immutable.

## 3. The Brief (source of truth)
Sections: identity (`summary`, `pitch`, `idea`: category, subcategory, business_model); `context` (geo, scale_launch); `audience`; `experience` (locales, tone, brand, density, devices); `access` (auth_methods, account_model, onboarding_approvals); `data.sensitive`; `constraints`. Records: `scope` (features FTR-###: name, intent, family, apps, priority must/nice/later, scope_status), `roles` ROL-###, `apps` APP-### (surface mobile or web), `integrations`, `references`, `glossary`. Detail: `domain_facts` (typed: rule, number, acceptance, compliance, nfr, constraint, example, screen, flow, entity, integration_detail, context; each with quote, refs, topics, consumers), `assumptions` ASM-### (claim, basis, impact, decision), `open_questions`, `declined`, `readiness`. **Research inputs written by Compass:** `research_requests` RR-### (topic, question, decision_unlocked, entity, geography, source_languages, terms, references, prefer_sources, avoid, depth, priority, shown_to_user_as, user_added) and `search_hints` (disambiguated_names, synonyms, local_competitors, languages). Structure sections: `modules`, `screens`, `flows`, `entities`. Meta: `meta.interview` (budget, counts, `fallback_count`, band, structure counts), `meta.plan`, `meta.hypothesis`, `meta.situation_profile`, `meta.degraded`, `meta.probes`, `meta.compile_digests`. Hashes: `_hash` and `_section_hashes`; structure sections are in the hash when present.

## 4. The structure (new since doc 11): modules, screens, flows, entities
**Compass now drafts an intent-level structure and the person approves it at Gate 1.** It is not routes, navigation or per-screen detail; those stay with Spec.
- Drafted by: an outline call, parallel module calls (at most 1, 3, 2 modules per call for small, medium, large; at most about 12 screens per call), code checks, a critic that can only report, one repair pass. Failed calls fall back to deterministic templates and are counted.
- Records: Module MOD-### (name, purpose, kind, apps, roles, features, entry), Screen SCR-### (purpose, module, apps, roles, serves, entities with use view/create/edit, pattern from a closed list), Flow FLW-### (role, steps, features, acceptance[], alternates[]), Entity ENT-### (fields with typed closed list, states, access map role to none/view/create/edit/manage). Each has `decision` proposed or accepted.
- Derived in code, not stored: role-by-module matrix, navigation edges, phase per screen (core, extended, later), counts.
- Checks (severity error or warn): unknown refs, empty module, flow under 2 steps, unused entity, screen for an excluded feature, must feature without a flow that has acceptance lines, a flow role that cannot open a step, a role that edits an entity without access, money typed as text, realism rules (health needs consent and audit trail; payments need receipt or refund), size (warn only).
- Provenance guard: a structure record resting on a later-superseded fact drops to Default and proposed.

## 5. The export (format 2) and consumer views
One JSON: `export` (format, version, `brief_hash`, `approved_baseline`, `reading_guide`), `session`, `brief` (with `_hash`, `_section_hashes`), `consumer_views`, `structure` (band, source, counts, outline, `slices` per module, role_matrix, navigation, issues, research), `brief_versions`, `interview` (`qa`, `facts` with quotes, `summary`, `batches`), `sources`, `contributors`, `change_sets`, `spend`.

**Consumer views** are values only (no quotes, no history, no chat text), built by code:
- **research:** idea, geography, languages, audience, references, feature_intents, glossary, `research_requests`, `search_hints`, declined, modules (names), entities (names), `domain_facts`, and **`guidance`**: `size_band`, `depth`, `search_budget`, `gap_fill`, `max_requests`, category, subcategory, business_model, `families`, and `focus` = list of {topic, about[]} for landscape (module names), domain (entity names), integrations, compliance, references, locales.
- **blueprint:** app, roles, features, routed facts, structure outline, one slice per module. **design:** look and tone, locales, screens with phase, modules. **verify:** flows with acceptance, and the must features that have none.

## 6. What the three real runs show (2026-11-10, live model gpt-6-luna)
| | Quick: yoga studio, Bengaluru | Standard: produce marketplace, Nashik | Thorough: freight and fleet, India |
|---|---|---|---|
| Brief | v8, 6 features (4 must, 2 nice), 4 roles, 1 app, 33 facts | v10, 9 features (6 must, 2 nice, 1 later), 9 roles, 4 apps, 44 domain facts | v13, 30 features (4 must, 26 nice), 18 roles, 6 apps, 151 domain facts (40 from a 2,263-byte document) |
| Questions | 8 (1 follow-up) | 13 (2 follow-ups) | 30 (5 follow-ups) |
| Research requests | 5 | 7 | 20 |
| Structure | 4 modules, 13 screens, 4 flows, 5 entities | 5, 28, 7, 10 | 11, 132, 20, 22 |
| Cost (USD) | 0.027 (37 calls) | 0.107 (68 calls) | 0.489 (100 calls) |
| `degraded` / probes | false, all pass | **true**, design probe false | false, all pass |
| Structure issues | 1 warn | 3 errors + 1 warn | 1 error, 75 warns (58 unreachable screens) |
| `fallback_count` | 0 (but see below) | 0 | 9 |

## 7. My review of the three Briefs (checklist in STATE.md)
I read the Research view, `guidance`, requests, Brief sections and issues in all three. Judged only as Brief quality for the next agents.

**What is good**
- Hash identity holds in all three. Sessions are APPROVED. Every Brief carries the full set of sections, and the research view builds from it.
- **Research requests are strong in Standard and Thorough**: specific, region-bound, each with `decision_unlocked`. Thorough names TruckSetu, FASTag, e-way bill, GST e-invoicing, GPS telematics, PUC and permits, hired trucks, and splits compliance, integrations and workflow questions. I can write well over 8 concrete searches from either.
- The Standard run found real contradictions and recorded them as assumptions (email/password sign-in against farmers using phone code; live tracking against "can wait"; online payment without a payment method), and the business model (free to join, 4% commission, delivery fee) is captured precisely.
- Languages are right (Quick: en, kn; Standard: mr, hi, en; Thorough: en, hi) and `search_hints` include Hindi terms for Thorough and Hindi and Marathi terms for Standard.
- Thorough keeps document claims (40) and the brand note (deep blue and orange, truck-and-bridge logo).

**Defects that matter to Research or later agents**
1. **Fallback undercount (Quick).** `cmp.seed_research` and `cmp.scan_ambiguity` each show about 60 s and **0 tokens**: they timed out, yet `fallback_count` is 0. The Quick research requests read like templates ("Features of comparable yoga studio app apps", "Members at home, Members on the go, Members at the studio" repeated as the audience, entity "yoga studio app apps"), and `search_hints` are empty. So Quick Research input is thin and the honesty counter is wrong. Meta numbers cannot be trusted until timeouts count as fallbacks.
2. **Standard is `degraded` and was still approved.** This answers my old open question: **Gate 1 does open on a degraded Brief** (at least via "Approve all"). Also `meta.probes.design` is false while `brief.readiness.design` says pass; the two disagree.
3. **Stale and junk `open_questions`.** Standard still lists "Who is the app for?" and "Which languages should the app support?" although both are answered, and a raw token "pilot_small" as question text. Thorough lists "FASTag" and "pilot_small" as questions. These are slot values leaking in as text.
4. **Thorough priorities are wrong.** 26 of 30 features are `nice`, only 4 `must`, though the closing ranking was "answered". The closing question did not set priorities. The features also repeat the same thing ("load booking and tracking", "Load booking", "Trip tracking"), and the Standard run has "Online payment" and "Pay for orders online".
5. **Roles and apps are not clean.** Thorough has 18 roles: six broad group names (customers, dispatchers, drivers, fleet team, finance team, compliance team) next to twelve specific roles with singular copies (dispatcher, driver), and a **"Fleet maintenance screens"** that is both a role and an app. Standard has "Manager side" as role and app. Roles flow to Research and Blueprint, so duplicates inflate both.
6. **Weak Locked geography (Thorough).** `context.geo` is Locked with the quote "Data stays in India" (a data-residency statement), confidence 0.6, and city "Pune" although the company has six cities. A Locked value should rest on a real location quote.
7. **Integrations are thin.** Thorough `integrations` has only payments and messaging. GPS telematics, FASTag, e-way bill and GST e-invoice live only in `domain_facts`, `integration_detail` and research requests, not as integration records. Standard has **none**, although online payment and delivery are core (only a research request covers it).
8. **Sign-in mismatch.** Standard `auth_methods` is email and password only, while the facts say farmers use phone code. The contradiction sits in assumptions, not resolved in the field.
9. **Tone is the same word in all three** (`calm_simple`, balanced density, phones) and is marked high impact in Standard's assumptions. It looks like a default, not a choice, for a farm marketplace and a freight enterprise alike. Design should not treat it as a decision.
10. **Structure quality (known and re-seen).** Thorough structure is partly fallback and inflated (132 screens, 58 unreachable warnings, near-duplicate screens, `must_feature_without_flow` error). Standard has three `screen_entity_access_conflict` errors left in an approved Brief. Treat structure as a first draft, which Spec must be allowed to change.
11. **Size.** Files are 0.5, 1.4 and 4.2 MB because of a nested-history bug, fixed in the product since. Re-record Thorough (about USD 0.2 to 0.3) with the fixed code before it is used as a test input.
12. **Facts without quotes (Thorough).** FCT-139 and FCT-140 are active, typed answers with an empty quote. They rest on no verified words and must not support anything Locked.

**Verdict as a Brief (my judgment).** Usable as Research input for Standard and Thorough today; Quick needs the seed timeout fixed first. Not yet reliable as Blueprint input without the priority, role and open-question fixes above. Done-list and not-done-list are updated in STATE.md.

## 8. What this changes in our architecture
1. **Compass now owns an approved intent-level structure.** Doc 12 gave Spec the Blueprint lane (features, entities, journeys) from the Brief. Now the user has already approved modules, screens, flows and entities at Gate 1 (as Default-tier proposals). Recommendation (AD-44, decided on my authority): Spec's Product lane **starts from that structure and refines it**, treats it as proposed (not Locked) when "Approve all" was used, may change it with a recorded reason, and owns everything below intent level. Compass must not grow into routes, navigation or per-screen detail.
2. **Research reads the `research` consumer view and its `guidance`.** That view is Research's contract (doc 13 §2 said "the Brief and the readiness report"; the view is the shape to code against).
3. **Budget numbers disagree: resolved 2026-11-11 (AD-46, doc 13 v2 §6).** Research follows Compass `guidance`: hard caps 6, 12 and 20 searches (+ gap-fill of up to 8 for Thorough), tiers Quick, Standard, Thorough. Research now runs in the foreground after Gate 1 (AD-45), reads the `research` view, and asks Compass for trust flags (prompt 04, CR-1 to CR-3). **Update (doc 13 v3, AD-49):** Research ignores the tier and `guidance` budget fields altogether and derives its own depth from the Brief's content, with ceilings 20 / 40 / 80 searches.
4. **Gate 1 can open on a degraded Brief.** Doc 11 §9 says it must not. Either the product enforces it or we accept it and make downstream agents read `meta.degraded` and `readiness`. Recommendation: the Research and Spec agents must read `degraded` and the failing probe, and Compass should show it at Gate 1 (the builder to confirm what the page shows).
5. **Doc 11 §14 figures.** Quick is now about 7 (cap 10) not 5; Standard 12 (15); Thorough 25 (30). Updated in doc 11.

## 8b. Corrections from the Compass author (2026-11-11), binding for Research
1. **`fallback_count` is not a complete failure counter.** Quick's `seed_research` and `scan_ambiguity` timed out (about 60 s, 0 tokens) and are not counted. Treat Quick's `research_requests` as **deterministic template text**, not model-written. Read `spend.by_call` for 60 s, 0-token calls.
2. **Thorough structure is mostly not model output** (8 module calls hit a per-session cap and fell back; cap now USD 1.00, was 0.50). Use it only as a hint. The 4 MB size was a history-nesting bug, fixed. Roles duplicate singular and plural.
3. **Standard:** 3 error-severity structure issues left; duplicate features (Online payment / Pay for orders online).
4. **Do not rely on `later` to mean "defer"**: "can wait for the second version" is stored as a fact but does not always set priority.
5. **Trust levels:** Locked = the person's verified words; Default or assumed = AI guess. "Approve all" accepts every assumption and structure part in bulk, so approval does not mean the person reviewed them. Structure records are Default unless a verified fact supports them.
6. Interview quirks in the data: repeated questions, standard questions about things already answered, odd categories, awkward request wording.
7. Hashes verified by the author (see the sources paragraph).
Effect on my review: §7 point 10 should read "mostly fallback" for Thorough, and Research must treat Brief values by tier, never by approval.

## 9. Not verified
- Whether the live Gate 1 page shows the degraded flag.
- Hash recompute by me (the author did it; see 8b).
- The "has since been fixed" claims about nested history and the budget cap (commits `d97c269`, `e1cbf31`, `8c43177`, D-123 and D-124 per the README); I have not seen the repo.
- Whether the closing ranking should set priority in the Thorough case (the builder knows).
