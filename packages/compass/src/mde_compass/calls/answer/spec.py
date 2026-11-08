"""``cmp.answer``: the call spec (gateway ``CallSpec`` plus lane, timeout, validators, fallback)."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.answer.fallback import fallback
from mde_compass.calls.answer.schema import AnswerInput, AnswerOutput
from mde_compass.calls.answer.validate import check_answer
from mde_compass.calls.base import CompassCallSpec
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.answer"
_PROFILE = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[AnswerInput, AnswerOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=AnswerInput,
        output_model=AnswerOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_PROFILE.route,
        reasoning_effort=_PROFILE.effort,
        max_output_tokens=_PROFILE.max_output_tokens,
        mock_handler="mde_compass.calls.answer.mock:handle",
        untrusted_inputs=("question", "slots"),
        cacheable=False,
        repair=True,
        fallback="compass.answer",
    ),
    lane=_PROFILE.lane,
    timeout_s=_PROFILE.timeout_s,
    fallback=fallback,
    validators=(check_answer,),
)
