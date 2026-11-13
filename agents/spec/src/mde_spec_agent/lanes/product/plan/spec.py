"""spec.plan: SPEC-PLAN, route `deep`. No tools; the Compass summary, the screen inventory, the release plan and the
research digest are untrusted data (hard rule 5). The one call with no safe default."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec

from .fallback import fallback
from .schema import PlanIn, PlanOut
from .validate import validate

CALL_ID = "spec.plan"
PROMPT_VERSION = "1"
SCHEMA_VERSION = "1"

SPEC: SpecCallSpec[PlanIn, PlanOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=PlanIn,
        output_model=PlanOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="deep",
        reasoning_effort="medium",
        max_output_tokens=16000,
        mock_handler="mde_spec_agent.lanes.product.plan.mock:handle",
        untrusted_inputs=("brief", "inventory", "release_lines", "research"),
        cacheable=False,
        repair=True,
        fallback="spec.plan",
    ),
    kind="SPEC-PLAN",
    timeout_s=240.0,
    fallback=fallback,
    validators=(validate,),
)
