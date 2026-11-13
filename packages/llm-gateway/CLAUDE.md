# mde_llm_gateway

**Job:** the only code that talks to an LLM. Executes versioned `CallSpec`s with budget guard, retries, repair, caching, metering and logging. The only package allowed to import a model vendor's SDK (`openai`: Responses API, never Chat Completions; `anthropic`: Messages API). It is where model independence lives (root rule 14): vendor, model, price, effort, schema dialect, token accounting and error mapping are all here, and engines see none of them.

**Public interface:** `Gateway.run(spec, input, ctx) -> CallResult[O]` (in-process `GatewayClient`), `BudgetGuard` (`reserve` / `commit`, scoped caps, tripwires), ledger (`data/.budget/ledger.jsonl`, portable file lock), `pricing.yaml`, providers (`OpenAIResponsesProvider`, `AnthropicMessagesProvider`, `RoutingProvider`, `ScriptedProvider`, `MockProvider`, `ReplayProvider`, `RecordingProvider`), `to_strict_schema(model)`, untrusted-content wrapper, circuit breaker, rate limits. HTTP flavour lives in `apps/llm-gateway-api`.

**Test:** `uv run pytest packages/llm-gateway`

**Conventions**
- Brief section 7 is the spec. Budget guard first: unknown model or missing price refuses the call; effective cap is 95% of `LLM_BUDGET_USD` (default 6.00: USD 5.70; ceiling 8.00: USD 7.60) and `HARD_CAP_USD = 8.00` (D-125) clamps the env var (the cap is never caller-influenced); unknown scope kinds are refused; the HTTP API strips `must-run` purposes.
- **Vendors are configuration (rule 14).** `MODEL_ROUTES` names a model per route; `pricing.yaml` names the `vendor` of every model; `factory._VENDORS` has one builder per vendor and `gating.VENDOR_KEYS` its key. One vendor in use -> its provider alone; several -> `RoutingProvider`, which picks per call by `request.model`. A vendor's key is required only when a route uses it, and the error names the route. To add a vendor: a provider module (one call, usage normalised so `input_tokens` includes cached ones, errors as `ProviderCallError`, its own schema sanitiser, no engine-visible dialect), a builder, a key entry, `vendor:` rows with prices, and the provider tests (copy `test_anthropic_provider.py`); add its SDK name to `.importlinter`, the ruff ban list and `MODEL_SDKS` in `tests/security/test_repo_hygiene.py`. A spent balance is status 402 and opens the breaker.
- Live needs `LLM_MODE=live`; `MDE_FORBID_LIVE=1` (set by the pytest autouse fixture) makes live provider construction raise `LiveCallForbidden`. Replay never goes live on a missing cassette.
- Never log keys or prompt text (hashes only unless `LOG_PROMPTS=1`, redacted). Ledger lines are append-only, cross-process safe on Windows too; the ledger path is anchored to the MAIN repo root and guarded by `<ledger>.guard` (deleted or truncated ledger => `LedgerIntegrityError`; reset only with `budget_cli init --force` or `MDE_BUDGET_LEDGER_RESET=1`).
- Accounting fails closed: a commit that fails, a timeout, a cancellation or a bug after the request left books the reserved WORST CASE; a stale reservation counts as SPENT, never as released.
- Specs with `untrusted_inputs` must not enable tools (hard error). Re-validate every output locally.
- Owner: `gateway-engineer`.
