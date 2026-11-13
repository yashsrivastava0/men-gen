# mde_spec_core

**Job:** the Spec Graph, V-01..V-45, renderer, packager, slicer, build planner, libraries loaders and `spec check`. Shared by `agents/spec` and `agents/verify`. No model, no network, no I/O stack (import-linter: spec-core-is-foundation). Design: `docs/architecture-v3/15..19`.

**Test:** `uv run pytest packages/spec-core`

**Conventions:** same graph + same consumer profile = same bytes (LF, sorted keys, no timestamps in bodies). Every number from doc 15 marked [J] is configuration in `spec-config.yaml`, never a constant. Content (libraries, vocabularies) is data under `src/mde_spec_core/data/`. Money is integer micro-USD. No vendor or model name anywhere.
