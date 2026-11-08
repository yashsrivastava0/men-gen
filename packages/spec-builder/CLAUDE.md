# mde_spec_builder

**Job:** step 4: build `APP_SPEC.md`. Principle: **a valid baseline that the model only refines.**

**Pipeline (B1..B9):** freeze inputs → B2 deterministic baseline from the Domain Pack (or base recipes for `custom`) → B3/B4 LLM *proposals* (C9a/b/c, C10 in batches of 3 to 4 screens) validated by code: valid proposals applied, invalid dropped and logged → B5 assemble IDs and reference graph → B6 nine validators (spec 6.4) → B7 critic (C12) → B8 repair (C13, at most 2 rounds, else one plain question via `needs_answer`) → B9 build sequence S0..S10, deterministic render (stable ordering, canonical YAML, no timestamps in hashed content), sha256, draft `spec_version`, Plan view, `plan_ready`.

**Test:** `uv run pytest packages/spec-builder`

**Conventions**
- Same input must produce the same bytes. Scope guard: cap 50 screens in Guided (policy-configurable), overflow goes to a Phase 2 backlog. `inherits` is copied in so the file is self-contained. Research additions are tagged `source: research`.
- No secrets in the file (validator 9). Approved versions are never mutated; a change makes a new version.
- Call specs C9a/b/c, C10, C12, C13 live in `mde_prompts` and are `builder-engineer`'s.
- Owners: `spec-engineer` (validators, renderer, B5-B9 deterministic parts, build sequence, Plan view) and `builder-engineer` (baseline, proposals, critic, repair). Separate modules; the lead merges the package `__init__`.
