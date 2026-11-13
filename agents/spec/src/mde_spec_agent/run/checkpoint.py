"""Checkpoint keys (doc 15 8.4, doc 18 4.2): a batch is checkpointed under
(program, input hash, step, batch) and the key ALSO carries the call-spec, prompt, schema and library versions and the
route fingerprint, so a deployment in the middle of a run never mixes versions.
"""

from __future__ import annotations

from dataclasses import dataclass

from mde_spec_core.callkit import checkpoint_key


@dataclass(frozen=True)
class Versions:
    call_spec: str  # e.g. "spec.plan@1"
    prompt: str
    schema: str
    library: str
    route_fingerprint: str  # opaque; Spec never sees what it hashes


def key_for(program: str, input_hash: str, step: str, batch: str, v: Versions) -> str:
    return checkpoint_key(
        program,
        input_hash,
        step,
        batch,
        {"call_spec": v.call_spec, "prompt": v.prompt, "schema": v.schema, "library": v.library},
        v.route_fingerprint,
    )
