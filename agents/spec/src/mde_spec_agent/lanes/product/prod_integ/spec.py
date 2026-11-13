"""spec.prod_integ: PROD-INTEG, batches of integration needs. Route `standard`; no tools; the needs, the Brief's facts and
the dossiers are untrusted data."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

from .fallback import fallback
from .schema import IntegIn, IntegOut
from .validate import validate

CALL_ID = "spec.prod_integ"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"

SPEC: SpecCallSpec[IntegIn, IntegOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=IntegIn,
        output_model=IntegOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=8000,
        mock_handler="mde_spec_agent.lanes.product.prod_integ.mock:handle",
        untrusted_inputs=("slice", "facts", "research"),
        cacheable=False,
        repair=True,
        fallback="spec.prod_integ",
    ),
    kind="PROD-INTEG",
    timeout_s=150.0,
    fallback=fallback,
    validators=(validate,),
)
