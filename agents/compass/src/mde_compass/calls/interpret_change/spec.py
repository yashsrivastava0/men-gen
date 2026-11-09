"""``cmp.interpret_change``: the call spec (gateway ``CallSpec`` plus lane, timeout, validators, fallback)."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.interpret_change.fallback import fallback
from mde_compass.calls.interpret_change.schema import ChangeIn, ChangeOut
from mde_compass.calls.interpret_change.validate import check_change
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.interpret_change"
_PROFILE = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[ChangeIn, ChangeOut] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=ChangeIn,
        output_model=ChangeOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_PROFILE.route,
        reasoning_effort=_PROFILE.effort,
        max_output_tokens=_PROFILE.max_output_tokens,
        mock_handler="mde_compass.calls.interpret_change.mock:handle",
        untrusted_inputs=("text", "history", "items"),
        cacheable=False,
        repair=True,
        fallback="compass.interpret_change",
    ),
    lane=_PROFILE.lane,
    timeout_s=_PROFILE.timeout_s,
    fallback=fallback,
    validators=(check_change,),
)
