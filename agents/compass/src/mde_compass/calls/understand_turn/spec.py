"""The ``cmp.understand_turn`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.understand_turn.fallback import fallback
from mde_compass.calls.understand_turn.schema import UnderstandInput, UnderstandOutput
from mde_compass.calls.understand_turn.validate import no_questions
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.understand_turn"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[UnderstandInput, UnderstandOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=2,
        input_model=UnderstandInput,
        output_model=UnderstandOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.understand_turn.mock:handle",
        # everything that carries the user's words, or text derived from them, is data and never instructions
        untrusted_inputs=(
            "text",
            "recent_turns",
            "fact_digest",
            "focus_question",
            "focus_intent",
            "focus_selected_labels",
        ),
        cacheable=False,
        repair=True,
        fallback="compass.understand_turn",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(no_questions,),
)
