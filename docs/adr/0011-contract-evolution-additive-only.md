# ADR 0011: Contracts evolve additively, through the lead

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (D-009, D-036, D-040; precedent [ADR 0003](0003-pack-navigation-and-feature-parts.md))

## Context

`mde_contracts` is the shared vocabulary: models, events, call IO and the ports through which `mde_flow` wires the engines. Five or six agents built engines against it in parallel from the first day. Freezing it completely would have blocked them the moment someone needed a field; changing it freely would have broken every consumer at random. In practice every engineer did need additions (a `Principal`, new transitions, new optional request fields), and exactly one needed a retype (ADR 0003).

## Decision

1. **Frozen at v1** (git tag `contracts-v1`, `CONTRACTS_VERSION` in `mde_contracts.common`; now `1.0.1` after ADR 0003). The tag stays as the historical freeze point.
2. **Additive changes are made only by the lead** and logged in `DECISIONS.md` **without a version bump**. Additive means: a new optional field with a default, a new model, a new protocol method with a default, a new member of an existing `Literal` (for example `UiActionName` gained `idle`, `HeavyChange` gained `screens`, `TransitionName` gained `answer_given` and `rediscover`). Consumers must tolerate values and optional fields they do not know yet.
3. **Breaking changes** (rename, remove, retype, make a field required, narrow a type) need an **ADR**, a version bump and **every consumer updated in the same change**.
4. **Everyone else asks.** An engineer who needs a change does not edit the package: they write the exact change under "Requested contract changes" in their report and work around it locally until the lead applies it.
5. **After every change** the JSON Schemas and the OpenAPI export are regenerated (`uv run python scripts/export_schemas.py`; `--check` reports drift) and a test fails if the committed OpenAPI file is stale. LLM output models must still convert to the OpenAI strict schema.
6. **A widened enum is announced to its producers.** When C14's output enum gained `toggle_assumption`, the discovery engineer was told to handle it (D-040): additive for the schema, still a behaviour to cover.

## Alternatives considered

- **A version bump for every change.** Too slow for parallel agents and noisy for consumers when nothing they use changed.
- **No freeze.** Silent breakage: a rename in one package would surface as a runtime error in another.
- **Each package defines its own models.** Duplicated vocabulary and mismatched fields at every seam (the spec hash rule, [ADR 0006](0006-one-spec-hash-rule.md), is what that costs).
- **Let any engineer edit contracts additively.** Two agents adding the same concept differently, and no single view of what was added.

## Consequences

- One person sees every change; the additive list in `DECISIONS.md` (D-022, D-036, D-040, D-047, D-059, D-062) is the changelog, summarised in [the contracts page](../packages/contracts.md).
- Requests cost a round trip; agents carry small local adapters in the meantime (and remove them afterwards).
- `CONTRACTS_VERSION` moves only for a breaking change; a patch bump is allowed for a retype that no consumer had yet used (ADR 0003).
- Optional contract fields that were asked for but not applied stay listed as open items in the owning package page (for example `C8Input.available_features`).

## References

`.claude/rules/contracts.md`, [contracts](../packages/contracts.md), brief 5.2, DECISIONS D-009, D-036, D-040.
