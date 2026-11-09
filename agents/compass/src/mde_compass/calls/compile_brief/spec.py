"""The ``cmp.compile_brief`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.compile_brief.fallback import wire_fallback
from mde_compass.calls.compile_brief.schema import CompileInput, CompileOutput
from mde_compass.calls.compile_brief.validate import structural
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.compile_brief"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[CompileInput, CompileOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=CompileInput,
        output_model=CompileOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.compile_brief.mock:handle",
        # everything that carries the user's words (or text derived from them) is data, never instructions
        untrusted_inputs=(
            "ledger",
            "needs_table",
            "assumptions",
            "asked_list",
            "qa_compact",
            "draft",
        ),
        cacheable=False,
        repair=True,
        fallback="compass.compile_brief",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
