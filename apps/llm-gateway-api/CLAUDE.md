# mde_llm_gateway_api

**Job:** expose `mde_llm_gateway` over HTTP so it can later become its own service. Same interface as the in-process gateway.

**Routes (bearer `GATEWAY_SERVICE_TOKEN`):** `POST /v1/runs` (call id, version, input, context -> result), `GET /v1/calls` (catalog), `GET /v1/budget`, `GET /v1/usage?scope=`, `GET /healthz`, `GET /readyz`; optional `/metrics`-style text endpoint. Run: `just gateway-dev`. Ships `HttpGatewayClient` implementing the `GatewayClient` protocol from `mde_contracts`.

**Test:** `uv run pytest apps/llm-gateway-api` (a contract test runs the gateway suite against both the in-process and HTTP clients)

**Conventions**
- Never log keys or prompt content. The API key is read only inside the gateway package.
- Budget and live gating are enforced in the gateway, not here; this app adds auth and transport only.
- Owner: `gateway-engineer`.
