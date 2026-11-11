# Runbook: the Research app

The Research agent runs as its own app: one process, one address (`http://127.0.0.1:8120/`), its own front end, live models and the live web only. It is not part of Compass or its console. This page is for the person who tests it. Package page: [research-agent](../packages/research-agent.md). HTTP contract: [api/research-agent](../api/research-agent.md). Budget rules: [live-llm-budget](live-llm-budget.md). Design: `docs/architecture-v3/13-research-agent-design.md` (v5) and `14-research-proof-plan.md`.

## Start, stop, look

The one-line commands (run from the repository folder; `just` recipes call the same script):

```
! LLM_MODE=live ALLOW_LIVE_LLM=1 uv run python scripts/research_app.py start    # detached; prints the address
uv run python scripts/research_app.py status                                    # running? ready for live runs?
uv run python scripts/research_app.py logs                                      # last lines of the app and process logs
uv run python scripts/research_app.py stop
uv run python scripts/research_app.py restart
```

The `!` prefix runs the command in your own shell, with the two live switches (`LLM_MODE=live` and `ALLOW_LIVE_LLM=1`) that only you set. Search keys (`TAVILY_API_KEY`, and `SERPER_API_KEY` for the bake-off and failover) are read from your environment or `.env` as before (`uv run mde doctor` shows which exist, never the values). If a switch or key is missing the app still starts and its front end says exactly what to set; it never runs on made-up content. Plain `just research-dev` runs the same server in the foreground.

## Money

USD 5.00 in total for the app, every cent recorded, never exceeded. There is no per-run cap and no call-count cap.

| What | Value |
| --- | --- |
| Total | `RESEARCH_LIVE_BUDGET_USD` = 5.00 |
| Tripwire | `RESEARCH_TRIPWIRE_USD` = 4.50: the run in progress finishes, no new run starts |
| Hard stop | 5.00: the gateway guard refuses a call that would pass it and the run commits `partial` with a budget warning |
| Gateway cap | the app raises its own cap to at least `total / 0.8 x 1.04` = USD 6.50 so the guard's 80% must-run tripwire sits above 5.00 (`LLM_BUDGET_USD=6.50` gives the same; a higher value is used as is) |
| Ledger | one file per budget period: `data/.budget/research/ledger-<period>.jsonl` (the shared ledger with USD 2.37 of earlier spend is left untouched) |
| Record | `agents/research/.runtime/spend-record.json`: one entry per live action (run, bake-off, probe, ping, smoke) with the cost by step and a running total |

"Restart budget" on the Budget page closes the period (history is kept), opens the next at 0.00 with its own ledger file, and is refused while a run is active.

## Test it from the front end (the owner's checklist)

1. Open `http://127.0.0.1:8120/`. The Preflight pill says **Ready**. If it says Not ready, click it: each failing check says what to set. "Test the connection" (about USD 0.01) does one search and one tiny model call.
2. **Inputs** shows the three real exports as Small, Medium and Big, each with a preview and an estimate ("estimate, not measured" until a real run of that size exists): idea, place, counts, size class, light mode, whether the extra trust checks run and why, the planned topics, warnings, and a cost and time range.
3. Run the **small** one: Run research, read the confirm dialog, confirm. Watch the plan, the live feed (queries, sites, pages being read, findings going from "checking" to "checked" or "rejected"), the counters and the cost so far by step. You can Skip research, or cancel one topic.
4. When it ends, **What we found** opens: the playbook by lane, the norms, what to confirm, gaps and conflicts, the appendix, **Quality** (lane quality, validation, extraction pass rates, checker rates, timings, cost by step, fallbacks used), the sources, the **Report** (read it, download it) and the raw JSON. Every item shows its claim, its data, the best quote, the source with date and link, and how well it is backed (official, corroborated, single source, unverified). Reject an item you disagree with; the committed file never changes.
5. Run the **medium**, then the **big** one. One run at a time; a second start waits in the queue and starts by itself.
6. **Spot-check**: pick the three runs, answer 20 cards per run (keys 1 to 6), one fact at a time, saved after every answer and resumable. The end screen compares your answers with the thresholds (supported 95%, ok 90%, no invented detail on a high-impact item and fewer than 3 overall) and names the job or kind that went wrong. Export it as `.md` or `.json`.
7. **Budget**: the spend table and the budget bar agree with the runs. Restart needs the word `RESTART`.
8. Close the tab during a run and come back: the run continued and the page resumes with no duplicate lines. Two tabs agree.
9. Drop a new export `.json` into `docs/compass/research-inputs/`: it appears within 10 seconds, no restart.
10. A bad file (not JSON, not a Compass export, a changed or unapproved Brief) shows under "Skipped files" with a plain reason.

**Tools** has the search-provider bake-off (about USD 0.40, needs the word `RUN` and budget room): it asks both providers the same generic queries and recommends one; "Use this provider" saves it for the next run.

## Where things are

| What | Where |
| --- | --- |
| Inputs (read-only for the app) | `docs/compass/research-inputs/` (`RESEARCH_INPUTS_DIR`) |
| Data | `agents/research/.runtime/` (`RESEARCH_DATA_DIR`, git-ignored): `runs/<run_id>/{research.json, evidence.json.gz, events.jsonl, input.json.gz, cassettes/}`, `spend-record.json`, `uploads/`, `spot-checks/`, `settings.json`, `bakeoff.json`, `logs/` |
| Logs | `logs/app.log` (rotating, structured) and `logs/process.log` |
| Front end | `apps/research-console/` (plain JavaScript, no build step) |
| PID file | `agents/research/.runtime/app.pid` |
| Store | files by default; `RESEARCH_STORE=postgres` (`just db-up`, `just research-migrate`) |

Every run records its search results, pages and model calls as compressed cassettes under its folder, so a developer can replay it offline.

## Reading a run and what is weak

Start with the Report or the Quality tab. Look at lane quality (`strong`, `partial`, `thin`, `none`), the builder-need coverage, the checker rates (a rate under 1% rejections is a rubber-stamp signal) and the acceptance rows. A run that finishes `partial` is still usable: the gaps and the run summary say why. The weak points to expect: an unmeasured provider choice, a checker from the same model family as the writer (`checker_vendor: same`), English-only labels, and estimates that are seeded, not measured.

## Developer scripts (offline unless the owner sets the switches)

| What | Command |
| --- | --- |
| Offline suite | `just research-test` |
| Everything offline, including layers, docs check and secret scan | `just ci` |
| PostgreSQL store | `just db-up`, `just research-migrate`, `just research-test-int` |
| Test-only fake app for the browser tests | `uv run python agents/research/tests/e2e/fake_app.py --port 8140` (offline doubles, a loud banner, never the real app) |
| Headless run on an export over fakes | `uv run python -m mde_research_agent.run --export <export.json> --mock --out <dir>` |
| Batch plan and cost estimate, calls nothing | `uv run python -m mde_research_agent.live_runs --run all --dry-run` |
| Evaluation plan and cost estimate | `uv run python -m mde_research_agent.eval planted --dry-run` (and `probe`) |

The evaluation commands (`planted`, `probe`) and the older batch scripts spend real money when run without `--dry-run`, `--mock` or `--replay`: only with the owner's switches, with the app stopped, booked in the spend record.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Run refused: "Research cannot start yet. Set: ..." | a switch or key is missing; the message names which (never a value); restart the app with it |
| "The budget tripwire was reached" | spend is at 4.50 or more; the run in progress finishes; restart the budget on the Budget page to continue |
| A run shows Interrupted | the app restarted while it ran; what it spent is recorded; use Re-run |
| A run is stuck with no events for 10 minutes | it is marked `failed: stalled` by the sweeper so its input is free again |
| Every search fails | wrong key or the provider is out; with both keys the app fails over to the other provider and says so in the warnings |
| Page says "cannot reach the app" | the app is not running: `scripts/research_app.py status`, then `start` |
| `StoreLocked` at start | another Research process uses the data folder; `scripts/research_app.py stop` |

## Revert

Everything of this work is on the branch `feat/research-agent-v5`, nothing is pushed or merged. Safety tag before it: `pre/research-v5-20261111-1512`. To go back: `git switch -c recover/research-v5 pre/research-v5-20261111-1512`; to undo the whole task as new commits: `git revert --no-commit pre/research-v5-20261111-1512..HEAD` then commit.
