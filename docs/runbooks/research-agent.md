# Runbook: the Research agent

Run, test and (for the owner) run live. Package page: [research-agent](../packages/research-agent.md). HTTP contract: [api/research-agent](../api/research-agent.md). Budget rules: [live-llm-budget](live-llm-budget.md).

## Offline (anyone, any time)

| What | Command |
| --- | --- |
| Offline suite (about 1,100 tests) | `just research-test` |
| Everything offline, including layers, docs check and secret scan | `just ci` |
| PostgreSQL store | `just db-up`, `just research-migrate`, `just research-test-int` |
| Dev server, no database | `just research-dev-memory` (port 8120) |
| Dev server with the private Postgres | `just research-dev` |
| Headless run on an export, fake search and models | `uv run python -m mde_research_agent.run --export <export.json> --mock --out <dir>` |
| Batch plan and cost estimate, calls nothing | `uv run python -m mde_research_agent.live_runs --run all --dry-run` |
| Batch plumbing over fakes (writes to a temp folder) | `uv run python -m mde_research_agent.live_runs --run quick --mock` |
| Replay a recorded batch (never goes live) | `uv run python -m mde_research_agent.live_runs --run quick --replay` |
| Bake-off plan and cost estimate | `uv run python -m mde_research_agent.live_bakeoff --dry-run` |
| Which keys exist (names, never values) | `uv run mde doctor` |

With the test console: start Compass (`uv run python -m mde_compass --memory --port 8110`) and Research (`just research-dev-memory`), open `http://127.0.0.1:8110/ui/`. The console says "MOCK MODE" while Research runs on fakes.

## Live prerequisites (owner only)

The agent never sets these, never reads `.env`, and the scripts refuse to start without them (exit code 2, nothing called).

1. A search key in `.env` or the environment: `TAVILY_API_KEY` and, for the bake-off, `SERPER_API_KEY` (check with `uv run mde doctor`).
2. The cap: the repo ceiling is USD 8.00 (D-125), the cap in force is `LLM_BUDGET_USD` (default 6.00). USD 2.37 was already spent, and at 8.00 the must-run-only tripwire is 6.40, so about USD 4.03 is usable. Research's own limit is `RESEARCH_LIVE_BUDGET_USD` (4.00 for all of Research, shared ledger, `rsch.` ids) and `RESEARCH_RUN_CAP_USD` (2.50 per run). Check the ledger first: `just budget`.
3. The second key, `ALLOW_LIVE_LLM=1`, together with `LLM_MODE=live`, on the command itself (the `!` prefix runs it in your own shell):

```
! LLM_MODE=live ALLOW_LIVE_LLM=1 LLM_BUDGET_USD=8.00 uv run python -m mde_research_agent.live_bakeoff --write-default
! LLM_MODE=live ALLOW_LIVE_LLM=1 LLM_BUDGET_USD=8.00 uv run python -m mde_research_agent.live_runs --run quick --record
! LLM_MODE=live ALLOW_LIVE_LLM=1 LLM_BUDGET_USD=8.00 uv run python -m mde_research_agent.live_runs --run all --record
```

## Batch B1: the search bake-off

About 40 queries derived from the example Briefs, sent to Tavily and Serper with the same locale parameters. Measured on the top 5 results: share from the target country's own domains or official sources, on-topic share (one cheap batched model judgement), distinct domains, page text availability, latency, cost per query and which locale parameters were really applied. The default is the provider with the higher local share (within 5 points, the cheaper one). Estimated cost about USD 0.40. Output: `docs/research-agent/reports/bakeoff-<date>.md`, raw results as cassettes, and with `--write-default` the winner in `data/providers.yaml`.

## Batch B2: full runs

`--run quick|standard|thorough|synthetic|all`; `--record` saves search, fetch and model cassettes under `agents/research/tests/cassettes/research-<name>/` so a run can be replayed offline with the same content hash. Estimated cost (dry run): quick 0.28, standard 0.57, thorough 1.15, synthetic 0.57, about USD 2.6 in all. Before each run the script checks the allowance (`--allowance-usd`, default 4.00) against the shared ledger; if it is nearly used it finishes the run in progress and starts no new one. Outputs: `agents/research/examples/<name>/{research.json, events.jsonl, evidence.json, summary.json}` and a one-page summary at `docs/research-agent/examples/<name>.md` with the acceptance measures (quote check 100%, required slots at least 70%, searches contributing at least 60%, norms with 3 or more sources at least 70%, recent share, high-impact items corroborated or flagged).

## Reading a run

The most useful first look is the one-page summary: status, load class, counts, the measures against their targets, unfilled required slots, top norms, the lane digests (what each Spec lane would read) and the gaps. Then open `research.json` and read a lane digest as the Spec lane would. A run that finishes `partial` is still usable: the `gaps` and `profile` say why.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `cannot run live: ...` | a switch or key is missing; the message names which (never a value) |
| `BudgetExceeded` / `budget_stop` warning | the shared ledger or the Research allowance is nearly used; `just budget`, finish what is running, report |
| every search fails with `provider_down` | the key is wrong or the provider is out; `uv run mde doctor`, then try the other provider with `--provider` |
| console says "could not reach" | Research is not running on 8120, or the address in Settings needs `MDE_RESEARCH_ORIGIN` on the Compass server |
| a Hypothesis or replay test fails after a prompt change | re-record cassettes (live) or update the mock; never edit cassettes by hand |
