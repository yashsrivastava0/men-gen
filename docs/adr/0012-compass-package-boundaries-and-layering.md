# ADR 0012: Compass package, boundaries and layering

- **Status:** accepted
- **Date:** 2026-11-09
- **Deciders:** lead (D-085); behaviour source: [doc 11](../architecture-v3/11-discovery-agent-design.md)

## Context

Compass is the first agent a person meets. It replaces the older Discover behaviour (`mde_discovery`) but the old package must stay untouched and green until a later task retires it. `mde_contracts` has a closed slot and call vocabulary (`SlotId`, `CALL_IO`, `Card.slot`), so Compass cannot extend it without a contract change. Everything for the agent must live in one folder so it can be developed, tested and later mounted or deleted as a unit.

## Decision

1. **One package**, `packages/compass/` (import name `mde_compass`, call ids `cmp.*`, API prefix `/v1/compass`, dev port 8110, persona name one constant in `mde_compass/__init__.py`). Code, prompts, catalogs, store, API, static test UI, tests and evals live there.
2. **May import** only `mde_core`, `mde_contracts` (`CallSpec`, `CallContext`, `GatewayClient`), `mde_llm_gateway` and `mde_persistence`. **Must not import** `mde_discovery`, `mde_research`, `mde_spec_builder`, `mde_flow`, `mde_prompts`, `mde_content`, `mde_spec_loader` or any app. Parts of `mde_discovery` worth keeping (slot rules, intent router, redaction, safety screen, the safe expression evaluator) are copied and adapted into Compass with their tests.
3. **Layering** (import-linter): `mde_compass` sits in the apps layer beside `mde_context_api` and `mde_llm_gateway_api` (independent siblings), plus a `forbidden` contract listing the packages above. Nothing below the apps layer imports Compass. The existing bans (only the gateway imports `openai`, no agent frameworks, no SQLite) now also cover Compass.
4. **Own domain, no contract edits.** Compass defines its own models (`mde_compass.domain`). No edits to `mde_contracts`, `mde_persistence`, `mde_llm_gateway` or `mde_discovery`.
5. **Own migrations.** The store has its own Alembic directory and version table (`compass_alembic_version`) with an `include_object` filter for `compass_*`, and its own ephemeral test database, because persistence tests assert a single head, an exact table set and zero drift.
6. **Router and app.** `create_app()` and a `router` both exist, so the standalone app (`python -m mde_compass --port 8110`) and a later mount by the Context API behave the same.

## Alternatives considered

- **Extend `mde_contracts` and `mde_prompts`.** Edits shared, frozen code and couples Compass to the old catalog of calls.
- **Add Compass tables to the persistence migration chain.** Breaks persistence tests (single head, table set, drift check) and mixes ownership.
- **Import `mde_discovery` pieces.** Would block retiring the old package.

## Consequences

- Duplicated small utilities (slot rules, lexicon) until the old package is retired; recorded in the report.
- Compass tests run in an isolated database and cannot disturb persistence tests.
- Mounting into the Context API later needs only `include_router` plus a composition-root entry; no Compass code changes.

## Amendment, 2026-11-09 (D-092)

Compass moved from `packages/compass/` to `agents/compass/` (global rules B: one self-contained folder per agent; `packages/*` stays shared infrastructure and legacy packages). Nothing else in this decision changes: the import name is still `mde_compass`, the layer and the `compass-isolated` contract in `.importlinter` are module-name based and were not edited, and the dependency rules above hold. Wherever this page says `packages/compass/` read `agents/compass/`.
