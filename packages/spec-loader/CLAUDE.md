# mde_spec_loader

**Job:** the read-only library the future frontend engine uses to consume `APP_SPEC.md`. Pure: no I/O besides reading a file or a string.

**Public interface:** `index()`, `get(ids)`, `slice(step_or_screen)`, `neighbors(id)`, `refs(id)`, `diff(vA, vB)`, `verify(hash)`, plus a token estimator. `slice()` builds tiered context (tier 0 header/summary/stack rules; tier 1 design + navigation; tier 2 the task's block + referenced entities/operations/integrations + kit component signatures; tier 3 on demand). A screen task should need well under 10k tokens of spec context.

**Test:** `uv run pytest packages/spec-loader` (coverage >= 85%); contract check: `uv run python scripts/simulate_frontend_consumer.py <spec file>` fails if a slice exceeds 10k estimated tokens or a reference does not resolve.

**Conventions**
- Blocks are fenced YAML with an info string like ` ```yaml id=SCR-014 kind=screen `; the loader indexes `ID -> location -> size`. Never edits the spec.
- pyright strict. Format and block ids must match `docs/specs/app-spec-format.md` and `docs/specs/frontend-consumption.md` (build sequence S0..S10, `raise_decision`, `@spec SCR-014@v7` headers, locked screens, incremental runs from `diff`).
- Owner: `spec-engineer`.
