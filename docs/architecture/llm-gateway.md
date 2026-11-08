# LLM Gateway

The gateway is the only code that talks to an LLM and the only package that imports `openai`. It is a library (`mde_llm_gateway`, package `packages/llm-gateway`) plus an HTTP service (`mde_llm_gateway_api`, `apps/llm-gateway-api`) with one interface, `mde_contracts.gateway.GatewayClient`. Brief section 7 is the specification; this page records how it was built, what was confirmed about the OpenAI API and what could not be.

Package page: [llm-gateway](../packages/llm-gateway.md). Operations: [live LLM budget runbook](../runbooks/live-llm-budget.md).

## Shape

```
engines ──► GatewayClient ─┬─ InProcessGatewayClient ─► Gateway ─► Provider
                           └─ HttpGatewayClient ──► gateway API (FastAPI) ─► Gateway ─► Provider

Provider = MockProvider (default) | ReplayProvider | ScriptedProvider (tests)
         | OpenAIResponsesProvider (live) | RecordingProvider(OpenAIResponsesProvider)
```

* `CallSpec`, `CallContext`, `CallResult`, `Usage`, `GatewayClient` live in `mde_contracts` (D-007); the gateway never imports `mde_prompts`. It receives a `SpecRegistry` (`get`, `catalog`) at construction; `mde_prompts.registry.PromptRegistry` implements it.
* `create_gateway(settings, registry, *, provider, ledger, sink, clock, config, ...)` chooses the provider from `settings.llm_mode` and the ledger from the provider's billing (live: shared file ledger; everything else: in-memory, cost 0, so tests never touch `data/.budget`).
* `openai` is imported only in `providers/openai_responses.py`, `doctor.py` and lazily in `factory.py` (live branch). Mock and replay never load the SDK. The SDK client is built with `max_retries=0`: the gateway owns retries and every attempt must be metered.

## Pipeline for one call (`Gateway.run`)

```
run(spec, input, ctx)
 1  ctx.cancel.check()
 2  check_spec        untrusted_inputs + tools      -> UntrustedWithTools (hard error)
                      untrusted name not an input field / free text without tools -> ConfigError
 3  validate input    spec.input_model re-validated -> ValidationFailed
 4  price check       unknown model or tool price   -> UnknownModelPrice (refused, nothing spent)
 5  render            jinja (sandboxed, StrictUndefined) from spec.template_root;
                      fields in untrusted_inputs wrapped by wrap_untrusted; UNTRUSTED_NOTICE
                      appended to the system prompt; template bug -> ConfigError
 6  strict schema     to_strict_schema(output_model), cached per (spec id, version)
 7  cache             cacheable specs: content-hash key; a HIT returns now, reserves nothing
 8  provider attempt  (repeated for retries, the escalated retry and the repair call)
      route slot (concurrency semaphore + RPM token bucket)
      ctx.cancel.check(), deadline check
      circuit breaker  -> CircuitOpen BEFORE any reservation (keyed by route + tool class)
      BudgetGuard.reserve(worst case)  -> BudgetExceeded (never retried)
      provider.complete()  under asyncio.timeout
      success: commit real usage      failure: commit partial/estimated cost, release the rest
 9  incomplete (max_output_tokens) -> ONE retry with a bounded higher cap (it reserves again),
    else treated as invalid (see "Reasoning tokens and the output cap")
10  parse + validate locally with output_model
11  invalid and spec.repair -> exactly ONE repair call (effort low, original input + invalid
    output + validation errors, counted in the budget, repaired=True); not made when the output
    ran out of tokens twice and the spec already runs at effort low or none
12  still invalid -> StructuredOutputError  (the engine uses its registered fallback)
13  CallResult; LlmCallRecord to the optional sink (a failing sink never fails the call);
    structlog line (no prompt text unless LOG_PROMPTS=1, always redacted)
```

Decision: the cache lookup (step 7) happens before the reservation, unlike the numbering in brief 7.3: a hit costs nothing and must not need budget.

Logging: `mde_core.logging.configure_logging` scrubs both structlog events (processor) and records written through the standard `logging` module (uvicorn, httpx, sqlalchemy, any third party; GAP-6): a `ScrubFilter` on the root handlers and a `LogRecord` factory wrapper redact the formatted message, args, `exc_text`, stack and `extra` fields with `mde_core.redact` plus the exact secrets registered by `register_secrets` (the gateway and the live provider register `Settings.secret_values()`), so handlers attached later (uvicorn's own) are covered too.

Retries (`RetryPolicy`): only 429, 5xx, 408, timeouts and connection errors (`ProviderError.retryable`); exponential backoff with +/-25% jitter (seeded RNG in tests), `Retry-After` honoured (never waits less), at most 4 attempts and 60 s total, never past `ctx.deadline`; 4xx is never retried; every attempt has its own reservation, failed attempts release. The circuit breaker is keyed by **route and class of tools** (`mde_llm_gateway.circuit.breaker_key`): `standard` guards plain structured calls, `standard+web_search` guards the calls that use the web search tool. 5 consecutive failures (retryable ones, or 401/403) open a breaker for 30 s, then one probe call is allowed. The two classes never share state: before this, five failed searches during research opened the shared `standard` breaker and the Spec Builder that followed skipped its model refinement for plain structured calls (eval plan, observation 1); now a search outage opens only `standard+web_search`, plain calls keep flowing, and the other way round.

Limits: per route semaphore (fast 8, standard 4, deep 2) and an RPM bucket (120 / 60 / 30). They stay per **route** (the provider's rate limit does not care which tool a call uses), not per breaker. All timing uses the injected `Clock`, so tests use `FakeClock` and never sleep.

### Reasoning tokens and the output cap

`max_output_tokens` covers the visible answer **and the reasoning tokens**, so a reasoning-heavy call with a small cap can come back `status: "incomplete"` / `incomplete_details.reason: "max_output_tokens"` with `reasoning_tokens == output_tokens` and no answer at all. What the gateway does about it (all offline-tested with an SDK-typed `Response` fixture of exactly that shape, `tests/test_openai_provider.py`):

1. **One retry, bounded.** `escalate_cap(cap, ceiling)` = `min(2 x cap, cap + 8,192, ceiling)` with `ceiling = GatewayConfig.max_escalated_output_tokens` (default and hard maximum `MAX_ESCALATED_OUTPUT_TOKENS` = 32,768; a live batch can lower it). If that is not above the spec's own cap there is no retry. `Gateway._call` additionally refuses to send any request whose `max_output_tokens` is above `max(cap, escalate_cap(...))` for its spec (`ConfigError`, nothing sent), so no code path can exceed the ceiling.
2. **The retry is metered like every other attempt.** It takes its own route slot and its own reservation with the larger worst case (+300 output tokens = +150 micros for a 300-token cap), so the budget guard can refuse it (`BudgetExceeded`, no provider call, nothing left reserved), and both truncated attempts are committed at the usage the API reported.
3. **No third call that cannot help.** If the retry also runs out of tokens, the single repair call (effort `low`) is only made when it can lower the reasoning effort (spec effort `medium` or `high`); a spec that already runs at `low` or `none` ends in `StructuredOutputError` instead, and the engine uses its registered fallback. Worst case per logical call is therefore three provider requests (try, retry, repair), never a loop.

Observed at L0 (2026-11-08, lead, `test:l0-smoke`; see "Verified live" below): on trivial prompts `reasoning.effort` `low` and `none` both reported **0 reasoning tokens**, so L0 never produced an `incomplete` response and the cap behaviour above has only been exercised offline. Reference numbers for sizing caps at USD 0.50 per 1M output tokens: a 300-token call costs at most 150 micros of output, its escalated retry at most 300 more (a fully truncated try plus retry billed 160 + 310 = 470 micros in the fixture with 100 input tokens each); one search call reported about 8,700 input tokens (USD 0.0109), so a call with a tool is dominated by input and the search fee, not by the cap. Settle during L1 to L3: how many reasoning tokens C9, C10 and C12 actually use at their effort and cap, then size `max_output_tokens` in the specs from the `reasoning_tokens` column of the `llm_call` log lines (`attempts` > 1 or `status` `repaired`/`failed` with `reasoning_tokens` near `output_tokens` is the signature of a cap that is too small).

Timeouts: fast 30 s, standard 60 s, deep 120 s, doubled for calls with tools, capped by `ctx.deadline`.

## Budget guard and ledger (the USD 6.00 rule)

* **Pricing** (`pricing.yaml`, package data): gpt-6-luna input 0.10, cached input 0.01, output 0.50 USD per 1M tokens; above 272000 input tokens the whole request is billed at 2.0x input and 1.5x output; `web_search` 0.01 USD per call plus content tokens at the model input rate. Unknown model, unknown tool or a missing/non-positive price raises `UnknownModelPrice` and the call is refused. Costs are integer micro-dollars, each component rounded up.
* **Effective cap** = 95% of `LLM_BUDGET_USD` (5,700,000 micros of 6,000,000). The cap itself is not caller- or operator-influenced: `HARD_CAP_USD = 6.00` clamps `LLM_BUDGET_USD` (a value above it logs `budget_cap_clamped` and the cap becomes 6.00; smaller values still work) and the effective cap is never above `HARD_EFFECTIVE_CAP_USD = 5.70`, whatever `effective_cap_fraction` says in `budget.yaml`.
* **`reserve(scopes, worst_case)`** refuses (`BudgetExceeded`) when `spent + reserved + worst_case` exceeds the effective cap globally or any scoped cap (`budget.yaml`: session 0.50, research 0.30, eval 1.50, test 0.30 USD by scope kind, exact-scope overrides allowed) or when a scope kind is unknown: only `session`, `research`, `eval`, `test` and kinds added through `budget.yaml` (`scope_caps_usd` keys, or the `scope_kinds` list for an uncapped kind) are accepted, because an unknown kind used to get no scope cap at all (`details.reason = unknown_scope_kind`). A refused reserve writes nothing. The check and the append happen in one critical section (file lock), so parallel processes cannot overshoot.
* **Worst case** = (`chars / 3.0` + 64) input tokens (system + user + schema JSON) at the uncached input price + `max_output_tokens` at the output price + for each allowed tool call (0.01 USD + 3000 content tokens at the input price). The estimator calibrates from real usage and only ever becomes more conservative (floor 1.5 chars per token). Long-context multipliers are assumed from 80% of the threshold.
* **`commit(reservation, usage)`** records the real cost and closes the reservation (idempotent). Cost above the reservation is recorded in full and logged as `budget_reservation_overrun`. **Settlement fails closed** (a cost that is lost is the one bug this module must not have):
  * A response without usage, or with usage that cannot be priced (a negative count, a model with no price), books the reserved **worst case** (token counts zeroed in the recorded usage).
  * A commit that fails for any reason (ledger write error, lock timeout) is retried as a worst-case booking; the paid answer is still returned. If even that write fails the call raises, the reservation stays open and is counted (see stale reservations).
  * A **timeout, a dropped connection, a cancellation, or a bug after the request left** books the **worst case**, not just the input tokens (nothing is known about output tokens or a web-search fee). Only a billing guard does this; mock, replay and scripted guards release.
  * A failed response that reports usage commits that usage. Only a failure the provider proves was not billed (no `maybe_billed`, no usage: for example HTTP 429 or 400) releases.
  * A failing settlement never masks a cancellation: the `CancelledError` is re-raised and the open reservation keeps counting.
* **Tripwires** on `spent + reserved` as a share of the cap: 50% logs a warning once; 80% allows only calls whose `ctx.purpose` starts with `must-run`; 95% refuses everything. `must-run` is a privilege of trusted in-process code (the lead's batches, the flow controller): the **HTTP API strips it** from any caller-supplied purpose (it becomes `api:<purpose>`), because HTTP callers are untrusted.
* **Stale reservations count as SPENT, not released**: an unclosed reservation older than 10 minutes (`reservation_ttl_seconds`) is folded into `spent` at its worst case (`stale_micros` / `stale_reservations` in the snapshot and in `just budget`). A crashed process, or a call whose commit could not be written, therefore *burns* its reservation: deliberately conservative, because that request may have been billed and the old rule ("counts as released") made real spend vanish from the total. The budget is only freed by an explicit `release`, or by a late `commit`, which replaces the worst case with the real cost (counted once).
* **Live gating**: a live provider is constructible only with `LLM_MODE=live` and `ALLOW_LIVE_LLM=1` and without `MDE_FORBID_LIVE=1` (the pytest autouse fixture sets it) -> else `LiveCallForbidden`. `RecordingProvider` has the same gate.

### Ledger location and guard file (`LLM_BUDGET_LEDGER`, default `data/.budget/ledger.jsonl`)

A relative ledger path is resolved against the **main repository root** (the parent of the git common directory, so every worktree agrees), never the working directory: `Settings.llm_budget_ledger` is already anchored (`mde_core.paths.anchor_to_main_repo`), and the factory and the budget CLI use the same helper. Before this, another cwd or worktree silently got an empty ledger and a fresh USD 6.

Next to the ledger lives `<ledger>.guard`, written when the ledger is first created and refreshed after every append: the ledger's size, line count, recorded spend (all only ever grow) and a hash of its first line. A live provider **refuses to start** (`LedgerIntegrityError`, code `ledger_integrity`, HTTP 503; a `BudgetExceeded` subclass) and every `reserve` is refused when the guard exists but the ledger is missing, smaller than recorded, has fewer lines or less spend, or starts with a different line (deleted, truncated, edited, replaced). A commit is still written (money already spent is always recorded). The operator resets on purpose, after reconciling with the provider dashboard: `python -m mde_llm_gateway.budget_cli init --force`, or `MDE_BUDGET_LEDGER_RESET=1` for the next start. `budget_cli init` without `--force` creates a missing ledger and guard, adopts a ledger that predates the guard, and refuses (exit 2) if the guard says it was deleted or truncated. Mock, replay and scripted gateways never touch any of this.

### Ledger format

Append-only JSON lines, written under `mde_core.filelock.FileLock` (`<ledger>.lock`, `msvcrt` on Windows, `flock` elsewhere), fsynced. One reservation lifecycle is two lines:

```json
{"event":"reserve","ts":"2026-11-08T12:00:00Z","reservation_id":"rsv_01...","scopes":["session:ses_1","research:run_1"],"call_id":"call_01...","spec_id":"C7.extract","model":"gpt-6-luna","mode":"live","worst_case_micros":2410}
{"event":"commit","ts":"...","reservation_id":"rsv_01...","scopes":[...],"call_id":"call_01...","spec_id":"C7.extract","model":"gpt-6-luna","mode":"live","usage":{"input_tokens":812,"cached_input_tokens":0,"output_tokens":240,"reasoning_tokens":150,"tool_calls":0,"cost_micros":202},"cost_micros":202}
```

`release` is the third event kind (same ids, no usage). Totals are never stored: they are derived by folding the file (tail-aware incremental read on every reserve, full re-derivation on start or if the file shrinks). A malformed line is skipped and counted (`corrupt_lines`, shown by the budget CLI); a half-written last line is ignored until complete and closed by the next writer. `python -m mde_llm_gateway.budget_cli [--scope S] [--json]` reads it without locking.

## Strict schema (`to_strict_schema`)

Converts a Pydantic output model to OpenAI's strict subset. Rules, all enforced by `check_strict` in the tests:

* root is an `object`; every object has `additionalProperties: false` and lists all its properties in `required`;
* `X | None` becomes `anyOf [X, {"type": "null"}]`; a field with a default that is not typed `| None` stays **required and non-null** (a `null` would fail local validation, so the model must always emit it);
* `$defs` are inlined; recursion is rejected; `oneOf` becomes `anyOf`; `const` becomes a one-value `enum`; `default`, `title`, `examples`, `discriminator` are dropped;
* kept: `type`, `enum`, `description`, `items`, `anyOf`, `minimum`/`maximum`/`exclusive*`/`multipleOf`, `minItems`/`maxItems`, string `pattern` (`keep_pattern=False` drops it) and `format` only from the API's list; dropped: `minLength`, `maxLength`;
* rejected with the path of the field: open objects (`dict`), tuples, `Any`, `allOf` with several parts, `not`, `if/then/else`, `patternProperties`, nesting deeper than 5 levels (objects and arrays each count one; the API allows 10).

The gateway always re-validates locally with the model. Every model in `mde_contracts.registry.llm_output_models()` converts (depth 5 at most today); a test walks the registry and round-trips a schema-minimal instance and a fully populated dump.

## Untrusted content

`wrap_untrusted(text, source)` strips hidden and zero-width characters (`mde_core.redact.strip_hidden_chars`), defuses any `<untrusted_content` tag inside the text (`&lt;`), caps the size (20,000 characters by default, `[truncated]` marker) and returns `<untrusted_content source="..." sha256="...">...</untrusted_content>` with the digest of the content. A `str` field becomes one block, a `list` one block per item, anything else its JSON. Calls with untrusted inputs get `UNTRUSTED_NOTICE` appended to the system prompt and must not enable tools.

## Cassettes (`tests/cassettes/`)

Layout: `<CASSETTE_DIR>/<spec_id>__v<version>/<key>.json` (default `tests/cassettes` in the **main repository**: `mde_core.Settings.cassette_dir`, env `CASSETTE_DIR`, a relative value is anchored to the main repo root like the ledger, so a worktree reads and records into one place; an absolute value is used as given). Key = sha256 of the canonical JSON of `{spec_id, spec_version, model, system, user, tools, effort, schema_hash}` (the rendered messages, not the input object). `max_output_tokens`, timeouts and attempt numbers are not in the key; the repair and escalated calls differ in their user text or are separate requests and get their own cassettes.

```json
{ "format": 1, "key": "<sha256>", "recorded_at": "2026-11-08T12:00:00Z",
  "request":  { "spec_id": "C1.understand", "spec_version": 1, "model": "gpt-6-luna", "effort": "low",
                "schema_hash": "...", "tools": [], "kind": "main", "system": "...", "user": "..." },
  "response": { "text": "{...}", "status": "completed", "incomplete_reason": null, "refusal": null,
                "citations": [{"url": "...", "title": "...", "start": 0, "end": 10}],
                "search_calls": 0, "usage": {"input_tokens": 812, "...": 0} } }
```

Sanitised on write: no headers, no keys, every text field passes `redact_text`; prompts are stored (redacted) so a replay miss can be diagnosed by diffing. Written atomically. `ReplayProvider` with no cassette raises `CassetteMissing` (names the spec and key) and never calls the API. `RecordingProvider` (live only, `Settings.record_cassettes`, env `MDE_RECORD_CASSETTES=1`, or the `record_cassettes=True` argument of `create_gateway`) writes a cassette after every successful call. Both switches are `Settings` fields (D-034); the factory no longer reads the environment itself, so the variable is read once when `Settings` is built.

## HTTP API

`apps/llm-gateway-api`: `POST /v1/runs`, `GET /v1/calls`, `GET /v1/budget`, `GET /v1/usage?scope=`, `GET /healthz`, `GET /readyz`; everything under `/v1` needs `Authorization: Bearer <GATEWAY_SERVICE_TOKEN>` (constant-time compare; no token configured means every `/v1` request is refused). Budget, gating and retries stay in the library; the API treats callers as untrusted (a `must-run` purpose is stripped, an unknown scope kind is refused). `create_app(settings=None, registry=None, *, gateway=None)`; the default registry is `mde_prompts.registry.PromptRegistry()`. `just gateway-dev` serves `mde_llm_gateway_api.main:app`.

Wire format (`mde_llm_gateway.wire`): request `{call_id, version, input, context}` (`context` is the JSON form of `CallContext` without the cancellation token); success body is `CallResult` JSON; errors are `{"error": {"code", "message", "details"}}`. `HttpGatewayClient` rebuilds the same exception class from the code. One contract suite (`apps/llm-gateway-api/tests/test_contract.py`) runs every behaviour against the in-process client and the HTTP client over ASGI and compares full results.

### Error table

| Code | Exception | HTTP | Raised when |
| --- | --- | --- | --- |
| `validation_failed` | `ValidationFailed` | 422 | input does not validate against the spec's input model; bad request body |
| `untrusted_with_tools` | `UntrustedWithTools` | 422 | spec combines untrusted inputs with tools |
| `not_found` | `NotFound` | 404 | unknown call id or version (API) |
| `unauthorized` | `GatewayAuthError` | 401 | missing or wrong bearer token |
| `live_call_forbidden` | `LiveCallForbidden` | 403 | live provider requested without both keys, or under `MDE_FORBID_LIVE` |
| `budget_exceeded` | `BudgetExceeded` | 402 | global or scoped cap, a tripwire, or an unknown scope kind; never retried |
| `ledger_integrity` | `LedgerIntegrityError` | 503 | the ledger contradicts its guard file (deleted or truncated); reset only with `budget_cli init --force` |
| `circuit_open` | `CircuitOpen` | 503 | breaker open for the route; use the fallback |
| `deadline_exceeded` | `DeadlineExceeded` | 504 | `ctx.deadline` passed |
| `provider_error` | `ProviderError` | 502 | provider failure after retries; `details.provider_status`, `retryable`, `retry_after` |
| `structured_output_error` | `StructuredOutputError` | 502 | output invalid after the single repair (or repair disabled); use the fallback |
| `call_cancelled` | `CallCancelled` | 499 | `ctx.cancel` fired (in process; over HTTP the client drops the request) |
| `unknown_model_price` | `UnknownModelPrice` | 500 | no price on file for the model or tool |
| `cassette_missing` | `CassetteMissing` | 500 | replay mode without a cassette |
| `config_error` | `ConfigError` | 500 | template bug, bad mock handler, strict-schema conversion failure, missing key or token |
| `gateway_error` | `GatewayError` | 500 | anything else (unexpected exceptions leak no detail) |

## What was confirmed, and what was not

Installed SDK: `openai` 3.46.0 (types read in `openai/types/responses/`). Docs read 2026-11-08 through a fetch tool that summarises pages: `models/gpt-6-luna`, `guides/tools-web-search`, `guides/structured-outputs`, `guides/reasoning`. Nothing below was run against the live API: the provider is tested with `Response.model_validate(<dict>)` fixtures built from the real SDK types and a stub client.

**Confirmed against the SDK types (and asserted by tests)**

* Every keyword we send is a real parameter of `AsyncResponses.create`: `model`, `instructions`, `input` (a plain string is allowed), `max_output_tokens`, `max_tool_calls`, `reasoning`, `store`, `text`, `tools`, `tool_choice`, `include`, `timeout`, `prompt_cache_key`.
* `text.format` is `{type: "json_schema", name, schema, strict}`; `name` must be letters, digits, `_` or `-`, at most 64 characters.
* `reasoning.effort` in the SDK allows `none, minimal, low, medium, high, xhigh, max`; the contract's `Effort` uses `none, low, medium, high`.
* `max_output_tokens` is documented as covering visible output **and reasoning** tokens; an exhausted budget gives `status: "incomplete"` with `incomplete_details.reason: "max_output_tokens"` (other reasons: `max_messages`, `content_filter`, `steered`).
* `max_tool_calls` is "the maximum number of total calls to built-in tools" across all built-in tools; further calls are ignored.
* Usage: `usage.input_tokens` (with `input_tokens_details.cached_tokens` and `cache_write_tokens`), `usage.output_tokens` (with `output_tokens_details.reasoning_tokens`), `total_tokens`; `tool_usage.web_search.num_requests` counts billable searches (page opens excluded).
* Web search: tool param `{type: "web_search", search_context_size: low|medium|high, filters: {allowed_domains}}`; output items `web_search_call` (`action` is `search`, `open_page` or `find_in_page`; `search` carries `sources`); `include` accepts `web_search_call.action.sources`; `url_citation` annotations carry `url`, `title`, `start_index`, `end_index`; an assistant message may carry `phase` (`commentary` or `final_answer`).
* SDK errors: `APIStatusError` (`status_code`, `response.headers`), `RateLimitError`, `InternalServerError`, `APITimeoutError` (a subclass of `APIConnectionError`). The SDK's own default is 2 retries; we set 0.

**Confirmed from the OpenAI docs pages (not yet from the API)**

* Model id `gpt-6-luna` is the only listed snapshot; 1,050,000 context, 922,000 max input, 128,000 max output; USD 0.10 / 0.01 / 0.50 per 1M tokens (input / cached input / output); above 272K input tokens input and cached rates are 2x and output 1.5x for the entire request; efforts `none, low, medium (default), high, xhigh, max`; structured outputs, function calling and web search supported; Responses and Chat Completions endpoints.
* Strict structured outputs: all fields required, `additionalProperties: false`, root must be an object (no root `anyOf`), nullable via a type array or `anyOf` with null, `$defs`/`$ref` and recursion supported, `pattern`, limited `format`, number bounds and `minItems`/`maxItems` supported; `allOf`, `not`, `dependent*`, `if/then/else` unsupported; `minLength`/`maxLength` are not listed as supported; limits 5000 properties, 10 nesting levels, 1000 enum values; a strict schema with an unsupported keyword returns an error.
* Web search: `tool_choice: "required"` forces a search; allowed/blocked domain lists accept up to 100 entries without the scheme; search context is capped at 128k tokens; the page gives no per-call price and does not say whether content tokens are billed separately.
* Reasoning: reasoning tokens are billed as output; the guide suggests reserving at least 25,000 tokens for reasoning and output when starting out.

**Unverified: the L0 smoke test (`tests/live/test_smoke.py`) must settle these**

1. `gpt-6-luna` is available on this key (`probe_model` uses `models.retrieve`, free).
2. Our strict schemas are accepted as produced. Risks: the `pattern` keyword and `format`, field descriptions, `anyOf` null unions. If `pattern` is rejected, build schemas with `keep_pattern=False`.
3. `tool_choice: "required"`, `max_tool_calls` and the `include` option are accepted together with `reasoning.effort: low` on Luna, and whether `max_tool_calls` also counts `open_page` and `find_in_page` actions.
4. Whether `usage.input_tokens` already contains the web search content tokens (assumed yes; the commit adds only the per-call fee, the reservation assumes 3000 extra tokens per call).
5. Whether `tool_usage.web_search.num_requests` is populated (we bill the larger of that and the number of `web_search_call` items).
6. The real characters-per-token ratio (the estimator starts at 3.0) and how much of a small `max_output_tokens` low-effort reasoning consumes. The guide's 25,000-token advice is far above the per-call caps in the brief: expect `incomplete` responses at first and use the L0 numbers to size caps.
7. Whether a timed-out request is billed (we book the full reserved worst case, deliberately conservative) and whether `cache_write_tokens` carry a surcharge that `pricing.yaml` does not model. Compare the ledger total with the OpenAI dashboard after L0.
8. `reasoning.effort: "none"` really produces no reasoning tokens on Luna; `store: false` and `prompt_cache_key` are accepted; HTTP 408 behaviour.


## Verified live: L0 smoke (2026-11-08, lead)

Run once under the scope `test:l0-smoke` (cap USD 0.30); 7 tests passed, USD 0.0221 spent, ledger reconciled exactly.

| Item | Result |
| --- | --- |
| Model `gpt-6-luna` | Available on this key (`models.retrieve`). No fallback was needed or used. |
| Strict JSON schema (`text.format`, `strict: true`) | Accepted for the test spec; output parsed and validated locally. Production output models are only checked structurally offline; any schema the API rejects at L1 surfaces as a provider 400 (try `keep_pattern=False`). |
| `reasoning.effort` low / none | Both accepted. On trivial prompts both reported 0 reasoning tokens. Heavier prompts (C9, C10, C12) may burn reasoning tokens against `max_output_tokens`: watch `incomplete` retries in L1/L3. |
| `web_search` with citations | One search call returned 27 `url_citation` annotations. `tool_choice="required"` works. |
| Search billing | One search reported about 8,700 `input_tokens` (search content is inside the input count) and cost USD 0.0109 (0.01 fee plus tokens). `pricing.yaml` `assumed_content_tokens` raised from 3,000 to 12,000 so the worst-case reservation is no longer below real cost. Budget planning: about USD 0.011 per search call, so a Light run (6 searches) is about USD 0.07 before extraction and synthesis. |
| `max_tool_calls` | Respected: a prompt asking for three separate searches billed exactly one call. |
| Usage fields | `cached_input_tokens` parsed (1,279 of 1,282 tokens cached on the second identical-prefix call, cost fell from 141 to 26 micros); `reasoning_tokens` parsed. |
| Budget guard vs real usage | Reserve/commit lines match; worst case >= real cost for structured calls; every reservation closed. |
| Latency | 1 to 2 s for a tiny structured call, 4 to 5 s with a search. |

Still unverified (settle during L1 to L3): schemas with `pattern` or deeply nested unions, cache-write surcharge (assumed none), `incomplete` behavior at small caps for reasoning-heavy calls.
