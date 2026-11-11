# Final report: Context Engine build

Status date: 2026-11-08. Draft by the lead; sections marked **PENDING** wait for work that was blocked or still running (see "Live evidence" and "Known weaknesses").

## 1. What was built, and what was deliberately not

**Built (all in this repo, Python 3.13, async, PostgreSQL only):**

- A uv workspace with 11 packages and 3 apps: `core`, `contracts`, `llm-gateway`, `prompts`, `content`, `persistence`, `spec-loader`, `discovery`, `research`, `spec-builder`, `flow`, plus `context-api`, `llm-gateway-api` and the `mde` CLI.
- **Contracts v1.0.x**: slots, cards, events, Brief, Research Note, spec blocks, Plan view, policy, content models, gateway types, call input/output models for C1..C14 and S1, repository and engine ports. Exported as JSON Schema (`docs/api/schemas/`) and OpenAPI (`docs/api/openapi.json`).
- **LLM Gateway** (library and HTTP API, same interface): call-spec pipeline with strict-schema conversion, one repair, retries, circuit breaker per route and tool class, mock/replay/record/scripted/OpenAI-Responses providers, untrusted-content wrapping, and the budget guard (hard cap USD 6.00, effective cap 5.70, scoped caps, 50/80/95% tripwires, file-locked ledger with an integrity guard, fail-closed accounting).
- **Discover engine** (intent router, slot store with provenance, question selector, bank cards, policy guard, Brief compiler, change interpreter), **Research engine** (C6 plan, S1 search, C7 extract on untrusted text, C8 synthesize, cross-check, depth caps, query privacy), **Spec Builder** (valid-by-construction baseline from Domain Packs, validated proposals C9/C10, nine validators, critic and repair, deterministic renderer with an embedded file hash, Plan view, build sequence S0..S10, incremental builds) and the read-only **Spec Loader** with a consumer simulator.
- **Flow controller**: pure state machine, gates with hash binding, approvals (taps and typed), Enterprise multi-approver rules with separation of duties, run manager with cooperative cancellation, change handling per spec 3.6, budget pause, hand-off to a local git repo, and `spec_ready` only after Gate 2 approval of the current hash and a successful commit.
- **Content**: question bank for five archetypes (English, Hindi, Hinglish), Domain Packs, 27 screen recipes, six WCAG-AA themes, a Stack Profile, four policy profiles (platform default, guided default, an IT-services enterprise example, a regulated example).
- **Interfaces**: FastAPI context API with SSE replay and an in-process job worker, and the `mde` CLI (`chat`, `sim`, `budget`, `doctor`, `spec ...`).
- **Tests and evals**: 40 scenarios pinned one-to-one to the spec's situation catalog, per-archetype end-to-end journeys (memory, Postgres and HTTP drivers), Enterprise journeys, injection / secret / tenant-isolation suites, a stateful property suite for the gate invariants, eval datasets, graders and runners.

**Deliberately not built (per the brief):** chat UI, frontend/backend/QA engines, real SSO/SCIM, real publishing, billing, server-side fetching of user URLs, PDF parsing (text, markdown and images only; other types are refused with a plain message).

## 2. How to run it (five commands)

```
uv sync --all-packages
uv run python scripts/dev_db.py up        # private PostgreSQL on port 54329 (just db-up)
just migrate
uv run mde chat --local                   # whole product in mock mode, no key, no network
just ci                                   # everything offline
```

`just dev` starts the API (`uv run python -m mde_context_api --port 8100`; on Windows always through that entry point, see the troubleshooting runbook) and `uv run mde chat` talks to it. More: `docs/runbooks/local-dev.md`.

## 3. Architecture on one page

Six steps, two gates: Discover (canned greeting, mostly multiple-choice cards) -> Gate 1 (the Brief) -> Research (after Gate 1, bounded pipeline, never alongside the interview) -> Build the spec (baseline the model only refines) -> Gate 2 (the Plan, bound to the file hash) -> Hand off (`spec_ready`). Plain code controls every step; each LLM call is a single structured call through the gateway. Engines never import each other: the flow wires them through ports in `contracts`, and `import-linter` enforces the layering and "only the gateway imports `openai`". Details: `docs/architecture/overview.md`, `flow-and-gates.md`, `llm-gateway.md`, `data-model.md`.

## 4. Decisions that deviate from the brief, and why

All are logged in `docs/implementation/DECISIONS.md` (D-001..D-070) and the ADRs. The main ones:

- **Postgres runs as a private cluster** on port 54329 from locally installed binaries, because Docker Desktop was paused (never touched) and port 5432 belongs to other projects (D-003). `docker-compose.yml` remains for CI.
- **Portable file lock** instead of `fcntl.flock` (Windows is the dev platform) (D-004).
- **`CallSpec` and the gateway types live in `contracts`**, not `prompts`, so the gateway never imports prompts (D-007). LLM outputs carry slot values as `value_text | value_list | value_bool` because strict schemas have no open objects (D-008).
- **Contracts evolve additively** after the `contracts-v1` tag; one retype (pack navigation) got ADR 0003 and a patch version (D-009, D-036, D-040).
- **Faithful in-memory store**, validated against the same 22-check contract suite as Postgres, so flow unit tests mean something (D-043, D-049).
- **Transactions never span an engine call**, because a unit of work pins a pooled connection (D-049).
- **One spec hash rule** shared by the loader, builder and flow (D-061).
- **The budget guard fails closed** and the cap is clamped in code, not only configured (D-056, D-064).

## 5. Test and eval results (numbers)

- Clean clone of `main` at 9d72ff8, no `.env`: `just ci` exits 0 (D-070): **4,167 offline tests**, **396 Postgres integration tests**, **219 end-to-end tests**, docs check 0 problems, security suite 142 passed (the real-key comparison skips without a `.env`).
- `pyright`: 0 errors over packages, apps, tests, evals and scripts (strict on contracts, core, gateway, flow and loader sources). `ruff` and the import-linter contracts: clean.
- 40/40 situation-catalog scenarios pass on the in-memory store (a subset also on Postgres and over HTTP); no strict-xfail pins remain.
- Mock-mode eval baseline (cards per Guided session, assumption rate, first-pass validator rate, screens per app, tokens per slice, and so on): `docs/evals/reports/mock-baseline.md`. Mock numbers describe the deterministic baseline, **not** model quality.
- The end-to-end harness found one blocker that no unit test could (two packages hashing the spec differently); the lesson is recorded in D-061 and ADR 0006.

## 6. Live spend by batch

| Batch | Purpose | Status | Spend |
| --- | --- | --- | --- |
| L0 smoke | model availability, strict schema, reasoning effort, web search with citations, usage parsing, budget reconciliation | **done, 7 of 7 passed** | USD 0.0221 |
| L1 Discover | persona conversations, cassettes | **PENDING** | 0 |
| L2 Research | three Light-depth runs | **PENDING** | 0 |
| L3 End to end | five full guided sessions, goldens | **PENDING** | 0 |
| L4 Safety and probes | injection payloads, sufficiency probe, critic check | **PENDING** | 0 |
| **Total** | cap USD 6.00, effective cap 5.70 | | **USD 0.0222** |

Planning figures from L0: one web search costs about USD 0.011 (search content counts as input tokens), so a Light research run is about USD 0.07 before extraction and synthesis; the full L1..L4 plan is estimated at about USD 2.5 with hard stops per batch allowance.

## 7. Live evidence (Definition of Done item 5)

**PENDING.** Only L0 ran live. The paid batches L1..L4 were not run; the operator's decision is open. Until they run, "three live end-to-end sessions on different archetypes with recorded cassettes" is **unmet** and the quality of real-model output (spec proposals, critic, research synthesis) is **unmeasured** beyond L0. The runner, its allowance stops, cassette recording and refusal paths are built and tested offline (`evals/runners/live_batch.py`).

## 8. Known weaknesses and risks

- **Real-model quality is untested** beyond L0: prompts, caps and reasoning effort were tuned against mocks. Watch for `incomplete` retries on reasoning-heavy calls (C9/C10/C12) and strict-schema rejections the offline conversion cannot foresee.
- **Latency after Gate 1** (research plus build) is unmeasured live; mock builds take about 2 s.
- **Research** plans at most 10 searches (2 queries x 5 web jobs), so the Standard (12) and Deep (20) ceilings are enforced but unreachable; citations quality is unmeasured live.
- **Two independent security reviews** ran, and every finding is fixed or accepted with a reason.
  - Review 1: three high and nine medium findings, all fixed with regression tests (D-055..D-058).
  - Review 2 (final tree): the first review's fixes held (three only partially) and it found three new high items plus lower ones (D-071).
    - Quadratic regexes on typed text, uploads, search results and change requests (N1, N2, N5): **fixed and merged** (D-073). Every regex over user, upload, search or model text is now linear, guarded by `tests/security/test_regex_linearity.py` (58 tests).
    - Dev auth let one token holder mint identities and roles, and the caller picked mode and policy (N3, N4): **fixed and merged** (D-074). `API_TOKENS` binds a token to a tenant, user and roles on the server, `TENANT_POLICIES` binds policies, and `python -m mde_context_api` refuses a non-loopback bind without tokens.
    - No abuse limits, `/v1/budget` leaking other tenants' spend, a 500 on a huge `Last-Event-ID`, unscreened id fields, and unredacted emails and phones in cassettes (N6 to N11): **fixed and merged** (D-074, 20 mutation checks, one per fix, all killed).
    - Accepted with a reason: N12 (summary lines containing `hash: ...`; the loader reads only the front matter) and N14 (the dev tooling allow-list, CI action tags, the org profile overwritable by any user of the tenant).
    - Known gaps in the merged hardening (D-074): the rate and stream limits are per process and keyed by the declared identity, so dev-header callers can dodge them; `mde sim` over HTTP makes 10 sessions in a minute so a second run within the minute gets a 429 and the CLI has no 429 handling (`API_SESSION_RATE_PER_MINUTE=0` disables the limit); the token table and bindings are read at start-up; running the module path directly with uvicorn bypasses the bind guard (unsupported).
    - Not covered by review 2: Postgres integration runs, SSE exhaustion beyond reading the code, `openai_responses.py`, `proposal_ops.py` and `baseline.py` in depth, the hand-off git adapter beyond name validation, dependency CVEs, and replay of real cassettes.
- Accepted gaps: policy checks run in the interview only (not at gates); flow-written notices are English only; Org Profile is stored but not yet read by Discover; `/v1/budget` and the CLI `--local` mode are development conveniences.
- Windows specifics: async Postgres needs a selector event loop (use the module entry points); the symlink test is skipped on Windows.

## 9. Recommended next steps for the frontend-engine team

Read `docs/specs/frontend-consumption.md` and `docs/specs/app-spec-format.md`; consume the file only through `mde_spec_loader` (`slice()` per build step and screen; a screen task is well under 10k estimated tokens, about 3k in the 50-screen sample); run `scripts/simulate_frontend_consumer.py` against every golden spec in CI; use `raise_decision` for gaps; stamp generated files with `@spec SCR-014@v7`; use `diff()` for incremental runs; treat locked screens as read-only.

## 10. External backstop

Set a USD 6 spend limit on the OpenAI project in the provider dashboard. The in-repo budget guard is strict, but a dashboard limit is the only control that does not depend on this code being correct.
