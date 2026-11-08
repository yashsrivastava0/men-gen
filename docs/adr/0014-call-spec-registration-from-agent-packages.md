# ADR 0014: Call specs live in the agent package; no gateway change

- **Status:** accepted
- **Date:** 2026-11-09
- **Deciders:** lead (D-085)

## Context

The gateway pipeline takes the spec object (`Gateway.run(spec, input, ctx)`); the registry is only used for catalog and HTTP lookup. `mde_contracts.CallSpec` is frozen, has no `lane`, `timeout` or `model` field, and `mde_prompts` knows only the old closed set of call ids. The prompt for Compass asks for a `lane` on every call so a second vendor can be added later, and for the single definition of each call to live in the Compass folder.

## Decision

1. Each call is a folder `calls/<name>/` (`spec.py`, `schema.py`, `prompt.jinja`, `examples/`, `mock.py`, `tests/`). `spec.py` builds a `CompassCallSpec`: the gateway `CallSpec` (id `cmp.*`, `template_root` = the folder, `mock_handler` dotted path into the folder) plus `lane`, `timeout_s`, local validators and the deterministic fallback function.
2. Compass calls go through `CallRunner` (`mde_compass.calls.base`), which wraps `GatewayClient.run`: budget pre-check, deadline from `timeout_s`, local validation after the gateway's own validation and single repair, fallback on any gateway-side failure, and a call-log entry. Specs need no registration to be called.
3. `StaticRegistry` from `mde_llm_gateway` serves the Compass specs for catalog and HTTP lookup. A `ChainedRegistry` hook in the gateway (to serve Compass and `mde_prompts` specs from one registry in one process) is **deferred**; it would be additive and is not needed for the standalone app.
4. LLM output models must convert to the strict schema (depth at most 5, no open objects, no `Any`, no recursion, optional = nullable); arbitrary values travel as `value_json: str` validated by code.
5. Engine code depends on **ports** (`mde_compass.engine.ports`) over domain types; each call folder supplies an adapter, and the deterministic fallbacks double as test doubles.
6. Budget: the gateway ledger and global USD 6.00 cap stay authoritative. Compass adds its own live cap (`COMPASS_LIVE_BUDGET_USD`, at most 2.00, stop at 95%, per session 0.25, fails closed) in `mde_compass.budget`, reconciled against the ledger.

## Alternatives considered

- **Add `lane`/`timeout` to `CallSpec`.** A contract change for one consumer.
- **Register Compass ids in `mde_prompts`/`CALL_IO`.** Couples the new agent to the old closed catalog.
- **A gateway registry hook now.** More shared surface, not needed yet.

## Consequences

- The gateway never imports Compass; Compass imports only the gateway's public client API and `StaticRegistry`.
- Vision and streaming are not available through the gateway today (`ProviderRequest` is text only, no streaming): image intake and `assistant_delta` are reported as unsupported until the gateway grows them.
