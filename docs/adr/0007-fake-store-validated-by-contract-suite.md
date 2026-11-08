# ADR 0007: The in-memory fake store is validated by the production contract suite

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (D-010, D-043, D-049)

## Context

The flow controller was built and tested in parallel with the PostgreSQL persistence layer. D-010 put the repository protocols (`Store`, `UnitOfWork`, the repos) in `mde_contracts.ports`, so the flow could run against an in-memory fake (`mde_flow.testing.InMemoryStore`) while `mde_persistence` implemented the same protocols on Postgres. A fake that is not held to the real thing drifts: every flow unit test and the hypothesis stateful suite would be proving the flow against behaviour production does not have. SQLite as the fake is forbidden (it differs from Postgres in locking, JSONB and partial indexes).

## Decision

1. **The contract suite is the authority.** `mde_persistence.testing_contract.CHECKS` (22 checks: sessions and compare-and-set, idempotent events, outbound `seq`, versions, immutability, approvals, the job queue, tenancy, system scope, commit and rollback) defines what a `Store` must do.
2. **Every implementation passes it.** Postgres runs it in `packages/persistence/tests` (needs `just db-up`). The fake runs the same checks offline in `tests/integration/test_inmemory_store_contract.py`.
3. **`FlowService` itself runs on both stores.** The scenario modules are parametrized over the `flow_store` fixture (memory and an ephemeral Postgres); assertions read through `kit.peek`, which uses only the repository ports.
4. **A divergence is a bug in the fake** (or, if a check is over-specified, a decision to change the suite, logged). A check is never loosened because the fake cannot meet it.

The first run found 9 of 22 checks failing: the fake accepted duplicate approvals, notes and versions, let system scope read everything, only flagged a queued job on cancel, completed a job with an error as `failed`, ignored lost heartbeats, never failed an exhausted job on recovery, did not stamp `approved_at`, and allowed `handed_off -> superseded`. All nine were fake bugs, none a suite error. After the fix on `feat/reconcile` the fake passes 22 of 22 (D-049).

## Alternatives considered

- **Test the flow only against Postgres.** Slower, needs the database for every unit test, and serializes the work of two engineers. Rejected for unit and property tests; Postgres still runs the scenarios.
- **SQLite as the offline store.** Forbidden by the stack and a different engine. Rejected.
- **Trust a hand-written fake without a suite.** That was the starting point and 9 of 22 behaviours were wrong. Rejected.
- **Mocks per test.** No shared semantics; each test would invent its own store. Rejected.

## Consequences

- A new store behaviour that matters gets a check, then an implementation in both stores. A change to a port needs both updated in the same change.
- One known, accepted difference: the fake makes an open unit of work's writes visible to others immediately (Postgres is READ COMMITTED). The flow writes only under the session row lock, so it cannot observe the difference.
- Parametrized flow tests run twice; the Postgres half is marked `integration`.
- The same pattern applies to any future fake of a port (the budget gate, the project repo): validate it against the real implementation's checks.

## References

[flow-and-gates, "The in-memory store is validated against the production contract"](../architecture/flow-and-gates.md), [persistence](../packages/persistence.md), DECISIONS D-010, D-043, D-049.
