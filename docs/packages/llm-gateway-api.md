# `mde_llm_gateway_api`

The LLM Gateway as a small HTTP service (`apps/llm-gateway-api`), so it can later become its own deployable. It adds a bearer token, JSON in and out and a stable error envelope; **everything that matters (budget, live gating, retries, redaction) is enforced inside `mde_llm_gateway`**, not here. The matching client, `HttpGatewayClient` (in `mde_llm_gateway.http_client`), implements the same `GatewayClient` protocol as the in-process one, so an engine cannot tell which it has. Owner: `gateway-engineer`. The library, its settings and the ledger are on [the llm-gateway page](llm-gateway.md); the design is [architecture/llm-gateway.md](../architecture/llm-gateway.md).

## Run it

```powershell
$env:GATEWAY_SERVICE_TOKEN = "any-non-empty-local-value"    # without a token every /v1 request is refused
just gateway-dev                                              # uvicorn mde_llm_gateway_api.main:app --port 8101
```

The default mode is `LLM_MODE=mock`: no key, no network, no cost. Live mode needs `LLM_MODE=live` (see [the live budget runbook](../runbooks/live-llm-budget.md)) and is never started by this app on its own: `create_app()` builds the gateway from settings, so a forbidden or keyless live configuration fails at start-up, not on the first call. To make the context API use it: `GATEWAY_MODE=http`, `GATEWAY_URL=http://127.0.0.1:8101` and the same `GATEWAY_SERVICE_TOKEN` (both required, otherwise `ConfigError`).

## Routes

Everything under `/v1` needs `Authorization: Bearer <GATEWAY_SERVICE_TOKEN>`, compared in constant time. With no token configured the service refuses every `/v1` request (fail closed).

| Route | Purpose |
| --- | --- |
| `POST /v1/runs` | `{call_id, version, input, context}` returns a `CallResult`; the input is re-validated against the spec's input model, and a caller-supplied `purpose` starting `must-run` is rewritten to `api:must-run...` (HTTP callers are untrusted for budget purposes) |
| `GET /v1/calls` | the catalog: id, version, route, effort, `max_output_tokens`, tools, untrusted fields, cacheable, repair, fallback, input and output JSON Schemas |
| `GET /v1/budget` | `BudgetStatus` (caps, spent, reserved, tripwire, live calls, spend by scope) |
| `GET /v1/usage?scope=` | spend for one scope, or the totals and the by-scope table |
| `GET /healthz`, `GET /readyz` | liveness and readiness, no authentication |

Errors use one envelope, `{"error": {"code", "message", "details"}}`, with the codes of [the error table](../architecture/llm-gateway.md) (`budget_exceeded`, `ledger_integrity` as 503, `circuit_open`, `structured_output_error`, `validation_failed` and so on). Request validation errors never echo the submitted values (they may be prompts or secrets); an unexpected error logs only its type name.

## Test

```powershell
uv run pytest apps/llm-gateway-api -q
uv run pyright apps/llm-gateway-api ; uv run ruff check apps/llm-gateway-api ; uv run lint-imports
```

`test_contract.py` runs the same suite against the in-process client and against the HTTP client over an ASGI transport (the harness is `mde_llm_gateway_api.testing`, importable because test modules cannot import each other, D-017). `test_api.py` covers authentication on every route, the fail-closed no-token case, the catalog, the error envelopes (never echoing the input) and the client's error mapping; `test_api_hardening.py` covers the `must-run` rewrite (including past the 80% tripwire) and the refusal of unknown scope kinds.

## Conventions

* Never log keys or prompt content; the API key is read only inside the gateway package.
* Budget and live gating are the gateway's job; this app adds authentication and transport only.
* It may import `mde_prompts` (the layering puts it above); the gateway library may not.
* Layering: it sits beside `mde_context_api` and must not import it.

## Known gaps

* `GET /readyz` has no authentication and reports `mode`, `provider`, `billable`, the budget `tripwire` and the number of specs. The context API's `/readyz` was reduced to `ok` or one word per check after the security review (D-066); this one was not. Put the service on a private network or behind the token-checking proxy until it is aligned (a lead item, see [security](../architecture/security.md)).
* There is no rate limiting or TLS here; the service is meant for a private network.
