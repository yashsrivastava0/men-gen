# Handoff: Context Engine build

Written 2026-11-08 by the lead, at `main` = `6c23e1b` (170 commits, tag `contracts-v1`). Read this first, then `PROGRESS.md`, `DECISIONS.md` (D-001 to D-073), `BRIEF.md` and `docs/product/context-engine-spec.md`. `REPORT.md` is the draft final report. Everything below was checked against the repository when this was written.

## 1. Where things stand (one paragraph)

The Context Engine is built end to end and verified offline: the six steps (Discover, Gate 1, Research, Build the spec, Gate 2, Hand off) run through the real engines, the API and the CLI, in mock mode, for all five archetypes, on three drivers (in-process, Postgres, HTTP). Two independent security reviews were done and their findings fixed or routed. **Not done:** (a) the paid live batches L1 to L4, blocked on an owner decision (section 2); (b) one fix branch (`feat/apisec`) is still in flight (section 3); (c) `REPORT.md` is a draft and the Definition of Done list is not ticked. Live spend so far is **USD 0.0222 of 6.00** (L0 smoke only, 10 live calls).

## 2. The one open decision: the paid live batches

The batch runner (`evals/runners/live_batch.py`, batches L1 Discover, L2 Research, L3 end to end, L4 safety and probes; allowances 1.20, 1.00, 2.00 and 0.50 USD, reserve at least 1.00; estimated total about USD 2.5) is built and tested offline. The brief's evidence item "three live end-to-end sessions on different archetypes with recorded cassettes" is **unmet until they run**.

The owner chooses one of:

1. **Let the lead run the batches** one at a time with the checks below. No other agents may be running meanwhile.
2. **Run the batches yourself** in your own terminal at the repo root. Check first with `--dry-run`, then run, then check spend:

```powershell
$env:LLM_MODE='live'; uv run python -m evals.runners.live_batch --batch L1 --dry-run
```
```powershell
$env:LLM_MODE='live'; uv run python -m evals.runners.live_batch --batch L1
```
```powershell
uv run python -m mde_llm_gateway.budget_cli
```

   Repeat with `L2`, `L3`, `L4`. Stop and inspect after each batch (fallbacks used, failed calls, repaired calls, cost).
3. **Skip live work.** Then say so plainly in `REPORT.md` (item 5 of the Definition of Done stays unmet).

Guard rails that stay on in every case: hard cap USD 6.00 clamped in code (effective 5.70), per-scope caps, tripwires at 50, 80 and 95 percent, fail-closed accounting, a ledger integrity guard, per-unit allowance stops in the runner (measured from the ledger, not estimated), cassettes recorded for every live response, refusal unless `LLM_MODE=live` is set. Also recommended: set a USD 6 limit on the OpenAI project in the provider dashboard.

What live runs will probably reveal (prepared for, but untested): strict-schema rejections the offline converter cannot foresee, `incomplete` retries on reasoning-heavy calls (C9, C10, C12), prompt quality and cost per session. L0 already confirmed: `gpt-6-luna` is available, strict JSON schema works, reasoning effort low and none work, web search returns citations (one search about USD 0.011), `max_tool_calls` is respected, prompt caching works. Details: `docs/architecture/llm-gateway.md` ("Verified live: L0").

## 3. Exact repository state

- `main` = `6c23e1b`, clean working tree. Local Postgres is up at `127.0.0.1:54329` (private cluster, `data/pg`, started by `uv run python scripts/dev_db.py up`).
- **In flight: branch `feat/apisec`, worktree `..\wt-apisec`** (an agent was working there): one commit so far (`3a51dd1`: token identity mode, tenant policy binding, abuse limits, budget totals only, safe `Last-Event-ID`, id field patterns and screening, cassette PII and key check) plus **uncommitted changes in the worktree**. It implements review-2 findings N3, N4, N6 to N11 (section 7). It is **not merged and not yet verified**. If the agent is gone: inspect `git -C ..\wt-apisec status` and `git -C ..\wt-apisec log main..HEAD`, finish or discard, test, commit, then merge into `main`.
- All other worktrees and merged branches are done; merged `feat/*` branches still exist (safe to delete with `git branch -d`, which refuses unmerged ones). The throwaway `ci-clone` was deleted by the owner.
- Ledger: `data/.budget/ledger.jsonl` (gitignored) holds the L0 spend; `<ledger>.guard` protects it from deletion or truncation (reset only deliberately).

## 4. Resume checklist (in order)

1. `cd` to the repo, `$env:Path += ";$env:USERPROFILE\.local\bin"` (for `uv` and `just`), `uv sync --all-packages`, `uv run python scripts/dev_db.py up`.
2. Read `git log --oneline -20`, `git status`, `git worktree list` (rule: finish what is in a worktree before removing it).
3. Resolve `feat/apisec` (section 3): commit, verify in the worktree, merge into `main`, **then verify on `main`** (the lesson of D-063).
4. Verify `main` fully: `uv run pytest -m "not integration and not live" -q`, `uv run pytest -m integration -q`, `uv run pyright packages apps tests evals scripts`, `uv run ruff check .`, `uv run lint-imports`, `uv run python scripts/docs_check.py`. Then the clean-checkout `just ci` as a **detached worktree** (`git worktree add --detach ..\tmp-ci HEAD`), removed right after with `git worktree remove --force`.
5. Get the owner's decision on the live batches (section 2) and run them in order L1, L2, L3, L4, one at a time; after each: budget CLI, record the total in `PROGRESS.md`, inspect, fix prompts or caps before the next. Cassettes land in `tests/cassettes` (commit them; the secret scan covers them); then add replay-mode tests so later runs cost nothing.
6. Update `REPORT.md` (live evidence, spend table, review 2 outcome, final test numbers), tick the Definition of Done list in `PROGRESS.md`, add a final retrospective line in `DECISIONS.md`.
7. End-of-job sweep: `git worktree list` shows only the main checkout; delete merged branches.

## 5. What was built (map)

- **Workspace:** uv monorepo, Python 3.13, 11 packages (`core`, `contracts`, `llm-gateway`, `prompts`, `content`, `persistence`, `spec-loader`, `discovery`, `research`, `spec-builder`, `flow`) and 3 apps (`context-api`, `llm-gateway-api`, `cli`). Layering enforced by `import-linter` (6 contracts; `openai` only in the gateway; no agent frameworks; no SQLite).
- **Contracts v1.0.x:** slots, cards, events, Brief, Research Note, spec blocks, Plan view, policy, content models, gateway types, call IO for C1..C14 and S1, repository and engine ports (`BudgetGate`, `Store`/`UnitOfWork`, hand-off ports). Additive evolution only after the `contracts-v1` tag (ADR 0003, ADR 0011).
- **LLM Gateway:** 16 call specs (templates, mock handlers, fixtures, fallbacks), strict-schema conversion, repair, retries, circuit breaker per route and tool class, providers (mock, scripted, replay, record, OpenAI Responses), untrusted-content wrapping, budget guard and ledger, HTTP API with a client behind the same interface.
- **Engines:** Discover (router, slot store, selector, policy guard, Brief compiler, change interpreter, hi and hi-Latn), Research (plan, search, extract on untrusted text, synthesize, cross-check, privacy), Spec Builder (baseline from Domain Packs, validated proposals, nine validators, critic and repair, deterministic renderer with embedded file hash, Plan view, build sequence, incremental builds), Spec Loader and consumer simulator.
- **Flow:** state machine, gates with hash binding, approvals (tap and typed), multi-approver Enterprise rules with separation of duties and role precedence, cooperative cancellation, budget pause, change handling, local git hand-off; `spec_ready` only after Gate 2 approval of the current hash and a successful commit. Transactions never span an engine call.
- **Persistence:** Postgres only, Alembic, tenant isolation (composite keys), idempotent events, `SKIP LOCKED` queue, worker, a 22-check store contract suite that both the Postgres store and the in-memory fake pass.
- **Interfaces:** FastAPI context API (REST, SSE with replay, in-process worker; Windows-safe entry point `python -m mde_context_api`), `mde` CLI (`chat`, `sim`, `budget`, `doctor`, `spec ...`).
- **Content (data):** question bank for 5 archetypes in en, hi, hi-Latn; Domain Packs; 27 recipes; 6 WCAG-AA themes; Stack Profile; 4 policies; policy alternatives.
- **Tests and evals:** 40 scenarios pinned one to one to the spec table, per-archetype journeys (Guided and Enterprise) over memory, Postgres and HTTP, injection, secret, tenant-isolation, regex-linearity and static-ban suites, a stateful property suite for the gate invariants, eval datasets, graders, mock and live runners.
- **Docs:** `docs/README.md` indexes everything: architecture (overview, repo layout, flow and gates, gateway, data model, security), specs, package pages, 11 ADRs, runbooks (local dev, live budget, troubleshooting), prompt catalog, exported OpenAPI and JSON Schemas, eval plan.

## 6. Verification record (and what is NOT current)

- Last full gate on `main` (3f6aa12): 4,379 offline tests passed, 396 Postgres integration tests passed, pyright 0 errors over packages, apps, tests, evals and scripts; ruff, formatting, layering and docs check clean.
- Clean-clone `just ci` (9d72ff8, no `.env`): exit 0; 4,167 offline, 396 integration, 219 end-to-end, docs check 0 problems, security suite 142 passed (the real-key comparison skips without `.env`, expected).
- **Since then** (docs merge, regex merge, review-2 small fixes, builder spec version bump) only targeted suites were run: prompts and spec-builder 1,043 passed; discovery, prompts, research, spec-loader and security 1,525 passed. **The whole suite and the integration suite have not been re-run after those merges**, and nothing from `feat/apisec` is verified. Step 4 of the checklist closes this.

## 7. Security reviews (details: `docs/architecture/security.md`, D-055 to D-058, D-071, D-073)

- Review 1: 3 high (H1 requester role claimable from roles, H2 post-handoff light-change bypass, H3 quadratic redaction) and 9 medium findings; all fixed with regression tests.
- Review 2 (final tree): the fixes hold (M1, M3, M9 partial); new: N1 and N2 (quadratic regexes on typed text and change requests, **fixed**, `tests/security/test_regex_linearity.py`), N3 (dev auth lets one token holder mint identities and roles), N4 (caller picks mode and policy), N6 (no stream or session limits), N7 (`/v1/budget` leaked other tenants' spend), N8 to N11 (low). N3, N4, N6 to N11 are in `feat/apisec`. Fixed by the lead: gateway API `/readyz` minimal, cache keyed by tenant, builder prompts no longer say "trusted" (specs v2), stale hash and budget wording. Accepted: N12, N14 (dev tooling allow-list, CI tag pins, org profile).
- Review 2 did not cover: Postgres integration runs, SSE exhaustion beyond reading code, `openai_responses.py`, `proposal_ops.py` and `baseline.py` in depth, the hand-off git adapter beyond name validation, mutation checks of the part A fixes, dependency CVEs, replay of real cassettes.

## 8. Environment facts that bite

- Windows 11, PowerShell is the main shell, Git Bash exists. `uv` and `just` live in `%USERPROFILE%\.local\bin` and `C:\ProgramData\chocolatey\bin`.
- **Postgres:** Docker Desktop is paused by the owner (never unpause or restart it; other projects live there) and port 5432 belongs to other projects. The project uses a private PostgreSQL 18 cluster from the installed binaries on port 54329 (`scripts/dev_db.py`). Tests create an ephemeral database per session; never touch database `mde`.
- Async Postgres on Windows needs a selector event loop: always start the API with `uv run python -m mde_context_api --port 8100` (`just dev`), never plain `uvicorn`.
- `.env` holds `OPENAI_API_KEY` (defined twice, harmless). Never print, log or commit it; the deny rules and the secret scan protect it; the real-key scan only runs where a `.env` exists.
- The symlink test is skipped on Windows. Multi-process test workers must live in a real package module (`--import-mode=importlib`). Tests must not leave global state changed (D-038) or depend on what another package has merged (D-053).
- Tooling quirks: recursive deletion of `.venv` paths is blocked by the tooling (use `git worktree remove`); a chained shell command can commit a leftover conflict marker, so check `git grep '^<<<<<<<'` before committing a merge.

## 9. Process rules and lessons (all in `AGENT_PROTOCOL.md` and the ADRs)

- **Temporary checkouts are git worktrees**, removed when their task is merged and verified on `main`, only if clean and merged, with `git worktree remove` (section 10 of the protocol, D-072). Never use the harness's automatic worktree option for agents: it builds from the home-directory repository, not this one.
- **One spec hash rule** (ADR 0006, D-061): the file hash is sha256 of the text with its own `hash:` value blanked; a blocker went unseen until the real builder ran into the real flow. Any seam between packages needs one shared implementation and one real producer-to-consumer test.
- Validate merges **on `main`**, not only in a side worktree, and commit everything before removing a worktree (D-063).
- Contracts evolve additively through the lead; a retype needs an ADR and a version bump (ADR 0003, ADR 0011). Fakes of ports are validated against the real store's contract suite (ADR 0007). Transactions never span an engine call (ADR 0004). The budget guard fails closed (ADR 0005).
- Agents never ask the user questions; they log decisions in their report and the lead records them in `DECISIONS.md`. Agent ids are easy to mix up: a request meant for one agent once reached another (D-067); address agents by branch and worktree, and re-read the roster before sending.

## 10. Known weaknesses and next steps

- Real-model quality is untested beyond L0 (prompts, caps, reasoning effort, strict-schema acceptance, research synthesis, critic, latency after Gate 1, cost per session). The live batches exist to measure exactly this.
- Research plans at most 10 searches (2 queries times 5 web jobs), so the Standard (12) and Deep (20) ceilings are enforced but unreachable.
- Accepted gaps: policy checks run in the interview only (not at gates); flow-written notices are English only; the Org Profile is stored but not read by Discover; dev auth (until `feat/apisec` lands and `API_TOKENS` is used); the first-draft token average per screen (about 250) is below the spec's 400 to 600 estimate and should be re-measured on live output.
- For the frontend-engine team: read `docs/specs/frontend-consumption.md` and `docs/specs/app-spec-format.md`; consume only through `mde_spec_loader` (`slice()` per build step and screen, about 3k tokens per screen slice in the 50-screen sample); run `scripts/simulate_frontend_consumer.py` on every golden spec in CI; use `raise_decision` for gaps; stamp generated files with `@spec SCR-014@v7`; use `diff()` for incremental runs; treat locked screens as read-only.

## 11. Pointers

`docs/README.md` (index) | `docs/implementation/{BRIEF,PROGRESS,DECISIONS,REPORT,AGENT_PROTOCOL}.md` | `docs/architecture/security.md` | `docs/runbooks/{local-dev,live-llm-budget,troubleshooting}.md` | `docs/evals/eval-plan.md` and `docs/evals/reports/mock-baseline.md` | `CLAUDE.md` (hard rules) and the nested `CLAUDE.md` files | commands: `just --list`.
