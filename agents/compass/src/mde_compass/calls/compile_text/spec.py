"""``cmp.compile_text`` call spec (profile from ``mde_compass.config.CALL_PROFILES``)."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.compile_text.fallback import fallback
from mde_compass.calls.compile_text.schema import CompileTextInput, CompileTextOutput
from mde_compass.calls.compile_text.validators import validate
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.compile_text"
_P = CALL_PROFILES[CALL_ID]

_CORE: CallSpec[CompileTextInput, CompileTextOutput] = CallSpec(
    id=CALL_ID,
    version=2,
    input_model=CompileTextInput,
    output_model=CompileTextOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route=_P.route,
    reasoning_effort=_P.effort,
    max_output_tokens=_P.max_output_tokens,
    mock_handler="mde_compass.calls.compile_text.mock:handle",
    untrusted_inputs=("slots", "items", "details"),
    cacheable=False,
    repair=True,
    fallback="compass.compile_text",
)

SPEC: CompassCallSpec[CompileTextInput, CompileTextOutput] = CompassCallSpec(
    spec=_CORE,
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(validate,),
)
