# Runbook: Compass

Audience: anyone who wants to run, test or debug [Compass](../packages/compass.md) (`mde_compass`, port 8110). Everything below runs in **mock mode** unless the live section says otherwise: no OpenAI key, no network, no cost. For the rest of the repo see [local-dev.md](local-dev.md); for failure modes of the shared parts (database, gateway, ledger, worktrees) see [troubleshooting.md](troubleshooting.md); live spend is governed by [live-llm-budget.md](live-llm-budget.md).

## The short version

```bash
just compass-dev                                        # API + test UI on http://127.0.0.1:8110/ (needs the database)
uv run python -m mde_compass --port 8110 --memory       # the same with no database at all (nothing is kept)
just compass-test                                       # offline unit tests, no database
just compass-test-int                                   # store tests against the private PostgreSQL
cd packages/compass && PYTHONPATH=src python -m evals.run_personas --no-write   # ten persona scripts, offline, prints the metrics
```

Open <http://127.0.0.1:8110/ui/>. Dev auth defaults are in the Settings dialog (`dev-token`, tenant `demo`, user `demo-user`).

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

`just compass-dev` also upgrades the schema when the service is first composed (on the first request that needs it), so the explicit step is only for checking. Migrations are append-only: never edit an applied revision, add a new one.

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

Checks: `curl -s http://127.0.0.1:8110/healthz` returns `{"status":"ok"}`; `/readyz` is 200 as soon as the app has a service or a factory to build one; it does **not** check the database (the service is composed lazily on the first request that needs it). Create a session and read the stream with the curl walk-through in [api/compass.md](../api/compass.md). To follow what the model calls did, open the Developer drawer in the UI or `GET /v1/compass/sessions/{id}/calls`; in mock mode every call shows cost 0.0000.

## 4. Run the tests

```bash
just compass-test                                    # pytest packages/compass -m "not integration and not live" -q
just compass-test-int                                # db-up, then pytest packages/compass -m integration -q
uv run pytest packages/compass                       # everything except live (integration needs the database)
uv run pytest packages/compass/tests/calls/write_card -q
just ci-fast                                         # lint, typecheck, layers, unit tests for the repo
```

* The integration tests (`tests/store`) create an ephemeral database `mde_test_compass_<ulid>` inside the private cluster for the session, run Compass's own migrations in it and drop it afterwards; they cannot disturb `mde_persistence`'s tests (which assert a single head and an exact table set).
* The same shared contract suite runs against the in-memory store (offline) and PostgreSQL (integration).
* `tests/scenarios` drives a real `Service` end to end in mock mode (welcome, idea, cards, review, gate, changes, undo, hand-off). `tests/api` and `tests/ui_smoke` cover the HTTP layer and the static UI contract.
* `tests/intake` covers the parsers, chunking, the SSRF-safe URL reader and the ingest pipeline; `tests/engine/flows` the targeted flows; `tests/scenarios` the 46 named `SB-*` scenarios (`data/*.yaml`), the flows, intake and the personas.
* One test is marked `live`: `tests/live/test_live_smoke.py`. It skips itself unless `ALLOW_LIVE_LLM=1` is already in the outer environment (the root `conftest.py` guard), so `just compass-test` never runs it; its offline twin, `tests/test_smoke_driver_offline.py`, runs the same driver in mock mode. Nothing else here needs a key.
* The persona eval prints the report and (without `--no-write`) writes `packages/compass/evals/reports/latest.json` and a dated copy; that folder is git-ignored. Measured results are on [the package page](../packages/compass.md#measured-behaviour-mock-mode).
* Layering: `just layers` must keep the `compass-isolated` contract.

## 5. Live calls (lead or the user only)

Subagents and everyday development stay in mock or replay. Compass has no `just` recipe for live work; it has one live test, the **smoke driver** (batch A of D-085): `mde_compass.live_smoke` runs one real session (idea, cards to the review, a question, a chat change, a disliked assumption) plus one direct call of each intake-side call, and reports per call the outcome, tokens, cost and latency, which calls fell back, the spend against the cap, and that vision and streaming are unsupported by the gateway. The one-line command, from the repository root, in the lead's or the user's own terminal:

```bash
ALLOW_LIVE_LLM=1 uv run pytest packages/compass/tests/live -m live -q -s
```

* The allowance is USD 0.10: the test sets `COMPASS_LIVE_BUDGET_USD=0.10` for itself and asserts the spend stays under it. The test turns live mode on only when the second key is present (the root `conftest.py` sets the mode for `live`-marked tests); no other switch is needed on the command line.
* `OPENAI_API_KEY` must be visible to the process (the test's docstring says it lives in `.env`; never print, paste or commit it). `MDE_FORBID_LIVE` must be unset.
* The report is printed (`-s`) and written to `packages/compass/evals/reports/live-smoke.json` (git-ignored). Read it for `fallbacks` (a call that fell back is a call the model could not satisfy: fix the prompt or the validator offline), `errors` and `cost_usd`; then `just budget`, write the new total in `docs/implementation/PROGRESS.md` and take the second key away again.
* The test passes only if the driver reports no errors, spend is at most 0.10 and the session ends in `BRIEF_REVIEW`, `INTERVIEWING` or `PROBING`.
* **This page has no live result.** Nothing here says how fast, how costly or how well the real model does Compass's jobs; the numbers on [the package page](../packages/compass.md#measured-behaviour-mock-mode) are mock-mode numbers.

The rules that apply to any live run:

1. `ALLOW_LIVE_LLM=1` is the second key: the user's decision, never an agent's (D-069), and it must be in the environment of the process that makes the calls. Do not look for a way around the live-call hook.
2. The Compass cap: `COMPASS_LIVE_BUDGET_USD`, **clamped to at most 2.00** (a larger, unparsable or non-positive value never widens it); calls stop at 95% of it (USD 1.90) and at USD 0.25 per session; the check fails closed and the call falls back to its deterministic fallback instead of spending.
3. The repo-wide cap stays authoritative: total live spend never exceeds **USD 6.00**; the shared ledger is `data/.budget/ledger.jsonl` in the main checkout and `just budget` prints spend by scope (Compass calls book to `session:<id>`). Tripwires, the external dashboard backstop and the per-batch procedure are in [live-llm-budget.md](live-llm-budget.md).
4. A recorded run needs `MDE_RECORD_CASSETTES=1`; replays are free (`LLM_MODE=replay`). No Compass cassettes exist yet, and a prompt edit invalidates older ones.

Known gap: the Compass spend counter (`CompassBudget`) is per process and starts at zero on every start; reconciling it with the ledger (`CompassBudget.reconcile`) is not wired. Until it is, check `just budget` by hand before and after any live work.

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
| `budget_exceeded` (429) or calls showing `fallback` with a budget reason | the Compass cap (1.90 total, 0.25 per session) or the gateway cap was reached; the engine used the deterministic fallbacks | read the call log; `just budget`; raise nothing: caps only go down |
| The Brief does not become approvable | a `must` slot is empty, a high-impact assumption has no decision, a hand-off probe has not passed, or the hash is stale | read `blockers` in the `brief_ready` event or `footer.blockers`; decide the assumption or edit the slot, then approve the **latest** version and hash |
| Enterprise gate says "Waiting for approval from: approver" | an approval counts only from a contributor with the role `approver` | send the gate decision with `contributor_id: "approver"` (created with the session); approving does not change the hash |
| An upload answers with a plain message instead of reading the file | that is the designed failure path (HTTP 200, one bad file never stops the rest): password protected, damaged, unsupported type, too many pages or files, a duplicate (same sha256), a scanned PDF page with no text layer (never OCRed) | send an unprotected copy, a PDF with a text layer, or paste the text; a message over 3000 characters is read as a document |
| `413 too_large` on an upload | the file is over 15 MB | send a smaller file or split it |
| A link is refused ("not a public web page", "needs a sign-in") | the SSRF guard: private or internal address, credentials in the URL, odd port, or a page behind a sign-in | paste the text or upload the file |
| The metrics report shows 8 of 9 targets | the median of questions per Brief is 8 against a target of 7 (see [the package page](../packages/compass.md#measured-behaviour-mock-mode)); not a failure of the run | none needed; the lever is a catalog decision on `auth.account_model` |
| UI shows English strings only or a blank page | the strings call failed or the browser blocked inline script (CSP) | open `/v1/compass/strings/en`; the UI has no inline script and needs no build step |
| psycopg async errors on Windows (`ProactorEventLoop`) | Compass's launcher does not set the selector loop policy before uvicorn starts its loop (unverified on Windows) | use `--memory`, or run on macOS or Linux |

## Where things are

| What | Where |
| --- | --- |
| Package and its README | `packages/compass/`, [README](../../packages/compass/README.md) |
| Catalog data (slots, frames, families) | `packages/compass/src/mde_compass/catalog/` |
| Call folders | `packages/compass/src/mde_compass/calls/<name>/`, [catalog page](../prompts/compass-calls.md) |
| Migrations | `packages/compass/src/mde_compass/store/migrations/` |
| Test UI | `packages/compass/src/mde_compass/static_ui/` |
| Decisions | D-084 to D-086 in [DECISIONS.md](../implementation/DECISIONS.md); status in the Compass section of [PROGRESS.md](../implementation/PROGRESS.md) |
