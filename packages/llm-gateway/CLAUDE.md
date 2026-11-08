# mde_llm_gateway

**Job:** the only code that talks to an LLM. Executes versioned `CallSpec`s with budget guard, retries, repair, caching, metering and logging. The only package allowed to import `openai` (HTTP client only; Responses API, never Chat Completions).

**Public interface:** `Gateway.run(spec, input, ctx) -> CallResult[O]` (in-process `GatewayClient`), `BudgetGuard` (`reserve` / `commit`, scoped caps, tripwires), ledger (`data/.budget/ledger.jsonl`, portable file lock), `pricing.yaml`, providers (`OpenAIResponsesProvider`, `ScriptedProvider`, `MockProvider`, `ReplayProvider`, `RecordingProvider`), `to_strict_schema(model)`, untrusted-content wrapper, circuit breaker, rate limits. HTTP flavour lives in `apps/llm-gateway-api`.

**Test:** `uv run pytest packages/llm-gateway`

**Conventions**
- Brief section 7 is the spec. Budget guard first: unknown model or missing price refuses the call; effective cap is 95% of `LLM_BUDGET_USD` (USD 5.70) and `HARD_CAP_USD = 6.00` clamps the env var (the cap is never caller-influenced); unknown scope kinds are refused; the HTTP API strips `must-run` purposes.
- Live needs `LLM_MODE=live` **and** `ALLOW_LIVE_LLM=1`; `MDE_FORBID_LIVE=1` (set by the pytest autouse fixture) makes live provider construction raise `LiveCallForbidden`. Replay never goes live on a missing cassette.
- Never log keys or prompt text (hashes only unless `LOG_PROMPTS=1`, redacted). Ledger lines are append-only, cross-process safe on Windows too; the ledger path is anchored to the MAIN repo root and guarded by `<ledger>.guard` (deleted or truncated ledger => `LedgerIntegrityError`; reset only with `budget_cli init --force` or `MDE_BUDGET_LEDGER_RESET=1`).
- Accounting fails closed: a commit that fails, a timeout, a cancellation or a bug after the request left books the reserved WORST CASE; a stale reservation counts as SPENT, never as released.
- Specs with `untrusted_inputs` must not enable tools (hard error). Re-validate every output locally.
- Owner: `gateway-engineer`.
