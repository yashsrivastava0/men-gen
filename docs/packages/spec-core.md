# spec-core

Package `mde_spec_core` (`packages/spec-core/`). The shared heart of the Spec engine: the Spec Graph, the 45 validators, the renderer, the packager, `spec check`, the libraries as data, and the portable call harness. No model, no network, no web or database stack (import-linter contract `spec-core-is-foundation`). Design: [architecture-v3/15 to 19](../architecture-v3/19-spec-big-apps-bench-and-result-first.md). Imported by `agents/spec` and `agents/verify`; never imports the gateway or an agent.

## What is in it

- **Graph** (`graph.py`, `ids.py`, `canon.py`): typed nodes with tier, provenance, status (`in`, `later`, `out`) and Spec-namespaced ids; no layout field exists anywhere; `SpecGraph.content_hash()` is what Gate 2 binds (COULD entries are outside it). Same graph, same bytes.
- **Config and profiles** (`config.py`, `data/spec-config.yaml`, `profiles.py`, `data/consumer-profiles.yaml`): every [J] number is data; the loader refuses an inconsistent budget set; profiles come from the providers' current documentation with the unset fields named.
- **Libraries and vocabularies** (`vocab.py`, `libraries.py`, `data/vocab`, `data/libraries`): 17 shapes, 38 intents, 21 screen types, the state library, the system screen set, scenarios, the error catalogue, exact synonyms, the form-word lint; the store-policy sheet (`policy.py`), capability sheet, delight patterns and script fonts (`platform_data.py`), free-tool smoke tests (`tooling.py`).
- **Derived by code**: tokens (`tokens.py`, contrast asserted), hero skeleton (`hero.py`), mock modes and safe fixtures (`mock.py`, pinned naughty-strings subset), glossary (`glossary.py`), asset needs (`assets.py`), Contract v0 (`contract.py`), the six code fixes of the repair ladder (`rcode.py`).
- **Pack** (`render.py`, `pack.py`): cards plus generated closures, exact shared blocks, needs, pilots, task cards with read totals, prefix bundle, build plan, manifest and hashes; fails the build on a breach it cannot re-shard.
- **Checks** (`validators/`, `check.py`, `bn.py`, `coverage.py`, `lint_pack.py`): V-01 to V-45 as code, the builder-needs rule BN-01 to BN-17, the coverage matrix, the G-1 to G-8 pack linter. `uv run python -m mde_spec_core check <pack-dir>` runs on any pack folder; `--synth <preset>` runs on a synthetic one.
- **Proof tools** (`planted.py`, `simulate.py`, `synth.py`): the 22 structural and 8 meaning planted defects, the consumption simulator (totals equal the packager's), seeded synthetic graphs (tiny to program and ERP).
- **Call harness** (`callkit/`): tolerant JSON parse with array salvage, schema compile check against a profile's limits, the fusion rule, one re-ask per node, cache and checkpoint keys, prompt layout, the repair log and the `weak_route` banner. `verify_port.py` is the port Verify implements.

Test: `uv run pytest packages/spec-core`.
