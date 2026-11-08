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
