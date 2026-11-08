# 04. Spec package, slices, Build Plan, contract and hand-offs

Everything the front-end, backend and QA engines consume comes from the **Spec Graph** through derived files and the **Spec Server**. This document defines the data model, the package, the Build Plan that makes parallel generation safe, the contract that bridges front-end and backend, and the G3 manifest.

## 1. Spec Graph
Typed nodes with stable IDs; edges express meaning. `APP_SPEC.md` is a deterministic render of the graph (AD-03).

| Prefix | Node kind | Prefix | Node kind |
|---|---|---|---|
| PRG | program | OP | operation |
| APP | app | EVT | event |
| ROL | role | RUL | rule |
| MOD | module (library ref or custom) | INT | integration |
| FTR | feature (scope ledger entry) | TOK | design token |
| FLW | flow (journey) | CPY | copy key |
| SCN | scenario (golden journey) | ACC | acceptance criterion |
| SCR | screen | ASM | assumption |
| ENT | entity | SRC | source document |
| LCY | lifecycle (state machine) | TSK | build task |

Edges: `satisfies` (screen -> feature), `appears_in`, `reads` / `writes` (screen or operation -> entity), `calls` (screen -> operation), `gated_by` (operation or screen -> role), `transitions` (lifecycle), `depends_on` (task -> task), `derived_from` (any -> source), `deviates_from` (implementation -> Default node).

**ID rules.** IDs are assigned once and never reused. Deleting a node leaves a tombstone. A rename adds an alias. This keeps regeneration, stamps, change sets and traceability stable across a hundred changes. (AD-04)

## 2. Node envelope: tiers, provenance, status
```yaml
id: SCR-021
kind: screen
tiers: {purpose: Locked, bindings: Locked, sections.order: Default, visual_layout: Open}   # field-level tier map
status: proposed | confirmed | approved          # approved = inside an approved baseline
confidence: 0.0-1.0
provenance: [{type: user|document|research|pattern|assumed|compliance, ref: "PRD p.4" | "R-014" | "pattern:detail-with-sticky-cta", date: 2026-11-09}]
version: 15            # graph commit that last changed it
introduced_in: 3
tombstone: false
```
- **Locked:** supported by user or document evidence (or an explicit decision). The builder must implement it. Changing it needs a recorded override (doc 05). A Locked node without evidence is rejected by the validators.
- **Default:** our recommendation. The builder may change it but must record a `deviation` with a reason; deviations are listed at G3.
- **Open:** the builder decides; the spec supplies only guidance and constraints (typically visual layout, micro-interactions, component internals).
- Research and assumptions can produce Default at most. Compliance pointers, when used, produce Locked requirements with `provenance.type = compliance` (deferred feature).
- A node's status becomes `approved` only inside a baseline approved at a gate; approved baselines are immutable.

## 3. Package files (derived; all hashed in `manifest.json`)
| File | Purpose | Consumer |
|---|---|---|
| `APP_SPEC.md` | The single compiled document | people, auditors, any agent (via slices) |
| `spec.json` | Canonical graph export | engines |
| `slices/tier0.md`, `slices/SCR-*.md`, `FLW-*`, `OP-*`, `ENT-*` | Budgeted context bundles | front-end workers |
| `design/tokens.dtcg.json`, `design/DESIGN.md` | Tokens (W3C DTCG 2025.10 [V]) and portable design guide | front-end engine, Style Dictionary, other agents |
| `contract/openapi.yaml`, `schemas/*.json`, `fixtures/*.json`, `auth-matrix.json`, `events.json` | Contract v0 | front-end (mock adapter), backend, QA |
| `build-plan.json` | Task DAG | front-end orchestrator |
| `scenarios/*.json` | Golden journeys | preview scenario runner, QA |
| `scope-ledger.json` | Feature statuses | review UI, change controller |
| `views/*.json` | Data for review surfaces (app map, wireframes, matrices) | review UI |
| `exports/` | BRD/SRS, user stories, traceability matrix | people |
| `AGENTS.md` | Entry file telling any coding agent how to use the Spec Server | coding agents |
| `manifest.json` | File list, hashes, versions, baseline names | everyone |

OpenAPI is emitted as 3.1.x (stable tooling); 3.2 (September 2025) is backward compatible and can be adopted when tooling catches up [S].

## 4. Slices and budgets
Large context hurts (context rot [S], "smallest set of high-signal tokens" [V]), so every consumer receives slices (AD-16, AD-20).
| Tier | Content | Budget |
|---|---|---|
| 0 (always) | header, Stack Kit version and rules, naming conventions, token names, navigation skeleton, role list, glossary, global behaviors summary | ≤ 8k tokens |
| 1 | design system detail, navigation of the app being built | on request, ≤ 6k |
| 2 | the screen itself, its entities, operations, rules, copy keys, kit component signatures, scenario steps touching it | ≤ 6k per screen |
| 3 | neighbors, related flows, fixtures, anything else | on demand through tools |
A worker context is Tier 0 + Tier 2 + the frozen interface files + one or two golden recipes, normally under 20k tokens. Budgets are measured per tokenizer (Haiku 5.5's tokenizer is ~30% heavier than its predecessor's [V]).

**Example of a Tier 2 screen slice** (about 1.5k tokens; what a small model receives for one screen; the example category is illustrative only):
```yaml
id: SCR-021   app: APP-CUS   title: Restaurant detail
purpose: Let a user judge a restaurant and start an order          # Locked (PRD p.4)
serves: [FLW-003]    role_access: [guest, customer]
sections:                                                           # order = Default; visual layout = Open
  - hero:    {bind: Restaurant.photos[0..4], actions: [favorite, share]}
  - header:  {bind: [name, rating, eta, fee], badge: open|closed|busy}
  - menu:    {bind: MenuItem grouped by category, sticky_tabs: true, action: add_to_cart}
  - reviews: {bind: Review top 3, action: see_all -> SCR-022}
states:
  loading: skeleton(hero, header, 6 rows)
  empty_menu: copy.menu.empty
  error: inline_retry
  offline: cached data, add_to_cart disabled, banner
  closed: badge, ordering off
copy: [copy.menu.empty, copy.restaurant.closed, copy.cart.added]
acceptance:
  - GIVEN open restaurant WHEN tap Add THEN cart count +1 and mini-cart bar shows
  - GIVEN closed restaurant THEN Add is disabled and the reason is shown
mock_seed: {Restaurant: 24 (3 closed, 2 without photos), MenuItem: 8-40 each, 1 sold out, 1 long name}
complexity: M      model_hint: small-high-effort
provenance: {purpose: document, sections: research R-014, states: pattern:detail-with-sticky-cta}
```

## 5. Spec Server (AD-16)
Read-only service exposing the graph over MCP (MCP and AGENTS.md are Linux Foundation projects under the Agentic AI Foundation since Dec 2025 [V]) and plain HTTP. Tools: `get_tier0()`, `get_slice(id, depth)`, `neighbors(id)`, `search(query)`, `get_tokens()`, `get_contract_op(id)`, `get_scenario(id)`, `list_tasks(wave)`, `trace(id)`, `diff(vA, vB)`, `verify(hash)`, and the only write: `raise_decision({node, question, options})`, which creates a gap routed back to the Context Engine. The engine behind it is model- and agent-agnostic: Claude Agent SDK, Codex-class agents, Cursor or a custom loop can all consume the same slices. `AGENTS.md` in the package tells an agent the rules (never edit the spec, read slices first, stamp files, raise decisions).

## 6. Build Plan DAG (AD-11)
Generated by code in Stage 6 from the inventory, the dependency graph and the complexity rubric. It exists so that many small workers can run **in parallel without colliding**, the lesson of the 16-agent compiler run (partition into independent failures, verifier first) [V] and of Cursor's planner/worker design (conflicts fell from ~70,000 to under 1,000) [S].

**Waves.**
| Wave | Content | Exit check |
|---|---|---|
| W0 | Scaffold from the Stack Kit | installs and builds |
| W1 | Foundation: tokens compiled, kit variants, navigation shell, typed API client, mock adapter and fixtures, auth gate, i18n, error boundary | type-check, lint, render shell |
| Interface Freeze | Publish `interfaces/`: route map, types from schemas, token names, kit prop signatures, store slice shapes, API client signatures, i18n namespaces, analytics event names | hash recorded; changes need an interface-change request |
| W2 | Vertical slice of the primary flow (feeds **G2.5**) | slice journey passes |
| W3 | Screen groups in parallel by module and app | per-task checks |
| W4 | Cross-cutting: deep links, notifications, analytics, offline policy | cross-flow journeys |
| W5 | Integration mocks and adapters | adapter contract checks |
| W6 | Polish: accessibility, token lint, visual QA by a vision critic, performance | budgets met |
| W7 | Full verification and scenario scripts | all golden journeys pass on mocks |

**Task card.**
```yaml
id: T-0142   wave: W3   app: APP-CUS   module: MOD-discovery   title: Restaurant detail screen
depends_on: [T-0030 tokens, T-0045 nav-shell, T-0051 api-client, T-0088 RestaurantCard]
owns: [app/(customer)/restaurant/[id].tsx, src/features/restaurant/**]      # nobody else writes here
reads: [SCR-021, ENT-Restaurant, OP-getRestaurant, tier0, recipe:detail-with-sticky-cta]
produces: [route component, 3 feature components, unit tests, fixtures hook]
checks: [tsc, lint, render-all-states, a11y-labels, token-lint, journey FLW-003 steps 2-4]
complexity: M     model_hint: small-high-effort     budget: {in: 18k, out: 6k}     escalate_after: 2
```
**Ownership.** A task writes only the files it owns. Anything shared (route files, layouts, barrel exports, types, API client, token files, i18n key files, fixtures loaders, mock handlers, navigation param types, `@spec` stamp headers, test scaffolds from acceptance criteria) is **generated deterministically by code** from the graph during integration, so tasks cannot collide. Models write screen bodies and bespoke components only.

**Merge strategy.** Each task works in an isolated workspace, passes its own checks, then merges to the integration branch; if the base moved, the task re-runs on the new base (cheap because tasks are small). Conflicts are therefore rare and mechanical.

**Complexity rubric [J, calibrate with data].** Add points: sections (≤ 3: 0, 4-6: 1, ≥ 7: 2); states (≤ 4: 0, 5-7: 1, ≥ 8: 2); side-effecting actions (0-1: 0, 2-3: 1, ≥ 4: 2); bespoke components (0: 0, 1: 1, ≥ 2: 2); integrations touched (0: 0, 1: 1, ≥ 2: 2); gestures or rich animation (none: 0, some: 1, rich: 2). Total 0-3 = S, 4-6 = M, ≥ 7 = L.
**Routing.** S -> small model; M -> small at high effort or a mid model; L -> mid or large. After two failed checks the task escalates one class and the rubric is flagged for recalibration. The Context Engine only *recommends*; the front-end engine decides (Default tier).
**Oracles.** Per-task checks plus the journey steps they touch; outputs short and machine-parseable; a progress file per wave; a fast sampled mode for large suites (practices from the compiler run [V]).
**Scale.** One app of ≤ 12 screens: a single wave. Large programs: 8-16 concurrent workers per wave, bounded by provider rate limits.

## 7. Contract-first bridge (AD-12)
**Contract v0** is produced in Stage 3c and ships with the package: OpenAPI 3.1 operations; JSON Schemas for entities; seeded fixtures; auth and permission matrix; event list; conventions (identifiers, timestamps, enums taken from lifecycle states, pagination, error model with stable codes, idempotency keys for mutations).
**Front-end rule.** The front-end talks only to a typed API client backed by a **mock adapter** driven by the fixtures (latency and failure injection available for the preview's state inspector). No screen contains data fetching logic that bypasses the client.
**Reconciliation at G3.** Static analysis of the generated app extracts every API call. The *usage map* (screen -> operation, parameters, fields read) is diffed against Contract v0. Differences are resolved additively (add the operation or field) or by fixing the front-end. The result is frozen as **Contract v1**.
**Evolution.** After v1, changes are additive unless explicitly approved as breaking; breaking changes go through the Scope Change Protocol and a release delta (doc 05).

## 8. Golden journeys
`scenarios/*.json` are generated from flows and acceptance criteria: ordered steps (actor, screen, action, input, expected observable result), cross-app steps (events), preconditions (seed profile, role), and expected final state. They run (a) in the preview scenario runner on mocks, (b) as the backend acceptance replay on the real backend, (c) in QA. They are the proof that "what you approved is what you get".

## 9. G3: Approved Build Manifest (AD-13)
At G3 the user approves once. The following are frozen into `approved-manifest.json`:
```yaml
spec: {version, hash, baseline: B-G3}
contract: {version: v1, hash}
scope_ledger: snapshot
frontend: {build_hash, preview_url, deviations: [...], known_issues: [accepted...]}
usage_map: screen -> operations
fixtures: become backend seed data (demo profile) and test data
state_machines: lifecycles exercised
golden_journeys: [SCN-001..] with expected outcomes
rules: table incl. values the user confirmed
roles_permissions: matrix
integrations: [{name, mode: mock, chosen_provider, credentials: sandbox-by-default}]
nfr_targets: {...}
```
**Behavior Book** (plain language, derived by code plus DISC-style summarization): entities and statuses; business rules and formulas, with a *confirm* list where the mock hardcoded a value or the PRD was silent; permissions per role; notifications and their triggers; integrations; reports. The user confirms it at G3 so that "what looked right" and "what must be built" cannot diverge.
**Integration Activation Checklist:** for each mocked integration (payments, SMS/OTP, email, push, maps, KYC, analytics, storage), recommended providers with trade-offs, "use recommended" as the default, and a list of credentials needed *later*. Sandbox or test keys are enough to finish; production keys can arrive afterward and never block delivery.

## 10. What each engine reads and writes
| Engine | Reads | Writes | Verified by |
|---|---|---|---|
| Front-end | Spec Server slices, Build Plan, tokens, contract v0, scenarios | app repo, preview, usage map, deviations, build manifest | per-task checks, wave exits, golden journeys on mocks, visual QA |
| Backend | Approved Build Manifest, spec domain/rules/permissions/integrations, Contract v1, fixtures | server, migrations, adapters, seeds, tests, deployment | OpenAPI conformance and property tests, journey replay on real backend, acceptance tests, fixture load |
| QA | Manifest, journeys, acceptance criteria, deployed backend | release report | journey results, regression suite, store-readiness checklist |
Stack Kit and Backend Kit are fixed defaults (AD-15). Backend default (OI-1, Default [J]): TypeScript with PostgreSQL so that schemas and types are shared with the app; the contract keeps other targets possible. Because the Backend Kit is fixed and the behavior is already approved, the backend engine's creative surface is small, like the front-end's.

## 11. Traceability (AD-25)
Generated files carry a stamp `@spec SCR-021@v15` and components carry `testID` values in the form `SCR-021.menu`. The preview inspector reads these to show the spec node, tier and provenance and to create a pre-bound change request. The traceability matrix (requirement -> feature -> screen -> acceptance -> test -> file) is exported from the graph and the stamps. Incremental regeneration uses `diff(vA, vB)` and the stamps to find only the affected files; hand-edited files are locked.

## 12. Versions and baselines
Every graph commit increments `v`. Named baselines freeze approved states: `B-G1`, `B-G2`, `B-G3`, then release baselines `R1.1`, `R1.2`. Approvals reference a baseline hash. Light changes (lanes L1/L2) create commits without re-approval of untouched sections; structural changes (L3) require section re-approval; foundational changes (L5) create a new program version (doc 05).

## 13. Exports (people-facing, derived)
BRD/SRS in Word or PDF, user stories with acceptance criteria, wireframe gallery, API contract (OpenAPI), design guide, test plan, traceability matrix (spreadsheet), Behavior Book. These are exports of the graph, not separate work (AD-25).
