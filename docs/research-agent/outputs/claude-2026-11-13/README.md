# Research outputs from the Claude test, 2026-11-13

What each Research run delivered, saved here so it can be read without the app. Report and opinion: [`../../claude-provider-test-report-2026-11-13.md`](../../claude-provider-test-report-2026-11-13.md). Every run used `claude-haiku-5-5` for all three routes (the earlier first-try runs are described in the report, not saved).

Each folder holds three files:

* `research.json`: the deliverable the Spec engine reads (schema `research.v5`), unchanged.
* `report.md`: the readable report the app's Results page renders (status, lane quality, to-confirm list, every item with its source and quote).
* `summary.json`: the numbers of the run (cost by step, slots, evidence labels, checker verdicts, fact rejections by reason, validation, unfilled slots).

| Folder | Input | Search | Status | Required slots | Facts (official / single / unverified) | USD |
| --- | --- | --- | --- | --- | --- | --- |
| `small-yoga-bengaluru-1-before-effort-floor` | `docs/compass/research-inputs/quick-yoga-studio-bengaluru.json` | Tavily | complete | 12 of 19 | 62 / 33 / 72 | 0.54 |
| `small-yoga-bengaluru-2-with-effort-floor` | the same | Tavily | complete | 19 of 19 | 101 / 84 / 24 | 0.61 |
| `small-bike-repair-porto` | `synthetic-small-bike-repair-porto.json` | Serper | complete | 16 of 22 | 96 / 100 / 39 | 0.27 |
| `medium-produce-marketplace-nashik` | `standard-produce-marketplace-nashik.json` | Serper | complete | 32 of 41 | 194 / 119 / 48 | 0.50 |
| `big-freight-fleet-india` | `thorough-freight-fleet-india.json` | Serper | partial (6 required slots empty) | 57 of 63 | 438 / 309 / 138 | 1.03 |

Other files:

* `consumer-probe-medium-nashik.json`: the stand-in Spec with and without the Medium research, judged per statement (not passed: invented 0 to 23).
* `evals/`: the checker (planted-error) and extraction (re-extraction) experiments behind the model and effort choices, one file each (D-172).

The full evidence of each run (page text, search results, model-call cassettes) is in `agents/research/.runtime/runs/<run id>/`, which is not in git. Run ids are in each `summary.json`.
