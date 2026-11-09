"""The ``cmp.plan_questions`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.plan_questions.fallback import wire_fallback
from mde_compass.calls.plan_questions.schema import PlanInput, PlanOutput
from mde_compass.calls.plan_questions.validate import structural
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.plan_questions"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[PlanInput, PlanOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=4,
        input_model=PlanInput,
        output_model=PlanOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.plan_questions.mock:handle",
        # everything that carries the user's or a document's words is data, never instructions
        untrusted_inputs=(
            "project_card",
            "fact_digest",
            "asked_list",
            "last_qa",
            "focus_answer",
            "closing",
        ),
        cacheable=False,
        repair=True,
        fallback="compass.plan_questions",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
