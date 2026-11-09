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
- [~] Phase 1. Interview Memory: models and events (bb42c5c), store with migration 0002 and the contract suite (096678e, 135 store tests, 77 integration), `apps/web` port of the UI (1964f73); pending: the pure engine modules (quotes, facts, projector, needs, terms, validators, context pack, policy) in `feat/compass-core`
- [~] Phase 2. Needs Registry: `answer_spec` for all 44 needs, frame style guides and examples, loader enforcement (3573367); pending: needs status computation (`engine/needs.py`, in `feat/compass-core`)
- [~] Phase 3. Calls: ports, profiles, placeholders and the per-spec scripted gateway (c27536b), `plan_questions` (db7a0a2); in progress: `audit_questions`, `rephrase_question`, `understand_turn` v2, `update_summary`; pending: `compile_brief`
- [ ] Phase 4. Engine: fact ingestion under the legacy flow, planner path behind a flag, follow-ups, replan, stop, budgets, edit/pivot/upload, `domain_facts`, compile from the ledger
- [ ] Phase 5. UI in `apps/web`: groups, answer-all, why-popover, Ask me more, Q&A editor, MOCK banner, fallback badge
- [ ] Phase 6. Offline tests: scenario and persona migration, SB-QP-01..20, property tests, needle test, metrics
- [ ] Phase 6b. Live prerequisites: persisted Compass spend (`reconcile`), batch runner, cassette tests
- [ ] Phase 7. Flip to the planner; live batches E, F, G (needs the owner's second key); cassettes
- [ ] Phase 8. Remove the legacy path, refresh docs, `just ci`, merge `--no-ff`, report
