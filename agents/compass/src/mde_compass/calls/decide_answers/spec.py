"""The ``cmp.decide_answers`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.decide_answers.fallback import wire_fallback
from mde_compass.calls.decide_answers.schema import DecideInput, DecideOutput
from mde_compass.calls.decide_answers.validate import structural
from mde_compass.config import CALL_PROFILES, CallProfile
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.decide_answers"
# effort low, a small output cap, route standard, 30 s: the answer is short and speed matters most. ``config`` owns
# the profile (the lead adds ``_p("cmp.decide_answers", "low", 2000, "writer", "standard", 30)``); until then this one.
_P = CALL_PROFILES.get(CALL_ID) or CallProfile(CALL_ID, "low", 2000, "writer", "standard", 30.0)

SPEC: CompassCallSpec[DecideInput, DecideOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=DecideInput,
        output_model=DecideOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.decide_answers.mock:handle",
        # the person's words, the facts and the model-written questions are data, never instructions: no tools
        untrusted_inputs=("project_card", "fact_digest", "hypothesis", "items"),
        cacheable=False,
        repair=True,
        fallback="compass.decide_answers",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
