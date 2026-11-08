# mde_contracts

**Job:** the frozen v1 vocabulary of the whole system: Pydantic models and `Protocol`s only, **no I/O**. Depends only on `mde_core`.

**Public interface:** slots and cards, inbound/outbound events, Brief, Research Note, spec block models and `AppSpec`, Plan view, gate/state enums, policy model, content models (question bank, domain pack, theme, stack profile), `CallSpec` / `CallContext` / `CallResult` / `Usage` / `GatewayClient`, call IO models for C1..C14 and S1, repository protocols (`Store`, `UnitOfWork`), hand-off ports (`ProjectRepoPort`, `FrontendEnginePort`, `InternalSourcePort`), engine protocols. JSON Schemas are exported by `just export-schemas` to `docs/api/schemas/`.

**Test:** `uv run pytest packages/contracts`

**Conventions**
- Frozen at v1 (tag `contracts-v1`): additive changes only through the lead and logged in `DECISIONS.md`; breaking changes need an ADR + version bump. See `.claude/rules/contracts.md`.
- LLM output models must convert to OpenAI strict schema: no open objects, no free-form JSON, depth 5 or less; slot values as `value_text | value_list | value_bool`.
- Models are frozen where practical; IDs are prefixed ULIDs; money is integer micro-dollars; timestamps are UTC-aware.
- Owner: the lead only. Others request changes in their report.
