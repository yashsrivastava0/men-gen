# 19. Result first: big apps, the Verify agent for deep, the Spec Bench and the hard budget cap (v1)

- **Status:** active, written 2026-11-14. **Proposed** until the gates of [doc 16](16-spec-proof-plan.md) (plus PS-11 and PS-12 below) pass. Decisions **AD-92 to AD-95** ([doc 00](00-index-and-decisions.md)). It **extends [doc 18](18-spec-final-architecture.md)**: where doc 19 differs, **doc 19 wins**; then doc 18, doc 17, doc 16, doc 15.
- **Owner directions (2026-11-14, voice, lightly cleaned; I am the sole authority):**
  - "Don't worry about the cost too much ... **Result is the main thing. What output they give to the front-end engine, that is the main thing.**"
  - "Give the detailed prompt ... my other Claude Code will be implementing it completely end to end ... it will be a long-running task ... he should not ask me any question."
  - "We'll be creating a **mock of a test static front end** for it. Understand the user journey ... it should **auto select the input** and the output of the previous engines. Auto select it, or Claude Code can also help when testing."
  - "**It will be separate from the main flow, a separate engine currently** ... same as the front end ... in the future we'll integrate it."
  - "The Haiku model will be used ... the cap is **max USD 5. No exceeding over the 5**, it will stop in any way possible."
  - "**Focus also on the big one. The big one is more important.** Detailed and correct ... focus on overall all the things."
  - "He can also do a little bit based on his reasoning, understanding and research."
- **Not changed:** the freedom contract, the Spec Pack, the static guarantees G-1 to G-8, the free-tools policy, model and provider independence, the Research and Compass contracts, "live is one switch, `LLM_MODE=live`".

## 1. Result first: what changes from doc 18

Doc 18 traded some result for cost and latency because the owner had asked for lean small and medium apps. The owner now says result comes first and big apps matter most. So:

| Doc 18 said | Now |
|---|---|
| No Verify agent now; reserved for deep, **not built** | **The Verify agent is built now, for deep apps** (§3). Compact: off. Standard: configurable (`verify.profiles`, default `["deep"]`), because small and medium keep their no-latency path |
| Un-fuse a call kind below 85% first-try node pass | **Below 90%**; for deep no fusion beyond the split forms of docs 15 and 17 unless the proof shows 90% or better for that kind |
| B3: `busy` and `extreme` made by code from 12 to 20 hero records | The **model writes every record that appears in a list or first-view screen** (deep: up to 30 per primary entity); code only multiplies for scroll volume and builds `empty`, `extreme`, `broken`, `offline`; the owner A/B of PS-10 stays |
| Three directions for standard, one for compact | **Three for standard and deep**, one for compact |
| Cost estimates drive design | Cost is a **hard cap only** (§5). Where quality and cost or speed conflict, quality wins; a deep run may take 12 to 30 minutes |
| Canary out of the delivery path | Out for compact and standard; **in the delivery path for deep** (a canary per task card) |
| OI-40: big apps without an independent check | **Resolved**: deep apps get the Verify agent |

Everything else in doc 18 stands (three code gates and every validator as code for all sizes; the repair ladder; the portable call contract; free local tools; the static pack).

## 2. The big-app playbook (deep)

**2.1 What a big app is.** The user's tier is Thorough, or content raises the profile to `deep` (doc 15 §4.1: two or more apps, sensitive data, three or more integrations, three or more locales or a right-to-left script, offline-first use, four or more apps or eight or more modules). Typical: a **program** of two to six apps (customer, courier or driver, vendor, one or more admin portals), **50 to 60 screens per app** at the front end's per-run capacity (`scope_cap_per_app_phase`, default 60, configuration), so **110 to 175 screens in a program**; 11 or more modules; 20 or more entities with 8 to 15 fields; 10 to 20 integrations; three or more locales; 10 to 20 roles; web portals next to phone apps.

**2.2 What deep adds** (doc 17 §7, made concrete): the Product lane = skeleton, one call per module, per-app shells and a **compliance pass** (data protection, retention, consent, from Research's platform and trust views); the Design lane = three directions, dark-mode parity and script-fallback checks, **per-role variants** (consumer against admin); cards with at least 8 scenarios including permission-denied and conflict; Contract v0 with idempotency, concurrency, webhooks and versioning notes; 100 or more stories including admin and operations; journeys including first-run, returning, cross-app and recovery; blocks with depth variants and cross-app blocks; needs and asset needs with sourcing and licence rules; motion intents per role; all six mock profiles, realtime scripts, cross-app coherence, locale packs, extreme-data performance checks; all behaviour categories plus localisation depth and performance targets per screen class; an analytics plan with a measure per role; a store-listing kit; a QA test-data matrix; COULD up to 30 per app, each with a basis.

**2.3 Program structure.**
- **One Spec run per program**; one G1, one G1.5 and one G2 per program; the scope cap and the Phase 1 / Phase 2 partition are **per app** (doc 15 §4.5).
- **Shared modules** (account, notifications, payments, help, legal) are specified **once** and referenced by every app that needs them; a shared screen keeps one id and is counted once per app that includes it; role differences are regions and actions by role, not copies.
- **One data model and one contract for the program** (one OpenAPI with per-app tags); an entity has one owner module and the same fields in every app (V-11 checks vocabulary across apps).
- **Cross-app coherence of mock data:** the hero skeleton gives one order one id and one timeline in the customer, courier, restaurant and admin apps; the hero calls for related entities run together (doc 18 §5).
- **Packs:** `global/` and `contract/` shared; `apps/<APP>/` per app with its modules and screens; the build plan has a **program-level interface freeze** (shared types, token names, store-slice shapes, route maps per app) and per-app waves.

**2.4 Scale mechanics.** A program pack is 500k tokens and up and is **never read whole** by anyone. Task cards (at most `task_read_budget`, 28k including the front end's allowance) are the only way in; waves W0 to W7 as in doc 15 §5.13: foundation, shared, **pilot slice (W2, 3 to 5 screens per app, one task each)**, canary fan-out, polish; an interface freeze after the foundation wave; disjoint file ownership; `parallelism_hint = min(wave size, k_fe)` from the front-end consumer's own profile; heavy cards move bulky detail to `screens/<id>.detail.md`; the packager splits an over-budget task and fails the build only on a breach it cannot re-shard.

**2.5 Spec's own run mechanics for big programs.** About 100 calls per 60-screen app without Verify (about 130 with it), a program of four apps about 2.3 times that; checkpoints per batch (program, input hash, step, batch, versions, route fingerprint); resume with identical bytes; a single-flight lock per program; concurrency computed from the profile and adapted on rate limits (doc 15 §10.3); a rate-limit storm ends the wave `partial` and resumes; progress events stream the whole time; estimates and time are measured, not promised (a run of 12 to 30 minutes is normal).

**2.6 Big-app quality rules** (extra to the validators):
1. **Program-wide coverage:** every role, entity and flow has a screen in some app; a role with no home screen is an error.
2. **Cross-app coherence:** same entity, same fields and statuses everywhere; the same hero ids; the same copy terms (the glossary spans apps).
3. **Role-variant screens:** one id, regions and actions by role; a screen split by role only when the purposes differ (doc 15 §4.2 rule 4).
4. **Web portals** (admin, vendor back office) are exempt from the column limit of V-37 and keep the rest.
5. **G1.5 is a hard gate** for deep: the phase split with the pull-in rule is shown and consent recorded (the Thorough export's Dispatch module has 0 core and 19 extended screens and must still land in Phase 1).
6. **Phase 2** features keep outlines in the ledger with no screen nodes; starting Phase 2 regenerates incrementally.
7. **Verify** (§3) runs; a deep pack never says `checked_by: self_check` alone.

**2.7 Fixtures for big** (the builder's generator, doc 16 §6): the real Thorough export; a synthetic **Uber Eats-class program** of four apps (about 140 screens: customer about 52, courier about 23, restaurant about 28, admin portal about 40); a synthetic single app of 60 screens with entities of 8 to 15 fields and checkout-class screens binding five operations; an ERP with hundreds of entities; twenty integrations; 90 core screens; global-only providers; a partial research store; five locales including a right-to-left one.

**2.8 Acceptance for big (PS-12):** (a) on the 60-screen synthetic app and on the real Thorough export, every task card is within `task_read_budget` with no unrepaired breach, every validator is green or flagged with a reason the owner can read, and the prefix bundle is identical across tasks; (b) Thorough: dispatch is in Phase 1, zero Locked nodes without evidence, canary blocking questions at most 0.3 per screen on average [J]; (c) a run killed at wave k resumes and ends with the same bytes as an uninterrupted one; (d) the cross-app coherence tests pass on the Uber Eats-class program; (e) Verify finds at least 90% of the 22 structural planted defects (80% per kind) and its meaning-defect catch is reported against the self-check's; (f) measured cost and time are within 1.5 times the estimate recomputed from the recorded profile.

## 3. The Verify agent for deep (built now; doc 15 §9 made concrete)

**3.1 Boundary.** `agents/verify` (import name `mde_verify_agent`) imports `packages/spec-core` and the gateway only; Spec and Verify import each other never (AD-33). They meet through a **`VerifyPort`** defined in `spec-core`; the composition root of the standalone app injects the Verify implementation. A profile without Verify skips the port.

**3.2 What it does (it only checks; it never writes into the graph or the pack):**
1. **Structure critic at the Freeze** (`standard` route; a different vendor if configured, else `critic_vendor: same` is stated): the Brief against the frozen structure: contradictions, missing flows, unreachable or dead-end screens, role and entity mismatches, cross-app disagreements, a Locked fact dropped or changed.
2. **Validators per batch** (the same code as the self-check; Verify re-runs them, it does not own different ones).
3. **A canary per task card** (the weakest configured consumer profile; it reads exactly the task card's reads and returns `{outline[], blocking_questions[], assumptions[], acceptance_coverage[{criterion, satisfiable, how}]}`; not code). Deep: **every card**. Standard with Verify on: every card; compact: never.
4. **Final critic** with the coverage matrix and the freedom checks: spec against Brief, contradictions across apps, the confirm list's ranking, what differs from the Brief.
5. **G2 summary** (the plain-language paragraph and tables, as in doc 18).

**3.3 Repair.** Each defect carries a class: **A** code fix (Spec applies, at most 3 passes, must converge), **B** lane call (Spec re-writes the node; counts as a round), **C** needs decision (routed to Product; counts inside the limit). Verify returns **structured patches** `{node, field, change, reason}`; **Spec applies them** (single writer); touched nodes are re-verified by hash. **At most 2 LLM rounds across the Freeze and the final pass**; then the node is `degraded` and listed at G2. A node already re-asked under doc 18's ladder may take one Verify-driven repair if it has not used its Verify round.

**3.4 Independence, honestly.** Only one vendor may be configured: Verify still runs and the pack says `critic_vendor: same`. **A bigger or dearer route is not assumed to be a better critic** (Research measured the opposite, D-172): the critic's route is chosen by the PS-7 measurement. The canary uses the weakest consumer profile.

**3.5 Outputs.** `evidence/verify.json` (findings, patches, rounds, the canary results per task card), `MANIFEST.json` `checked_by: ["self_check", "verify"]` and `critic_vendor`, the G2 data (what was checked, what was repaired, what is still open). Config: `verify.profiles`, `verify.canary_share`, `verify.max_llm_rounds` (2).

## 4. The Spec Bench: a standalone mock front end for testing and for the owner

**4.1 What it is.** A separate app, **not part of the main flow**, built like the Research app: one process, one address (**`http://127.0.0.1:8130/`**), its own static front end (`apps/spec-bench/`, plain HTML, JavaScript modules and CSS, no build step, like `apps/research-console/`), its own backend (`agents/spec`'s `api` package, FastAPI), file-based storage under `agents/spec/.runtime/`, `scripts/spec_app.py start|stop|status|logs|restart`, `just spec-dev`, `spec-app-*`, `spec-test`. **It imports nothing from `mde_flow`, `context-api`, `web` or any other agent**, uses no database, and has its own composition root. Future integration into the main flow is a thin adapter over the documented contract (`docs/api/spec-agent.md`: input = a Compass export plus a Research output; output = a Spec Pack folder, its manifest and the events); **do not build that adapter now**.

**4.2 It plays the owner's journey and the front end's role.** The left rail is the journey: **1 Inputs → 2 Preview (zero cost) → 3 G1.5 → 4 Run → 5 G2 → 6 Hand-off to the mock front end.** Everything the owner needs is in plain words; budget and spend are always visible.

**4.3 Auto-select of inputs** (a pure function with tests; the UI and the CLI use the same one):
- **Discover** Compass exports (`docs/compass/research-inputs/*.json`, `agents/spec/.runtime/inputs/`, and any directory in `SPEC_INPUT_DIRS`), classify each with `compass-intake` (format version, tier, size band, derived profile, readiness, drift flags) and discover **Research outputs** (`agents/research/.runtime/runs/*/run.json` and `research.json`, status complete, partial or failed).
- **Match** a Research run to a Compass export by the recorded input hash (and, if absent, by the Brief id and content hash); prefer the newest complete run; show gaps ("no Research run: Spec runs with a thin research lane and says so").
- **Auto-select** proposes: the smallest export of each class (Quick yoga: compact; Standard produce marketplace: standard; Thorough freight: deep), the synthetic Uber Eats-class program, and the best Research run for each, with the reason for every choice and a one-click override. A run can also start with Compass only.
- **Headless:** `uv run python -m mde_spec_agent bench auto --size deep` and `bench run --input <path> [--research <path>] [--offline]` use the same function, so the builder (Claude Code) can drive end-to-end tests without clicking.

**4.4 The mock front end** (the front end's role, **static, never a design**):
- **Consumption simulator:** three modes against a chosen consumer profile: *all at once* (only if the pack fits that profile's `whole_pack_tokens`; otherwise it says why not), *one by one* (pick a screen: the exact files and bytes a worker would read, prefix plus card plus closure, and the token total against the budget), *parallel* (the task cards grouped by wave, worker slots from the profile's `k`, a dry-run timeline).
- **Consumer probe runner:** the weakest profile (Haiku) reads a task card and returns the outline, blocking questions and assumptions (live, inside the budget; an offline stub otherwise).
- **Screen content map:** for each screen a static page of what it must communicate and do (regions grouped by importance with no position, actions with intent and effect, states, source-language copy, hero records on the chosen mock profile); MUST, USUALLY and COULD are visibly different voices; **labelled "illustrative: the final layout is the front end's"**.
- **Journey walkthrough:** choose a role and a journey (stories, emotion line, moments that matter, unhappy paths) and step through the screens by their declared entries and exits; switch the mock profile (`empty`, `typical`, `busy`, `extreme`, `broken`, `offline`).
- **Navigation graph:** an SVG of apps, roles and routes with role filter, `in` and `later` styling, unreachable screens and dead ends highlighted.
- **Data and contract:** entities and lifecycles, operations (from OpenAPI), fixtures, mock behaviours, integrations with their interrupted-flow and pending-timeout rules.
- **Pack explorer:** the tree with token sizes per file and per task; the budget report; every validator result; the builder-needs matrix; the coverage matrix; the self-check report (code fixes, re-asks, flags, banners); the Verify report (deep); the G1.5 and G2 views; a diff between two pack versions by hash; run history; the budget page.
- **Static export:** one self-contained `preview.html` per pack (works offline, no network) so a pack can be handed to anyone.

**4.5 Safety.** A pack holds user text and model text: render everything with `textContent` or a sanitizer, never `innerHTML`; allow-list link schemes (copy the pattern of `apps/research-console/lib/safe.js`); the bench never executes anything from a pack.

**4.6 Tests.** Offline: the helper and static tests like `apps/research-console/tests`; a **test-only fake app** with scripted doubles (like `agents/research/tests/e2e/fake_app.py`, port 8150) for browser checks; the auto-select tests against the nine exports; the e2e journey test through the API (inputs → preview → run offline → G1.5 → G2 → simulator). Live: one smoke run inside the budget. **The composition root cannot select a test double** (a test checks it, as for Research).

## 5. The hard budget cap (USD 5.00; never exceeded)

- **Money is integer micro-USD.** The Spec spend record lives at `agents/spec/.runtime/spend-record.json` with a cross-process lock (copy the mechanism of `agents/research/src/mde_research_agent/spend/`); the cap `SPEC_LIVE_BUDGET_USD` defaults to **5.00** (set in the launch environment, never in `.env`), tripwire `SPEC_TRIPWIRE_USD` **4.50**; restartable only by the owner.
- **Reserve before every live call:** worst-case cost = input tokens times the input price plus the call's maximum output tokens times the output price (plus the long-prompt surcharge, from the profile). If `spent + reserved + in-flight > cap`, the call is **refused**, the step checkpoints and the run ends `partial`. After the call, reconcile with actual usage.
- **Tripwire:** at 4.50 no new wave starts; in-flight calls finish; the run checkpoints; only report-writing (no model) continues.
- **No bypass:** every Spec and Verify model call goes through one wrapper that enforces the reserve (a test uses a scripted gateway that tries to overspend and must fail to). The environment's global `LLM_BUDGET_USD` must also be at least the cap and never lower than the spend record's.
- **Several ways to stop:** the reserve check, the tripwire, `POST /api/stop` (graceful: checkpoint and end `partial`), SIGTERM, and `scripts/spec_spend_check.py` (an independent sum of the ledger and the spend record that exits non-zero above the cap).
- **A run whose estimate's upper bound exceeds the remaining budget is refused with a plain message** before it starts. This is the **money budget, the only spend control** (no quality stop, no second flag).
- **Order and allocations (starting values, USD):** price check 0.05; PS-3 0.35; PS-4 0.20; PS-5 0.50; PS-6 0.30; PS-7 0.25 (with the meaning set, the critic arm and Verify-for-deep catch rates); **PS-12a** a synthetic 60-screen app with Verify 0.60; **PS-12b** the real Thorough export with Verify 0.60; PS-8 extras 0.15; PS-9 live 0.20; PS-10 0.30; Bench live smoke 0.05; reserve 0.45; **PS-12c** the Uber Eats-class program with Verify about 1.0 **only if at least that much remains**. **Never drop** the Thorough and 60-screen runs; drop in this order when short: PS-12c, PS-9 live runs, PS-10 extra arms, PS-5 repeats (keep two). The price check recomputes every figure; stop if reality is more than three times the estimate.

## 6. Edge cases for big programs (`SPC-BG`, 16; with doc 18 the suite is **200**)

| ID | Situation | What Spec does |
|---|---|---|
| BG-01 | Four apps share account, notification and help screens | specified once, one id, counted once per app that includes them; role differences by region and action |
| BG-02 | One screen serves several roles | one id; regions and actions by role; role-visibility part of the block match |
| BG-03 | One order appears in four apps | one hero id and timeline from the skeleton; related entities written together; V-41 checks coherence |
| BG-04 | An app derives more screens than the cap and holds user-evidenced `must` features | defer by criticality with reason `capacity`; never a `must` without recorded G1.5 consent; conflict options shown |
| BG-05 | An admin web portal with wide tables | exempt from the column limit of V-37, other mobile-fit rules kept; marked `surface: web` |
| BG-06 | Hundreds of entities | shards by entity cluster; an owner module per entity; the data model never exceeds a shard cap; task cards name only the clusters they need |
| BG-07 | Twenty integrations | one `PROD-INTEG` batch per five integrations; each with flow, interruptions, pending timeouts, events; a Gate 2 record check |
| BG-08 | Five locales including right-to-left | glossary into all; locale batches by 300 strings; RTL as a global behaviour; script-fallback tokens; `needs_native_review` |
| BG-09 | A rate-limit storm in wave 3 | the wave ends `partial`; concurrency halves; resume from the checkpoint; identical bytes |
| BG-10 | The critic finds two apps disagreeing on an entity's statuses | class B patch to the owning module; Spec applies; touched nodes re-verified |
| BG-11 | A task card would exceed the read budget | the packager splits at a module boundary; a heavy card moves detail to `.detail.md`; never truncated |
| BG-12 | The process is killed at wave 4 | resume from the last batch checkpoint; same bytes as an uninterrupted run |
| BG-13 | Phase 2 starts after approval | incremental regeneration; Phase 1's approved baseline stays immutable; Phase 2 gets its own |
| BG-14 | A feature changes after G2 (L2) | the change controller files a change set; Spec regenerates the affected slices and tasks; Verify re-checks touched nodes |
| BG-15 | Two canary runs of one task card disagree | both recorded; the blocking questions are the union; the card is judged by the stricter result |
| BG-16 | The budget cap is reached mid-run | the call is refused, the step checkpoints, the run ends `partial` with a plain message; the pack is never delivered half-checked |

## 7. Proof gates added (doc 16)

- **PS-11, the Bench:** auto-select proposes the right pairs on the nine exports (snapshot tests); the offline e2e journey passes through the API and in a browser against the fake app; the consumption simulator reproduces the packager's task totals; the preview renders every screen of the Standard and Thorough packs without script injection (a poisoned quote test); a live smoke run stays inside the budget; the app starts, stops and restarts with `scripts/spec_app.py`; the composition root cannot select a double.
- **PS-12, big apps:** §2.8.
- **PS-7 is extended:** the self-check's planted-defect test (22 structural plus 8 meaning) and **Verify-for-deep's** catch rates, reported separately, with the critic's `critic_vendor` stated.
- **PS-9** runs the 200 cases; **PS-10** adds the owner's comparison of code-made and model-written lists and assets on 5 previews.

## 8. Decisions and open items

| ID | Decision | Amends |
|---|---|---|
| **AD-92** | **Result first.** Quality beats cost and speed; the Verify agent is built now for deep apps (config-gated: compact off, standard optional); fusion un-fuse threshold 90%; the model writes all list-visible mock records; three directions for standard and deep; a canary per task card for deep in the delivery path | AD-83, AD-85, AD-87, AD-86, doc 18 §3.5, §5 |
| **AD-93** | **The big-app playbook** (§2): program structure, shared modules once, one data model and contract per program, cross-app coherence, program-level interface freeze, fixtures, PS-12 | doc 15 §4, §5.13, doc 17 §8 |
| **AD-94** | **The Spec Bench** (§4): a standalone app on port 8130 with a static mock front end, auto-select of inputs, a consumption simulator and journey walkthrough; separate from the main flow; headless driving for Claude Code | doc 15 §8.7 (S6), prompt 12 |
| **AD-95** | **The hard cap** (§5): USD 5.00, reserve before every call, tripwire 4.50, several ways to stop, estimate refusal; the money budget is the only spend control | doc 16 §8, prompt 12 |

**Open items:** **OI-44** the writer's recorded limits (OI-31) decide which deep call kinds can be fused; **OI-45** whether `verify.profiles` should include `standard` by default after PS-7 and PS-12 (the owner's call, by result); **OI-46** the hand-off adapter to the main flow (deliberately not built; designed after the Bench works).

## 9. Latitude for the builder

The builder may decide, and must record in `DECISIONS.md`: module and file names inside the structure given here, the UI layout of the Bench, the exact thresholds marked [J] after measuring, the Module Library's contents (about 12 modules), the store-policy and capability sheets (from official pages only), extra validators and tests, extra fixtures, better prompts and few-shot examples, and small design improvements that raise the result for the front end without breaking a non-negotiable. It may **not** change: the freedom line (meaning is Spec's, expression is the front end's), model and provider independence, the static pack guarantees, the free-tools policy, the budget cap, "live is one switch", the separation from the main flow, or a decision's status (nothing is Proven until its gate passes).
