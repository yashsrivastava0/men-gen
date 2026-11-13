# mde_verify_agent (Verify)

Checks deep Spec packs and returns findings and structured patches. It only checks: it never writes into the graph or the pack. Spec applies the patches (single writer) and re-verifies touched nodes by hash.

- **Meeting point:** the `VerifyPort` in `mde_spec_core.verify_port`. Spec loads `mde_verify_agent.port:build_verify_port` through the entry point group `mde.spec.verify` (`load_verify_port`); the two packages never import each other. Verify reaches a model only through the `ModelCall` Spec injects (Spec's runner enforces the money cap and the one-re-ask rule).
- **Pure code (`checks.py`, zero model cost):** validator findings as defects with repair classes A/B/C; the code half of the structure critic (structure validators, role and entity mismatch, cross-app lifecycle disagreement, a Locked fact dropped or changed); the confirm list's ranking; the canary's missing-id reading.
- **Three model calls (`calls/<name>/`, ADR 0014 layout):** `structure_critic` (route `standard`), `canary` (route `fast`, the weakest consumer, reads exactly one task card's reads, not code), `final_critic` (route `standard`). Single, schema-validated, tool-free; Brief quotes, research and pack text are untrusted data. Routes are names; no vendor or model appears here.
- **Repair (`repair.py`):** at most `config.verify.max_llm_rounds` (2) rounds across the Freeze and the final pass, one model call per phase; class A patches are free (Spec applies them in at most 3 converging passes); B/C patches need an open round and a node that has not used its one Verify repair; otherwise a blocker leaves the node `degraded` (listed at G2).
- **Evidence (`evidence.py`):** `evidence_json` for `evidence/verify.json`; `manifest_fields` gives `checked_by: [self_check, verify]` and `critic_vendor` (`same` unless the gateway said `different`).

Test: `uv run pytest agents/verify` (offline; the test double runs every call through the real gateway pipeline in mock mode).
