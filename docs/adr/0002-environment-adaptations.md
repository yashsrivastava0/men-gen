# ADR 0002: Environment adaptations

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead

## Context

The brief was written for a generic Linux-like setup. The actual machine is Windows 11 with about 3.7 GB free RAM, Docker Desktop paused by the user, other projects' Postgres services on 5432, and a home directory that is itself a git repo. These adaptations keep the brief's rules intact on this machine. Full entries are in [DECISIONS.md](../implementation/DECISIONS.md).

## Decision

- **D-001 Repo root.** `Mobigen` gets its own `git init`; the home directory repo (no commits) is not used.
- **D-002 Python.** 3.13.7 (installed) via uv, hatchling build backend, `requires-python >=3.12,<3.14`.
- **D-003 Postgres.** Docker Desktop is manually paused by the user and must not be unpaused or restarted (other projects' containers live there); ports 5432 and another PG belong to other projects. A private PostgreSQL 18 cluster is `initdb`'ed from `C:\Program Files\PostgreSQL\18\bin` binaries into `data/pg` on port 54329 (trust auth, 127.0.0.1 only) by `scripts/dev_db.py`; `docker-compose.yml` remains for CI and other machines. Tests create an ephemeral database per session on that server.
- **D-004 File locking.** `fcntl.flock` is POSIX-only; `mde_core.filelock` is portable (msvcrt on Windows, fcntl elsewhere) and guards the budget ledger.
- **D-005 `.env`.** Contains `OPENAI_API_KEY` twice; harmless; settings and doctor tolerate it; the key is never printed.
- **D-006 `just`.** Installed with `uv tool install rust-just`; the justfile uses PowerShell as the Windows shell; recipes are plain `uv run ...`.
- **D-007 CallSpec location.** `CallSpec`, `CallContext`, `CallResult`, `Usage`, `GatewayClient` live in `mde_contracts` because the layering forbids the gateway from importing `mde_prompts`; prompts instantiates specs and the gateway loads them through a registry passed at construction.
- **D-008 Strict-schema-safe slot values.** LLM output models avoid free-form JSON; slot values are `value_text | value_list | value_bool`.
- **D-009 Contract freeze policy.** v1 tagged `contracts-v1`; additive changes by the lead logged without a bump; breaking changes need an ADR and a version bump.
- **D-010 Repository protocols.** `Store` / `UnitOfWork` live in `mde_contracts.ports` so `flow` is built against in-memory fakes while `persistence` implements them on Postgres. No SQLite.
- **D-011 Parallelism.** At most 4 to 5 subagents; pyright only on own package paths; no background processes.
- **D-012 Archetype ids.** Plain strings validated against content, not a `Literal`.
- **D-013 Live guard.** `MDE_FORBID_LIVE=1` (set by the pytest autouse fixture) makes any live provider construction raise `LiveCallForbidden`.

## Alternatives considered

- Unpausing Docker for compose-based Postgres: rejected, it changes shared machine state the user chose.
- Using the user's existing Postgres on 5432: rejected, it belongs to other projects.
- `pgserver` embedded Postgres: kept only as a last-rung fallback.

## Consequences

- `just db-up` works without Docker; CI uses the compose service.
- All paths and commands in docs are Windows-first with POSIX notes.

## References

Brief sections 2, 5.2, 6.3, 7.4, 7.9, 10.2; [ADR 0001](0001-stack.md).
