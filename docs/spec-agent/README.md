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

## Saving a run's output under docs/

A finished run lives in `agents/spec/.runtime/runs/<run_id>/`, which is not in git. To keep it where the owner can read it (the way Compass keeps `docs/compass/outputs/` and Research keeps `docs/research-agent/outputs/`), copy it with one command:

```
uv run python -m mde_spec_agent bench export --run <run_id> [--label "<input id>"] [--out docs/spec-agent/outputs/<folder>]
uv run python scripts/spec_export_outputs.py agents/spec/.runtime/runs/<run_id> --out docs/spec-agent/outputs/<folder>
```

Both do the same thing. The default folder is `docs/spec-agent/outputs/<mock|live>-<date of the run>`; the run goes in `<folder>/<run_id>/`, and several runs can share a folder.

What lands in `<folder>/<run_id>/`:

* `README.md`: plain words for a non-engineer: which input, MOCK (placeholder text written by code, no model, no cost) or LIVE, status, cost, what to open first (`pack/INDEX.md`, `pack/screens/`, `pack/contract/`, `evidence/self-check.json`) and how to re-check.
* `pack/`: only the files `pack/MANIFEST.json` lists (each checked against its hash), plus `MANIFEST.json`, `index.json` and `build/build-plan.json`. The run store never deletes a stale file after a Verify re-assemble, so the tree is never copied whole. The bytes are the engine's own.
* `evidence/*.json`, `graph.json`, `run.json`, `report.json` (the numbers of `scripts/spec_run_report.py`) and `summary.json` (one small numbers file: calls, first-try pass and repair rate per kind of call, pack size and hash, task reads against the budget, validator result, banners, cost).
* Not copied: `checkpoints/`, `events.jsonl`, `input.json.gz`.

Re-check a saved pack at any time, free of charge: `uv run python -m mde_spec_core check docs/spec-agent/outputs/<folder>/<run_id>/pack`.

`docs/spec-agent/outputs/README.md` is the index of every saved run (one row each, newest first, MOCK or LIVE). The exporter rewrites it on each export, with the same bytes for the same folders; do not edit it by hand. `--no-index` skips it.

**Safety check.** Everything under `docs/` is scanned by the repo's guards, so the exporter runs the same patterns before it writes anything. They are imported from `tests/security/test_single_live_switch.py` (the forbidden phrases of D-140) and `tests/security/test_repo_hygiene.py` (key-like text, through `mde_core.redact`), never copied. A file that hits is not copied and is never edited, because a changed byte would break the pack's hashes. It is listed in `EXPORT-NOTES.md` as "not copied: path, reason" (never the matched text) and the command exits 1 with nothing else copied. With `--allow-skip` the rest is copied, the README says "Not complete", and the exit code is 0. A `secret-scan: allow` marker is not honoured: a run is model output, not a fixture. The check is pattern based; it does not compare against real values in `.env` (nothing here reads that file).

Other refusals (exit 2): the run is still going, the run record says MOCK and `--mode live` was given (or the reverse), a listed pack file changed after assembly, the destination folder exists and is not a Spec export.

Reports made before this exporter show an empty `validators.by_validator` and `by_severity`: `scripts/spec_run_report.py` read them from the top of `evidence/self-check.json`, but they sit under `counts`. It reads them correctly now; `validators.ok` was always right.
