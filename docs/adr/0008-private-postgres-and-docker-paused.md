# ADR 0008: A private PostgreSQL cluster; Docker Desktop stays paused

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (D-003; refines [ADR 0002](0002-environment-adaptations.md))

## Context

The brief assumes `docker compose up -d` gives a database. This machine is Windows 11 with about 3.7 GB of free RAM. Docker Desktop is **paused on purpose by the user** and holds other projects' containers, so starting, unpausing or restarting it changes state the user chose. Port 5432 and another PostgreSQL belong to the user's other projects. The stack forbids SQLite, including in tests ([ADR 0001](0001-stack.md)). Persistence needs real Postgres semantics (`FOR UPDATE SKIP LOCKED`, advisory locks, partial unique indexes, JSONB, triggers).

## Decision

- `scripts/dev_db.py` (`just db-up`, `db-down`, `db-reset`; `status` and `url` subcommands) provides a **private cluster**: `initdb` from the local PostgreSQL 18 binaries (`C:\Program Files\PostgreSQL\18\bin`) into `<main checkout>/data/pg`, listening on `127.0.0.1:54329` with trust authentication. The default URL is `postgresql+psycopg://mde:mde@127.0.0.1:54329/mde`.
- The script follows the brief's availability ladder: (0) a server already reachable on `MDE_PG_PORT`; (1) Docker, **only if its daemon already answers** (never unpausing or restarting it); (2) the private cluster; (3) the `pgserver` package as a last resort. Never SQLite. Never port 5432.
- Tests create an ephemeral database `mde_test_<ulid>` per session on that server, migrate it to head and drop it; they never write to the `mde` database. An unreachable server is a test **failure**, not a skip.
- `docker-compose.yml` stays for CI and other machines; CI runs a Postgres service on port 54329 so the same URL works there.
- Standing rules for agents and docs: never start, stop or restart Docker Desktop, never run `wsl --shutdown`, never use 5432.

## Alternatives considered

- **Unpause Docker and use compose.** Changes shared machine state the user deliberately set and costs RAM that is scarce. Rejected.
- **Use the user's PostgreSQL on 5432.** Other projects' data and credentials; a test run could damage them. Rejected.
- **`pgserver` embedded Postgres.** Kept as the last rung only.
- **SQLite.** Forbidden; behaviour differs.

## Consequences

- `just db-up` works without Docker and without admin rights; the data directory lives in the **main** checkout, so git worktrees share one server (the port is fixed, so only one cluster runs).
- Trust authentication is acceptable only because the server listens on loopback; the cluster must never be exposed.
- The binary path is Windows-specific; on another machine the ladder falls to Docker or `pgserver`.
- Postgres integration tests and `just migrate` need the server up: troubleshooting entry in [the runbook](../runbooks/troubleshooting.md). A paused Docker Desktop is normal and is not a reason to touch it.

## References

[local-dev runbook](../runbooks/local-dev.md), [data-model](../architecture/data-model.md), DECISIONS D-003, brief 6.3.
