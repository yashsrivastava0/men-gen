"""The ``cmp.situate`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.situate.fallback import fallback
from mde_compass.calls.situate.schema import SituateInput, SituateOutput
from mde_compass.calls.situate.validate import structural
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.situate"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[SituateInput, SituateOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=SituateInput,
        output_model=SituateOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.situate.mock:handle",
        untrusted_inputs=("user_text", "statements"),
        cacheable=False,
        repair=True,
        fallback="compass.situate",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(structural,),
)
