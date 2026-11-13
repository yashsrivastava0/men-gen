# ADR 0017: The Brief carries a user-approved structure (modules, screens, flows, entities); tiers map to size bands

- **Status:** accepted
- **Date:** 2026-11-10
- **Deciders:** lead, with the owner (messages of 2026-11-10: size tiers, "the final Brief must be logically correct and realistic", research guidance, live testing allowed within USD 3.00; D-113 to D-120)

## Context

The downstream AI that writes React / Expo code builds from the Brief. The Brief had typed parts only for features, roles, apps, integrations and a flat list of `domain_facts`. The two real exports (`docs/compass/outputs/`) hold 3 and 9 features, and about half of their `domain_facts` are repeats. That is enough for 8 to 15 screens at best. The owner wants the three tiers to mean app size (Quick about 8-15 screens, Standard about 20-30, Thorough or a document about 40-60 and more), the AI drafting the breakdown and the person approving it at Gate 1, a Brief that stays correct and realistic for big apps, and a Brief that tells research how deep to go.

Two documents place this work in a later stage: product spec section 5 (the Spec Builder expands screens, navigation, entities and flows after Research) and architecture-v3 doc 03 section 5 (Blueprint 3a to 3c, optional G1.5 structure gate). Nothing reads the Compass Brief yet (hand-off is `LoggingHandoff`), so this decision changes no consumer.

## Decision

1. **Boundary.** Compass drafts a user-approved STRUCTURE at intent level and gives it Spec Graph id prefixes (MOD, SCR, FLW, ENT, doc 04 section 1) so the later Blueprint inherits ids instead of re-deriving them. Compass does not draft routes, the navigation tree, API operations, lifecycle transitions or per-screen sections, bindings, copy, states and GIVEN/WHEN/THEN; those stay with the later stage. Drafted items are `Default` tier with provenance `model_knowledge`; `Locked` only when a person's or a document's verified fact supports them; Approve All never creates `Locked`.
2. **Representation: four new optional Brief list sections** `modules`, `screens`, `flows`, `entities` (records with `ItemMeta` and a real `decision` field), not `DomainFact`. A fact is a projection of the ledger with a verified quote and is read-only; typical details are capped at 12; a statement cannot hold roles, steps or fields, so code could not check consistency. `DomainFact.kind` and `refs` stay for rules, numbers, acceptance, compliance and the like, and `refs` may now point at structure ids.
3. **Hash safety.** The sections join `OPTIONAL_SECTIONS` (an empty one is left out of the hash and of the section hashes, the `domain_facts` precedent of ADR 0015), no `SCHEMA_VERSION` bump, no new hashed `meta` key, no new field on an existing record. Size band, source and counts live in `meta.interview` (outside the hash). The four fields are omitted from the dump while empty so code before this change can still read a Brief that holds no structure. Pinned by recomputing the hashes of the two real exports and of the stored fixture.
4. **Size bands** (`catalog/size_bands.yaml`): `small | medium | large` (the page says "Enterprise" for `large`; the id avoids the clash with `Mode == "enterprise"`). The tier (question count) sets the effort and the target band; it does not set the level of detail: every band has the same record schema. Bands are guidance with warn-only checks, never minimums, so nothing is padded. A document raises the band by the number of verified claims, not by existing. Review strictness by band mirrors doc 03: small shows a summary, medium nudges, large requires every module to be accepted (or Approve All).
5. **Generation.** Three gateway calls, each with a versioned spec, mock handler and deterministic floor: `cmp.draft_outline` (modules, entity names, journeys), `cmp.draft_module` (screens, journey legs, entity fields for the modules of one group, in parallel) and `cmp.review_structure` (a findings-only critic). Code assembles cross-module flows from journey legs, allocates ids, merges pinned items, runs the checks, applies deterministic fixes and runs ONE repair-and-expansion pass. The step runs inside the existing seed and scan gather, and again, digest-gated, at review.
6. **Correct and realistic.** Logical checks in code (a flow's role can open every step; a role that creates or edits an entity has that access; role homes; scope exclusions; data typing), a data-driven realism rule pack, the critic, and a provenance guard that downgrades a record whose cited facts are gone.
7. **Research guidance.** The size band maps to a research depth (light, standard, deep = up to 6, 12, 20 searches plus a gap-fill for deep; the product spec's dial) that the research view carries next to the category; `cmp.seed_research` caps its requests by band.

## Alternatives considered

- **`DomainFact.kind` and `refs` for the whole structure** (the ADR 0016 choice for detail): unstructured, capped, read-only, copies the duplicate-fact problem 60 times into the views.
- **New fields on `FeatureRecord` and `RoleRecord`:** move the hash of every stored Brief with features.
- **A new session state for structure review:** needs flow-controller and contract changes; the review sits inside BRIEF_REVIEW instead (modules are Brief-view rows, so the existing Accept, assumed-ratio and Approve All logic apply).
- **Minimum screen counts per tier:** pads small ideas; replaced by guidance, warnings and per-feature and per-module thinness checks.
- **Leave the structure to the Blueprint stage:** the person could not approve scale before research is paid for, and the downstream AI would have nothing to build from today.

## Consequences

- Easier: a Brief that describes a 60-screen app, can be sliced by module, is checked by code, and tells research what kind of app this is.
- Harder: four more sections to keep in step (patch registries, views, validation, strings), a Brief that holds a structure cannot be read by code from before this change, and a new check layer to calibrate on live runs.
- Work: phases S0 to S9 of `docs/implementation/COMPASS-SIZE-PLAN-2026-11-10.md`, each green and revertible; `mde_contracts` is untouched; no migration (the Brief is a JSONB document).

## References

ADR 0004, 0012, 0013, 0014, 0015, 0016; product spec sections 4 and 5; architecture-v3 doc 03 sections 1, 5 and doc 04 section 1; doc 11 section 17.10; D-093, D-110, D-111, D-112, D-113 to D-120.
