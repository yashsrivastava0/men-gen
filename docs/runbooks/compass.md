# Runbook: Compass

Audience: anyone who wants to run, test or debug [Compass](../packages/compass.md) (`mde_compass`, port 8110). Everything below runs in **mock mode** unless the live section says otherwise: no OpenAI key, no network, no cost. For the rest of the repo see [local-dev.md](local-dev.md); for failure modes of the shared parts (database, gateway, ledger, worktrees) see [troubleshooting.md](troubleshooting.md); live spend is governed by [live-llm-budget.md](live-llm-budget.md).

## The short version

```bash
just compass-dev                                        # API + test console on http://127.0.0.1:8110/ (needs the database)
uv run python -m mde_compass --memory --port 8110       # the same with no database at all (nothing is kept)
COMPASS_INTERVIEW_ENGINE=planner uv run python -m mde_compass --memory --port 8110   # the AI-planned interview, mock mode
just compass-test                                       # offline unit tests, no database
just compass-test-int                                   # store tests against the private PostgreSQL
cd agents/compass && PYTHONPATH=src python -m evals.run_personas --no-write   # ten persona scripts, offline, prints the metrics
uv run python agents/compass/scripts/compass_latency_report.py   # per-turn and per-call latency breakdown table
```

Open <http://127.0.0.1:8110/ui/>. The page is the global test console in `apps/web/console/` ([apps/web README](../../apps/web/README.md)); the server serves it from `MDE_WEB_DIR` (default `apps/web/console`; a relative path is resolved against the repository root) and refuses to start if that directory or its `index.html` is missing. Dev auth defaults are in the Settings dialog of the Compass screen (`dev-token`, tenant `demo`, user `demo-user`). UI smoke tests: `uv run pytest apps/web/tests -q`. The screens for the AI-planned interview (groups, answer-all, "Ask me more", the Questions-and-answers list, a mock banner) are being built separately; until they are merged the console shows the planner's questions as plain cards, and the API ([api/compass.md](../api/compass.md)) is the reference.

Mock mode is the default (no `LLM_MODE` set, or `LLM_MODE=mock`): the real gateway pipeline runs, every call answers from its mock handler, nothing leaves the machine, every cost is 0.0000 and no key is read. With `--memory` nothing is stored either; with the database a session survives a restart.

## 1. Start the private PostgreSQL

Compass shares the repo's private cluster on **port 54329** (data in `data/pg` of the main checkout, trust auth, 127.0.0.1 only; ADR 0008). Never use port 5432 and never touch Docker Desktop state.

```bash
just db-up                       # uv run python scripts/dev_db.py up
uv run python scripts/dev_db.py status
```

**On this Mac** there is no system PostgreSQL and Homebrew is not usable (D-084). The cluster binaries come from the `pgserver` wheel unpacked in a throwaway tool venv, and `scripts/dev_db.py` finds them through `MDE_PG_BIN`:

```bash
MDE_PG_BIN=~/.local/share/mde-pg-tools/lib/python3.12/site-packages/pgserver/pginstall/bin just db-up
```

(or `export MDE_PG_BIN=...` once per shell). Only the bundled `initdb`, `pg_ctl` and `postgres` are used. The default URL is `postgresql+psycopg://mde:mde@127.0.0.1:54329/mde`; override with `DATABASE_URL`. `just db-down` stops it, `just db-reset` wipes it.

## 2. Migrate

Compass has its **own** Alembic history and version table (`compass_alembic_version`), separate from the rest of the repo's `just migrate` ([ADR 0012](../adr/0012-compass-package-boundaries-and-layering.md)); its tables are all `compass_*`.

```bash
just compass-migrate             # uv run python -m mde_compass.store.migrate  (upgrade to head)
uv run python -m mde_compass.store.migrate downgrade     # one step back, development only
```

`just compass-dev` also upgrades the schema when the service is first composed (on the first request that needs it), so the explicit step is only for checking. Migrations are append-only: never edit an applied revision, add a new one. The head is `0002`, the Interview Memory (`compass_qa`, `compass_facts`, `compass_interview_summary`, `compass_batches` and the trigger that refuses to delete a fact); its downgrade drops those four tables, so a `downgrade` throws the interview memory away: development databases only.

## 3. Run the app

```bash
just compass-dev                                    # uv run python -m mde_compass --port 8110
uv run python -m mde_compass --port 8110 --memory   # in-memory store
COMPASS_MEMORY=1 uv run python -m mde_compass       # same as --memory
```

| Setting | Effect |
| --- | --- |
| `--host` | default `127.0.0.1`; a non-loopback host is **refused** (exit 2) unless `COMPASS_API_TOKENS` is set |
| `COMPASS_DEV_TOKEN` | the dev bearer token (default `dev-token`) |
| `COMPASS_API_TOKENS` | token mode: `tok=tenant:user[:role+role];...` |
| `COMPASS_MAX_BODY_BYTES` | request body limit (default 262144) |
| `COMPASS_MEMORY=1` | in-memory store |
| `DATABASE_URL` | PostgreSQL URL (default: the private cluster) |
| `COMPASS_INTERVIEW_ENGINE` | `legacy` or `planner`: which question engine runs (see below); read when the service is built, so restart the server after changing it |
| `COMPASS_STRUCTURE` | `on` or `off`: draft the structure of the app (modules, screens, flows, entities, ADR 0017) while the Brief is built. On in live product runs; for a mock run set `COMPASS_STRUCTURE=on` (`COMPASS_STRUCTURE=on uv run python -m mde_compass --memory --port 8110`). Read when the service is built |
| `MDE_WEB_DIR` | the directory served at `/ui/` (default `apps/web/console`) |
| `COMPASS_LIVE_BUDGET_USD` | the Compass spend cap, at most 3.00 (live mode only matters; see section 5) |

**The structure in the console (ADR 0017).** With the structure on (`COMPASS_STRUCTURE=on` in mock mode, on by default in live product runs) the tier cards show the app size each tier plans for, and once the Brief is drafted a **Structure** tab appears next to **Brief**: the size banner, one card per area with its screens, the journeys, the data the app keeps, the role by area matrix and the checks that were left. Each area can be accepted, renamed, redrafted with your own words or removed, and "Plan as Small / Medium / Enterprise" redrafts from the same answers; in an Enterprise-size plan every area must be accepted (or use Approve All) before Gate 1. A quick mock check: `COMPASS_STRUCTURE=on uv run python -m mde_compass --memory --port 8110`, pick Thorough, send an idea, answer "decide the rest" and open the Structure tab.

Checks: `curl -s http://127.0.0.1:8110/healthz` returns `{"status":"ok"}`; `/readyz` is 200 as soon as the app has a service or a factory to build one; it does **not** check the database (the service is composed lazily on the first request that needs it). Create a session and read the stream with the curl walk-through in [api/compass.md](../api/compass.md). To follow what the model calls did, open the Developer drawer in the UI or `GET /v1/compass/sessions/{id}/calls`; in mock mode every call shows cost 0.0000.

### Choosing the engine

Two question engines are in the code while the AI-planned interview is finished ([package page](../packages/compass.md#the-ai-planned-interview)):

| `COMPASS_INTERVIEW_ENGINE` | What you get |
| --- | --- |
| not set, or `legacy` | the frame engine: one catalog card at a time. In mock mode its questions are built from the catalog's canned fallback cards, so they barely change from idea to idea (that is the problem the new engine solves) |
| `planner` | the AI-planned interview: after the playback, a batch of project-specific questions in 2 to 3 groups, follow-ups, replans, the Interview Memory |

Any other value is ignored (the default stays). To check which engine a session used, read `GET /v1/compass/sessions/{id}/brief`: `meta.interview` is normally `null` on the legacy engine and an object with counts on the planner engine; `GET .../qa` lists questions only for the planner engine. The default will change to `planner` in Phase 7 and the legacy engine will be deleted in Phase 8; the variable is then removed with it.

### What a mock session looks like

With the planner engine in mock mode the questions are plumbing, not product, and are labelled so. For the idea "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch." the first turn gives a `notice` (what was picked up), the `playback`, then a `question_batch` of seven questions in two groups titled `[mock] Group 1` and `[mock] Group 2`:

```
Q-002  [mock] Who it is for, for your daily project?            (free text, serves audience.segments)
Q-004  [mock] Apps needed, for your lunch project?              (choose one: [mock] One app for everybody / ... )
Q-007  [mock] How it earns money, for your pune project?        (choose one: [mock] A small fee on each sale or booking / ...)
```

Each mock question is the label of a need in the Needs Registry plus a `[mock]` tag and one word taken from your own message (here "daily", "lunch", "pune"): that proves the project terms reach the planner, nothing more. The choices come from the registry's canned cards where it has three, and the auditor's hints are labelled `[mock]` too. A re-worded question starts with `[mock] rephrased: ` or `[mock] simpler: `. Real question quality can only be judged in the live batches. The spend meter shows `mode: mock` and 0.0000.

Things to try: answer a card with the recommended option; answer a free-text card with a short vague reply (a mock "vague" answer to a high-impact need earns one follow-up); type "I don't understand" on a pending question; send the command `ask_more` or `budget_thorough` (see [api/compass.md](../api/compass.md#planned-batches)); preview AI suggestions with "Suggest answers with AI" (`/decide-preview`); approve all assumptions at Gate 1 via "Approve All"; read `GET .../qa` and `GET .../brief` (`meta.interview`, `domain_facts`). Option taps in product mode bypass the understander model for 0 ms deterministic slot updates.

## 4. Run the tests

```bash
just compass-test                                    # pytest agents/compass -m "not integration and not live" -q
just compass-test-int                                # db-up, then pytest agents/compass -m integration -q
uv run pytest agents/compass                       # everything except live (integration needs the database)
uv run pytest agents/compass/tests/calls/plan_questions -q     # one call
uv run pytest agents/compass/tests/engine/test_interview_flow.py -q   # whole planner sessions (mock, in memory)
just ci-fast                                         # lint, typecheck, layers, unit tests for the repo
```

* The integration tests (`tests/store`) create an ephemeral database `mde_test_compass_<ulid>` inside the private cluster for the session, run Compass's own migrations in it and drop it afterwards; they cannot disturb `mde_persistence`'s tests (which assert a single head and an exact table set). They include migration `0002` (upgrade, downgrade round trip, no drift) and the database guard that refuses to delete a fact.
* The same shared contract suite runs against the in-memory store (offline, `test_contract_memory.py`) and PostgreSQL (integration, `test_contract_postgres.py`); `test_store_equivalence.py` runs random operation sequences on both and compares them.
* `tests/scenarios` drives a real `Service` end to end in mock mode (welcome, idea, cards, review, gate, changes, undo, hand-off). `tests/api` covers the HTTP layer; the console contract and smoke tests are in `apps/web/tests`.
* `tests/engine/test_interview_flow.py` drives whole sessions on the planner engine (it sets `engine="planner"` per test through `build_service(compass_config=...)`; the default is `planner` since the Phase 7 flip; `COMPASS_INTERVIEW_ENGINE=legacy` is the rollback). The pure modules have their own files in `tests/engine/` (`test_facts`, `test_quotes`, `test_fact_projector`, `test_needs`, `test_question_validators`, `test_interview_policy`, `test_context_pack`). To check exact behaviour without a model, a test injects a scripted gateway with hand-written outputs: `build_service(memory=True, gateway=...)`.
* `tests/intake` covers the parsers, chunking, the SSRF-safe URL reader and the ingest pipeline; `tests/engine/flows` the targeted flows; `tests/scenarios` the 46 named `SB-*` scenarios (`data/*.yaml`), the flows, intake and the personas. These still run on the legacy engine; their move to the planner is Phase 6.
* One test is marked `live`: `tests/live/test_live_smoke.py`. It skips itself unless the owner's second key is already in the outer environment (the root `conftest.py` guard), so `just compass-test` never runs it; its offline twin, `tests/test_smoke_driver_offline.py`, runs the same driver in mock mode. Nothing else here needs a key.
* The persona eval prints the report and (without `--no-write`) writes `agents/compass/evals/reports/latest.json` and a dated copy; that folder is git-ignored. Measured results are on [the package page](../packages/compass.md#measured-behaviour-mock-mode).
* Layering: `just layers` must keep the `compass-isolated` contract.

## 5. Live calls (lead or the user only)

Subagents and everyday development stay in mock or replay. Nothing in this section is a step for an agent to take, and nothing in it is a way around the live-call hook or the permission system.

### What a live run needs

A live Compass call needs three things in the environment of the process that makes it, plus a key:

1. **Live mode switched on** (`LLM_MODE=live`).
2. **The second key**, `ALLOW_LIVE_LLM=1`. It is the **owner's decision**, made for the batch at hand (D-069, D-091 point c); no agent adds it, asks for it to be added or looks for another way. The root `conftest.py` and the gateway both refuse live calls without it.
3. **`MDE_FORBID_LIVE` unset** (the test fixtures set it; a normal shell does not).
4. `OPENAI_API_KEY` present (never print, paste or commit it; `uv run mde doctor` and `uv run python scripts/doctor.py` report presence only).

Who may run what, the external dashboard backstop, the order of batches, the tripwires and how to read the ledger are in [live-llm-budget.md](live-llm-budget.md). Read it before any live work.

### Batch H: the structure drafted on a recorded ledger (ADR 0017)

`mde_compass.live_structure` runs only the structure calls (outline, module groups, critic, one repair pass) on the Brief and the active facts of a recorded session, so the structure can be calibrated for about USD 0.01 to 0.03 a run instead of paying for a whole interview. The inputs are `delhi` and `pune` (the two real exports of `docs/compass/outputs`) and `hospital` (a Thorough session with a ten-role document, the enterprise-size case), or the path of any export.

```
uv run python -m mde_compass.live_structure --replay pune --band medium --mock        # offline, free
uv run python -m mde_compass.live_structure --replay hospital --band large --dry-run  # plan only, no call
LLM_MODE=live ALLOW_LIVE_LLM=1 uv run python -m mde_compass.live_structure --replay hospital --band large   # owner's second key
```

It prints the wall-clock of every call, fallbacks, cost, the counts against the band, the checks that are left and the structure as plain text (every module with its screens and roles, the flows with their acceptance lines, the entities with fields and access) for a manual realism review; the full report goes to `agents/compass/evals/reports/` (git-ignored). A live run goes through the same `preflight` as every batch (allowance against the ledger, the switches in the outer environment).

### The numbers

| What | Value | Where it lives |
| --- | --- | --- |
| Repo-wide cap on all live spend | USD 6.00 | gateway ledger, `LLM_BUDGET_USD` (can only be lowered) |
| Compass cap | USD 3.00 at most (the owner's ceiling, D-093); `COMPASS_LIVE_BUDGET_USD` can only lower it; a larger, unparsable or non-positive value is ignored | `config.BudgetPolicy.cap_usd` |
| Aim for all Compass live batches together | under USD 2.00 | `target_usd`: planned and reported against, not enforced |
| Stop | 95% of the cap: USD 2.85 | `effective_stop_micros`; the call falls back instead of spending (fails closed) |
| Per session | USD 1.00 (was 0.50: a Thorough interview with a document starved its own Brief and structure, D-124) | `per_session_cap_usd` and the gateway `budget.yaml` |
| Kept for the owner's manual sessions | USD 0.30 | `reserve_usd`: declared for the batch plans, not enforced by the guard |

Planned allowances (D-093): batch E (planner quality) about USD 0.50, F (full interviews) about 0.70, G (rich input and language) about 0.40; each is printed with an expected cost before it runs and stopped at its allowance.

### How Compass spend is remembered

`CompassBudget` is a counter in the process, so on its own it would start from zero in every new process and a series of batches could overspend the cap. In live mode `compose.build_service` therefore starts it from the gateway ledger (`ledger_spend.compass_spent_micros`, `CompassBudget.reconcile`):

* It reads `data/.budget/ledger.jsonl` of the **main checkout** (the setting `LLM_BUDGET_LEDGER`; relative paths are anchored there, whichever worktree you run from), read-only. It never locks, writes or creates the file; a missing ledger counts as 0 and a line it cannot read is skipped (the gateway's own guard is what refuses a damaged ledger).
* It adds up the **committed** cost of every reservation whose `spec_id` starts with `cmp.`, that is, all Compass calls, whoever made them. Other agents' calls (`C1`, `S1` ...) do not count towards the Compass cap, only towards the USD 6.00 total.
* The counter adopts that total if it is larger than its own, then adds this process's calls. This happens once, at start-up.
* So `COMPASS_LIVE_BUDGET_USD` is a cap on **all Compass spend in the ledger**, not on one run. A later run with a small figure (the smoke test sets USD 0.10) stops spending at once if earlier Compass batches already passed 95% of it, and every call falls back.
* Spend by another process while this one runs is not seen until the next start. `just budget` (read-only) prints spend by scope from the ledger at any time; check it before and after any live work. `GET /v1/compass/budget` shows this process's counter (`mode`, `cap_usd`, `effective_stop_usd`, `spent_usd`, `remaining_usd`); it is hidden in token mode.

### The smoke driver (batch A)

`mde_compass.live_smoke` runs one real session (the idea, taps on the recommended option until the review, a question, a chat change, a disliked assumption) plus one direct call of each intake-side call (`cmp.read_chunk`, `cmp.merge_claims`, `cmp.scan_ambiguity`), and reports per call the outcome, tokens, cost and latency, which calls fell back, the spend against the cap, and that vision and streaming are unsupported by the gateway. It follows whichever engine `COMPASS_INTERVIEW_ENGINE` selects. The test that runs it is `tests/live/test_live_smoke.py`:

```bash
uv run pytest agents/compass/tests/live -m live -q -s
```

It skips itself unless the second key is in the environment, so it is the lead's or the owner's to run, in the owner's own terminal, after the owner's decision.

* The allowance is USD 0.10: the test sets `COMPASS_LIVE_BUDGET_USD=0.10` for itself and asserts the spend stays under it (see "How Compass spend is remembered": that is a cap on the Compass total in the ledger). The root `conftest.py` turns live mode on for `live`-marked tests only when the second key is present.
* The report is printed (`-s`) and written to `agents/compass/evals/reports/live-smoke.json` (git-ignored). Read it for `fallbacks` (a call that fell back is a call the model could not satisfy: fix the prompt or the validator offline), `errors` and `cost_usd`; then `just budget` and write the new total in `docs/implementation/PROGRESS.md`.
* The test passes only if the driver reports no errors, spend is at most 0.10 and the session ends in `BRIEF_REVIEW`, `INTERVIEWING` or `PROBING`.

### Live batches E, F, G

Live batches E, F, G: runner documented when merged.

### Rules that apply to any live run

1. A fallback is a failure: in a live batch `meta.interview.fallback_count` must be 0 and no call may show `outcome: fallback` ([package page](../packages/compass.md#fallbacks-are-failures)). A fallback is a bug to fix offline, never something to hide.
2. A recorded run needs `MDE_RECORD_CASSETTES=1`; replays are free (`LLM_MODE=replay`). No Compass cassettes exist yet, and a prompt edit invalidates older ones.
3. **This page has no live result.** Nothing here says how fast, how costly or how well the real model does Compass's jobs; the numbers on [the package page](../packages/compass.md#measured-behaviour-mock-mode) are mock-mode numbers.

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `[Errno 48] address already in use` or the app exits at start on 8110 | another process holds the port (often a previous `just compass-dev`) | `lsof -nP -iTCP:8110 -sTCP:LISTEN` (macOS) or `netstat -ano \| findstr :8110` (Windows), stop that process, or start with `--port 8111`. Never kill processes that are not yours |
| `Refusing to listen on <host>: this server is using dev authentication` (exit 2) | non-loopback `--host` without `COMPASS_API_TOKENS` | bind `127.0.0.1`, or configure a token table |
| `401 unauthorized` from every call | missing or wrong bearer token, or `X-Tenant-Id` or `X-User-Id` missing or malformed (all failures look the same on purpose) | send all three headers; check `COMPASS_DEV_TOKEN` |
| `404 not_found` on a session you just created | the session belongs to a different tenant or user header than the one you are using | use the same `X-Tenant-Id` every time |
| `connection refused` / `OperationalError` in the log, `/readyz` 200 but session calls answer 500 `internal_error` | the database is not running or not migrated | `just db-up`, check `uv run python scripts/dev_db.py status`, `just compass-migrate`; or use `--memory` |
| `No PostgreSQL available: ...` from `db-up` | `scripts/dev_db.py` found no `pg_ctl` | set `MDE_PG_BIN` to a PostgreSQL 16 `bin` folder (see section 1) |
| Integration tests fail with a database error | the private cluster is down or on another port | `just db-up` (the `compass-test-int` recipe already does); `MDE_PG_PORT` must match |
| `just compass-test-int` leaves `mde_test_compass_*` databases behind | a run was killed before cleanup | drop them with `psql` on port 54329; never touch databases you did not create |
| A turn ends with an `error` event `turn_failed` | an exception inside the turn: in live mode without both switches the gateway raises `LiveCallForbidden`, which Compass does not hide behind a fallback; otherwise read the server log line `compass_turn_failed` | in mock mode this should not happen: report it with the log. In live mode check `LLM_MODE`, `ALLOW_LIVE_LLM`, `MDE_FORBID_LIVE` |
| `Blocked by .claude/hooks/guard_live.py` | the live-call hook blocks commands that would turn live mode on unless `ALLOW_LIVE_LLM=1` is already in the session environment. It matches command **text**, so even a command that only quotes those spellings is blocked | do not work around it: live work is the lead's or the user's ([troubleshooting.md](troubleshooting.md#4-the-ledger-and-the-live-call-guard)). Put documentation text in files with the Write tool, not in shell commands |
| `CassetteMissing: no cassette for cmp.<call> v1 (key ...)` | `LLM_MODE=replay` and the rendered request has no recorded cassette (none exist for Compass yet; any prompt, schema, model or effort change invalidates old ones). Replay never goes live | use `LLM_MODE=mock` (the default) for development; record only in an authorised live batch. A worktree replays only cassettes already in the main checkout |
| `budget_exceeded` (429) or calls showing `fallback` with a budget reason | the Compass cap (USD 2.85, which is 95% of 3.00, in total, or 1.00 per session) or the gateway cap was reached; in live mode the counter starts from the ledger, so spend of earlier batches counts; the engine used the deterministic fallbacks | read the call log and `GET /v1/compass/budget`; `just budget`; raise nothing: caps only go down |
| The Brief does not become approvable | a `must` slot is empty, a high-impact assumption has no decision, a hand-off probe has not passed, or the hash is stale | read `blockers` in the `brief_ready` event or `footer.blockers`; decide the assumption or edit the slot, then approve the **latest** version and hash |
| Enterprise gate says "Waiting for approval from: approver" | an approval counts only from a contributor with the role `approver` | send the gate decision with `contributor_id: "approver"` (created with the session); approving does not change the hash |
| An upload answers with a plain message instead of reading the file | that is the designed failure path (HTTP 200, one bad file never stops the rest): password protected, damaged, unsupported type, too many pages or files, a duplicate (same sha256), a scanned PDF page with no text layer (never OCRed) | send an unprotected copy, a PDF with a text layer, or paste the text; a message over 3000 characters is read as a document |
| `413 too_large` on an upload | the file is over 15 MB | send a smaller file or split it |
| A link is refused ("not a public web page", "needs a sign-in") | the SSRF guard: private or internal address, credentials in the URL, odd port, or a page behind a sign-in | paste the text or upload the file |
| The metrics report shows 8 of 9 targets | the median of questions per Brief is 8 against a target of 7 (see [the package page](../packages/compass.md#measured-behaviour-mock-mode)); not a failure of the run | none needed; the lever is a catalog decision on `auth.account_model` |
| UI shows English strings only or a blank page | the strings call failed or the browser blocked inline script (CSP) | open `/v1/compass/strings/en`; the UI has no inline script and needs no build step |
| psycopg async errors on Windows (`ProactorEventLoop`) | Compass's launcher does not set the selector loop policy before uvicorn starts its loop (unverified on Windows) | use `--memory`, or run on macOS or Linux |

### Troubleshooting the interview

The checks below use the API of the planner engine ([api/compass.md](../api/compass.md)); `GET .../brief` is the Brief with `meta.interview`, `GET .../qa` the questions and answers, `GET .../calls` the call log.

* **A fallback was used (`meta.interview.fallback_count` above 0, a card with `fallback: true`, a call with `outcome: fallback`).** A fallback is a failure. The server log line `compass_call_fallback` names the call and the reason (a validator message, a timeout, an empty output, or a budget stop). In mock mode the count must be 0; a non-zero count there is a bug, report it with the log. In a live batch fix the prompt, schema or validator offline and run the batch again; never hide it. The count covers the planner, auditor, rephraser, summary and compile; a fallback of `cmp.understand_turn` or another call is only in the call log.
* **The first questions are plain catalog cards, not about the project.** The planner's questions were all rejected (code validators, then the auditor, then one repair) and the engine took the flagged registry cards (`origin: "fallback"`). The reasons are stored with the run: in PostgreSQL, table `compass_batches`, columns `rejected` (each question id with its reasons, written as `<issue code>: <message>`, for example `no_project_term`, `duplicate_intent`, `wrong_language`, `choose_one_options`, `technology_question`, or the auditor's `specificity 2 is below 3`) and `verdicts` (the auditor's scores), plus `used_fallback` and `repaired`. Fix the prompt (`calls/plan_questions/system.jinja`) or the rule it trips offline; do not loosen a validator to make a batch pass.
* **Budget reached.** `GET /v1/compass/budget` shows `spent_usd` at or past `effective_stop_usd` (or the session at 0.50): every further call falls back with a budget reason. The counter starts from the ledger in live mode (section 5), so spend of earlier batches counts. Only `just budget` and the owner can say what is left; the caps do not go up.
* **Still seeing the catalog's questions.** The planner engine is off. Start the server with `COMPASS_INTERVIEW_ENGINE=planner` (read when the service is built, so restart), and check `meta.interview` is an object.
* **Too many or too few questions.** Read `meta.interview`: `budget` (5, 12 or 25 questions in all, follow-ups included), `questions_asked`, `followups`, `stop_reason` (`fatigue`, `budget_reached`, `coverage_reached`, or "the planner has nothing important left to ask"). The commands `budget_quick`, `budget_standard`, `budget_thorough` and `ask_more` change the depth.
* **No follow-up after a vague answer.** A follow-up needs a high-impact need (catalog impact 4 or more) that is `partial` or flagged `vague`, no earlier follow-up for it, depth below 2 and follow-ups below 40% of the base questions. A second thin answer is assumed with a visible chip, not chased. A tap is never vague. In mock mode a typed answer is "vague" if it has fewer than six words and no digit, comma or "and".
* **"I do not understand" or "I do not know" does nothing.** It works on the planner engine, for a pending planned question, with a short phrase from the phrase tables (at most 8 words). The first requests re-word or simplify; the next one makes "You decide" assume visibly.
* **A thing the person said is missing from the Brief.** `GET .../qa` shows the facts each answer produced; `domain_facts` in `GET .../brief` must hold every active fact that no slot cites. `meta.interview.facts_unreferenced` above 0 means the compile left a fact out and code placed it anyway (report it); `facts_not_used` counts facts the compile dropped with a reason.

## Where things are

| What | Where |
| --- | --- |
| Package and its README | `agents/compass/`, [README](../../agents/compass/README.md) |
| Catalog data (slots, frames, families) | `agents/compass/src/mde_compass/catalog/` |
| Call folders | `agents/compass/src/mde_compass/calls/<name>/`, [catalog page](../prompts/compass-calls.md) |
| Migrations | `agents/compass/src/mde_compass/store/migrations/` |
| Test UI (global front end, not in the agent) | `apps/web/console/` (`agents/compass/` screens, `shared/`, `index.html` shell), [README](../../apps/web/README.md); tests in `apps/web/tests/` |
| Engine modules of the AI-planned interview | `agents/compass/src/mde_compass/engine/` (`interview.py`, `question_pipeline.py`, `question_validators.py`, `needs.py`, `facts.py`, `quotes.py`, `fact_projector.py`, `context_pack.py`), listed on the [package page](../packages/compass.md#what-each-engine-module-owns) |
| Build prompt 02 and the "before" evidence | `docs/compass/build-prompts/`, `docs/compass/evidence/before-first-questions.json` |
| Decisions | D-084 to D-086 (first build) and D-090 to D-099 (AI-planned interview) in [DECISIONS.md](../implementation/DECISIONS.md), [ADR 0015](../adr/0015-ai-planned-interview-and-interview-memory.md); status in the Compass sections of [PROGRESS.md](../implementation/PROGRESS.md) |
