"""proof.lookup_probe (PS-10): the weakest configured consumer maps needs to the public catalogue of a component library
and declares gaps. Route `fast`; no tools; the catalogue and the needs are untrusted data."""

from __future__ import annotations

from pathlib import Path

from evals.spec.calls.lookup.mock import fallback
from evals.spec.calls.lookup.schema import LookupIn, LookupOut
from evals.spec.calls.lookup.validate import validate
from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

CALL_ID = "proof.lookup_probe"

SPEC: SpecCallSpec[LookupIn, LookupOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=LookupIn,
        output_model=LookupOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=6000,
        mock_handler="evals.spec.calls.lookup.mock:handle",
        untrusted_inputs=("catalogue", "items"),
        cacheable=False,
        repair=True,
        fallback="proof.lookup",
    ),
    kind="PROOF-LOOKUP",
    timeout_s=120.0,
    fallback=fallback,
    validators=(validate,),
)
