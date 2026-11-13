# 03. The Context Engine: six stage-engines

> **Superseded in part, 2026-11-10 ([doc 12](12-four-agent-pipeline.md), AD-35..38).** The Context Engine is now **four agents**: Compass (stage 1), Research (stage 2, runs in the background from the first question batch), **Spec** (stages 3, 4 and 5 as the Product, Design and Screens lanes of one agent: Product ‖ Design → join → Freeze → Screens batches) and **Verify and package** (stage 6, streaming). The steps, validators, artifacts, depth profiles, gates and worked example below stay valid as lane and agent content. Where this page and doc 12 differ, doc 12 wins. Research depth, jobs, pipeline and output format: doc 13 wins (2026-11-10).

The Context Engine is six engines in sequence. Each reads the committed Spec Graph (only the parts it needs), runs a pipeline of schema-checked steps, writes **one artifact**, and commits it to the graph after validation. Code controls the order. There are no free-roaming agent loops; the only bounded tool loop is Research (read-only). (AD-02)

```
 sources --> [1 Discovery] --brief.json--> G1 --> [2 Research] --research.json-->
 [3 Blueprint] --blueprint.json--> (G1.5) --> [4 Design] --design.json-->
 [5 Screens] --screens.json--> [6 Verify and package] --APP_SPEC.md + package--> G2
        ^ typed back-edges: any stage may raise needs_decision to an earlier stage (max 2 loops)
```
After G2 the chain continues in other engines: Front-end (spec slices -> app + preview, G2.5 and **G3**), Backend (Approved Build Manifest -> server), QA (journey replay), optional G4.

## 1. Depth profiles (data, chosen from size and customer)
| Parameter (Compass tiers: Quick = Express, Thorough = Large; see doc 12 §4.3) | Express | Standard | Large |
|---|---|---|---|
| Size | ≤ 12 screens, one app | 13-60 screens, 1-2 apps | 60+ screens, multi-app, multi-approver |
| Interview | ≤ 5 cards, defaults allowed from the first message | ~7 cards, 10 max | per-module pass, documents first, 10 cards per module max |
| Research (superseded by doc 13 §7, v3) | derived from the Brief, not the tier: focused load, ceiling 20 searches | standard load, ceiling 40 | deep load, ceiling 80; all foreground after G1 |
| G1.5 structure checkpoint | off | soft (non-blocking) | hard |
| G2.5 slice preview | off | soft | hard |
| Sufficiency probe | sampled 20% | all screens, sampled flows/ops | 100% of slices, flows and operations |
| Gate UI | Brief and Plan on one review page, two hash-bound approvals | separate G1 and G2 | per-module section approvals plus whole |
| Scope cap | 12 screens | 60 screens | policy-set; auto phase 1 / phase 2 beyond cap |
Targets (initial [J]): Express spec in ~3 min after approval, Standard ~8 min, Large ~15 min for 100 screens.

## 2. Gates owned by the Context Engine
| Gate | After | Shows | Approver | Bound to |
|---|---|---|---|---|
| **G1 Brief** (mandatory) | Stage 1 | Brief View: what you are building, apps and who uses them, features per app with toggles (Scope Ledger), look, login, name and logo, "we assumed" list with toggles | requester; Enterprise: required approver roles | `brief.json` hash and section hashes |
| **G1.5 Structure** (policy) | Stage 3 | app map, roles and permission matrix, entities in plain words, screen inventory | requester / product owner | `blueprint.json` hash |
| **G2 Spec** (mandatory) | Stage 6 | Plan View: app map, flows, roles matrix, Look (style tile from tokens), screen gallery (wireframes), data in plain words, what research added (toggles), assumptions, deviations, risks and out-of-scope, full spec viewer | requester; Enterprise: all required roles | `APP_SPEC.md` hash and section hashes |

Gate mechanics (code-enforced, tested): approval is tied to the artifact hash and section hashes; an edit voids only the affected sections and shows a "changed since approval" diff; typed "yes" counts only when a gate is open and the classifier is confident and the message is not compound ("ok but change the color" becomes a change request); a change the user asks for is its own approval of that change, while things the engine added, assumed or repaired need a tap; silence never approves; Enterprise requires every required approver role to approve the same hash and rejection comments become change requests; an approved version is never mutated. Details of G2.5, G3, G4: doc 06.

## 3. Stage 1: Discovery
> **Detailed design: [doc 11](11-discovery-agent-design.md)** (situation profile, consumer-driven slot catalog, question frames, assumptions, research seeding, handoff probes). Where doc 11 differs from the steps below, doc 11 wins.

**Purpose.** Turn whatever the user brings (an idea, a PRD, decks, screenshots, a voice note, or nothing) into the Brief, complete enough that Stages 2-5 never need to ask the user again.

**Entry paths.** Idea only (guided cards); document-led (read first, show "Here is what I understood", ask only gaps); Enterprise (Org Profile preloaded, documents first, per-module pass); defaults ("just build it": only the idea and the look are asked, everything else assumed and flagged).

**Steps.**
1. *Intake* [code]: parse every file to structured text plus page images; transcribe audio; chunk by structure; record coverage. (AD-21)
2. *DISC-1* per chunk, in parallel: claims with source pointers (file, page, paragraph, timestamp).
3. *DISC-2* merge: dedupe, detect contradictions (chat beats documents; document vs document becomes a resolution card), report coverage ("read 100%, 214 requirements found").
4. *DISC-3* classify: category in free text, candidate Capability Modules, **apps in scope** (AD-22: default customer + admin, admin as a responsive Expo app; partner or rider apps are offered, never forced), size estimate, capability tier flags, unsupported-category detection (AD-06).
5. *Slot graph* [code]: universal slots with levels (`must` always asked, `should` asked in Enterprise and assumed in Guided, `may` always defaulted) and states (`empty`, `assumed`, `volunteered`, `confirmed`, `disputed`). A correction overwrites; a negation clears; an assumed value never replaces a confirmed one; an untouched slot is never reset.
6. *Gap detection* [code] plus *DISC-7* ambiguity scan (vague quantifiers, undefined actors, missing acceptance, conflicting statements). Elicitation is structured, not "think harder" (ClarifyCodeBench [V]).
7. *Interview loop*: one card per gap. A tap costs no model call; typed text goes through DISC-4; questions and chat through DISC-5. Card types: `choice3` (three options, one recommended, "something else", "you decide"), `multi` (checklist), `visual3` (three looks), `text`, `upload`, `confirm`. Universal cards come from templates; domain options are written by DISC-6 inside the card schema and validated (three options, one recommended, what each option sets).
8. *Feature Catalog* [code + DISC-3]: features per app grouped by module, each with status in / later / out / unsupported / needs-engineering, source ("your PRD p.4", "like X", "typical"), size (screens, entities, integrations) and capability tier. "Like [app]" requests become a reference list the user toggles; patterns only, never brand assets (IN-12).
9. *DISC-8* compile the Brief; open G1.

**Universal slot groups.** Business (what, why, goals, KPIs, scale, revenue model); Apps and people (apps, surfaces, roles, who administers); Scope (features per app, MVP vs later, must-not-haves, reference apps, size band); Experience (look direction or brand, tone, locales including RTL, device profile, accessibility target, dark mode); Access (login methods, onboarding, approval flows); Data and integrations (payments, maps, OTP, notifications, existing APIs, sample data, real vs mock); Constraints (offline, performance, low-end devices); Enterprise (Org Profile, approvers, existing design system, existing API catalog).

**Languages and input.** Chat follows the user's language (English, Hindi, Hinglish and others detected per turn). Voice dictation is accepted through the platform's speech-to-text; run-on dictation is segmented into atomic statements and echoed back before it is applied (IV-10). Spec keywords stay in English; app copy follows the chosen locales.

**Exit rules.** No `must` slot empty; or the user taps "Finish with smart defaults"; or a cap is reached (10 cards, 30 turns in Guided).

**`brief.json` outline.**
```yaml
meta: {program_id, depth_profile, locales[], device_profile, created_at}
business: {summary, goals[], kpis[], scale, revenue_model}              # each item: value + tier + provenance + confidence
apps: [{id: APP-CUS, name, surface: mobile|responsive|web, audience[], roles[]}]
feature_catalog: [{id: FTR-014, name, apps[], module, status, tier, source, size:{screens,entities,integrations}, capability_tier}]
experience: {look_direction, brand_assets[], tone, locales[], dark_mode, accessibility_target}
access: {auth_methods[], onboarding, approvals}
integrations: [{name, preference: real|mock|undecided}]
constraints: {offline, performance, devices}
references: [{label: "like X", features[]}]
assumptions: [{id, text, reason, toggle}]
open_questions: []
sources: [{id: SRC-001, kind, name, hash, coverage}]
ambiguity_flags: []
```

## 4. Stage 2: Research
**Purpose.** After G1, find what the Brief cannot know: comparable features and patterns, entity and lifecycle hints, brand facts, integration feasibility, mock-data realism. Research enriches; it never decides. Output: `research.json` (≤ 8k tokens), grouped by consumer. (Bounded parallel reading is safe; AD-02.)

**Jobs (generic, driven by the Brief).**
| Job | Finds | Feeds |
|---|---|---|
| Landscape | 3-6 comparable apps per app in scope, common and differentiating features | Blueprint (3a) |
| Patterns | navigation, key flows and screens, admin dashboard patterns | Blueprint (3b), Screens |
| Domain hints | typical entities, status lifecycles, roles, business rules, KPIs | Blueprint (3c) |
| Mock-data hints | realistic names, price ranges, currencies, units, regional conventions | Screens |
| Feasibility | official SDK facts, Expo compatibility, checked against the Stack Kit (the Stack Kit is authoritative) | Blueprint, capability flags |
| Brand | colors, fonts, tone from the user's own site, only if owned or confirmed | Design |
| Named references | public feature lists of apps the user named; patterns only | Blueprint, Design |

**Pipeline.** RSCH-1 plans queries within the depth budget (no fixed ceiling) -> search in parallel -> RSCH-2 extracts per page (no tools; the page is untrusted data) -> RSCH-3 synthesizes by consumer -> code cross-checks: every item has a source URL and retrieval date; high-impact items need two independent sources or are marked `unverified` -> size cap -> commit.

**Rules.** Research items are Default tier at most (never Locked, AD-04); suggestions arrive as toggles at G2 under "what research added"; long lists are ranked by prevalence with the tail shown as "also seen"; short paraphrase plus URL and date, no long quotes; queries use generic terms. **Failure is soft:** with search off, no results or budget exhausted, the Blueprint proceeds from module defaults and model knowledge flagged `assumed`, and G2 says so (RS-01).

**`research.json` outline.** `feature_candidates[]`, `pattern_notes[]`, `entity_lifecycle_hints[]`, `mock_data_hints[]`, `integration_facts[]`, `brand_facts[]`, `reference_features[]`, `unsupported_flags[]`; each item: claim, source URL, date, confidence, applies_to apps, recommendation (include / consider / skip), used_by (filled later for relevance metrics).

## 5. Stage 3: Blueprint
**Purpose.** Decide what the product is: roles, modules, journeys, structure, domain and contract. This is the largest stage and runs as three sequential sub-steps, each validated before the next. Output: `blueprint.json`.

- **3a Product (BLUE-1).** PRD summary; personas; roles and a permission-matrix skeleton; modules (library references plus flagged custom modules); final Scope Ledger (in / later / out) with MVP boundary; NFR targets (performance, device profile, offline, accessibility default WCAG 2.2 AA as a quality target); analytics event taxonomy defaults.
- **3b Structure (BLUE-2).** Journeys (steps, alternates, failure paths); **cross-app scenarios** (e.g. a customer action that changes what the admin or rider app shows); navigation per app (tabs, stacks, modals, deep links); screen inventory with id, module, role access and purpose; size check against the profile cap with automatic phase 1 / phase 2 split. Optional **G1.5** here.
- **3c Domain and contract (BLUE-3/4/5).** Entities (fields, types, relations, visibility per role); **lifecycles** (state machines with allowed transitions per role and timers); **rules** (id, plain text, formula where numeric, tier, `confirm` flag when a value is unknown); **operations** (Contract v0: id, method and path, parameters, request and response schemas, errors, pagination, auth, role gating); events (push or realtime); integrations (mock / adapter / real with provider options); mock-data plan (entity counts, locale, edge cases, relationships, imagery strategy).

**Validators (code).** Every screen reachable; every flow starts and ends on real screens; every operation role-gated or explicitly public (default deny); every status reachable and terminal states defined; every reference resolves; OpenAPI lint passes and examples validate; shared vocabulary is consistent across apps (entity names, role names, status names); screen count within budget. **Module use:** screens come from library recipes wherever a module covers them; deviations are recorded and cost extra validation.

**`blueprint.json` outline.** `product{prd, personas[], roles[], permission_matrix, modules[], scope_ledger[], nfr, analytics}`, `structure{journeys[], cross_app_scenarios[], navigation{app: ...}, screen_inventory[]}`, `domain{entities[], lifecycles[], rules[], operations[], events[], integrations[], mock_plan}`.

## 6. Stage 4: Design (AD-14)
**Purpose.** Give the front-end engine a design **direction and constraints**, not pixel layouts. Layout stays in the Open tier so the front-end engine can be beautiful; consistency comes from tokens and composition rules. Output: `design.json`, `tokens.dtcg.json`, `DESIGN.md`.

- **DSGN-1 Direction.** Reads brand assets or site (vision), tone, apps and surfaces. Produces three candidate directions that differ along defined axes (hue family, shape language, density, typographic personality) to avoid the generic-template failure mode that UI-Bench observed [S]. In Express, the recommended direction is chosen automatically; otherwise a `visual3` card shows style tiles rendered from tokens.
- **DSGN-2 Tokens and rules.** Intent-level tokens (color roles for brand, neutral, semantic and surface, text and border, light and dark; type scale with font stacks including Indic/Arabic fallbacks; spacing scale; radii; elevation; opacity; motion durations and easing; breakpoints for phone, tablet and web; minimum touch targets), component variants over the Stack Kit components, and **composition rules per screen type** (list, detail, form, dashboard, auth, onboarding, settings, search, chat, checkout, map; admin density rules). Anti-patterns (no raw color values in screens, one primary action per screen, at most two font families).
- **DSGN-3 Voice and policy.** Tone and microcopy patterns (errors, empty states, confirmations); imagery policy (placeholder strategy, no hotlinked or copyrighted images); icon policy (allowlisted icon set only); motion principles with reduce-motion; accessibility rules (contrast, targets, dynamic type to 200%, focus order, labels); **platform fidelity** setting: brand-consistent by default, platform-adaptive optional (for example iOS 26 Liquid Glass via `expo-glass-effect` with capability checks and fallbacks [S]).
- **Validators.** Token schema valid (W3C DTCG 2025.10 [V]); text contrast ≥ 4.5:1 and large text/UI ≥ 3:1 in both themes, adjusting tokens while keeping hue if a brand color fails; every token reference resolves; scales monotonic; dark/light parity.
- **Export.** `tokens.dtcg.json` is emitted mechanically from `design.json`; `DESIGN.md` is a portable design-system file for coding agents (the same idea as Google Stitch's DESIGN.md [S]).

## 7. Stage 5: Screens
**Purpose.** Write every screen so a small model can implement it without asking questions. Inputs are frozen: inventory, entities, operations and rules from the Blueprint; tokens, variants and composition rules from Design. Because shared decisions are frozen, screen batches can be written **in parallel** (AD-02).

- **SCRN-1** (3-4 screens per call, grouped by module for coherence): purpose; role access; ordered sections, each with a kit component, data bindings (entity fields or operations), actions and their results (navigate, mutate, modal), validation rules; all states (loading, empty, error, offline, no-permission, long-content, plus domain states); copy keys; accessibility notes; responsive notes (phone, tablet, web); analytics events; acceptance criteria (GIVEN/WHEN/THEN); mock-seed needs; a **complexity score** and model-class hint (rubric in doc 04). Tiers: purpose and bindings Locked or Default; section order Default; visual layout Open.
- **SCRN-2** global behaviors (auth and session, error taxonomy, permission prompts, deep links, push mapping, offline policy) and the copy store (keys to text per locale).
- **Validators per screen.** Bindings and actions resolve; mandatory states present (SC-05); copy keys exist; only tokens referenced; seeds satisfy entity constraints; slice within its token budget.
- **Output.** `screens.json`.

## 8. Stage 6: Verify and package
**Purpose.** Prove the spec is sufficient and consistent, then package it for people and for engines. Output: `APP_SPEC.md` plus the package (doc 04).

1. Full graph validators (doc 07 lists them).
2. *VRFY-1* critic from a **different vendor** than the writer: spec vs Brief, contradictions, missing flows, cross-app consistency.
3. *VRFY-2* sufficiency probe on each slice with a deliberately weak canary model: it must produce an implementation outline or a list of questions; any blocking question is a **gap**.
4. Gap resolution [code]: fill with a pattern default (Default tier, marked assumed, appears under "We assumed" at G2); ask the user only for must-confirm items (VF-01).
5. *VRFY-3* repair from validator, critic and probe findings: at most 2 rounds; if a node still fails, replace it with the simplest valid pattern, mark it `degraded` and show it at G2 (VF-02); structural problems raise `needs_decision` back to the owning stage.
6. Package [code]: finalize IDs and references; build slices; **Build Plan DAG**; golden journeys; Scope Ledger snapshot; view data for the review surfaces; exports (BRD/SRS, traceability matrix); render `APP_SPEC.md` deterministically (stable order, canonical serialization) and compute its hash.
7. *VRFY-4* plain-language plan summary; open G2.

## 9. `APP_SPEC.md` (the single file)
Markdown for people with fenced YAML blocks that carry an id and kind (for example `yaml id=SCR-014 kind=screen`). Sections: 0 Header (program, version, hash, baseline, Stack Kit version, profile) · 1 Summary (plain-language PRD) · 2 Program and apps · 3 Scope Ledger · 4 Roles and permissions · 5 Design system · 6 Navigation · 7 Domain model (entities, lifecycles, rules) · 8 Contract (operations, events, errors) · 9 Integrations · 10 Global behavior · 11 Screens · 12 Flows and scenarios (golden journeys) · 13 Build plan · 14 Acceptance and traceability · 15 Assumptions, sources, deviations, revision history. The front-end engine never loads the file whole; it reads slices through the Spec Server (doc 04). Estimated size for 100 screens across two apps: 100-150k tokens, because screens carry states, copy and acceptance; humans read views (doc 06), not the raw file.

## 10. Worked example (illustration only; no category is hardcoded)
A fictional city food-ordering program, "Dinewise", with a customer app and a restaurant-and-admin app.

**Stage 1.** The owner uploads a 40-page PRD and says "like the big delivery apps, plus an app for restaurants". DISC-1/2 extract 187 requirements with page pointers; DISC-3 proposes apps `APP-CUS` (mobile) and `APP-RST` (responsive; assumed), flags live tracking and payments as Tier B. The Feature Catalog:
```yaml
- {id: FTR-014, name: Live order tracking, apps: [APP-CUS], status: in,    tier: Locked,  source: PRD p.12, capability_tier: B}
- {id: FTR-031, name: Group ordering,      apps: [APP-CUS], status: later, tier: Default, source: research R-007, reason: "common elsewhere, not requested"}
- {id: FTR-052, name: In-app games,        status: unsupported, reason: category declined, alternative: loyalty rewards}
```
**Stage 2.** `R-007`: "group ordering appears in 3 of 5 comparable apps" with URLs and dates; mock-data hint: realistic price range and tax conventions for the region.
**Stage 3.** Entity `Order` with lifecycle `placed -> accepted -> preparing -> ready -> picked_up -> delivered`, side transitions `rejected` (restaurant, with reason), `cancelled` (customer until accepted). Rule `RUL-012: customer may cancel until accepted`. Rule `RUL-020: delivery fee = base + distance band` with `confirm: true` because the PRD gives no table. Operation `OP-placeOrder` (POST, customer role, idempotency key, errors 409 closed-restaurant). Cross-app scenario `SCN-001`: customer places order -> restaurant app shows it -> restaurant accepts -> customer app status changes.
**Stage 4.** Three directions (warm-vibrant / clean-minimal / bold-editorial) as style tiles; the user picks one at G1 or the recommended is used; tokens such as `color.brand.primary`, `color.surface.card`, `radius.card`, `space.4`; admin density rule: table on tablet/web, cards on phone.
**Stage 5.** `SCR-021 Restaurant detail` (the slice shown in doc 04 section 4) with all states and acceptance criteria; complexity score M.
**Stage 6.** The canary asks: "What counts as 'busy'?" -> resolved by a Default rule `RUL-021: busy when more than 15 active orders`, shown under "We assumed" at G2. The critic (other vendor) finds that the rider app mentioned in the PRD has no inventory entry -> repair adds the entity `Delivery` and an operation. Package built: 94 screens, 41 operations, 6 golden journeys, 212 tasks in 7 waves.
**Preview.** At G3 the user opens `SCR-021`, clicks the menu section, the inspector shows `SCR-021.menu`, provenance "research R-014, Default", and asks for a veg-only toggle: change set `CS-0071` (lane L2) is applied in under a minute (doc 05). The user later asks for group ordering: it is `later`, so the Scope Change Protocol shows +3 screens, +1 entity, +1 operation and offers add-now / Phase 2 / simpler alternative.
**G3 and backend.** The Behavior Book lists `RUL-020` under "confirm"; the user answers it. After approval the backend is generated; it is accepted only when OpenAPI conformance passes and all six golden journeys replay with the same observable outcomes as in the preview.
