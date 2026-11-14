# Spec agent: what it is and where things are

Spec turns an approved Compass Brief plus Research into one static folder of files (the Spec Pack) that tells the front-end engine what the app is and does, never how it looks. Start with [BUILD-PLAN](BUILD-PLAN.md) (state and exit checks), the design in [architecture-v3/19](../architecture-v3/19-spec-big-apps-bench-and-result-first.md), then 18, 17, 16, 15. Package pages: [spec-core](../packages/spec-core.md), [compass-intake](../packages/compass-intake.md), [spec-agent](../packages/spec-agent.md), [verify-agent](../packages/verify-agent.md). HTTP contract: [api/spec-agent](../api/spec-agent.md). Decisions: `DECISIONS.md` from D-174.

Money: hard cap USD 5.00, tripwire 4.50, set in the launch environment. Nothing is Proven until its gate passes.

Fresh session on another machine: start with [HANDOFF](HANDOFF.md) (state, corrections, pending list, how to run, lessons).

## Live results so far

Eight live runs are saved in [outputs/live-2026-11-14](outputs/live-2026-11-14/). The table shows one run per size. Each row is a single run. A single run decides nothing, and none of these is a scored proof gate (BUILD-PLAN, "Proof gates"). Figures are copied from the report files.

| Run (single run) | Profile | Cost (USD) | Calls | Clean first try | Repaired | Fallbacks | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Quick, `run_01M7CPYEQ62FEN12Q05YE8S6BA` | `compact` | 0.0701 | 17 | 16 of 17 | 1 | 0 | approved |
| Standard, `run_01M7CQ4F4MPG6ZGFRA9QWNVFCX` | `standard` | 0.145 | 25 | 22 of 25 | 3 | 0 | approved |
| Thorough with Verify, `run_01M7CQCXT7XKPQKGVVT7JGYTQH` | `deep` | 0.5382 | 83 | 65 of 83 | 18 | 10 | approved |

- "Clean first try" means the call passed with no repair and no fallback. That is how `scripts/spec_run_report.py` counts it.
- The profile names come from `packages/spec-core/src/mde_spec_core/data/spec-config.yaml` (`tier_floor`: quick is `compact`, thorough is `deep`).
- Quick and Standard show no banner. Thorough shows the `weak_route` banner: "this route needed many repairs for step screens (12 of 65 nodes); consider a stronger route for it".
- Validators: Quick reports `validators.ok` true. Standard and Thorough report it false. Their validator summary does not name the failing check (the by-validator field is empty). Not explained yet.
- Thorough fallbacks: 4 in PROD-JOURNEYS, 1 in PROD-MODULE, 5 in VERIFY-CANARY. Its largest task card read 27,868 tokens of the 28,000 budget, and no card went over budget.
- Thorough's critic vendor is recorded as `same` (the gateway computed it, see [proof-tools](proof-tools.md)). Its final Verify call did not pass first time: two confirm items were written as "RI-086 text" and rejected. The call was repaired, not a fallback. Commit 838368c maps that wording back by code (D-188). The report was written at 15:38 and that commit at 15:40, so this run does not show the fixed code.
- Five earlier Quick runs are in the same folder, at USD 0.0413 to 0.0834 each (17 calls each). They were made while the code was changing and are not compared here.
- The eight reports add up to USD 1.08. The spend record reads USD 1.26 (read-only check on 2026-11-14), so about USD 0.18 is spend that these reports do not show.
