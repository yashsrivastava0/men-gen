# 09. Roadmap, repo delta and builder prompts

My role (owner, 2026-11-09): architecture, guidance, prompts and review; the other Claude (Claude Code on the owner's Windows 11 PC) implements. I never write or run code, I do not approve live spend or commits on the owner's behalf, and I cannot see the repo.

## 1. Operating loop (agent by agent)
For every step: (1) design review with the owner (the owner approves or changes); (2) I write **one** builder prompt with scope, interfaces and acceptance criteria from doc 07; (3) the owner runs it; (4) the owner pastes the report; (5) I judge it against the gate, update `STATE.md` and the log; (6) next step only when the gate passes. Live LLM spend needs an explicit yes naming the batch, allowance, expected cost and what it proves (existing rule). Commits go to local `main` after tests pass; nothing is pushed.

The architecture folder must reach the builder: copy `architecture/` into the repo as `docs/architecture-v3/` (the repo's markdown rule allows `docs/**`), and have the builder log decisions in `DECISIONS.md` and write ADRs where noted below.

## 2. Roadmap
Sizes are relative (S/M/L), not durations. "Gate" refers to doc 07 section 7.
| Step | Scope | Depends on | Gate | Size | Owner decisions |
|---|---|---|---|---|---|
| 0 | **Read-only repo audit** against this architecture (section 4) | none | Report received; reuse map complete | S | none |
| 1 | **Backbone:** Spec Graph v3 (nodes, edges, tiers, provenance, status, stable IDs, tombstones, aliases, variants), schema registry, validator framework (V-01 to V-05 first), invariants I-1 to I-12 as property tests, deterministic renderer skeleton, artifact store | 0 | invariants pass; determinism; ADRs written | M | contracts versioning (`contracts-v2` tag vs additive) |
| 2 | **Gateway + eval skeleton:** Anthropic provider, failover, call-catalog registration (doc 02 §4.1), cassette parity; SpecBench-Mobile harness skeleton in replay mode | 1 | parity tests; harness runs one case | M | live-spend approvals when needed |
| 3 | **Stage 1 Discovery** incl. document intake, slot graph, domain lens, ambiguity scan, Feature Catalog, Brief View data | 1, 2 | doc 07 gate for Stage 1 | L | confirm Express merged-UI behavior (OI-10) |
| 4 | **Stage 2 Research** (generic jobs, depth budgets, consumer-grouped output) | 3 | Stage 2 gate | M | search provider and budget |
| 5 | **Module Library v1 (~20 modules) + Stage 3 Blueprint + Contract v0** | 4 | Stage 3 gate | L | who reviews modules (OI-5) |
| 6 | **Stage 4 Design** (tokens, DTCG, `DESIGN.md`, composition rules, directions) | 5 | Stage 4 gate | M | none |
| 7 | **Stage 5 Screens** (batch writer, global behaviors, copy store, complexity scoring) | 6 | Stage 5 gate | L | none |
| 8 | **Stage 6 Verify and package** (probe, critic, repair, Build Plan DAG, journeys, renderer, Spec Server) | 7 | Stage 6 gate | L | canary model choice |
| 9 | **Cross-cutting:** Program model (multi-app), change controller (lanes, change sets, undo, variants), review-surface data (app map, wireframes, diff), G1.5/G2.5/G4 plumbing | 8 | cross-cutting gates | L | pricing/credit display (later) |
| 10 | **Design the Front-end engine** (separate design doc): preview infra, harness bake-off (OI-3), model routing, visual QA, Review Mode (RM-01..18) | 9 | design approved | M | harness choice |
| 11+ | Backend engine design; QA engine design; then builds in the same agent-by-agent loop | 10 | per engine | L | backend stack (OI-1) |
Parallelism between steps: Steps 5 (Module Library authoring) and 4 can overlap, and the eval harness grows with every stage; everything else follows the pipeline order the owner asked for.

## 3. Repo delta (per documents; the repo is unchecked, so this is a hypothesis for Step 0 to confirm)
| Package / app | Change |
|---|---|
| `contracts` | New schemas: Brief v3, Research, Blueprint, Design, Screens, Spec Graph, Change Set, Build Plan, Approved Build Manifest. The `contracts-v1` tag implies additive evolution; Program (one session = many apps) is a structural change, so decide a `contracts-v2` tag with a migration ADR |
| `persistence` | Graph tables with version ranges, artifact store, variants, event log extensions; keep the validated-fake-vs-real contract suite approach |
| `llm-gateway` | Anthropic provider, vendor failover, new call specs, per-tenant routing; keep budget, ledger and live-gating rules |
| `prompts` | New templates per call catalog; retire archetype-specific prompts |
| `content` | Capability Modules and recipes replace archetype Domain Packs (migrate the five archetypes into modules); Stack Kit versions; design presets; keep content tests |
| `discovery` | Add document intake, domain lens, Feature Catalog, ambiguity scan; reuse slot machinery, card engine, intents, safety handling |
| `research` | Generic jobs, depth budgets without the 10-query ceiling, consumer-grouped output with size cap |
| `spec-builder` | Split into `blueprint`, `design`, `screens`, `verify` packages (new import-linter contracts); reuse baseline-expansion ideas and validators where they fit |
| `spec-loader` | Becomes the Spec Server (MCP tools plus `AGENTS.md`), keeps slices and budgets |
| `flow` | Program model, generalized gates (G1, G1.5, G2, G2.5, G3, G4), change controller; keep the pure state machine, gate invariants and property tests |
| `context-api` | Endpoints for programs, change sets, views, downloads, preview links, share links |
| `cli` | `sim` and `bench` commands for SpecBench-Mobile |
Known gaps from the Nov 8 status that remain relevant (Master 07 §3): live quality unmeasured beyond L0, token estimates unverified, review-2 coverage gaps. Branch and security items (`feat/apisec`, N3/N4/N6-N11) are **parked** by the owner's de-prioritization of security; Step 0 should still report their state.

## 4. Step 0: read-only repo audit (prompt draft for the builder)
> You are auditing the Mobile design engine repo against a new architecture. This is **read-only**: no edits, no commits, no live LLM calls, no printing of `.env` or secrets. Use the Read and Grep tools (not shell commands) to inspect files, because the live-guard hook matches command text. Check free memory before running anything heavy; run at most one offline test suite at a time, in the foreground, and only if memory allows.
>
> Read `docs/architecture-v3/00-index-and-decisions.md`, then `03-context-engine.md`, `04-spec-package-and-handoffs.md`, `05-change-management-and-edge-cases.md`, `07-quality-evals-and-models.md`, `09-roadmap-and-repo-delta.md` (the repo-delta table).
>
> Report, in this order and concisely:
> 1. **Git state:** current branch, `main` hash, tags, other branches and worktrees (names only), uncommitted changes (file list and one-line purpose), whether `feat/apisec` and `feat/regex` are merged.
> 2. **Inventory:** each package and app with its public API (module names and main classes), approximate size, and test count; layering contracts (import-linter) as configured.
> 3. **Verification record:** last known test results from `docs/implementation/PROGRESS.md`; do not re-run live batches. State whether L1, L2 and L3 ran and what the ledger says about spend by batch; give the L3 failure cause if recorded.
> 4. **Reuse map:** for each component in the repo-delta table, classify the current code as *reuse as is*, *adapt* or *replace*, with the evidence (file paths and one line each).
> 5. **Risks and conflicts:** places where the new architecture conflicts with current contracts, DB schema, ADRs or decisions (D-001..D-073); anything that blocks the Backbone step.
> 6. **Open discrepancies** between the repo and the project documents (the repo wins).
> 7. **Proposed Backbone plan:** the minimum change list for Step 1 (Spec Graph v3, schema registry, validators, invariants), with file-level pointers.
> Do not propose code in this report. End with a list of questions that only the owner can answer.

## 5. Template for every later builder prompt
1. *Context:* paths of the architecture docs to read first and the specific sections that bind this step.
2. *Goal:* one sentence.
3. *Scope and non-goals:* what is in, what is explicitly out.
4. *Interfaces:* input and output schemas, call specs (ids from doc 02 §4.1), validators to implement (ids from doc 07 §2), invariants touched.
5. *Constraints:* existing repo rules (Python 3.13, FastAPI, Pydantic v2, PostgreSQL only, no agent frameworks, only the gateway imports provider SDKs, offline-first tests, cassettes, Windows quirks) and live-spend rules.
6. *Acceptance criteria:* the stage gate from doc 07 §7 plus the named edge-case scenarios.
7. *Tests required:* unit, property, scenario, eval cases to add.
8. *Report format:* what changed, tests run with counts, metrics against the gate, deviations from the prompt, ADRs and decisions logged, open questions.

## 6. Later design documents (outside this folder's scope)
Front-end engine design (preview infrastructure, harness bake-off, worker model routing, visual QA, Review Mode implementation), Backend engine design (Backend Kit, conformance and replay), QA engine design (journey replay, store-readiness). Each follows the same pattern: research, architecture, owner approval, then the agent-by-agent loop.
