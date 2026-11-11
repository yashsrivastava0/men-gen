# 12. The four-agent pipeline (v3.2)

- **Status:** active, created 2026-11-10. Amends AD-02 (six stages), AD-27 (where probes run) and invariant I-1 (speculative work). Everything not named here stays as in docs 01-11.
- **Owner directive D15 (2026-11-10, voice, lightly cleaned):** there are too many agents; aim for four, five at most. They exist only so the front-end engine can generate the whole app, so what it receives must be correct, detailed, sufficient and easy to consume, for big apps and for Android and iOS. Background and parallel execution are welcome. Decide on your own logic.
- **Update 2026-11-11 (AD-44 to AD-48):** Research is now **foreground** after Gate 1 (no waves A and B, no read at G1 + 90 s), its depth comes from the Brief's content (research load), not from the tier (AD-49), and Spec starts from Compass's approved structure. Where §1, §4.2, §7, §8 and §11 below say otherwise, [doc 13](13-research-agent-design.md) and this note win.
- **Decisions:** AD-35 four agents · AD-36 background, parallel and speculative execution · AD-37 builder-needs contract · AD-38 the agent-boundary test · AD-39 to AD-43 the Research design, in [doc 13](13-research-agent-design.md).
- Evidence labels as in doc 00. Time and cost numbers are estimates **[J]** until the first end-to-end run.

## 1. The result in one picture
```
 idea -> [1 COMPASS] -- interview --> Brief + intent-level structure --> G1 (approved, hash-bound)
                                                                              |
                                                                              v
                                       [2 RESEARCH]  foreground, visible, time-boxed (Skip allowed)
                                                                              |  -> research.json
                                                                              v
              +-- Product lane  (roles, journeys, data, rules, contract) --+
 [3 SPEC] ----+                                                            +--> FREEZE --> Screens lane (parallel batches) --> Spec Graph
              +-- Design lane   (look, tokens, composition rules, voice) --+
                                          | structure critic at FREEZE, slice probes per batch (streamed)
                                          v
 [4 VERIFY AND PACKAGE]  builder-needs check, repair, bundle --> APP_SPEC.md + package --> G2 --> Front-end Engine
```
Six stages became four agents. Blueprint, Design and Screens are now three **lanes** of one agent (Spec). Nothing the front-end engine receives was removed; only handoffs between steps that share one graph were.

## 2. Terms
| Term | Meaning |
|---|---|
| Engine | A top-level system: Context, Front-end, Backend, QA (AD-01) |
| Agent | A unit inside an engine with its own folder (`agents/<name>/`, AD-33), prompts, contract, evals and **one output file** |
| Lane | A group of steps inside an agent that runs beside another lane |
| Step | One schema-checked model call or code function with its own validators. Call ids (doc 02 §4.1) and validator ids (doc 07 §2) are unchanged |

Earlier docs say "six stage-engines": read each of Blueprint, Design and Screens as a lane of the Spec agent and Verify and package as the Verify agent.

## 3. Why these four (AD-38: the agent-boundary test)
A boundary costs a handoff (serialize, validate, probe, gate, re-read) and adds a failure point. It earns its place only if at least one of these holds:
- **T1 Different tools or trust**: web access versus none.
- **T2 Independence**: the checker must not share the writer's context or vendor.
- **T3 A user dialogue or gate** sits there.
- **T4 Different time**: it can run in the background or before its input is final.

| Stage in v3.1 | T1 | T2 | T3 | T4 | Verdict |
|---|---|---|---|---|---|
| Discovery (Compass) | | | yes: dialogue, G1 | | **Agent** |
| Research | yes: web | | | no (v2: runs after G1; foreground) | **Agent** (T1 alone) |
| Blueprint | | | optional G1.5 | | Product **lane** |
| Design | | | | partly: needs the Brief and Research, not the Blueprint | Design **lane**, beside Product |
| Screens | | | | no: must wait for the freeze | Screens **lane** |
| Verify and package | | yes | G2 | yes: streams while Screens run | **Agent** |

Blueprint, Design and Screens write the same Spec Graph, make coupled decisions, and are separated only by a freeze point. A parallel-writing risk exists only before the freeze, so the lanes share one orchestrator and one graph (parallel reading is safe; parallel writing against moving decisions is not [S, doc 10 R-43]; Anthropic's compiler run: coupled tasks serialize [V, R-14]).
**Why four and not five.** Any fifth boundary (for example Blueprint apart from Design plus Screens) would be a handoff with no T1-T4 reason, and Design must not wait for the Blueprint. The merge is reversible: each lane keeps its own call specs, validators, views and evals, so a lane can be split out later if its evals demand a different model class or team.
Every future agent (change controller, module factory and so on) must pass the same test or be a step.

## 4. The four agents

### 4.1 Compass (Discovery): built
**Does:** the user dialogue; produces `brief.json`, consumer views, `research_requests`, assumptions, handoff probes (doc 11). **Changes from v3.1:** (a) *(superseded 2026-11-11, AD-45)* Research no longer starts after the first batch; the orchestrator starts it on `gate1.approved`, and Compass owes it trust flags (doc 13 §14, CR-1 to CR-3); (a2) Compass now also drafts an approved intent-level structure (modules, screens, flows, entities; see [notes/compass-as-built-2026-11-10.md](../notes/compass-as-built-2026-11-10.md)); (b) the Gate 1 "Blueprint planner" and "Design planner" canaries now stand for the Spec agent's Product and Design lanes. Hardening items stay in `STATE.md`.

### 4.2 Research (new; foreground after Gate 1)
**Full design: [doc 13](13-research-agent-design.md) v3** (AD-39 and AD-41 to AD-43 kept in part; AD-45 to AD-52). This section is the summary; where it differs from doc 13, doc 13 wins.
**Purpose.** Bring in what the user cannot tell us and the model must not guess: named products, regional providers, fees and rules, data shapes, integration specifics. It enriches and never decides: items are Default tier at most (I-6).
**When and how.** It starts when **Gate 1 is approved** and runs visibly (live events, time box, Skip, cancel a topic); Spec starts when it commits. It reads Compass's `research` view plus trust flags. **It is never told the tier:** it computes a **research load** from the Brief's content (focused, standard, deep) that sets **ceilings** (searches 20 / 40 / 80, pages 24 / 60 / 140, time 3 / 6 / 10 min), and stops each target as soon as its typed slots are filled.
**Method.** Code orchestrates (no free-running agent loop): a planner makes targets with slot schemas; **one worker per target runs in parallel with isolated context** (2 or 3 query variants merged by rank fusion, search, triage, passage reading, typed extraction, quote check); a coverage audit drives round 2 and at most round 3 for unfilled required slots and newly discovered entities; evidence-aware stopping.
**Jobs (eight, organized by what Spec must decide; AD-53):** features, structure (screens, navigation, roles), flows, data and rules, services, design, screens, local. For each: how it is usually done, how common each choice is, and what is current, plus the idea's own details.
**Output:** `research.json` v4, a **playbook per Spec lane**, **indexed by the Brief's ids**, with slot-typed records, computed `norms` (prevalence and recency), `suggestions` (never scope), `confirm` list, and code-rendered digests (blueprint, integrations, design, screens, mock, verify), sized by load class (soft: about 12k, 20k, 32k tokens).
**Fails soft (RS-01).** No search, no results, time box or Skip: Spec proceeds on module defaults flagged `assumed`; Verify records "less research-backed".
**Gate (doc 13 §14):** quote check 100%; high-impact items corroborated or flagged; zero category-only queries; required slots filled at least 70% on the real inputs; events complete and resumable; injection pass; fail-soft verified.

### 4.3 Spec (new; merges Blueprint, Design and Screens)
**Purpose.** Decide what the product is, how it looks, and write every screen so a small model can build it without asking. One agent, one orchestrator, one Spec Graph commit; every lane writes through the same validators.

| Lane | Steps | Reads | Writes |
|---|---|---|---|
| **Product** | BLUE-1 product → BLUE-2 structure → BLUE-3 domain; then BLUE-4 contract ‖ BLUE-5 mock plan, integrations, NFR. **Starts from Compass's approved structure as a proposal** (Default tier unless a verified fact supports it) and refines it with a recorded reason (AD-44) | Brief (Blueprint view), Compass structure, research (Product slice), Module Library | personas, roles and permission matrix, Scope Ledger, journeys, navigation, screen inventory, entities, lifecycles, rules, Contract v0, mock plan |
| **Design** | DSGN-1 direction → DSGN-2 tokens and composition rules → DSGN-3 voice and policy | Brief (Design view), research (look norms, brand facts), app list and surfaces | `design.json`, `tokens.dtcg.json`, `DESIGN.md` |
| **Screens** | SCRN-1 batches (3-4 screens per call) in parallel, SCRN-2 global behaviors and copy store | frozen graph slice per batch, tokens, composition rules | one record per screen, copy keys, acceptance criteria, complexity score |

**Run order.** Product ‖ Design → **join**: code runs V-01..V-11, V-16, V-12, V-19 on the graph; the composition rules are matched to the screen types in the inventory by code → **Freeze** (roles, entities, operations, inventory, navigation, tokens, composition rules and the copy approach become read-only for the Screens lane; a Screens step that finds a gap raises `needs_decision` to the Product lane, at most 2 loops) → Screens batches in parallel → commit. Optional policy gate **G1.5** sits at Freeze.
**Tier names (2026-11-11):** Compass's tiers map to the profiles used by Spec and Verify as **Quick = Express (small band), Standard = Standard (medium), Thorough = Large (large, shown as "Enterprise")**. Research does **not** use the tier: it derives its own load class (focused, standard, deep) from the Brief's content (doc 13 §3.3).
**Depth profiles (doc 03 §1 unchanged).** Express: BLUE-3/4/5 collapse into one call, the recommended look is chosen automatically, Screens in one or two batches. Large: after a program skeleton call, the Product lane runs per module in parallel.
**Never:** invent Locked nodes; read the transcript or raw web pages; mark a research suggestion `in` scope; hardcode a category (modules and recipes carry what varies).
**Failure ladder per step:** retry → repair → deterministic default (module recipe, design preset) → node marked `degraded`, shown at the next gate. The Design lane always has a preset fallback, so it never blocks.
**Output:** the graph commit (`spec.json`) plus lane views (blueprint, design and screens views) for review surfaces and debugging.
**Gate:** the union of the stage 3, 4 and 5 gates in doc 07 §7, evaluated per lane.

### 4.4 Verify and package (new shape; independent)
**Purpose.** Prove the package is enough, consistent and easy to consume; then bundle it. It is a separate agent because the checker must not share the writer's context or vendor (T2).
**Runs as a stream, not a last step:**
1. **At Freeze:** structure critic (VRFY-1, other vendor than the writer) on Product + Design against the Brief. Structural errors are found **before** the expensive Screens fan-out.
2. **Per screen batch:** validators V-06, V-07, V-13, V-14, V-15, V-22, V-23 plus the canary sufficiency probe (VRFY-2, weakest intended model, low effort).
3. **Final:** full V-01..V-23, critic on screens against rules, **builder-needs coverage matrix** (§5), gap resolution (pattern default, else must-confirm question), repair (VRFY-3, at most 2 rounds, else simplest valid pattern marked `degraded`), then package.
**Package (doc 04 §3, unchanged):** `APP_SPEC.md` (the one file), slices, `spec.json`, tokens, `DESIGN.md`, Contract v0, fixtures, scenarios, Build Plan DAG, `AGENTS.md`, `manifest.json`, views, exports. The front-end engine reads **slices** through the Spec Server; an Express app (12 screens or fewer) is small enough to read whole.
**Gate G2** unchanged (Plan View, hash-bound approval). **A package with any builder need `missing` never reaches G2.**

## 5. The builder-needs contract (AD-37)
**The thought experiment.** If the only input were "build me a mobile app", the builder would have to ask the questions below. The Context Engine exists so that **none reaches the builder unanswered**. Compass answers what the user knows; Research brings what the world knows; Spec decides; Verify proves. This is the same consumer-driven idea as the Brief (AD-28), applied to the whole chain.

| # | The builder needs to know | Produced by | Proven by |
|---|---|---|---|
| BN-01 | What it is, who uses it, which apps and roles | Compass → Spec Product | Brief probes; V-08; critic |
| BN-02 | What to build first: features in / later / out, intent and priority | Compass + Research landscape → Spec Product | V-16, V-19; critic |
| BN-03 | Which screens exist and how a user moves between them (entry points, deep links, role access) | Spec Product | V-04, V-05, V-07 |
| BN-04 | The data: entities, fields, relations, statuses, who sees what | Spec Product (+ Research domain shape) | V-06, V-09, V-11 |
| BN-05 | The rules: business rules, formulas, validations, permissions, state changes; unknown numbers flagged `confirm` | Spec Product (+ Research norms) | V-08, V-09; `confirm` list goes to the G3 Behavior Book |
| BN-06 | The contract: operations the screens call, errors, pagination, auth; mock fixtures | Spec Product (Contract v0) | V-10, V-15 |
| BN-07 | What realistic data looks like: local names, prices, formats, edge cases | Research + Spec mock plan | V-15 |
| BN-08 | Look and feel: tokens, variants, density, composition rules, tone, microcopy, icon and image policy, accessibility, dark mode | Spec Design lane (+ Research look norms) | V-12 |
| BN-09 | Per screen: sections, bindings, actions, every state, copy, responsive notes, acceptance | Spec Screens lane | V-06, V-07, V-13, V-14, V-22; canary probe |
| BN-10 | What talks to the outside world (login, OTP, maps, payments, push) and what is mocked now | Compass flags + Research services → Spec | V-16; Integration Activation Checklist |
| BN-11 | What is allowed: Expo and React Native versions, packages, kit components, icons. One Expo codebase targets Android, iOS and web | Stack Kit (fixed data, not an agent) | V-23; Tier 0 header |
| BN-12 | Build order: waves, what runs in parallel, which files a task owns, interface freeze, model hints | Verify (code) | build-plan checks: acyclic, every screen in exactly one task, owned files disjoint |
| BN-13 | What "done" means: acceptance criteria, golden journeys, test data; which parts are Locked, Default or Open and where deviation is allowed | Spec + Verify | V-17; journey probe |
| BN-14 | What is unknown or assumed, what is degraded, and how to ask when stuck | Verify (package) + Spec Server `raise_decision` | assumptions register complete; `degraded` list |

**Coverage matrix.** Verify scores every BN as `satisfied` (evidence in the graph), `defaulted` (pattern or research default, flagged and shown at G2), `degraded` (simplest valid pattern after failed repair, flagged) or `missing`. `missing` blocks G2. The bar is measured **at the output**, not by counting agents, so merging lanes cannot lower it.
**Freedom.** The front-end engine is not bound where it should be free: visual layout stays Open tier, section order Default, purpose and bindings Locked or Default (doc 04 §2).

## 6. Thin input: what happens when Compass hands over little
The owner's first real sample (Lucknow car marketplace, 2026-11-10) shows why the chain must be the safety net. Compass captured: new and used cars, Hyundai and Tata showrooms, Lucknow only, three surfaces (buyer app, showroom listing screen, platform manager screen), five first-release features, showrooms pay per sale, buyers free. It did **not** capture: roles in detail, used-car condition fields (answered "yes"), look and tone, languages, login, how a "sale" is recorded, whether the owner runs the showrooms or a separate platform that charges them. The fill ladder, applied by Verify's coverage matrix (never inventing Locked):
1. **Brief** (the user's words) → 2. **Research** (sourced, Default) → 3. **module or pattern default** (Default, flagged assumed) → 4. **ask the user** only for must-confirm items (money, legal, identity, irreversible), through the G2 list or the G3 Behavior Book.

| Gap in the sample | Who fills it | How |
|---|---|---|
| Used-car condition and listing fields | Research (domain shape) → Product lane | field list from comparable products, Default, shown under "we assumed" |
| Who the users are, tenancy (one dealer group or many independent showrooms) | Product lane + critic | roles and a tenancy Default, flagged; critic checks it against "platform manager" and "pay per sale" |
| How "per sale" is known | Product lane | entities (enquiry, sale record, fee) and a rule with `confirm: true`, asked at G3 |
| Look, tone, languages | Design lane + Research local truth | recommended direction, locales Default (for example English with Hindi), flagged |
| Contact channels, login | Research services → Product lane | what comparable apps in the region use; mock-first with an Integration Activation Checklist entry |
| Features common elsewhere but not asked | Research landscape | `later` toggles, never `in` |
This table is the **shopping list and the mechanism**, not findings; Research confirms every fact with sources.

## 7. Speed plan
**Where the time goes (estimate [J], Standard app about 30 screens, about 30-60 s per model call as the Compass live runs show; some calls hit 90-150 s timeouts, so this is the main thing to measure):**
| | Six stages in a line | Four agents, Phase 1 |
|---|---|---|
| Research after G1 | about 3-4 min | **about 2 to 3 min (focused load), 4 to 6 (standard), 7 to 10 (deep)**, foreground, parallel workers (2026-11-11; no longer hidden) |
| Product (Blueprint) | about 4-5 min | about 4-5 min (the critical path) |
| Design | about 2 min | hidden (parallel with Product) |
| Screens | about 1-1.5 min | about 1-1.5 min (parallel batches) |
| Verify and package | about 3.5-4.5 min | about 2 min after the last batch (critic and probes already ran) |
| **After G1** | **about 15 min** | **about 9 to 17 min depending on research load** (the Research row is no longer hidden; recalibrate after the first run) |
The earlier 8-minute Standard target was optimistic for a line of six stages; with four agents it becomes realistic. Per-profile targets are recalibrated after the first end-to-end run.

**Phase 1 (designed in from the start):** Research in the foreground with parallel lanes (2026-11-11); Product ‖ Design; Screens fan-out after Freeze; Verify streaming; cache by input hash (AD-26); prompt caching of static prefixes.
**Phase 2 (after the chain works end to end; each a switch, not a rewrite; Research pre-planning at Brief draft is also a candidate, doc 13 §15):** (a) **speculative Product lane** starts at `brief_ready`, while the user reviews the Brief, and is reused only if the approved Brief hash matches or the diff touches unrelated sections; (b) **front-end foundation at Freeze**: scaffold, tokens, navigation shell, typed client, mock adapter and fixtures depend only on frozen shared decisions, so they can start before Screens and Verify finish, in a sandbox that is discarded if the baseline changes (the Build Plan already puts these in waves W0-W1).
**Speculation rule (AD-36; amends I-1).** *No stage or engine may commit to the Spec Graph, create a baseline or show something as approved before its gate is approved. Read-only or sandboxed work may start earlier if it is keyed by input hash and discarded or reconciled when the approved hash differs.* Silence still never approves (I-12). Cost of a miss is bounded (a pivot wastes a few searches or one lane).
**Deliberately not parallel:** Screens before Freeze; Verify's final pass before every batch exists; anything that writes shared decisions (roles, entities, operations, tokens).

## 8. Contracts between the agents
| From → To | Artifact | Rule |
|---|---|---|
| Compass → Research | `research` view with `guidance`, `research_requests`, `search_hints`, plus trust flags (doc 13 §14) | trigger: `gate1.approved` (Brief hash); the orchestrator relays, agents never import each other |
| Compass → Spec | `brief.json`, views and the **approved intent-level structure** (modules, screens, flows, entities), approved at G1 | bound to the Brief hash; Gate 1 probes already ran; the structure is a proposal that the Product lane refines (AD-44) |
| Research → Spec | `research.json` (v2) | read at commit (or `skipped`); later findings follow doc 13 R7 (G2 toggles or change requests), never lane L1 |
| Spec → Verify | graph commits per step and per screen batch | Verify streams on commits; Freeze is an event |
| Verify → Front-end | the package through the Spec Server | `APP_SPEC.md` hash bound at G2; every BN not `missing` |
**Handoff probes (AD-27, amended).** Kept at the Compass → Research and Compass → Spec boundary (already built) and at the end (Verify's sufficiency probe on the slices the front-end will receive). **Inside Spec** the lane-to-lane checks are deterministic code validators, which are stricter and cheaper than an LLM canary.

## 9. What changes in the earlier documents
| Document | Change |
|---|---|
| 00 | AD-35..AD-38 added; AD-02, AD-27 amended; OI-11..OI-13 |
| 02 | "Context Engine, 6 stages" reads "4 agents"; call catalog unchanged |
| 03 | Stage list and diagram: stages 3-5 are Spec lanes, stage 6 is the Verify agent; steps, validators, gates and artifacts below remain valid as lane content |
| 04 | Build Plan, slices, G3 manifest unchanged; the Build Plan is produced by the Verify agent (code) |
| 05 | Invariant I-1 amended (speculative work) |
| 07 | §7 per-stage gates: rows 3-5 apply as Spec lane gates; row 6 is the Verify agent gate |
| 09 | Roadmap steps 5-7 become one agent built in three phases (below) |
| 13 | New: the Research agent in full (AD-39 to AD-43) |
| atlas | `architecture-atlas.html` refreshed to v3.2 on 2026-11-10 |

## 10. Build order (for the builder prompts)
Compass hardening and Phase 8 continue in parallel. Then: **Backbone** (Spec Graph v3, validator framework, schema registry; roadmap step 1) → **Research agent** (`agents/research`) → **Spec agent** (`agents/spec`, in three build phases that share one orchestrator: Product lane with Module Library v1 and Contract v0; Design lane; Screens lane with join and Freeze) → **Verify agent** (`agents/verify`: validators, critic, probe, repair, packager, Build Plan, renderer) → Spec Server and cross-cutting services → Front-end Engine design. **Honest note:** merging lanes removes run-time handoffs, gates and artifacts; it does **not** remove build work, because all three lanes still have to be written and evaluated.

## 11. Risks and how they are handled
| Risk | Handling |
|---|---|
| Spec becomes one big, hard-to-debug agent | Lanes keep separate call specs, validators, per-step checkpoints (resumable), per-lane evals and views; code controls the flow |
| A lane failure stalls everything | Failure ladder per step; deterministic fallbacks (module recipes, design presets); `degraded` is flagged, never hidden |
| Loss of the Blueprint → Design → Screens handoff probes | Replaced by code validators at the join and Freeze, a structure critic at Freeze, and the end-to-end sufficiency probe on real slices |
| Context pollution inside one agent | AD-20 holds: every call receives only a view assembled by code, never the whole graph |
| Research on the critical path (v2, foreground) | Parallel lanes, tier time boxes, Skip, resumable run; Phase 2 switches measured first (doc 13 §15) |
| Thin Compass Briefs (§6) | Fill ladder plus coverage matrix; `missing` blocks G2 |
| Model latency (150 s timeouts seen in Compass live runs) | Measure per call in the first end-to-end run; effort and model-class tuning is Phase 2 (a) |

## 12. Open items
| ID | Item | My default |
|---|---|---|
| OI-11 | Search provider for Research | Decided in principle (AD-48, doc 13 §7): one provider at a time behind a port, Tavily default, Brave alternative, set by a bake-off. Owner approves the live spend and the accounts |
| OI-12 | Phase 2 speculation (Product lane at `brief_ready`; front-end foundation at Freeze) | Off until the chain works end to end; then measure hit rate before switching on |
| OI-13 | Per-profile time targets | Recalibrate after the first end-to-end run |
