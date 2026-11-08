# mde_context_api

**Job:** the FastAPI service in front of the flow controller (REST + SSE), the composition root that wires the engines, the in-process job worker and the Windows event-loop fix. No business logic of its own: every route authenticates, validates, calls one `FlowService` method with the caller's `Principal` and shapes the answer.

**Run:** `uv run python -m mde_context_api --port 8100` (Windows-safe: sets the selector event loop before any loop exists; the real Spec Builder is the default, `--fake-builder` or `SPEC_BUILDER=fake` selects the scripted one for development). Separate worker: `WORKER_ENABLED=0` for the API plus `python -m mde_context_api.worker`.

**Routes:** `POST /v1/sessions`, `GET /v1/sessions/{id}`, `POST /v1/sessions/{id}/events` (idempotent by `event_id`; Discover turns answer synchronously; approving Gate 1 enqueues the plan job: `202`), `GET /v1/sessions/{id}/stream` (SSE, `Last-Event-ID` replay), `GET /v1/sessions/{id}/brief|plan|spec|versions`, `GET /healthz`, `/readyz`, `GET /v1/budget` (dev). Contract: `docs/specs/events-and-api.md`; package page: `docs/packages/context-api.md`.

**Test:** `uv run pytest apps/context-api` (ASGI client; the `state` fixture runs everything over the in-memory store and over an ephemeral Postgres, `just db-up`). `test_process.py` starts the real server process.

**Conventions**
- Auth: constant-time bearer `API_DEV_TOKEN` plus `X-Tenant-Id`, `X-User-Id`, `X-Roles`; anything wrong is the same 401. Tenant isolation is enforced by binding every unit of work to `principal.tenant_id` (flow calls take the `Principal`); another tenant's id, or a session you hold no role in, is a 404 in the same words as an unknown id. Only the owner is the `requester`.
- Every failure is `{code, message}` (`errors.describe` is the one table); 5xx adds `request_id` and never a stack trace. Request bodies over `API_MAX_BODY_BYTES` are refused before parsing.
- Uploads: text, markdown and images only (PDF gets a friendly 415); image bytes never enter an event, only a `data_ref`. Never fetch a user-supplied URL.
- **Never hold a unit of work (DB connection) across an SSE stream or a sleep** (D-042, D-049): each poll is its own short transaction (`FlowService.list_events`).
- `/readyz` has no authentication: `database` and `ledger` (the in-process gateway ledger's `integrity_problem()`, run off the loop) answer `ok` or one word, never a reason, a path, a number or the budget's state. The numbers are `/v1/budget`, behind the token.
- `/docs`, `/redoc` and `/openapi.json` are unauthenticated, so they are served only with `API_DOCS=1` (or `--docs`); `create_app().openapi()` works either way and feeds `docs/api/openapi.json`.
- The budget gate (`budget.py`) is the flow's `BudgetGate` over the gateway's status; its message is plain words, never a number. `build_app_state` wires it; the eval harness (`evals/harness/stack.py`) builds the same gate.
- `create_app()` must work with no arguments, offline and without a database (state and settings are built lazily in `StateHolder`); the lifespan builds the state and starts the worker. Do not configure global logging in the lifespan (tests must not leave global state changed).
- The OpenAPI document is exported to `docs/api/openapi.json` by `uv run python scripts/export_schemas.py`; a test fails when the committed file is stale.
- Test helpers live in `mde_context_api.testing` (test modules cannot import each other, D-017). Every wait in a test is bounded.
- Owner: `flow-engineer`.
