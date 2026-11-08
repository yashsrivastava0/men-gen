# ADR 0003: Pack navigation type and feature sidecar (contracts 1.0.1)

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (found by content-author-packs)

## Context

Contracts v1 declared `DomainPack.navigation` as `spec.NavNode`, whose `screen` field must match `SCR-###`. The same module's docstring says pack screens are referenced as `@key` until B2 assigns ids, so no real pack navigation tree could validate. The contract also had no place for the feature-gated pieces that flat lists cannot express (API operations, entities, integrations, and sections or actions inside always-on screens). The packs author worked around both locally and reported it.

## Decision

- Add `PackNavNode` to `mde_contracts.content` (`screen` matches `^@[a-z][a-z0-9_]*$`) and type `DomainPack.navigation` with it. B2 rewrites it into a `spec.NavNode` with real screen ids.
- Add `FeatureParts` and `FeatureScreenParts` and an optional `DomainPack.feature_parts` sidecar (default empty).
- `CONTRACTS_VERSION` becomes `1.0.1`.

## Alternatives considered

- Loosen `NavNode.screen` to any string: weakens the real spec model for every consumer. Rejected.
- Keep the local workaround in `mde_content`: contract and data would disagree and B2 would have to know about the private type. Rejected.

## Consequences

- Retyping `DomainPack.navigation` is formally breaking, but no consumer existed yet (only `mde_content`, updated in the same change), so the version bump is a patch level. Tag `contracts-v1` is kept as the historical v1 freeze; later contract changes are listed in `DECISIONS.md`.
- `mde_content.packs.select_features` prunes the sidecar to the features and screens that remain, so an expanded pack validates on its own.
- The builder-engineer expands `PackNavNode` -> `NavNode` in B2.
