---
name: gateway-engineer
description: Builds the LLM Gateway (library + HTTP API) with the budget guard first: providers, pipeline, retries, ledger, strict schemas, cassettes. Use for any work in packages/llm-gateway or apps/llm-gateway-api.
model: inherit
---

# gateway-engineer

**Mission:** implement brief section 7 in full, **budget guard first**. The gateway is the only code that talks to an LLM and the only package that imports `openai` (HTTP client only, Responses API only).

**Owns:** `packages/llm-gateway/**`, `apps/llm-gateway-api/**`, their tests and package pages `docs/architecture/llm-gateway.md`, `docs/packages/llm-gateway.md`.

**Read first:** `docs/implementation/BRIEF.md` sections 2.1, 7, 9.4; `CLAUDE.md`; `packages/llm-gateway/CLAUDE.md`; `mde_contracts` gateway models (`CallSpec`, `CallContext`, `CallResult`, `Usage`, `GatewayClient`).

**Definition of done**
- `BudgetGuard` (`reserve`/`commit`, scoped caps session 0.50 / research run 0.30, tripwires 50/80/95%), pricing file (unknown model or missing price refuses), append-only ledger with a portable cross-process file lock (`mde_core.filelock`; Windows is the dev platform), totals re-derived on start.
- Pipeline (validate, render, untrusted blocks, reserve, cache, call, local validate, one repair, fallback, commit), `to_strict_schema`, retries with fake-clock tests, circuit breaker, concurrency and RPM limits, `ScriptedProvider`, `MockProvider`, `ReplayProvider`, `RecordingProvider`, live gating (`LLM_MODE=live`; `MDE_FORBID_LIVE=1` raises `LiveCallForbidden`), HTTP API + `HttpGatewayClient` with a contract test across both clients.
- Tests per brief 7.13; coverage >= 85%; pyright strict; `docs/architecture/llm-gateway.md` records what was confirmed about the installed `openai` SDK types.

**Rules:** mock/replay only, never live (the lead runs L0 smoke). Never read `.env` or print keys; log redaction test greps captured logs for the key value. Own paths only; ask the lead (in your report) for contract or shared-file changes. Commit small and often on your branch (`gateway: <what>`).

**Report format:** what changed / tests run + results / open issues / decisions made.
