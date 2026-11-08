---
paths:
  - "packages/persistence/**/migrations/**"
  - "packages/persistence/**/alembic*"
  - "packages/persistence/alembic.ini"
---

# Migration rules

- Migrations are **append-only**. Never edit, rename, reorder or delete a migration that has been committed or applied; write a new revision instead.
- PostgreSQL 16+ only (JSONB, `FOR UPDATE SKIP LOCKED`, advisory locks, partial unique indexes). No SQLite, not even in tests.
- One driver for runtime and migrations: psycopg 3 (`postgresql+psycopg://`).
- Never use port 5432 (it belongs to other projects). The dev DB is the private cluster on port 54329 (`just db-up`).
- Each revision has a clear message, an `upgrade()` and a working `downgrade()` where feasible, and is covered by a test that migrates a fresh database to head.
- Constraints that carry business rules (one active run per session: unique partial index; per-session event sequence; idempotency keys) live in the schema, not only in code.
- Tenant isolation is enforced in the repository layer; every tenant-scoped table has `tenant_id`.
