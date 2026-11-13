# Spec agent build plan (prompt 12 v4)

Resume point for any session. Branch `feat/spec-agent`, safe-point tag `pre/spec-agent-20261114-0258`. Design: `docs/architecture-v3/15..19` (doc 19 wins, then 18, 17, 16, 15). Decisions from D-174. Money: hard cap USD 5.00 (`SPEC_LIVE_BUDGET_USD`, tripwire 4.50). Tick a box in the commit that finishes it.

## Phases and exit checks

- [x] **W0 Checkpoint, branch, design docs copied, packages scaffolded, layering contracts (11 kept)**
- [ ] **S0 Foundation** (no model, no money): graph model and node envelope, validator framework, `spec-config.yaml` with consistency check, consumer profiles, vocabularies, free-tool smoke tests, portable call harness, compass-intake and Research reader. *Exit (PS-0, PS-1):* unit tests green, import-linter green, nine inputs load as `SpecInput`, drift and readiness tests.
- [ ] **S1 Deterministic core** (no model): libraries v1, R-INV derivation, release planner, trust grading, tokens, mock modes, hero skeleton, glossary, renderer, sharder, closure, slicer, task cards, prefix bundle, build plan, needs, blocks, pilot picks, `spec check`, repair-ladder code fixes, pack linter, fixture generator. *Exit (PS-2):* no budget breach after re-shard, identical bytes across two runs and two locales, identical prefix, re-package changes only sharding and task cards.
- [ ] **S6a Bench skeleton**: auto-select with tests, API, journey rail, pack explorer, fake app on 8150, `scripts/spec_app.py`, justfile recipes.
- [ ] **S2 Product lane** (live): SPEC-PLAN, PROD-MODULE, PROD-INTEG, PROD-JOURNEYS, money wrapper, checkpoints and resume, single-flight. *Exit (PS-3).*
- [ ] **S3 Design lane**: DSGN, tokens by code, preset fallback.
- [ ] **S4 Screens lane**: GLOSS-L, SCRN, MOCK-HERO, BEHAV, COPY-L, freedom lint. *Exit (PS-4, PS-5).*
- [ ] **S5 Self-check and Verify for deep**: three gates, ladder, `spec check`, G1.5 and G2 data, `agents/verify`, proof tools under `evals/`. *Exit (PS-6, PS-7).*
- [ ] **S6b Bench second half**: simulator, probe runner, content maps, journeys, graph, data views, G views, preview export. *Exit (PS-11).*
- [ ] **S7 Proof and big runs** (live): price check, PS-3..PS-7, PS-12a, PS-12b, PS-8, PS-9 (200 cases), PS-10, PS-12c if money allows, Bench smoke.
- [ ] **Report**: `docs/spec-agent/REPORT-<date>.md`, `docs/api/spec-agent.md`, CURRENT_STATE, PROGRESS, Bench left running on 8130.

## Rules that bite
Meaning is Spec's, expression is the front end's. No vendor or model name in Spec code. No web call from Spec. `LLM_MODE=live` is the only live switch. Money is integer micro-USD. Do not read `.env`. Do not merge into `main`. Nothing is Proven until its gate passes.
