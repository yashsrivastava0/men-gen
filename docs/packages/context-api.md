# mde_context_api: the Context API

The FastAPI service in front of the flow controller (`apps/context-api`, import `mde_context_api`). It has **no business logic of its own**: every route authenticates, validates, calls one `FlowService` method with the caller's `Principal` (which binds every unit of work to the caller's tenant) and shapes the answer. It also owns the composition root that wires the engines together, the in-process job worker and the Windows event-loop fix. The HTTP contract (routes, shapes, SSE, errors) is in [../specs/events-and-api.md](../specs/events-and-api.md).

## Run it

```powershell
uv run python scripts/dev_db.py up                          # the private Postgres on port 54329
uv run alembic -c packages/persistence/alembic.ini upgrade head
uv run python -m mde_context_api --port 8100                 # the API, the worker included
# the real Spec Builder is the default; for a scripted one (development only): ... -m mde_context_api --fake-builder
```

The interactive docs (`/docs`, `/redoc`, `/openapi.json`) are **off by default** (they need no credentials): add `--docs` or set `API_DOCS=1` to serve them at `http://127.0.0.1:8100/docs`. `just dev` runs the command without the flag; put `API_DOCS=1` in your environment (or add `--docs` to the recipe) when you want them. `create_app().openapi()` and the exported `docs/api/openapi.json` work either way, offline. Mock mode (`LLM_MODE=mock`, the default) needs no key and no network. `--reload` restarts on code changes (development; the loop factory is imported by name in the child, so it works there too, but the process test covers the non-reload run).

`python -m mde_context_api` exists because of Windows: psycopg's async mode cannot run on the default `ProactorEventLoop` (D-042), and the selector policy must be set before any loop exists. `mde_context_api.loop` sets it at import and offers `selector_loop_factory`; `__main__` calls `uvicorn.run("mde_context_api.main:app", loop="mde_context_api.loop:selector_loop_factory")`. `uvicorn mde_context_api.main:app` also imports, but on Windows leaves the loop to uvicorn's default (Proactor): use `python -m`.

A separate worker process: `WORKER_ENABLED=0` for the API and `python -m mde_context_api.worker [--fake-builder]` for the worker (same settings, same database). A stop request reaches a job in the other process through the database flag the worker polls every second.

> **Dev authentication must never face a network.** Without `API_TOKENS`, the caller's tenant, user and roles are request headers the caller writes itself, and one shared token (`API_DEV_TOKEN`, default `dev-token`) opens every door: its holder can be two different approvers and satisfy a two-approver Gate 2 alone. That is fine on your own machine and nowhere else. So `python -m mde_context_api` **refuses to start** (message on stderr, exit code 2) when the address it would bind is not loopback (`127.0.0.0/8`, `::1`, `localhost`; `0.0.0.0`, `::`, other addresses and host names all count as a network) and `API_TOKENS` is not set. Changing the dev token does not make the headers safe, so the check does not care about it. There is no override flag on purpose: to listen on a network, configure real identities (below). Running `uvicorn mde_context_api.main:app` yourself bypasses this check and is not supported (it is also wrong on Windows, see above).

## Authentication modes

| | dev headers (default) | token table (`API_TOKENS`) |
| --- | --- | --- |
| who the caller is | `X-Tenant-Id`, `X-User-Id`, `X-Roles` written by the caller | the configured entry of the presented token; the `X-*` headers are **ignored** |
| the token | one shared `API_DEV_TOKEN` | one secret per identity (16 to 256 characters), compared as SHA-256 digests in constant time, every entry compared on every request |
| unknown or missing token | `401`, one fixed body | the same `401`; the dev token is **not** accepted |
| use | local development and the tests | anything that listens on a network |

`API_TOKENS` is a JSON object, token to identity:

```json
{
  "7f3c9a1e-long-random-secret-for-alice": {"tenant_id": "acme", "user_id": "alice"},
  "b21d5e07-long-random-secret-for-bob":   {"tenant_id": "acme", "user_id": "bob",
                                             "roles": ["client_product_owner"]},
  "c90ae4d8-long-random-secret-for-carol": {"tenant_id": "acme", "user_id": "carol",
                                             "roles": ["delivery_lead"],
                                             "allowed_policies": ["enterprise-it-services-firm"],
                                             "default_policy": "enterprise-it-services-firm"}
}
```

`tenant_id`, `user_id` and `roles` follow the same rules as the headers (see the spec). The optional `allowed_policies`, `default_policy` and `default_mode` bind what that token may start (next section). A malformed table stops the service at start-up with a message that names the entry and the field and never repeats a token or a value; the tokens are registered with the log scrubber like every other secret and `repr()` of the settings shows none. The table is read once at start-up (restart to change it). With a table, a forged `X-User-Id`, `X-Roles` or `X-Tenant-Id` changes nothing: `test_token_auth.py` replays the "one token holder invents two approvers" attack in both modes (it succeeds in dev mode, which is the documented reason for the rule above, and fails with a table).

## Who may open which session (`TENANT_POLICIES`)

Without a binding the **caller** picks the `mode` and `policy_id` of a new session, so any user can open a Guided session and so skip the tenant's banned libraries, regions, multi-approver gates and research limits (all of which live in the policy). A deployment with stricter policies for some tenants binds them:

```json
{ "acme":   {"allowed_policies": ["enterprise-it-services-firm", "regulated-bank"],
             "default_policy": "regulated-bank"},
  "globex": {"allowed_policies": ["guided-default"]} }
```

* A tenant with an entry may only start sessions whose policy is in `allowed_policies` (so only the modes those policies have); anything else is `403` with a plain message (`forbidden`). The `mode` and `policy_id` the request leaves out come from the entry: `default_mode`, else the mode of `default_policy`, else the one mode the allowed policies share, else `guided`; the policy is `default_policy`, else the catalog default for the mode if allowed, else the first allowed policy of that mode.
* `allowed_policies` omitted means no restriction on the choice (only the defaults change); `default_policy` alone means that policy is the only one allowed; an explicit empty list allows nothing.
* In token mode an entry's own `allowed_policies` / `default_policy` / `default_mode` bind that token as well. When both a tenant entry and a token entry apply, **both** must hold: the allowed sets are intersected and the token's defaults win.
* Every policy named must exist: a binding to an unknown policy stops the service at start-up.
* A tenant with no entry keeps the development behaviour (the request decides). Binding only applies to creating a session: a session keeps its policy for its whole life.

## Abuse limits

| Limit | Default | Setting | Over the limit |
| --- | --- | --- | --- |
| live event streams per caller (tenant + user) | 5 | `API_MAX_STREAMS_PER_PRINCIPAL` | `429` `too_many_streams`, `Retry-After: 5` |
| session creation per caller, per minute and per hour | 10, 100 | `API_SESSION_RATE_PER_MINUTE`, `API_SESSION_RATE_PER_HOUR` | `429` `too_many_requests`, `Retry-After` in seconds |
| session creation per tenant, per minute and per hour | 10, 100 | `API_TENANT_SESSION_RATE_PER_MINUTE`, `API_TENANT_SESSION_RATE_PER_HOUR` | the same, with a message about the organisation |

`0` turns a limit off (for a local load test or `mde sim` in a loop). Why these numbers: every live session may spend up to the session cap (USD 0.50), so about a dozen sessions would use the whole live budget; the budget guard stays the authority on spend, these limits keep a runaway or hostile client from reaching it in seconds.

* **Streams.** A live stream (`follow=true`, the default) holds a *lease* for as long as it runs; the lease is released when the stream ends for any reason (finished, `limit`, timeout, the client hung up, an error mid-stream, a `404` before it started) and expires on its own 30 s after the stream's own maximum duration, so a lost `finally` cannot hold a slot for ever. A bounded read (`follow=false`) holds none. An idle stream also **backs off**: it polls every `SSE_POLL_SECONDS` while events flow and, after each empty poll, waits half as long again up to `SSE_MAX_POLL_SECONDS` (default 2 s), returning to the base interval as soon as an event is sent; a sleep never overshoots the stream's end or its next keepalive. So many quiet streams make few queries.
* **Session creation.** A sliding window per key: a refused request is not counted, `Retry-After` is the time until the oldest counted request leaves the window, and every request that got past authentication counts (a loop of invalid requests cannot probe for free). Only `POST /v1/sessions` is limited by this.
* **Caveats.** The state is in process memory (one API process; a second process has its own counters) and idle keys are swept, so invented callers cannot grow it without bound. The caller key is `(tenant_id, user_id)`: in dev-header mode both are self-declared, so there these limits bound an honest or careless client only; with `API_TOKENS` they bind real identities. A gateway in front is still expected for the network edge (spec 2.4).

## Layout

```
apps/context-api/src/mde_context_api/
  main.py          create_app(settings=None, *, state=None, state_factory=None); module-level `app`
  composition.py   build_app_state(settings, ...) -> AppState (the composition root)
  budget.py        GatewayBudgetGate, NoopBudgetGate, budget_gate_for(client): the flow's BudgetGate port
  holder.py        StateHolder: the lazily built state behind the app
  settings.py      ApiSettings (the shared Settings plus the API's own variables)
  routes.py        the HTTP routes (sessions, ops, dev)
  auth.py          bearer token -> Caller (Principal + policy binding): dev headers or the API_TOKENS table
  identity.py      id shapes, TokenEntry / TokenTable, parse_api_tokens (value-free errors)
  bindings.py      PolicyBinding (what a tenant or token may start), merge()
  limits.py        StreamLimiter (leases), SessionRateLimiter (sliding windows), AbuseLimits
  errors.py        exception -> {code, message} mapping, ErrorBody
  middleware.py    request ids and the request body size limit (pure ASGI)
  inbound.py       checks on inbound events: ids, upload types and sizes
  sse.py           the event stream generator and frame format
  worker.py        BackgroundWorker, make_job_handler, `python -m mde_context_api.worker`
  loop.py          the Windows selector event loop factory
  policies.py      PolicyCatalog (default policy per mode, resolve_new for bound callers, resolver for the flow)
  profiles.py      the tenant's Org Profile document (read at session creation, Enterprise only)
  schemas.py       request and response models
  testing.py       importable test helpers (settings, state over any store, Api driver, serve_app)
```

## Composition (`build_app_state`)

Everything is built from settings alone; nothing connects to the database while building (the pool connects on first use), so the state and the OpenAPI document exist with no database running.

| Part | From |
| --- | --- |
| gateway | `GATEWAY_MODE=inprocess`: `InProcessGatewayClient(create_gateway(settings, PromptRegistry(), sink=PgLlmCallSink))` (mock, replay or live by `LLM_MODE`; live also needs `ALLOW_LIVE_LLM=1`). `http`: `HttpGatewayClient(GATEWAY_URL, GATEWAY_SERVICE_TOKEN)` (both required, else `ConfigError`) |
| engines | `DiscoverEngine(gateway)`, `DefaultResearchEngine(gateway)`, the Spec Builder |
| Spec Builder | `mde_spec_builder.engine.SpecBuilderImpl(gateway, clock=...)`, imported lazily; if it cannot be imported a `ConfigError` says so and names the way out. `--fake-builder` / `SPEC_BUILDER=fake` use `mde_flow.testing.FakeSpecBuilder` (dev and tests) |
| store | `PostgresStore(create_engine(settings))` (pool of 5, no overflow), or any `Store` passed in |
| policies | `PolicyCatalog`: `guided` is `guided-default`, `enterprise` is `DEFAULT_ENTERPRISE_POLICY`; a request's `policy_id` overrides and must exist and match the mode (`ValidationFailed`); for a caller bound by `TENANT_POLICIES` or a token entry, `resolve_new` keeps the request inside the binding (`Forbidden`, 403). Every policy a binding names must exist (checked at start-up) |
| hand-off | `LocalGitProjectRepo(PROJECTS_DIR)`, `NoopFrontendEngine` |
| budget gate | `budget_gate_for(gateway)`: in-process, `GatewayBudgetGate` over the gateway's budget status and the session's own cap; an HTTP gateway with a status, the global part only; no status, `NoopBudgetGate`. Not ok when the tripwire is at 80% or 95%, or when the whole budget or the session scope has less headroom than one plan needs (`BUDGET_PLAN_RESERVE_USD`). The verdict message is plain words, never a number |
| ledger check | `AppState.ledger_problem`: the in-process gateway ledger's `integrity_problem()` (a memory ledger is always sound); `None` for an HTTP gateway. `/readyz` runs it off the event loop with a 3 s limit |
| limits | `AbuseLimits(settings, clock)`: stream leases and session-creation windows (`AppState.limits`, driven by the same clock as everything else) |
| flow | `FlowService(...)` over all of the above, with the budget gate |
| worker | started by the app's lifespan when `WORKER_ENABLED` (default on) |

`build_app_state(settings, store=..., gateway=..., project_repo=..., frontend=..., clock=..., ids=..., fake_builder=...)` replaces any collaborator (the tests pass an in-memory store or an ephemeral Postgres). `create_app()` takes no arguments and needs nothing: state and settings are built lazily (`StateHolder`), eagerly in the lifespan, so `scripts/export_schemas.py` can import it and write `docs/api/openapi.json` offline.

## The job worker

`FlowService.run_plan_job(job, worker_id, cancel)` is wrapped by `make_job_handler` into a persistence `JobHandler`. The flow cancels a run through the in-process token of its `RunManager` and through the database flag it polls at every checkpoint; the persistence `JobWorker`'s watcher adds a third path (a cancel set by another process, a lost lock). The handler joins them: whichever token is cancelled stops the run. `BackgroundWorker` runs `JobWorker.run_forever` as an asyncio task (poll 0.5 s, heartbeat 10 s, stuck jobs of a dead worker are released after 120 s); stopping lets a running job finish for up to 10 s and then cancels it. No unit of work is held while the worker sleeps.

## Authentication, limits and errors

See [../specs/events-and-api.md](../specs/events-and-api.md) and the sections above. In short: a bearer token that is either the shared dev token plus self-declared identity headers (local only) or one entry of the `API_TOKENS` table (constant time, headers ignored); one `ErrorBody` for every failure; a request body over `API_MAX_BODY_BYTES` is refused with `413` before it is parsed (by `Content-Length`, or while reading a chunked body); every response carries `X-Request-Id`; uploads are text, markdown and images only.

**No connection is held across a stream or a sleep.** The SSE generator opens one short unit of work per poll (`FlowService.list_events`) and only sleeps between polls; tests prove it with a pool of five connections serving twelve idle streams, and by watching the open-connection count while a stream sleeps.

## Configuration

Names are environment variables (case-insensitive); the shared ones are in [../architecture/llm-gateway.md](../architecture/llm-gateway.md) and brief 6.1.

| Variable | Default | Meaning |
| --- | --- | --- |
| `API_DEV_TOKEN` | `dev-token` | the shared bearer token of dev-header mode (ignored when `API_TOKENS` is set) |
| `API_TOKENS` | unset | JSON: token to `{tenant_id, user_id, roles, allowed_policies, default_policy, default_mode}`; set = token mode, and the only way to bind a network address |
| `TENANT_POLICIES` | unset | JSON: tenant id to `{allowed_policies, default_policy, default_mode}`: what that tenant may start |
| `API_HOST`, `API_PORT` | `127.0.0.1`, `8100` | where `python -m mde_context_api` listens |
| `DATABASE_URL` | local private cluster | `postgresql+psycopg://...` |
| `PROJECTS_DIR` | `data/projects` | hand-off git repositories |
| `GATEWAY_MODE`, `GATEWAY_URL`, `GATEWAY_SERVICE_TOKEN` | `inprocess` | the gateway flavour |
| `DEFAULT_GUIDED_POLICY`, `DEFAULT_ENTERPRISE_POLICY` | `guided-default`, `enterprise-it-services-firm` | the policy per mode |
| `SPEC_BUILDER` | `real` | `fake` for the scripted builder |
| `BUDGET_PLAN_RESERVE_USD` | `0.05` | headroom one plan needs; below it planning pauses (spec 9) |
| `API_DOCS` | `0` | `1` serves `/docs`, `/redoc`, `/openapi.json` (unauthenticated: dev only) |
| `API_MAX_BODY_BYTES` (or `MAX_BODY_BYTES`) | 1048576 | request body limit |
| `MAX_UPLOAD_TEXT_BYTES`, `MAX_UPLOAD_IMAGE_BYTES`, `MAX_ORG_PROFILE_BYTES` | 512 KB, 10 MB, 64 KB | upload and profile limits |
| `WORKER_ENABLED` | `1` | `0`: run `python -m mde_context_api.worker` elsewhere |
| `WORKER_POLL_SECONDS`, `WORKER_HEARTBEAT_SECONDS`, `WORKER_RECOVER_AFTER_SECONDS`, `WORKER_STOP_TIMEOUT_SECONDS` | 0.5, 10, 120, 10 | worker timing |
| `SSE_POLL_SECONDS`, `SSE_MAX_POLL_SECONDS`, `SSE_KEEPALIVE_SECONDS`, `SSE_MAX_SECONDS`, `SSE_BATCH_SIZE` | 0.5, 2, 15, 900, 200 | the event stream (an idle stream backs off from the first to the second) |
| `API_MAX_STREAMS_PER_PRINCIPAL` | 5 | live streams per caller (`0` = no cap) |
| `API_SESSION_RATE_PER_MINUTE`, `API_SESSION_RATE_PER_HOUR` | 10, 100 | session creations per caller |
| `API_TENANT_SESSION_RATE_PER_MINUTE`, `API_TENANT_SESSION_RATE_PER_HOUR` | 10, 100 | session creations per tenant |

## Tests

```powershell
uv run python scripts/dev_db.py up
uv run pytest apps/context-api -q                      # offline and Postgres variants
uv run pytest apps/context-api --cov=apps/context-api/src --cov-report=term-missing -q
uv run ruff check apps/context-api ; uv run pyright apps/context-api ; uv run lint-imports
```

The fixtures (`conftest.py`) build the whole service over the in-memory store and over an ephemeral Postgres (`@pytest.mark.integration`): the real Discover and Research engines on the mock gateway, the scripted Spec Builder, an httpx ASGI client. Modules: auth, token identity and the entry-point refusal (`test_token_auth.py`), policy bindings (`test_policy_binding.py`), abuse limits with a fake clock (`test_abuse_limits.py`), sessions, a whole conversation from "hi" to a committed `APP_SPEC.md`, idempotent event ids, stale hashes, typed approvals, Stop during PLANNING, the worker (lifespan, a separate state on the same store, cancellation of a running job), SSE (replay, `Last-Event-ID`, live tail, keepalive, many idle streams on a small pool, a real socket), tenant isolation and approver roles, limits and uploads, the error table, ops (including the ledger check and that `/readyz` never shows the budget), the budget gate and the whole pause over HTTP with an approver's resume (`test_budget_gate.py`), the docs switch (`test_docs_switch.py`), composition and the entry points, the OpenAPI document (and that the committed export is current), and `test_process.py`, which starts `python -m mde_context_api` as a process and plays a whole conversation over HTTP. ASGI test transports buffer a whole response, so every SSE test is bounded (`follow=false`, `limit`, `timeout`) and the incremental behaviour is checked against a real localhost server (`testing.serve_app`).

## Known gaps

* The Org Profile has no port in `mde_contracts` yet (D-044): the API reads and writes it through `OrgProfiles` (`profiles.py`) and hands it to the flow, which gives it to Discover with the first turn of an Enterprise session (D-077). It is a snapshot at session start: changing the stored profile does not change a session that already began.
* No CORS or HTTPS, and the abuse limits are per process and keyed by the caller's declared identity: a gateway in front is expected for the network edge (spec 2.4, "Chat Gateway"). Dev-header authentication is for loopback only (see above); real SSO is still out of scope and `API_TOKENS` is the seam where it plugs in.
* The token table and the tenant policy bindings are read once at start-up (restart to change them); there is no token rotation or revocation API.
* The budget gate's reserve is a heuristic (what the build phase costs, USD 0.05): it stops early and asks an approver; the gateway's own guard stays the authority and still refuses every call that would break a cap.
* `/readyz` answers `failed` for a ledger problem without saying which; the reason is in the server log only.
* Flow messages are English only; a client that wants Hindi or Hinglish for the flow's own notices needs the strings moved into content (D-048).
