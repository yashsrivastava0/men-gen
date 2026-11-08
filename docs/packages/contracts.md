# `mde_contracts`

The vocabulary of the whole system (`packages/contracts`): Pydantic models and `Protocol`s only, **no I/O**. It depends only on `mde_core` (and Pydantic). It was frozen at v1 (git tag `contracts-v1`); `CONTRACTS_VERSION` is `1.0.1` (ADR 0003). Pyright strict. Owner: **the lead only**; everyone else asks for a change in their report. Evolution policy: [ADR 0011](../adr/0011-contract-evolution-additive-only.md) and `.claude/rules/contracts.md`.

## Modules

Import from the submodule you need (`mde_contracts.events`, never a wildcard).

| Module | Holds |
| --- | --- |
| `common` | `Frozen` base model, `Principal`, `Mode`, `CONTRACTS_VERSION`, `SPEC_SCHEMA_ID` |
| `slots` | slot levels, states, sources, `SlotDef` and `SlotRecord` |
| `cards` | `Card`, `CardInstance`, option and i18n models (semantics in [question cards](../specs/question-cards.md)) |
| `events` | inbound events (`UserMessage`, `OptionSelected`, `UploadAdded`, `GateDecision`, `UiAction`) and outbound events (`AssistantMessage`, `CardEvent`, `Notice`, `ProgressEvent`, `GateEvent`, `StateChanged`, `PlanReady`, `SpecReady`), with field limits ([events and API](../specs/events-and-api.md)) |
| `brief`, `research`, `plan`, `spec` | the Brief, the Research Note, the Plan view, the spec block models and `AppSpec` ([spec format](../specs/app-spec-format.md)) |
| `gates` | session states, plan phases, `TransitionName`, gate kinds, `ApprovalRecord` |
| `policy` | `Policy` and its parts (gates, limits, web, role precedence, heavy-change categories) ([policy format](../specs/policy-format.md)) |
| `content` | the shapes of every data file in `mde_content`: question bank, Domain Pack, theme, stack profile ([domain pack format](../specs/domain-pack-format.md)) |
| `gateway` | `CallSpec`, `CallContext`, `CallResult`, `Usage`, `BudgetStatus`, the `GatewayClient` protocol (D-007) |
| `calls` | input and output models of every LLM call (C1..C14 and S1) |
| `registry` | `CALL_IO` (call id to its input and output models), `llm_output_models()`, `exported_models()` (what becomes a JSON Schema) |
| `engines` | `DiscoveryEngine`, `ResearchEngine`, `SpecBuilderEngine` protocols and their request and result models, `RunContext` |
| `records` | persistence records as plain models |
| `ports` | repository protocols (`Store`, `UnitOfWork`, `SessionRepo`, `EventRepo`, `SpecRepo`, `ApprovalRepo`, `JobRepo`, ...) and the hand-off ports (`ProjectRepoPort`, `FrontendEnginePort`, `InternalSourcePort`, `BudgetGate`) |

## Rules every model obeys

* **No I/O**, no network, database, filesystem or subprocess (`.importlinter` forbids the libraries).
* **LLM output models must convert to the OpenAI strict schema**: no open objects (`dict`), no free-form JSON, depth 5 or less, enums as `Literal`; slot values travel as `value_text | value_list | value_bool` (D-008). A test walks `llm_output_models()` and converts each one (see [the gateway page](../architecture/llm-gateway.md), "Strict schema").
* Models are frozen where practical; ids are prefixed ULIDs; money is integer micro-dollars; timestamps are timezone-aware UTC.
* Archetype ids are plain strings validated against content at runtime (D-012), never a `Literal`.

## Changing a contract

* **Additive** (a new optional field, a new model, a new protocol method with a default): the lead makes it and logs it in `DECISIONS.md` without a version bump (D-009).
* **Breaking** (rename, remove, retype, make required): an ADR, a version bump and every consumer updated in the same change (precedent: [ADR 0003](../adr/0003-pack-navigation-and-feature-parts.md), which retyped a field nothing used yet and bumped the patch level).
* After any change: `uv run python scripts/export_schemas.py` and commit the regenerated files in `docs/api/schemas/` (`--check` reports drift). A test fails when the committed OpenAPI export is stale.

Additive changes since the freeze, for orientation (the log of record is `DECISIONS.md`): `Principal` (D-022); `BlockKind` backlog, `PlanView.compliance`, `ScreenBlock.title`, `Limits.max_spec_tokens`, `BuildRequest.gate1_spec` (D-036); `TransitionName` `answer_given` and `rediscover`, `ChangeOpKind` `toggle_assumption`, `BuildRequest.change_ops`, `DiscoveryRequest.source`, `PlanJobPayload.answer_slot_ids` and `via` (D-040); `UiActionName` `idle`, `ResearchRequest.references` and `brand_url` (D-047); `HeavyChange` `screens` (D-059); the `BudgetGate` and `BudgetVerdict` port and `BuildRequest.screen_notes` (D-062); port docstrings (D-051).

## Tests

```powershell
uv run pytest packages/contracts -q
uv run python scripts/export_schemas.py --check      # are docs/api/schemas and openapi.json current?
uv run pyright packages/contracts ; uv run ruff check packages/contracts
```
