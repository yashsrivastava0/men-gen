# Runbook: live LLM budget (USD 6.00 total)

Audience: the lead (or the one designated `live-runner`) and the user who owns the budget. Subagents never run live batches. Background: [architecture/llm-gateway.md](../architecture/llm-gateway.md), [ADR 0005](../adr/0005-budget-guard-fails-closed.md), [security](../architecture/security.md).

## Who decides: the second key

A live call needs **three** things at once: `LLM_MODE=live`, `ALLOW_LIVE_LLM=1`, and `MDE_FORBID_LIVE` unset. `ALLOW_LIVE_LLM=1` is the **second key**, and it must be in the **environment of the process that runs the batch**: the gateway, the batch runner and the pytest guard all read it from there, and the live-call hook refuses commands that would switch live mode on unless it is already present. **Adding that key is a decision for the user (the operator)**, made deliberately and for the batch at hand; no agent adds it on its own, and nothing in this runbook is a way around the hook or the permission system.

Where things stand (2026-11-08): L0 ran (7 of 7, USD 0.0222 spent). L1 to L4 are waiting for that decision (D-069). The options, as logged there: (1) the user allows the lead's session to carry the key, by an `env` entry in the gitignored `.claude/settings.local.json` that the user writes or explicitly approves, removed again after the batches; (2) the user runs each batch in their own terminal (commands below); (3) live work is skipped, and Definition of Done item 5 (three live end-to-end sessions) is reported as unmet.

## The rules in one paragraph

Total live spend for the whole job never exceeds USD 6.00; the guard plans against 95% of that (USD 5.70). `LLM_BUDGET_USD` can only lower the cap: a larger value is clamped to 6.00 with a `budget_cap_clamped` warning. Live calls need `LLM_MODE=live` AND `ALLOW_LIVE_LLM=1` and are impossible while `MDE_FORBID_LIVE=1` (the pytest fixture sets it). Every call reserves its worst case first and commits the real cost afterwards, in one append-only ledger shared by all processes. Anything uncertain is booked as spent (worst case), and a deleted or truncated ledger stops live calls until it is reset on purpose (see "Recovering"). Only code that runs inside the process can use the `must-run` purpose; the HTTP API strips it.

## Before the first live batch

1. **Set an external backstop**: in the OpenAI dashboard set a USD 6 monthly spend limit on the project that owns the key (and an email alert at USD 3). The in-repo guard cannot see spend by other tools or retries it did not make; the dashboard limit can.
2. `uv run python scripts/doctor.py` reports key presence only. Make sure the main checkout's `data/.budget/` is writable and `just budget` prints USD 0.0000 (or the total so far). The ledger always lives in the **main repository** (`<main checkout>/data/.budget/ledger.jsonl`), whichever worktree or directory you run from; `uv run python -m mde_llm_gateway.budget_cli init` creates it with its guard file (a first live call does the same).
3. Confirm `LLM_BUDGET_USD` is 6.00 (default) and that `pricing.yaml` still matches the model page (gpt-6-luna 0.10 / 0.01 / 0.50 per 1M tokens, web search 0.01 per call).

## Running a batch (lead, or the user in their own terminal)

One batch at a time, in the order of brief 9.4: L0, L1, L2, L3, L4. The second key goes in the OUTER environment of the terminal that runs the batch, never in a committed file:

```powershell
$env:ALLOW_LIVE_LLM = "1"        # this terminal only; closing it removes the key
$env:LLM_MODE = "live"           # the batch runner needs BOTH switches in the outer environment
just live-smoke                  # L0: uv run pytest tests/live -m live -k smoke -q   (at most USD 0.30; already done)
just live-eval L1                # L1 to L4, one at a time (the batch id is required)
just budget                      # spend by scope, % of cap, live-call count, tripwire
```

`just live-eval` sets neither switch, so both lines above are needed in the same terminal. The equivalent without `just`: `uv run python -m evals.runners.live_batch --batch L1` (add `--dry-run` first, and `--quick` for one unit of each kind).

Without `ALLOW_LIVE_LLM=1` every `live` test skips itself. The batch runner (`evals/runners/live_batch.py`, behind `just live-eval`) does **not** switch live mode on by itself: it **refuses**, with the reasons in plain words, unless the second key **and** `LLM_MODE=live` are in the outer environment, `MDE_FORBID_LIVE` is not 1, the ledger's tripwire is below 95% (below 80% unless `--must-run`) and the headroom under the effective cap covers the batch's allowance. (`--dry-run` needs no switches in the runner, but the live-call hook in a Claude Code session still blocks the command text unless `ALLOW_LIVE_LLM=1` is already in the session environment.) It stops a batch when the ledger delta (not an estimate) reaches the allowance, records a cassette for every live response and prints the delta. Calling the module without `--batch` prints the plan and runs nothing; `--dry-run` shows what a batch would run.

| Batch | Allowance (USD) | Runs |
| --- | --- | --- |
| L0 smoke | 0.30 | seven live checks of the gateway (done: USD 0.0221) |
| L1 Discover | 1.20 | ten personas, two runs each, up to the Brief |
| L2 Research | 1.00 | three Light research runs |
| L3 End to end | 2.00 | five full guided sessions, one per archetype |
| L4 Safety and probes | 0.50 | nine injection payloads, the sufficiency probe, a critic check |

The allowances are ceilings, the reserve is at least USD 1.00 of the effective cap, and the plan for L1 to L4 is about USD 2.5 in total (D-069). Calibrate from L0 and shrink an allowance, never the cap.

**After each batch, before the next:**

1. `just budget`; write the new total and the batch result into `docs/implementation/PROGRESS.md`.
2. Read the raw result (`evals/reports/live-<batch>.json`, gitignored) and the `llm_call` log lines for fallbacks, failed calls and `incomplete` responses (`reasoning_tokens` close to `output_tokens`, `attempts` above 1). Fix what they show, offline, before spending on the next batch.
3. `uv run pytest tests/security -q` (the secret scan covers cassettes), then replay a few of the new cassettes offline with `LLM_MODE=replay`.
4. Take the second key away again: close the terminal (or remove the `env` entry that carried it).

Use `purpose="must-run:..."` only for items that must run once the 80% tripwire is hit (in-process code only). Stop live work at 80% unless an item is must-run.

**The live-call hook.** `.claude/hooks/guard_live.py` (registered for the Bash and the PowerShell tool) blocks a command that assigns the live value to the mode variable in any quoting or obfuscation form, runs a live `just` recipe, or names the live batch runner, unless `ALLOW_LIVE_LLM=1` is **already in the environment** of the session. It reads command text only (not a sourced file, not `--env-file`), so it also blocks a command that merely *quotes* those spellings: keep such text out of shell commands ([troubleshooting](troubleshooting.md#4-the-ledger-and-the-live-call-guard)). It lets the inline form `$env:ALLOW_LIVE_LLM='1'; uv run pytest tests/live -m live` through on purpose, because it assigns no mode and runs no live recipe and the root `conftest.py` is the guard for live tests. The hook stops accidents and the obvious; the gateway's three-key check is the boundary. For a Claude Code session, the key therefore has to be in the session's own environment; the sanctioned mechanism is the gitignored `.claude/settings.local.json` (D-014), and whether to put it there is the user's decision (see "Who decides").

Record cassettes while you pay for the calls: `$env:MDE_RECORD_CASSETTES = "1"` (and optionally `$env:CASSETTE_DIR`), then later runs replay for free. Both are `mde_core.Settings` fields (`record_cassettes`, `cassette_dir`) read when `Settings` is built, so set them before the process starts; a relative `CASSETTE_DIR` (default `tests/cassettes`) is anchored to the **main repository**, like the ledger, so a worktree records into and replays from one place (commit the new cassettes from there). `PROJECTS_DIR` (hand-off repositories, default `data/projects`) is anchored the same way.

## Reading the budget

```powershell
just budget                                  # text
uv run python -m mde_llm_gateway.budget_cli --json
uv run python -m mde_llm_gateway.budget_cli --scope eval            # only scopes starting with "eval"
uv run python -m mde_llm_gateway.budget_cli --ledger path\to\other.jsonl
```

The report shows spent USD and micros, percent of the cap and of the effective cap, reserved (open) amounts, stale reservations (counted as spent), the tripwire level, live-call count, spend by scope with each scope's cap, and a warning if any ledger line was unreadable or the ledger contradicts its guard file. It is read-only: no lock, no files created.

## Tripwires

| Level | Share of the cap (spent + reserved) | Effect |
| --- | --- | --- |
| warn | 50% (USD 3.00) | one warning in the logs (`budget_tripwire_50`); everything still runs |
| must-run only | 80% (USD 4.80) | only calls with `ctx.purpose` starting `must-run` are accepted; the rest raise `BudgetExceeded` |
| refuse | 95% (USD 5.70) | everything raises `BudgetExceeded` |

Scoped caps (`packages/llm-gateway/src/mde_llm_gateway/budget.yaml`): session USD 1.00 (0.50 before D-124), research run 0.30, eval run 1.50, test 0.30; add an exact-scope entry under `scope_overrides_usd` for an eval run with its own allowance. Only these scope kinds (and kinds added in `budget.yaml`) are accepted: a scope such as `x:1` is refused (`details.reason = unknown_scope_kind`) instead of running uncapped. The `must-run` purpose works for in-process callers only; the HTTP API rewrites it to `api:must-run...`. Engines treat `BudgetExceeded` as "use the deterministic fallback", never as a reason to retry.

## Recovering from a stuck reservation

A reservation that was never closed (the process died mid-call, or its commit could not be written) is counted against the budget as *reserved* for 10 minutes and then as **spent at its worst case**: it is never released by the passage of time, because that request may have been billed. This is deliberately conservative: a crashed process burns its reservation. `just budget` shows "reserved (open)" and, after the 10 minutes, "stale (spent)". Do not edit the ledger. A late commit for an expired reservation replaces the worst case with the real cost. Likewise a call that times out, is cancelled or fails after the request left books its worst case, not just the input tokens, so expect the ledger to run ahead of the OpenAI invoice slightly; reconcile against the dashboard, not against the guard.

If the ledger is damaged: unreadable lines are skipped and reported (`WARNING: n unreadable ledger line(s)`); a line cut off by a crash is closed by the next write. If a stale `.lock` file is left behind (`data/.budget/ledger.jsonl.lock`) it is harmless: the lock is an OS lock held by a live handle, not the file's existence.

Never reset the ledger to "get budget back": the real spend is on the OpenAI invoice. The ledger has a guard file (`data/.budget/ledger.jsonl.guard`: size, line count, spend, first-line hash). If the ledger is deleted, truncated, edited or replaced, a live gateway refuses to start and every reserve fails with `LedgerIntegrityError` (HTTP 503, code `ledger_integrity`) instead of handing out a fresh USD 6. If you really must start a new ledger (for example after a deliberate archive), copy the old file aside, note the carried-over total in `PROGRESS.md`, reconcile with the dashboard, and then reset on purpose: `uv run python -m mde_llm_gateway.budget_cli init --force` (or `$env:MDE_BUDGET_LEDGER_RESET = "1"` for the next start). `init` without `--force` only creates a missing ledger and refuses when the guard says it was deleted. The order of the steps (stop the processes, copy the files aside, reconcile, reset, check) is in [troubleshooting](troubleshooting.md#4-the-ledger-and-the-live-call-guard). Resetting is the lead's or operator's decision, never a routine fix.

## Replaying cassettes (free)

```powershell
$env:LLM_MODE = "replay"                    # (tests use it through the factory); optional:
$env:CASSETTE_DIR = "tests\cassettes"       # default
```

A missing cassette raises `CassetteMissing` naming the spec and the key; replay never goes live. A cassette goes stale (a miss) whenever the rendered prompt, the model, the tools, the effort or the output schema changes: re-record that call in the next live batch. Cassettes contain no headers or keys and redact secret-looking text; the repo secret scan covers them.

## When something looks wrong

* `LiveCallForbidden`: one of the three switches is wrong. Check `LLM_MODE`, `ALLOW_LIVE_LLM` (must be `1`) and that `MDE_FORBID_LIVE` is not set in your shell.
* `UnknownModelPrice`: the model (or tool) is not in `pricing.yaml`. Do not guess a price and do not point `OPENAI_MODEL` at a costlier model; fail loudly and continue in mock or replay.
* `CircuitOpen` (code `circuit_open`): five consecutive retryable failures opened a breaker for 30 s. Breakers are keyed by route AND tool class (`standard` for plain structured calls, `standard+web_search` for search calls), so a search outage only stops searches; the engines' fallbacks apply. It clears by itself after the open window (one probe call).
* `StructuredOutputError` with "cut off (max_output_tokens)" in `details.errors`: reasoning tokens ate the call's `max_output_tokens`, the single bounded retry (up to 2x the cap, at most 32,768, lower with `GatewayConfig.max_escalated_output_tokens`) ran out too, and for a spec at effort `low` or `none` no repair call follows. It costs at most the try plus the retry, both metered; the engine falls back. In the `llm_call` log lines look for `reasoning_tokens` close to `output_tokens` and `attempts` above 1, and raise that spec's `max_output_tokens` (or lower its effort) before the next batch instead of paying the retries again.
* `BudgetExceeded` with a `scope` in the details: that scope's cap (not the global one) is the limit. `details.tripwire` names a tripwire instead; `details.reason = unknown_scope_kind` means the scope kind is not allowed.
* `LedgerIntegrityError` / `ledger_integrity`: the ledger contradicts its guard file; see "Recovering" above. Do not reset before reconciling with the dashboard.
* Ledger total differs from the dashboard after L0: check the unverified items in the architecture page (search content tokens, cache-write tokens, timed-out requests) and adjust `pricing.yaml` before more spend.

More failure modes (database, tests, worktrees, the hook's false positives): [troubleshooting](troubleshooting.md).

## Batch H: structure replay (ADR 0017)

`uv run python -m mde_compass.live_structure --replay <delhi|pune|hospital|path> --band <small|medium|large>` runs only the structure calls on a recorded Brief and ledger (about USD 0.01 to 0.03 a run; default allowance USD 0.10). It uses the same `preflight` as batches E to G, so a live run needs `LLM_MODE=live` and the second key in the outer environment; `--mock` and `--dry-run` need neither. See [compass.md](compass.md#batch-h-the-structure-drafted-on-a-recorded-ledger-adr-0017).
