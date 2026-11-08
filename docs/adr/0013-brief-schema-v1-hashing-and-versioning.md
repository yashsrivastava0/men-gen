# ADR 0013: Brief schema v1, hashing and versioning

- **Status:** accepted
- **Date:** 2026-11-09
- **Deciders:** lead (D-085)

## Context

The Brief is the only product of Compass and the input of every agent below it. Approval at Gate 1 must be exact (what was approved is what is handed off), changes after approval must void only what they touch, and nothing may be "Locked" without evidence.

## Decision

1. **Schema.** `brief.json`, `schema_version` 1 (`mde_compass.domain.brief`). Dict sections (`idea`, `context`, `audience`, `experience`, `access`, `data`, `constraints`) map a catalog field name to a `BriefItem`; list sections hold typed records. Every item carries `tier`, `provenance[]`, `confidence`, `status` and `history[]`. Which keys exist is data (`catalog/slots.yaml`), not code.
2. **Tier rules (code, tested).** `Locked` only if provenance holds an evidence entry (type `user_tap`, `user_text`, `voice`, `document` or `org_profile`) with a non-empty `ref`. `assumed`, `pattern` and `model_knowledge` yield `Default` (value present) or `Open`. A Locked item without evidence is rejected by the validator.
3. **Stable ids** (`APP-`, `ROL-`, `FTR-`, `ASM-`, `SRC-`, `RR-`, `Q-`, `CHG-`) come from an `IdLedger` stored inside the Brief: counters only increase, deletions leave tombstones, renames add aliases; an id is never reused, also across undo.
4. **Versions.** An integer `version` increments on every committed change. Saved versions are append-only; a stale writer gets `Conflict` (optimistic).
5. **Hashes.** `canonical_json` is sorted-key, compact, UTF-8, with volatile keys (`date`, `at`, `created_at`, `updated_at`) and bookkeeping (`version`, `ids`, `readiness`) removed. `brief_hash` is sha256 over the whole content (including `meta`); `section_hashes` is one sha256 per content section (`SECTION_KEYS`). **Approval binds to the whole hash and the section hashes**; a later change voids only the sections whose hash moved.
6. **Baseline.** The approved version is labelled `B-G1` once and is immutable; later edits create new versions and a "changed since approval" diff.
7. **Changes** are `ChangeSet`s of `PatchOp`s (`set`, `add`, `remove`, `clear`) addressed by one path grammar (`<section>.<key>`, `<section>.<ID>.<field>`), validated on a copy, applied as `version + 1`, revertible by applying the inverse ops as a new version. History is never rewritten.

## Alternatives considered

- **One hash only.** Any edit would void the whole approval.
- **Hash the raw JSON.** Key order and timestamps would change hashes without changing content.
- **Tier as a free label.** Cannot be enforced; evidence has to be structural.

## Consequences

- Hash stability is covered by tests (key order, volatile fields, bookkeeping do not move hashes; a changed section moves only its own hash).
- `meta` is inside the whole hash, so changing the situation profile changes it; sections stay stable.
