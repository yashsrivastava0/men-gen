"""The ``cmp.rephrase_question`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.rephrase_question.fallback import wire_fallback
from mde_compass.calls.rephrase_question.schema import RephraseInput, RephraseOutput
from mde_compass.calls.rephrase_question.validate import structural
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.rephrase_question"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[RephraseInput, RephraseOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=RephraseInput,
        output_model=RephraseOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.rephrase_question.mock:handle",
        # the person's own recent words are data, never instructions
        untrusted_inputs=("recent_user_words",),
        cacheable=False,
        repair=True,
        fallback="compass.rephrase_question",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
