"""spec.plan_fused: SPEC-PLAN that also writes the product of a compact app, in one call. Route `deep`; no tools; the
Compass summary, the slices, the facts and the research digest are untrusted data."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

from .fallback import fallback
from .schema import FusedIn, FusedOut
from .validate import validate

CALL_ID = "spec.plan_fused"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"

SPEC: SpecCallSpec[FusedIn, FusedOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=FusedIn,
        output_model=FusedOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="deep",
        reasoning_effort="medium",
        max_output_tokens=32000,
        mock_handler="mde_spec_agent.lanes.product.plan_fused.mock:handle",
        untrusted_inputs=(
            "brief",
            "inventory",
            "release_lines",
            "module_slice",
            "facts",
            "integ_slice",
            "journey_slice",
            "research",
        ),
        cacheable=False,
        repair=True,
        fallback="spec.plan_fused",
    ),
    kind="SPEC-PLAN",
    timeout_s=300.0,
    fallback=fallback,
    validators=(validate,),
)
