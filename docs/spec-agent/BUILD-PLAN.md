# Spec agent build plan (prompt 12 v4)

Resume point for any session. Branch `feat/spec-agent`, safe-point tag `pre/spec-agent-20261114-0258`. Design: `docs/architecture-v3/15..19` (doc 19 wins, then 18, 17, 16, 15). Decisions from D-174. Money: hard cap USD 5.00 (`SPEC_LIVE_BUDGET_USD`, tripwire 4.50). Tick a box in the commit that finishes it.

Status on 2026-11-14: the build is in git through S6b, and the edge-case suite is built but was not green on the working tree that day. The live proof has started. A tick here means the build item is in git. A proof gate is never ticked on this page; "Proof gates" below says what has been run.

## Phases and exit checks

- [x] **W0 Checkpoint, branch, design docs copied, packages scaffolded, layering contracts (11 kept)**
- [x] **S0 Foundation** (no model, no money): graph model and node envelope, validator framework, `spec-config.yaml` with consistency check, consumer profiles, vocabularies, free-tool smoke tests, portable call harness, compass-intake and Research reader. *Exit (PS-0, PS-1):* unit tests green, import-linter green, nine inputs load as `SpecInput`, drift and readiness tests. *PS-0 and PS-1: no saved gate report.*
- [x] **S1 Deterministic core** (no model): libraries v1, R-INV derivation, release planner, trust grading, tokens, mock modes, hero skeleton, glossary, renderer, sharder, closure, slicer, task cards, prefix bundle, build plan, needs, blocks, pilot picks, `spec check`, repair-ladder code fixes, pack linter, fixture generator. *Exit (PS-2):* no budget breach after re-shard, identical bytes across two runs and two locales, identical prefix, re-package changes only sharding and task cards. *PS-2: no saved gate report. Commit 4ab1546 says it ran on the three real exports.*
- [x] **S6a Bench skeleton**: auto-select with tests, API, journey rail, pack explorer, fake app on 8150, `scripts/spec_app.py`, justfile recipes. *Not done: the Bench was never opened in a real browser. `agents/spec/tests/e2e/browser_check.py` exists (it needs Playwright and a browser); no saved result.*
- [x] **S2 Product lane** (live): SPEC-PLAN, PROD-MODULE, PROD-INTEG, PROD-JOURNEYS, money wrapper, checkpoints and resume, single-flight. *Exit (PS-3): run once, not scored.*
- [x] **S3 Design lane**: DSGN, tokens by code, preset fallback.
- [x] **S4 Screens lane**: GLOSS-L, SCRN, MOCK-HERO, BEHAV, COPY-L, freedom lint. *Exit (PS-4, PS-5): PS-4 not run; PS-5 run once, not scored. Not done: Screen lacks doc 15 5.7a type-specific fields.*
- [x] **S5 Self-check and Verify for deep**: three gates, ladder, `spec check`, G1.5 and G2 data, `agents/verify`, proof tools under `evals/`. *Exit (PS-6, PS-7): not run. Not done: G1.5 "change" moves are recorded but not applied. Verify runs the final stage in the service and class A patches only.*
- [x] **S6b Bench second half** (offline parts): simulator, probe runner, content maps, journeys, graph, data views, G views, preview export. *Exit (PS-11): not run. Not done: the Bench was never opened in a real browser.*
- [ ] **Edge-case suite, 200 cases** (`tests/edge`, offline): built in c6c946b. *Not ticked: on 2026-11-14 the working tree gave 208 passed, 68 xfailed and 1 failed (`test_fn_31`, the integration re-ask). Tick it when that test passes. PS-9's live part is not run.*
- [ ] **S7 Proof and big runs** (live): price check, PS-3..PS-7, PS-12a, PS-12b, PS-8, PS-9 (200 cases), PS-10, PS-12c if money allows, Bench smoke. *Started: eight single live runs are saved (six Quick, one Standard, one Thorough with Verify), none scored. Details in `docs/spec-agent/README.md`.*
- [ ] **Report**: `docs/spec-agent/REPORT-<date>.md`, `docs/api/spec-agent.md`, CURRENT_STATE, PROGRESS, Bench left running on 8130.

## Proof gates

A proof gate is never ticked here. "Run once, not scored" means a saved live report covers the gate's calls. The bars in doc 16 were not applied, so nothing has passed. Reports: `docs/spec-agent/outputs/live-2026-11-14/`.

- [ ] **PS-0** offline build: no saved gate report.
- [ ] **PS-1** intake on the nine real exports: no saved gate report.
- [ ] **PS-2** packager and budgets: no saved gate report.
- [ ] **PS-3** derivation and phases, Product lane: run once, not scored (the Quick, Standard and Thorough live reports ran the Product lane). Not done: the run on the nine exports, and the owner's 10-minute read of the Thorough G1.5 view.
- [ ] **PS-4** freedom: not run. Not done: the freedom probe and the owner's read of five cards.
- [ ] **PS-5** writing quality per step: run once, not scored (six Quick runs and one Standard run saved). Not done: the bar check and the ranges across runs; Standard has one run, not three.
- [ ] **PS-6** consumer probe and developer test: not run.
- [ ] **PS-7** planted-defect test: not run. Verify did run once inside the Thorough live run; that is not this test.
- [ ] **PS-8** scale: not run.
- [ ] **PS-9** edge-case suite and provider switch: the offline suite is built (above); the live provider-switch runs are not run.
- [ ] **PS-10** content model: not run.
- [ ] **PS-11** Spec Bench: not run.
- [ ] **PS-12a** synthetic 60-screen app: not run.
- [ ] **PS-12b** real Thorough export with Verify: run once, not scored (one Thorough run, status approved, 83 calls, 42 task cards, largest read 27,868 of 28,000 tokens, no card over budget).
- [ ] **PS-12c** Uber Eats-class program: not run.

## Rules that bite
Meaning is Spec's, expression is the front end's. No vendor or model name in Spec code. No web call from Spec. Live runs follow CLAUDE.md rule 13. Money is integer micro-USD. Do not read `.env`. Do not merge into `main`. Nothing is Proven until its gate passes.
