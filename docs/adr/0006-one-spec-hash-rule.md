# ADR 0006: One spec hash rule, one implementation

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (D-061; found by the end-to-end test harness)

## Context

`APP_SPEC.md` embeds its own hash in its front matter, and approvals bind to that hash. Two packages had each defined the rule: the loader and the builder hashed the file text with the `hash:` value blanked (the only rule that can work when the file contains its own hash), while the flow required plain `sha256(text)`. With the real builder, **no session could ever reach Gate 2** (a blocker). Every flow unit test passed because the flow tests used a fake builder written to the flow's rule: the producer and the consumer shared a wrong assumption, and only the end-to-end harness, which ran the real builder into the real flow, noticed.

## Decision

1. **The rule.** The spec hash is the sha256 (UTF-8, LF newlines) of the file text with the front-matter line `hash: "<hex>"` replaced by `hash: ""`. The file embeds the result as `hash: "<64 hex>"`.
2. **One implementation.** `mde_spec_loader.hashing.compute_spec_hash` (with `embed_hash`, `verify_hash`, `blank_hash`) is the only code that defines it. The renderer in the builder, the loader's `verify`, the flow's `gates.assert_handoff_allowed` and `service._check_build` all call it. The hand-off writes `APP_SPEC.md` byte for byte (the hand-off repository's `.gitattributes` pins `-text`) so `compute_spec_hash(file)` equals the recorded hash. The API's `X-Spec-Hash` header carries the same value.
3. **One real test across the seam.** A regression test renders a real spec with the real renderer and runs it through the flow's checks; the strict `xfail` pin on the bug passed and was removed.
4. **The general rule** this teaches: *any contract between two packages needs one shared implementation, and at least one test must run the real producer into the real consumer*, not a fake that shares the consumer's assumption.

## Alternatives considered

- **Make the builder and loader use plain `sha256(text)`.** Impossible: the text contains the hash, so the value would depend on itself.
- **Keep two rules with an adapter.** The two would drift again; the adapter is the bug.
- **Let the flow recompute the hash its own way** (what happened). It is exactly the failure above.
- **Put the rule in `mde_contracts`.** Contracts holds no parsing; the front-matter scanner lives in the loader, which is a foundation package the flow may import under the layering.

## Consequences

- `mde_flow` imports `mde_spec_loader.hashing`, and nothing else from the loader (allowed by the layering, see [repo-layout](../architecture/repo-layout.md)).
- Docs that said "sha256 of `spec_text`" were corrected (data model, flow page, API page). Two stale code strings remain for the lead: the OpenAPI description of `GET /spec` and the comment on `BuildResult.spec_hash`.
- The same lesson applies to every seam: ids and `seq` of events, the dependency map, policy fields, hand-off paths. New seams get a shared function and a real producer-to-consumer test.

## References

[flow-and-gates](../architecture/flow-and-gates.md), [app-spec-format](../specs/app-spec-format.md), [spec-loader](../packages/spec-loader.md), [eval plan, BUG-1](../evals/eval-plan.md), DECISIONS D-061.
