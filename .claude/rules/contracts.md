---
paths:
  - "packages/contracts/**"
  - "docs/api/schemas/**"
---

# Contracts rules (`mde_contracts`)

- Pydantic models, enums, constants and `Protocol`s only. **No I/O** (no network, DB, filesystem, subprocess).
- Frozen at **v1** (tag `contracts-v1`). Only the lead edits this package.
  - **Additive** changes (new optional field, new model, new protocol method with a default, a new member of an existing `Literal`) are allowed through the lead and are logged in `docs/implementation/DECISIONS.md`, no version bump; tell the producers of a widened enum (ADR 0011).
  - **Breaking** changes (rename, remove, retype, make required) need an ADR in `docs/adr/`, a version bump and every consumer updated in the same change.
- If you need a contract change, do not edit the package: describe the change in your report so the lead can apply it.
- LLM output models must stay convertible to OpenAI strict JSON schema: no open objects (`dict`), no free-form JSON, shallow nesting (depth 5 or less), enums as `Literal`. Slot values travel as `value_text | value_list | value_bool`.
- Money is integer micro-dollars or `Decimal`, never float. Timestamps are timezone-aware UTC. IDs are prefixed ULIDs.
- After a change run `just export-schemas` (`uv run python scripts/export_schemas.py --check` reports drift) and commit the regenerated JSON Schemas in `docs/api/schemas/` and `docs/api/openapi.json`.
- Policy and history: [ADR 0011](../../docs/adr/0011-contract-evolution-additive-only.md), [docs/packages/contracts.md](../../docs/packages/contracts.md).
