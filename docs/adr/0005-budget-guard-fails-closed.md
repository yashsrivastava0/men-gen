# ADR 0005: The budget guard fails closed

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (D-031, D-056, D-064; findings of the first security review)

## Context

Total live LLM spend for the job must never exceed USD 6.00 (hard rule 3). The money is real and the invoice is the only ground truth, so the guard in the repository must err on the side of counting too much. The first security review found places where it erred the other way: `LLM_BUDGET_USD=60` raised the cap, unknown scope kinds and a caller-supplied `must-run` purpose weakened the checks, a relative ledger path meant another working directory started with a fresh budget, and accounting failed open (a commit failure, a stale reservation or a timeout lost spend).

## Decision

The guard is a safety system and every uncertainty about money resolves to "spent":

1. **Reserve before every provider attempt**, inside the ledger's critical section (an OS file lock shared by all processes), in the same step as the check `spent + reserved + worst case <= effective cap`, globally and for every scope. Retries, the escalated retry and the repair call each reserve again.
2. **Limits nobody can lift.** `HARD_CAP_USD = 6.00` clamps `LLM_BUDGET_USD`; the effective cap is at most USD 5.70 whatever `budget.yaml` says; an unknown model, tool or scope kind is refused instead of running uncapped; tripwires at 50% (warn), 80% (only purposes starting `must-run`) and 95% (refuse everything). The HTTP API rewrites `must-run` to `api:must-run...`, so only in-process code holds the privilege.
3. **Book the worst case when unsure.** A commit that fails, a negative or unpriceable usage, a timeout, a cancellation, or a bug after the request left books the reserved worst case. Only a failure the provider proves was not billed releases. A reservation never closed counts as **spent** after ten minutes and is never released by time; a late commit replaces the worst case with the real cost.
4. **Ledger integrity.** The ledger path is anchored to the main repository root. A guard file (`<ledger>.guard`: size, lines, spend, first-line hash) makes a deleted, truncated or replaced ledger raise `LedgerIntegrityError` (HTTP 503) until an operator resets on purpose (`budget_cli init --force` or `MDE_BUDGET_LEDGER_RESET=1`) after reconciling with the dashboard.
5. **Three keys for live**: `LLM_MODE=live`, `ALLOW_LIVE_LLM=1` and no `MDE_FORBID_LIVE`. The pytest autouse fixture sets the last one, so tests cannot go live.
6. **Bounded retries** (D-064): the single escalated retry is capped (`max_escalated_output_tokens`, hard maximum 32,768) and metered; when two attempts ran out of tokens the repair call is skipped for efforts that cannot help; circuit breakers are keyed by route and tool class so a search outage does not open the breaker for plain calls.
7. An external backstop remains recommended: a USD 6 spend limit on the OpenAI project.

## Alternatives considered

- **Trust the environment for the cap.** Lets a typo or a hostile caller raise the limit. Rejected: clamp.
- **Release stale reservations after a time-to-live** (the first design). A crashed process's request may have been billed, so real spend vanished from the total. Rejected.
- **Book only input tokens on a timeout.** Under-counts output and search fees. Rejected.
- **Fail open when the ledger cannot be written** (finish the call, log it). The next call would be unbounded. Rejected: with the ledger unwritable every later reserve fails too.
- **In-memory counters per process.** Cannot be shared across the API, the worker and a CLI. Rejected.
- **Rely on the provider dashboard alone.** It is the backstop, not a guard, and offline tests cannot use it. Kept only as the external layer.

## Consequences

- The ledger runs slightly ahead of the invoice (worst-case bookings, burned reservations): reconcile against the dashboard, never "reset to get budget back".
- Every change to `budget.py`, `ledger.py` or `pricing.py` needs a test first; the gateway keeps 95% source coverage.
- Engines treat `BudgetExceeded` as "use the deterministic fallback", never as a reason to retry. The flow's budget gate parks a session in PAUSED before a phase it could not afford (Guided and Enterprise alike; an approver or the owner resumes).
- Operating procedure: [live budget runbook](../runbooks/live-llm-budget.md).

## References

[llm-gateway](../architecture/llm-gateway.md), [security](../architecture/security.md), DECISIONS D-031, D-056, D-064, brief 7.9.
