"""spec.prod_journeys: PROD-JOURNEYS, about `flows_per_journey_call` flows per call. Route `standard`; no tools; flows,
screens and the Brief's facts are untrusted data."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

from .fallback import fallback
from .schema import JourneysIn, JourneysOut
from .validate import validate

CALL_ID = "spec.prod_journeys"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"

SPEC: SpecCallSpec[JourneysIn, JourneysOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=JourneysIn,
        output_model=JourneysOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=12000,
        mock_handler="mde_spec_agent.lanes.product.prod_journeys.mock:handle",
        untrusted_inputs=("slice", "facts", "research"),
        cacheable=False,
        repair=True,
        fallback="spec.prod_journeys",
    ),
    kind="PROD-JOURNEYS",
    timeout_s=150.0,
    fallback=fallback,
    validators=(validate,),
)
