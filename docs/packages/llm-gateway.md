# llm-gateway and llm-gateway-api

Packages `mde_llm_gateway` (`packages/llm-gateway`) and `mde_llm_gateway_api` (`apps/llm-gateway-api`, with its own short page: [llm-gateway-api](llm-gateway-api.md)). Owner: `gateway-engineer`. Architecture, the confirmed/unverified list about the OpenAI API, ledger and cassette formats and the error table are in [architecture/llm-gateway.md](../architecture/llm-gateway.md); how to run live batches is in [runbooks/live-llm-budget.md](../runbooks/live-llm-budget.md).

## Using it

```python
from mde_contracts.gateway import CallContext
from mde_core.settings import load_settings
from mde_llm_gateway import InProcessGatewayClient, create_gateway
from mde_prompts.registry import PromptRegistry

settings = load_settings()                       # LLM_MODE defaults to mock: no key, no network
gateway = create_gateway(settings, PromptRegistry())
client = InProcessGatewayClient(gateway)         # a GatewayClient; HttpGatewayClient has the same API

ctx = CallContext(request_id="req_1", budget_scope="session:ses_1", purpose="discover")
result = await client.run(spec, spec.input_model(...), ctx)    # CallResult[O]
```

* Engines depend on `mde_contracts.gateway.GatewayClient` only. Catch `StructuredOutputError`, `CircuitOpen`, `ProviderError` and `BudgetExceeded` (all `GatewayError`s) and use the spec's registered fallback; by the time they reach you the gateway has already retried what it may (never `BudgetExceeded`).
* `ctx.budget_scope` is the primary scope (`session:<id>`, `eval:<run>`, `test:<name>`); `ctx.extra_scopes` adds nested caps (`research:<run>` inside a session). `ctx.purpose` starting with `must-run` is the only thing allowed past the 80% tripwire.
* In tests use `ScriptedProvider` (queued or callable outputs, injected errors, delays, malformed output) with `create_gateway(settings, registry, provider=...)`, or `mde_llm_gateway.testing_specs.build_test_gateway`. A scripted provider is not billable unless built with `billable=True, mode="live"`, in which case pass an explicit `MemoryLedger` (or a `FileLedger` in a temp directory).

## Modules

| Module | Job |
| --- | --- |
| `gateway` | `Gateway` pipeline, `GatewayConfig` |
| `client`, `http_client`, `wire` | `InProcessGatewayClient`, `HttpGatewayClient`, shared wire models and error mapping |
| `factory` | `create_gateway` (provider by `LLM_MODE`; in live mode one provider per vendor the routes need, `vendors_in_use`, live gating, ledger choice) |
| `budget`, `ledger`, `pricing`, `budget_cli` | `BudgetGuard`, file-locked ledger, `pricing.yaml`, `budget.yaml`, `python -m mde_llm_gateway.budget_cli` |
| `providers/*` | `ScriptedProvider`, `MockProvider`, `ReplayProvider`, `RecordingProvider`, `OpenAIResponsesProvider`, `AnthropicMessagesProvider`, `RoutingProvider`, cassette I/O |
| `strict_schema` | `to_strict_schema`, `check_strict` |
| `render`, `untrusted` | jinja rendering, `wrap_untrusted`, `UNTRUSTED_NOTICE` |
| `retry`, `circuit`, `limits` | backoff policy, circuit breaker, concurrency and RPM limits |
| `gating`, `doctor` | live gating (`require_live`), `probe_model` for `scripts/doctor.py --live` |
| `registry` | `SpecRegistry` protocol, `StaticRegistry` |
| `testing`, `testing_specs`, `testing_fixtures/` | importable test helpers: multi-process workers, test call specs and templates, `build_test_gateway`, `make_ctx` (D-017) |

## Commands

```powershell
uv run pytest packages/llm-gateway apps/llm-gateway-api -q
uv run pytest packages/llm-gateway --cov=packages/llm-gateway/src --cov-report=term-missing -q
uv run pyright packages/llm-gateway apps/llm-gateway-api
uv run ruff check packages/llm-gateway apps/llm-gateway-api ; uv run lint-imports
just gateway-dev          # uvicorn mde_llm_gateway_api.main:app --port 8101
just budget               # live spend by scope from the ledger
```

## Settings it reads

`LLM_MODE`, `MDE_FORBID_LIVE`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_MODEL`, `MODEL_ROUTES` (JSON, `route=model,...` or one model id; see "Model vendors" in the architecture page), `LLM_BUDGET_USD`, `LLM_BUDGET_LEDGER`, `LOG_PROMPTS`, `GATEWAY_SERVICE_TOKEN`, `CASSETTE_DIR` (`Settings.cassette_dir`, default `tests/cassettes` in the main repository, anchored like the ledger) and `MDE_RECORD_CASSETTES=1` (`Settings.record_cassettes`: record cassettes in live mode). The factory reads the two cassette switches from `Settings` (D-034), not from the environment; the `cassette_dir` and `record_cassettes` arguments of `create_gateway` override them.

`GatewayConfig` knobs worth knowing: `breaker_failures` / `breaker_open_seconds` (circuit breakers are keyed by route and tool class: `breaker(route, tool_kinds)`), `concurrency` / `rpm` (per route), and `max_escalated_output_tokens` (ceiling of the one retry of a response that ran out of output tokens; see "Reasoning tokens and the output cap" in the architecture page).

## HTTP API

Set `GATEWAY_SERVICE_TOKEN` first (any non-empty value for local use; with none set every `/v1` request is refused with `config_error`), run `just gateway-dev`, then send `Authorization: Bearer $GATEWAY_SERVICE_TOKEN` on every `/v1` call:

```text
POST /v1/runs   {"call_id": "C1.understand", "version": 1, "input": {...},
                 "context": {"request_id": "req_1", "budget_scope": "session:ses_1", "purpose": "discover"}}
GET  /v1/calls  catalog: id, version, route, effort, tools, untrusted fields, input/output JSON schemas
GET  /v1/budget BudgetStatus          GET /v1/usage?scope=session:ses_1
GET  /healthz   GET /readyz           (no auth; /readyz shows mode, provider and tripwire, never a secret)
```

## Conventions

* Brief 7 is the spec; the budget guard is a safety system: a bug there is severe, so every change to `budget.py`, `ledger.py` or `pricing.py` needs a test first.
* Never log keys or prompt text; errors are scrubbed before they are raised or logged; tests grep captured logs for a fake key.
* Prompts and templates never live in Python here: only `testing_fixtures/` has templates, for tests.
* Layering: depends on `mde_core` and `mde_contracts` only; never imports `mde_prompts` (the API app may).


## Ledger guard

The ledger path is anchored to the main repository root and guarded by `<ledger>.guard`. A deleted or truncated ledger makes live calls refuse with `ledger_integrity`. Reset deliberately with `python -m mde_llm_gateway.budget_cli init --force` or the environment variable `MDE_BUDGET_LEDGER_RESET=1`.
