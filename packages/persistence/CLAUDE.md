# mde_persistence

**Job:** PostgreSQL storage: SQLAlchemy 2.0 async models, repositories implementing the `Store` / `UnitOfWork` protocols from `mde_contracts`, Alembic migrations, the Postgres-backed job queue. Repositories hide SQLAlchemy from engines. Guides: `docs/packages/persistence.md`, `docs/architecture/data-model.md`.

**Tables:** `tenant`, `session`, `session_event` (the `turn` event log: inbound and outbound, per-session `seq` for SSE replay; `(session_id, event_id)` is the idempotency guarantee, so there is no separate `idempotency_key` table), `slot`, `card_instance`, `research_job`, `research_fact` (projections maintained by the repositories; the JSONB on the owning record is the truth), `brief_version`, `research_note`, `spec_version` (`spec_draft` is a version with status `draft`; content is immutable, enforced by a trigger), `approval`, `memory_item`, `org_profile`, `policy`, `llm_call`, `job`.

**Queue:** `job` has kind `plan`, state, phase, payload, attempts, lock owner + heartbeat, `cancel_requested`; claim with `FOR UPDATE SKIP LOCKED` (system scope only); `recover_stuck`; a unique partial index plus an advisory lock guarantee one active run per session. `JobWorker` heartbeats and bridges cancellation to a `CancellationToken`; `InlineJobRunner` for tests.

**Test:** `uv run pytest packages/persistence` (needs `just db-up`; the pytest plugin creates an ephemeral database `mde_test_<ulid>` per session on port 54329, migrates it to head and drops it; an unreachable server is a failure, never a skip). The reusable store contract suite is `mde_persistence.testing_contract.CHECKS`.

**Conventions**
- Postgres 16+ only, psycopg 3 for runtime and migrations, **no SQLite**. Never use port 5432.
- Migrations append-only (`.claude/rules/migrations.md`): add a revision, never edit an applied one. The drift test must stay green.
- Tenant isolation enforced in the repository layer (every query filters by `tenant_id`, foreign ids behave as not found) and by composite foreign keys; `begin(None)` is system scope for job claim and recovery only.
- Constraint violations must not abort the transaction: use `ON CONFLICT ... RETURNING` and raise `Conflict` yourself.
- Money as integer micro-dollars; UTC-aware timestamps; time from the injected `Clock`.
- Owner: `persistence-engineer`.
