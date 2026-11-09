"""The ``cmp.write_card`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.write_card.fallback import fallback
from mde_compass.calls.write_card.schema import WriteCardInput, WriteCardOutput
from mde_compass.calls.write_card.validate import structural
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.write_card"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[WriteCardInput, WriteCardOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=WriteCardInput,
        output_model=WriteCardOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.write_card.mock:handle",
        untrusted_inputs=("evidence", "hints", "known_slots"),
        cacheable=False,
        repair=True,
        fallback="compass.write_card",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(structural,),
)
