# ADR 0009: An independent security review before live work, and how its findings are routed

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (D-055, D-056, D-057, D-058)

## Context

The system's safety rests on a few hard rules: approvals bound to a hash, no secrets stored, no spend past USD 6.00, untrusted content never obeyed, tenants isolated. The authors of those controls are the worst people to attack them. Meanwhile two agents were editing the very files in which most findings would land (the flow and API on one side, the core, gateway and hooks on the other), so a single sweeping fix branch would have collided with work in flight.

## Decision

1. **Review before the live batches.** A read-only reviewer with no write access examines `main` and **reproduces every finding in mock mode** (a failing test or a transcript), so each is real and each fix can be proved. The first review found no critical issues and four high ones (D-055 counts four and itemises H1 to H3; the full table is in [security](../architecture/security.md)).
2. **Route by file ownership**, not by severity:
   - findings in the flow, the API and their tests went to the agent that was editing those files (`feat/api`, merged in D-058): H1, H2, M1, M7, M8, M9 and the hand-off name checks;
   - findings in `mde_core`, the gateway, the hooks and the repository scans went to a hardening agent (`feat/harden`, merged in D-056): H3, M2 to M6 and the low items;
   - cross-cutting follow-ups (validator 9 of the spec builder, D-057) were taken by the lead.
3. **Every fix lands with a test that fails when the fix is removed.** For anything on the approval path the test is mutation-checked; the stateful property suite gained attacker rules (an attacker who claims the requester role presses approve on every open gate; one person holding two roles).
4. **Open items stay on the record** (D-055 lists them) instead of being dropped: the gateway cache and tenants, the "trusted" label on research summaries in four templates, and the review's "not reviewed" list.
5. **A second review pass** over the API and the final tree is a Phase 5 item; its results go in the "Review 2" section of the security page, with the same table shape.

## Alternatives considered

- **Fix everything on one branch by the lead.** Serializes the work and conflicts with in-flight changes to the same files. Rejected.
- **Route every finding to one agent.** Loses file ownership; an agent would edit packages it does not know. Rejected.
- **Self-review by the authors.** Not independent; blind to the assumptions the authors share. Rejected.
- **Fix without regression tests.** No proof, and the same bug can return silently. Rejected.

## Consequences

- [security.md](../architecture/security.md) carries the findings table, what the reviewer confirmed safe and what it did not review, so the second review has a scope.
- The pattern is reusable: reproduce, route by ownership, fix with a mutation-checked test, record what remains.
- Some fixes changed behaviour visible to clients (a stranger now gets 404 where it was 403, `/docs` is off by default, `/readyz` says less); API docs and tests were updated in the same change.
- Process slips are recorded too (D-063: marker edits lost when a worktree was removed; D-067: a conflict marker committed before a follow-up fixed it), and their rules (check `git status` before removing a worktree, `git grep '^<<<<<<<'` before committing a merge) are in [troubleshooting](../runbooks/troubleshooting.md).

## References

[security](../architecture/security.md), [flow-and-gates](../architecture/flow-and-gates.md), DECISIONS D-055 to D-058, D-063, D-067, `.claude/agents/security-reviewer.md`.
