# ADR 0004: Transactions never span engine calls

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (with the flow and persistence engineers; D-042, D-049)

## Context

A unit of work pins one pooled PostgreSQL connection (pool of 5, no overflow) and, in the flow, holds a session row lock. An engine call is an LLM call: tens of seconds, sometimes a web search. A Discover turn that kept its transaction open across `engine.handle(...)` would hold a connection and a row lock for that whole time, so a handful of slow sessions would exhaust the pool and every other request, an SSE poll included, would wait. The same applies to a plan job and to an SSE stream.

The flow also needs the guarantees a transaction gives: an inbound event is recorded together with its effects, a retried event id is processed from scratch after a crash, the session is saved with a compare-and-set.

## Decision

**No unit of work is open while an engine runs.** A turn is a transaction *replayed around its engine calls* (`FlowService._run_turn`, engine calls through `_journalled`):

1. Pass 1: begin, lock the session row, record the inbound event, run the turn logic. When the logic needs an engine it raises, the transaction rolls back and the connection is released.
2. The engine runs with no connection held; its result is appended to the turn's journal.
3. Pass 2: begin, lock, record, run the same logic again, served the journalled result. Only the last pass writes anything durable, so replaying never double-applies.
4. Each journal entry stores a fingerprint of its request. If the logic on replay asks for a different request, the state moved while the engine ran: the journalled result is dropped and the turn starts over.
5. A lost compare-and-set (`Conflict`) re-reads and reruns the turn, at most 5 times (`MAX_TURN_RETRIES`).

The inbound event is recorded **in the same unit of work as its effects**, never in an earlier committed one. An in-flight duplicate in the same process is answered at once from what is stored (or with a `message_in_progress` notice). The plan job works in short per-phase transactions between engine calls; the SSE generator opens one short transaction per poll. Side effects that must follow a commit (notify the frontend, reminders, token cancellation) run after it; the only side effect allowed inside the logic is an idempotent one (`commit_spec`).

## Alternatives considered

- **Hold the transaction across the engine call.** Simple, but a pool of 5 serves five slow turns; locks are held for the length of an LLM call; a failing engine needs care to leave nothing behind. Rejected.
- **Commit a "received" record first, then process.** A crash or an engine failure would leave an event that was recorded but never applied, and the ports offer no way to un-record it; its retry would be refused as a duplicate. Rejected.
- **A bigger pool.** Hides the problem and moves the limit; the row lock would still be held. Rejected.
- **A session-level advisory lock across the engine call.** It needs a connection held for the same time. Rejected.

## Consequences

- A turn with N engine calls is N + 1 short units of work (a typed answer that completes the Brief makes two engine calls; draining a queue of 25 notes makes 25). Turn logic must be repeatable: no side effect outside the unit of work except idempotent ones.
- `tests/test_transactions.py` (flow) proves it: a Postgres pool of two connections serves six sessions stuck inside a slow engine, `checkedout()` is 0 while they wait, and a mutation that calls the engine inside the unit of work fails the test on both stores.
- A duplicate event delivered to a *second process* while the first is in flight runs the engine again (wasted cost, no second effect); closing that would need a durable in-flight marker.
- The budget gate's answer is journalled like an engine call, because the gate may be remote.

## References

[flow-and-gates, "Events and transactions"](../architecture/flow-and-gates.md), [data-model](../architecture/data-model.md), DECISIONS D-042 and D-049, `packages/flow/CLAUDE.md`.
