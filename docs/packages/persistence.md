# mde_persistence

PostgreSQL implementation of the persistence ports from `mde_contracts.ports`: SQLAlchemy 2.0 async models, repositories, `PostgresStore`, Alembic migrations, the Postgres job queue and its workers, the LLM call sink, and the test support (pytest plugin, record factories, a reusable store contract suite). Depends only on `mde_core` and `mde_contracts`. Schema and rules: [architecture/data-model.md](../architecture/data-model.md).

PostgreSQL 16+ only, psycopg 3 for runtime and migrations, no SQLite. Never port 5432, never Docker.

## Layout

```
packages/persistence/
  alembic.ini                         script_location -> src/mde_persistence/migrations (just migrate)
  src/mde_persistence/
    db.py             create_engine(settings), normalize_url, redact_url, ensure_selector_loop_policy
    models.py         declarative models (schema of record) + naming convention
    migrations/       env.py (sync psycopg 3), versions/0001_initial_schema.py (append-only)
    migrate.py        alembic_config(url), upgrade(url), downgrade(url) for tests and tooling
    store.py          PostgresStore, PgUnitOfWork
    repositories/     sessions, events, briefs (+ research), specs (+ approvals), jobs, standing
    worker.py         JobWorker, InlineJobRunner, JobHandler
    llm_calls.py      PgLlmCallSink
    jsonutil.py       model -> JSON dict, NUL stripping, UTC normalisation
    testing_plugin.py   pytest fixtures (ephemeral database per session)
    testing_factories.py  valid sample records
    testing_contract.py   the reusable store contract suite
  tests/              integration tests (marker `integration`) and unit tests
```

## Using it

```python
from mde_persistence import PostgresStore, create_engine

engine = create_engine(settings)          # pool of 5, no overflow; URL from DATABASE_URL
store = PostgresStore(engine)             # satisfies mde_contracts.ports.Store

async with store.begin(principal.tenant_id) as uow:      # one transaction, one tenant
    ses = await uow.sessions.get(session_id, for_update=True)
    ...
    await uow.sessions.save(new_rec, expected_version=ses.version)   # compare-and-set
    await uow.events.append_outbound(session_id, event)               # next per-session seq
# commit on clean exit, rollback on any exception
```

`PgUnitOfWork` also exposes `memory` (opt-in user and org memory items), `org_profiles` (one JSON document per tenant; the context API reads it when an Enterprise session starts and Discover turns it into `assumed` slots, see [discovery.md](discovery.md)) and `policies` (per-tenant `Policy` documents). They are tenant-bound like every repository but have no port in `mde_contracts` yet.

Error contract: `Conflict` for a stale `save`, a duplicate version or id, a second active run, an illegal status move; `NotFound` for an id that does not exist **or belongs to another tenant**; `TenantViolation` for a record of another tenant or any use of system scope outside job claim and recovery; `ValidationFailed` for an unknown status value or a mismatched event session id.

Rules for callers:

- A unit of work holds one pooled connection: keep it short; never hold one across an LLM call or an SSE stream.
- On Windows psycopg's async mode needs a selector event loop. Call `mde_persistence.db.ensure_selector_loop_policy()` before the loop is created (the pytest plugin does; `create_engine` calls it too, which is enough only when no loop exists yet).
- `SpecRepo.set_status` is the only way to change a stored spec version; approved content is immutable (also enforced by a database trigger).
- `JobRepo.complete(job_id, error="...")` retries while attempts remain (`attempts < max_attempts`, default 3): only report an error for failures worth another attempt, and make the handler resumable (`job.phase` survives retries).

## Job workers

```python
async def handle(job: JobRecord, cancel: CancellationToken) -> None:
    async with store.begin(job.tenant_id) as uow:      # after the claim, work in the job's tenant
        await uow.jobs.set_phase(job.id, "research")
    cancel.check()                                      # between steps; raises CallCancelled
    ...

worker = JobWorker(store, handle, worker_id="w1", poll_interval=1.0, heartbeat_interval=10.0,
                   recover_after=120)                  # recover_after: idle workers also release stuck jobs
await worker.run_forever(stop_event)                   # or: await worker.run_once()
```

While the handler runs, a watcher task heartbeats the job and polls `is_cancel_requested`, cancelling the handler's token with reason `cancel_requested`. Outcomes: handler returns -> `succeeded` (or `cancelled` if a cancel request was seen); `CallCancelled` -> `cancelled`; any other exception -> `complete(error=...)` (requeued, or `failed` after `max_attempts`); heartbeat rejected (job was recovered) -> handler cancelled with reason `lock_lost` and the worker does **not** complete the job. Keep `heartbeat_interval` well below the `older_than_seconds` used for `recover_stuck`.

`InlineJobRunner(store, handler).run_next()` / `run_all()` claims and runs queued jobs inline, with the same outcome mapping and no background task: deterministic for tests.

## Tests

```powershell
uv run python scripts/dev_db.py up        # or: just db-up   (private cluster, port 54329)
uv run pytest packages/persistence -q     # unit + integration
uv run pytest packages/persistence --cov=packages/persistence/src --cov-report=term-missing -q
```

The root `conftest.py` registers `mde_persistence.testing_plugin` automatically. Fixtures:

| Fixture | Scope | What |
| --- | --- | --- |
| `pg_admin_url` | session | Server DSN from `MDE_TEST_PG_URL` (default `postgresql://mde:mde@127.0.0.1:54329/postgres`). Unreachable server: the test **fails** with "start it with: just db-up"; it never skips. |
| `pg_database` | session | Fresh database `mde_test_<ulid>` with migrations applied, dropped `WITH (FORCE)` at the end. Never touches database `mde`. Names are unique, so agents sharing the server do not collide; databases left by crashed runs (older than 6 hours) are dropped at start. |
| `pg_engine`, `store`, `make_store` | function | Tables truncated first, async engine created inside the test loop and disposed after. `make_store(clock=..., max_attempts=...)` for fake-clock and attempt-limit tests. |
| `empty_database`, `pg_dsn`, `fake_clock` | function / session / function | A database with no tables (migration tests), a libpq DSN for raw SQL, a `FakeClock`. |

Mark database tests `@pytest.mark.integration`.

### The store contract suite

`mde_persistence.testing_contract.CHECKS` maps a name to an async check taking a `StoreFactory` (an async callable returning an **empty** `Store`). It asserts the protocol semantics through the ports only: compare-and-set, idempotent inbound events, outbound `seq` and `replies_to`, concurrency, spec immutability, one active run, exclusive claim, cancel, stuck-job recovery, attempt limits, tenant isolation for every repository, system scope, commit and rollback. `PostgresStore` runs it in `tests/test_store_contract.py`; the flow package's in-memory store runs the same checks:

```python
from mde_persistence.testing_contract import CHECKS

@pytest.mark.parametrize("name", sorted(CHECKS))
async def test_in_memory_store_honours_the_contract(name):
    async def fresh() -> Store:
        return InMemoryStore()
    await CHECKS[name](fresh)
```

Where the ports leave the exact error open (a cross-tenant write may raise `TenantViolation` or `NotFound`; an illegal spec status move may be any `MdeError`) the checks accept both. `mde_persistence.testing_factories` builds valid sample records (`new_session`, `new_brief`, `new_research`, `new_spec`, `new_approval`, `plan_payload`, `assistant_message`, `card_event`, `option_selected`, ...).

## Migrations

`just migrate` (or `uv run alembic -c packages/persistence/alembic.ini upgrade head`) applies migrations to `DATABASE_URL`. Never edit an applied revision; add a new one: `uv run alembic -c packages/persistence/alembic.ini revision --autogenerate -m "what changed" --rev-id 0002`, then review and hand-edit (triggers and raw SQL are not autogenerated). The drift test (`test_models_match_the_migrations`) compares the models with the migrated schema.
