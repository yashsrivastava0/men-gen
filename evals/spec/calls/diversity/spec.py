"""proof.diversity_critic (PS-10): an optional critic of between-app diversity and within-app consistency; the owner
samples its answers. Route `standard`; no tools; the records are untrusted data."""

from __future__ import annotations

from pathlib import Path

from evals.spec.calls.diversity.mock import fallback
from evals.spec.calls.diversity.schema import DiversityIn, DiversityOut
from evals.spec.calls.diversity.validate import validate
from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

CALL_ID = "proof.diversity_critic"

SPEC: SpecCallSpec[DiversityIn, DiversityOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=DiversityIn,
        output_model=DiversityOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="low",
        max_output_tokens=900,
        mock_handler="evals.spec.calls.diversity.mock:handle",
        untrusted_inputs=("records",),
        cacheable=False,
        repair=True,
        fallback="proof.diversity",
    ),
    kind="PROOF-DIVERSITY",
    timeout_s=90.0,
    fallback=fallback,
    validators=(validate,),
)
