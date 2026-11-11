# Progress

Checklist built from brief section 10. Tick an item with one line and its commit hash, e.g. `- [x] item (abc1234)`, then commit. Resume protocol: read the brief, spec, this file and DECISIONS.md, run `git log --oneline -20` and `git status`, continue from the first unchecked item.

Verified state of main before the live batches (2026-11-08, commit a51cd42, after the resume session's merges; D-075 to D-080): ruff, format, import-linter (6 contracts) and docs check clean; schema drift clean; pyright 0 errors on packages/apps/tests/evals/scripts; 427 Postgres integration tests passed; the full offline run had 5,114 passed and 6 failed, all six stale mock goldens (regenerated and reviewed, 9959c3f; the 22 journey tests pass; a full offline re-run follows in the final clean-checkout `just ci`). Earlier gate: 4,379 offline and 396 integration at 3f6aa12; clean-clone `just ci` passed at 9d72ff8 (D-070).

Done in the resume session: `feat/apisec` was already merged (D-074); a FileLock first-acquire race on Windows fixed (9c8755e, D-075); Org Profile read by Discover for Enterprise sessions (da7acff, D-079); Standard and Deep research ceilings reachable (2429002, D-080); REPORT security paragraph and the live runbook corrected.

REMAINING: (1) live batches L1 to L4 (the owner authorized the one-key file for this session, D-069 option 1); (2) cassettes plus replay tests; (3) REPORT, Definition of Done, final clean-checkout `just ci`, sweep.

Ledger note: `data/.budget/ledger.jsonl` had no `.guard` file before the first resumed live call (the guard is adopted on the first write for a ledger older than the guard feature, `ledger.py` `_verify`); the ledger was copied aside before the batches.

Live spend total (update after every live batch with `just budget`): USD 0.1984 of 6.00 (effective cap 5.70). Batches run: L0 smoke (2026-11-08, 7/7 passed, USD 0.0221); L1 Discover (20 sessions, USD 0.0102, D-081); L2 Research (3 runs, USD 0.1660, D-082).

## Phase 0: Bootstrap (lead)

- [x] Docs copied: BRIEF.md and spec written to `docs/`
- [x] Repo: own `git init`, `.gitignore` protects `.env`, `.gitattributes`, `.python-version` (9b17030)
- [x] uv workspace root `pyproject.toml`, shared ruff / pyright / pytest config (9b17030)
- [x] `justfile` (all targets in brief 6.2), `docker-compose.yml`, `.env.example`, `.editorconfig`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` (9b17030)
- [x] `.importlinter` (layering, only the gateway imports `openai`, no agent frameworks, engines independent) (9b17030)
- [x] Package and app skeletons (11 packages, 3 apps; each with `pyproject.toml`, `src/<import_name>/`, `tests/`) (9b17030)
- [x] `mde_core` (settings, logging + redaction, ids, clock, errors, hashing, filelock, cancellation, money) (9b17030)
- [x] Contracts v1 frozen: slots, cards, events, Brief, Research Note, spec blocks, Plan view, call IO C1..C14 + S1, state/gate events, policy, content models, ports; exported as JSON Schema (9b17030)
- [x] `CLAUDE.md` (root + nested), `AGENTS.md`, `README.md`, `.claude/` (settings, rules, agents, skills, hooks) written and reviewed (9b17030)
- [x] ADR-0001 (stack) and ADR-0002 (environment adaptations) (9b17030)
- [x] `scripts/`: `doctor.py`, `dev_db.py`, `export_schemas.py`, `gen_prompt_catalog.py`, `docs_check.py`; secret-scan test; budget CLI moved to gateway-engineer as `mde_llm_gateway.budget_cli` (9b17030)
- [x] Root `conftest.py`: autouse fixture forces mock mode, sets `MDE_FORBID_LIVE=1`, fails live attempts outside `live` tests (9b17030)
- [x] `just ci-fast` green; layering enforced; tag `contracts-v1` (9b17030)
- [ ] Checkpoint: PROGRESS and DECISIONS updated, committed, two lines on what to change next time

## Phase 1: Foundations (parallel)

> Phase 1 dispatch (2026-11-08): gateway, flow, spec, content-questions, content-packs running in worktrees `../wt-<name>` on branches `feat/<name>` (from 37b95e5). merged so far: content (q+p), gateway, spec, flow, persistence, discovery, research. reconcile merged (fake store now passes the 22-check Postgres contract suite; flow runs on both stores; no DB transaction across engine calls). builder merged (all five engines on main). API merged (with flow security fixes), tests/evals merged. Bug-fix wave from the end-to-end suite merged (builderfix, flowfix, discoveryfix, corefix, cli). Next: live batches L1 to L4, second security review, docs completion, final report. Next wave when RAM allows: discovery, research, builder, test-engineer. Merge order: contracts/core (done) -> content -> spec -> gateway -> flow -> persistence -> engines.

- [x] gateway-engineer: `BudgetGuard`, pricing, ledger with portable lock, tripwires, scoped caps
- [x] gateway-engineer: pipeline, `to_strict_schema` over every registered output model, retries, circuit breaker, rate limits
- [x] gateway-engineer: providers (OpenAI Responses, Scripted, Mock, Replay, Recording), live gating, log redaction
- [x] gateway-engineer: `apps/llm-gateway-api` + `HttpGatewayClient` + contract test on both clients; `docs/architecture/llm-gateway.md`
- [x] persistence-engineer: models, Alembic migrations, repositories, tenant isolation, idempotency
- [x] persistence-engineer: job queue (`SKIP LOCKED`, heartbeat, stuck-job recovery, cancellation, one active run), `InlineJobRunner`, ephemeral-DB test helper
- [x] content-author-questions: question bank per archetype + shared, chips, help cards, nudge/finish copy, knowledge base, loaders and tests (merged 165179c)
- [x] content-author-packs: domain packs, base recipes, six themes (contrast test), stack profile, policies, loaders and tests (merged 3f3f593; contracts 1.0.1 + bundle b2acf86)
- [x] spec-engineer: renderer (deterministic), nine validators, assembler, build sequence, Plan view
- [x] spec-engineer: Spec Loader (`index/get/slice/neighbors/refs/diff/verify`), `scripts/simulate_frontend_consumer.py`
- [x] L0 live smoke run once the gateway is ready (lead or live-runner); ledger total recorded (USD 0.0222, 7/7)
- [x] Merge Phase 1 branches in dependency order; `just ci` green
- [ ] Checkpoint: PROGRESS and DECISIONS updated, committed, two lines on what to change next time

## Phase 2: Engines (parallel)

- [x] flow-engineer: pure state machine + transition table, gates (all eight rules), approvals, runs, queue during PLANNING, hand-off ports
- [x] flow-engineer: hypothesis stateful property tests (`spec_ready` impossible without Gate 2 approval of the current hash)
- [x] discovery-engineer: turn loop, `IntentRouter`, `SlotStore`, `QuestionSelector`, `BriefCompiler`, `SafetyScreen`
- [x] discovery-engineer: call specs C1, C2, C3, C5, C11, C14 (+ mock handlers, fallbacks)
- [x] research-engineer: pipeline (C6, S1, C7, C8), cross-check, depth caps, safety, `InternalSourcePort` stub
- [x] research-engineer: call specs S1, C6, C7, C8 (+ mock handlers per archetype)
- [x] builder-engineer: baseline expansion, proposals validation/apply, critic, repair, scope guard
- [x] builder-engineer: call specs C9a, C9b, C9c, C10, C12, C13
- [x] Scenario tests per engine green in mock mode; merge in dependency order; `just ci` green (main f5f408a..7571007; 40 scenarios, e2e, security merged)
- [ ] Checkpoint: PROGRESS and DECISIONS updated, committed, two lines on what to change next time

## Phase 3: Integration

- [ ] Context API routes, SSE with `Last-Event-ID` replay, idempotent events, tenant isolation
- [ ] Queue worker wired to planning runs; hand-off to a local git repo at `PROJECTS_DIR/<tenant>/<app_id>/`
- [ ] CLI: `mde chat`, `mde sim`, `mde budget`, `mde doctor`, `mde spec simulate-consumer`
- [ ] Persona simulator (scripted by default)
- [ ] test-engineer: scenario tests, one per situation-catalog row (about 40)
- [ ] test-engineer: e2e mock for all five archetypes, both policy profiles, multi-approver gates
- [ ] Consumer simulator passes on every golden spec
- [ ] `just ci` green; `mde chat` completes a session offline
- [ ] Checkpoint: PROGRESS and DECISIONS updated, committed, two lines on what to change next time

## Phase 4: Live and evals

- [x] L1 Discover (persona conversations, cassettes recorded), allowance <= USD 1.20: USD 0.0102 spent, ledger total USD 0.0324; 20 sessions, 0 failed or repaired calls; one finding (scope.apps) fixed in code (D-081)
- [x] L2 Research (three Light-depth runs: commerce, booking, internal), allowance <= USD 1.00: USD 0.1660 spent, ledger total USD 0.1984; 3 of 3 runs ok; fixes: tool-call reserve x2, output-token caps (D-082)
- [ ] L3 End to end (four to five full live sessions across archetypes, golden outputs recorded), allowance <= USD 2.00
- [ ] L4 Safety and probes (injection payloads, sufficiency probe, critic check), allowance <= USD 0.50
- [ ] Evals report committed in `docs/evals/reports/` incl. honest notes on where Luna was weak
- [ ] Prompts, caps and effort tuned from eval findings; ledger total recorded
- [ ] Checkpoint: PROGRESS and DECISIONS updated, committed, two lines on what to change next time

## Phase 5: Hardening

- [ ] security-reviewer adversarial pass (gate bypasses, budget leaks, secrets, injection, tenant isolation, SQL, SSRF); findings fixed
- [ ] Gap-filling tests; coverage >= 85% on flow, llm-gateway, spec-loader, spec-builder validators, discovery slot/selector
- [ ] docs-writer: architecture, specs, runbooks, package pages, prompt catalog, API docs; `just docs-check` green
- [ ] Exported `docs/api/openapi.json` and JSON Schemas current
- [ ] `docs/implementation/REPORT.md` written
- [ ] Checkpoint: PROGRESS and DECISIONS updated, committed

## Definition of Done (brief section 11)

- [ ] 1. `just ci` passes from a clean clone offline (lint, format check, types, import-linter, unit, integration, scenario, e2e mock)
- [ ] 2. Five archetypes (commerce, food delivery, booking services, internal workforce, custom) go "hi" -> Brief -> Gate 1 -> research -> build -> Plan -> Gate 2 -> `spec_ready` with a committed `APP_SPEC.md` via API and CLI; validators pass; consumer simulator passes with every slice within budget
- [ ] 3. A passing scenario test for every row of the spec's situation catalog
- [ ] 4. Gate rules and state machine covered by unit and property tests; `spec_ready` provably impossible without Gate 2 approval of the current hash
- [ ] 5. Live evidence: L0 smoke passed; at least three live end-to-end sessions on different archetypes produced valid specs; cassettes recorded; total live spend <= USD 6.00 (target < 4.50) with ledger summary in the report
- [ ] 6. No secrets anywhere in repo, logs or cassettes (scan test passes)
- [ ] 7. Guided and Enterprise policy profiles run on the same engines (two sample policies), incl. multi-approver gates
- [ ] 8. Documentation complete: README, CLAUDE.md files, `docs/` tree, ADRs, prompt catalog, exported OpenAPI and JSON Schemas, runbooks (local dev, live budget, troubleshooting), `frontend-consumption.md`
- [ ] 9. Evals report committed with honest notes on where Luna was weak and what changed
- [ ] 10. `PROGRESS.md` fully ticked, `DECISIONS.md` current, `REPORT.md` written

## Compass (build prompt 01; started 2026-11-09 03:10)

State at the end of the first working session (the 04:40 cutoff was a usage limit; work resumed 11:03). Everything below is merged on `main` and green offline; counts are from the last full run.

- [x] Toolchain on macOS without Homebrew (uv, just, Python 3.13, PostgreSQL 16.2 private cluster on 54329) and `docs/architecture-v3/` copied (D-084)
- [x] Package, import-linter layer + isolation contract, ADR 0012 to 0014, D-085
- [x] Catalog as data: 44 slots, 17 frames, 16 families, 9 tier B + 8 declined capabilities, en/hi strings
- [x] Store: in-memory fake + PostgreSQL + own Alembic dir + one shared contract suite (58 integration tests on the private Postgres)
- [x] All 12 structured calls as folders with spec, schema, templates, examples, mock, fallback, adapter, tests: understand_turn, write_card, situate, compile_text, seed_research, probe_handoff, answer, interpret_change, make_variants, read_chunk, merge_claims, scan_ambiguity
- [x] Engine: state machine, slot and tier rules, value-of-information queue, card validation, assumptions, Gate 1, patch engine with undo, safety, the turn-loop `Service`, targeted flows (priority card, references, voice read-back, phased apps, locked-edit preview, back-edge hook)
- [x] Views: BriefViewModel, consumer views (no transcript), readiness, diffs, provenance
- [x] API (SSE with replay, dev auth, CSP) and the static test UI (spent/cap meter, change panel, history drawer, real file upload)
- [x] Document intake: parsers (PDF, DOCX, XLSX, PPTX, CSV, TXT, MD, EML), chunking, SSRF-safe URL reader, ingest pipeline, wired into the service and `POST /sessions/{id}/uploads` (D-086)
- [x] 46 named scenarios (`SB-*`), ten persona scripts and a metrics report (`cd packages/compass && PYTHONPATH=src python -m evals.run_personas`)
- [x] Docs: README, `docs/packages/compass.md`, `docs/prompts/compass-calls.md`, `docs/api/compass.md`, `docs/runbooks/compass.md`
- [x] Live smoke driver (`mde_compass.live_smoke`) with an offline twin test
- [ ] **Live batch A (smoke, allowance USD 0.10): BLOCKED on the second key.** `ALLOW_LIVE_LLM=1` is not set in the environment (CLAUDE.md D-069: the user's decision). Run: `ALLOW_LIVE_LLM=1 uv run pytest packages/compass/tests/live -m live -q -s` (report in `packages/compass/evals/reports/live-smoke.json`). Batches B to D were not authorized for this session.

Measured offline in mock mode (ten personas): 8 of 9 targets met. Questions: Standard/Express median 8 (target 7: the one miss), max 10, Express 3; Large (enterprise) is budgeted per module and reported separately (18). Locked without evidence 0; assumptions unchanged at Gate 1 100%; all three handoff probes 100%; cards valid or fallback 100%; hidden instructions ignored 100%; turns with errors 0; all ten personas reach approval.

Open items and exact next steps:
1. Median questions 8 against 7: the lever left is the policy of `auth.account_model` (risk high, must_confirm, asked as its own card): an owner decision (default `individual`, shown in "We assumed").
2. Run live batch A once `ALLOW_LIVE_LLM=1` is set; read the report, tune `CALL_PROFILES` token caps, then record cassettes (secret-scanned) so the calls replay offline. Vision and streaming are unsupported by the gateway (no image input, no streaming).
3. Intake scenarios still deferred (`tests/scenarios/data/DEFERRED.yaml`): huge document, scanned pages, contradictions across documents, mostly-vision PRD, sheets and decks. The intake tests cover duplicates, corrupt files, private links and the SSRF table.
4. Not built: an HTTP route for `reopen_slot`, `ChainedRegistry` gateway hook, `CompassBudget.reconcile` against the ledger, per-language intake messages (English only), prefetch.
5. Retiring `mde_discovery` is a later task (Compass does not import it; it is untouched and its suites pass).

## Compass: AI-planned interview (build prompt 02; started 2026-11-09 12:30; branch `feat/compass-ai-interview`, safe-point tag `pre/compass-ai-interview-20261109-1237`)

Design: ADR 0015 and doc 11 section 17; build prompt: `docs/compass/build-prompts/`. No push (D-091). Each phase ends with an annotated tag `compass/ai-interview-p<n>`.

- [x] Phase 0. Diagnosis and "before" evidence (c9847b6): 30 questions, 9 distinct wordings, 93% shared across five ideas
- [x] Phase 0a. GLOBAL_RULES (74482f4); doc 11 section 17, AD-29 amended, AD-30 to AD-32, ADR 0015, D-090, D-091
- [x] Phase 0b. Restructure `packages/compass` -> `agents/compass` (08a1085 moves + config, e19d154 in-package paths, docs paths; D-092)
- [x] Phase 1. Interview Memory: models and events (bb42c5c), store with migration 0002 and the contract suite (096678e, 135 store tests, 77 integration), `apps/web` port of the UI (1964f73), pure engine modules (81847ec: quotes, facts, projector, needs, terms, validators, context pack, policy); tag `compass/ai-interview-p1`
- [x] Phase 2. Needs Registry: `answer_spec` for all 44 needs, frame style guides and examples, loader enforcement (3573367), needs status computation (`engine/needs.py`, 81847ec); tag `compass/ai-interview-p2`
- [x] Phase 3. Calls: ports, profiles, placeholders and the per-spec scripted gateway (c27536b), `plan_questions` (db7a0a2), `audit_questions` + `rephrase_question` (766a07d), `understand_turn` v2 + `update_summary` (48a029c), `compile_brief` (dcdf38c); tag `compass/ai-interview-p3`
- [x] Phase 4. Engine: facts load and commit with every turn (51967a7), planner path behind the flag (bb9a6a8), follow-ups, replan, stop, budgets, `domain_facts` and compile from the ledger (773a297), edit/pivot/upload/Q&A list (8091fba), help and rephrase (ed417e4); legacy engine still the default until Phase 7; Compass offline suite 2211 passed, 1 xfailed (BUG-18); tag `compass/ai-interview-p4`
- [x] Phase 5. UI in `apps/web` (merged 3fdf4a9, e7148c6): groups, answer-all and live layout switch, why-popover, Ask me more, Q&A editor, MOCK banner, fallback badge; web tests 93 passed; checked in headless Chrome against the real planner in mock mode (D-101); tag `compass/ai-interview-p5`
- [x] Phase 6. Offline tests (merged aa391d5): 46 scenarios on both engines, SB-QP-01..21 with strict provider fixtures, property tests, needle test, planner metrics; 2599 passed, 16 strict xfail (D-103); tag `compass/ai-interview-p6`
- [x] Phase 6b. Live prerequisites: persisted Compass spend from the gateway ledger (2b1d8be, D-093 ceiling USD 3.00 / target 2.00); batch runner `python -m mde_compass.live_batches --batch E|F|G [--dry-run|--mock|--replay]` with preflight, ledger-delta allowance stop, report JSON and an offline twin (75 tests); expected cost E 0.05, F 0.19, G 0.12 (ceilings 0.15 / 0.58 / 0.35); tag `compass/ai-interview-p6b`
- [~] Phase 7. Planner is the default (D-103). Live batches E, F and G ran with the real model (D-104, spend USD 0.52): E passes 23 of 23; F and G reach Brief review with fixes applied for timeouts, strict validators, tap-to-answer prompt and document needs. NOT DONE: re-running F after the fixes, the open items listed in D-104, recording cassettes, deleting the legacy path (Phase 8)
- [ ] Phase 8. Remove the legacy path, refresh docs, `just ci`, merge `--no-ff`, report

## Compass v2: necessary questions, submit-once batches, fixed tiers, speed, detailed Brief (owner message 2026-11-09 evening; branch `feat/compass-ai-interview`; decisions D-106, D-107)

Owner's list: a detailed, correct Brief; one batch at a time answered and submitted ONCE; tiers 7 (hard max 10) / 12 / 25+ chosen first; a reserved last question; multi-select plus custom input; faster generation; per-question and "decide the rest" auto-answer; enterprise documents; user changes; front end. Speed first, cost not a concern, USD 3.00 ceiling. Tags `compass/v2-p<n>`; safe point `pre/compass-v2-20261109-2226`. No push.

- [x] P0. Follow-up fixes landed and merged (390c9e6): BUG-27, defaults at stop, settled-assumption notice, label codes, BUG-28, BUG-22, GAP-5, GAP-1 partial; offline 2641 passed / 11 xfailed, integration 77, security 220
- [x] P1. Interface freeze (eeedcde, tag `compass/v2-p1`): `Room`, hard caps, closing card origin + call mode (stored as replan), wire fields, `DomainFact.kind/refs` (hash-stable), decide port, ADR 0016; `plan_compute`/`plan_apply` moves to P3
- [x] P2. Engine (tag `compass/v2-p2`): tiers and `Room` accounting, BUG-19 fixed (4311f6a); the batch form as default, submit once (ecd30f1); the closing question, always last, decided when nobody answers, reopened by Ask me more (12c4911, 47c0b9a); D-108
- [x] P3. Latency: idea turn runs understand, situate, and initial plan in parallel; folded follow-ups eliminate intermediate rounds; deterministic option taps resolve in 0ms; CALL_PROFILES tuned for max speed.
- [x] P4. Auto-answer: stateless AI preview endpoint `POST /sessions/{id}/decide-preview` powered by `cmp.decide_answers`; untouched suggestions recorded as `Default` assumptions (`origin="assumption"`); Web UI "Suggest answers with AI" button with busy state and `.ai-suggested` indicator.
- [x] P5. Feature C "Approve All": `POST /sessions/{id}/gate` accepts `ApproveAll` event with stale hash verification (409 Conflict), bulk-accepts all pending assumptions via a single ChangeSet (`source="approve_all"`), and approves Gate 1 in a single turn. Assumed ratio badge and high-impact confirmation sheet in web UI.
- [x] CALLS (merged 38c2b53, D-110): closing mode, planner type rules, `type_fit` / `too_many_open` / `necessity` validators, `refines`, slim planner, `cmp.decide_answers`; the engine does not call `decide_answers` yet
- [x] BRIEF (merged 439b707, wired 10faf8f and 21c6b7b, D-110): fact kinds, consumer views, parallel compile v2 with digests, corrections and review assumptions, probes after compile, documents, lock guard; the document confirm card is built but not shown
- [x] WEB (merged c79cb76, D-109): tier-first welcome, form for every batch, select-all / none of these / picks plus custom, You decide and Decide the rest, retry, phase-aware waiting, upload checks; 2789 offline tests green
- [~] P6. Changing a slot-less assumption keeps the words (ledger + recompile). Left: panel edits writing facts, recompile of only the affected sections for Q&A edits
- [ ] P7. Live verification: NOT RUN (needs the owner's second key; USD 3.00 ceiling, about 2.4 left). Run `python -m mde_compass.live_batches --batch E|F|G` (the players answer card by card with `default_ui_mode: one_at_a_time`; a batch-form player is the first thing to add) and measure first-batch and later-turn latency, fallbacks, closing last, cap
- Offline state at tag `compass/v2-p3`: 3031 passed, 3 skipped, 8 xfailed; integration 77; security 220; ruff, pyright, import-linter, docs-check clean

## Compass size: structure draft, size bands, research guidance (owner messages 2026-11-10; branch `feat/compass-speed-v3`; ADR 0017; plan `COMPASS-SIZE-PLAN-2026-11-10.md`; decisions D-113 to D-120; safe point tag `pre/compass-size-20261110`; tags `compass/size-s<n>`; no push)

Baseline before any change: offline Compass + web suite 3044 collected (3033 passed, 3 skipped, 8 xfailed), ruff clean, pyright 0 errors on own paths, lint-imports 7 kept, docs-check 0 problems; live spend USD 0.8541 (shared ledger).

- [x] S0. Docs and data, behaviour-neutral: plan, ADR 0017, D-113 to D-120, `size_bands.yaml` + loader, view-level dedupe of repeated facts (4caeab7)
- [x] S1. Model: four optional sections, omit-when-empty dump, hash pins on both real exports, patch registries, ids (df43169)
- [x] S2. Pure logic: derive, checks (structural, sufficiency, logical, realism), rule pack, slices, provenance guard
- [x] S3. Calls: `cmp.draft_outline`, `cmp.draft_module`, `cmp.review_structure`, `cmp.seed_research` v3, `cmp.plan_questions` v5
- [x] S4. Engine step and band logic (inside the seed and scan gather), switches, notices (a2e15ff, with S5 and S6: the three share `engine/service.py`)
- [x] S5. Gate and edits: `structure_action`, blocker `structure_unreviewed`, Approve All, undo (a2e15ff)
- [x] S6. Views, API, export (format 2), research guidance (a2e15ff; full offline suite 3321 passed, 3 skipped, 8 xfailed after the section-count fix)
- [x] S7. Web: tier cards with size, Structure tab, module rows, strings en and hi (full offline suite 3344 passed, 3 skipped, 8 xfailed; real-browser walk-through in headless Chrome)
- [ ] S8. Optional prefetch (off by default): NOT built on purpose (D-123): the structure runs inside the seed and scan wave and measured 45 to 75 s live
- [x] S9. Scenarios SB-ST-01 to 04 (+ `structure` expectation and policy forwarding), batch H (`live_structure`, three replays), live program (D-123: ~USD 0.21), docs; final suite below
- [x] Browser test of the three tiers and the three research inputs in `docs/compass/research-inputs/` (D-124): found and fixed the list-overwrite, role and app, closing-priority, geo and history-nesting defects; Thorough input to be re-recorded with the fixed code (needs the owner's live server)


## Research agent (build prompt 05; started 2026-11-11; branch `feat/research-agent`; safe-point tag `pre/research-20261111-1206`; ADR 0018; decisions D-125 to D-131; no push, no merge into `main`)

- [x] Baseline, design docs copied, guard hook uses `python3` and recognises the Research live scripts (f50249c, 1573ae9, 958e346)
- [x] Package `agents/research` registered (root pyproject, import-linter contract `research-agent-isolated`, justfile) and fixtures extracted from the three exports (3178c31, 39c4db9)
- [x] Pure cores, quotes, catalog and data files, intake, ports and adapters, provider facts (bf031b7 to 340d11e)
- [x] Planner, six model calls with runner and test gateway, engine, validators RV-01..17, schemas (3cdc44e to 586e6c3)
- [x] Run service, HTTP and SSE API, stores (memory, PostgreSQL), CLI, synthetic Briefs, search cache (f9afc10, 9c129b0)
- [x] Scenario RS-01..34, validator, determinism, generality, contract and CLI suites (aebe744)
- [x] Metrics, cassette recording and replay, bake-off judge, bake-off and batch-run scripts (5bc837f); quote fold fix and distinct platform labels (a075b49)
- [x] Test console: Research screens, picker, CSP origin (7d209c6, 14d2340); checked in real Chrome against the fake and the real Research dev server
- [x] Live-spend ceiling raised to 8.00 (a9ed1e9, D-125); event and API refinements (8e65604)
- [x] Docs: README, package page, API page, runbook, ADR 0018, web README, provider facts
- [ ] B1 live bake-off (owner starts it, `docs/runbooks/research-agent.md`)
- [ ] B2 live runs on the three examples and a synthetic Brief, then read the outputs as the Spec lanes would and iterate (prompts, slot schemas, query strategy, assembly)
- [ ] Final report (prompt section 12)
