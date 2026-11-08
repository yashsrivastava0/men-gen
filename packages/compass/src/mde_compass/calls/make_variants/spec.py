"""``cmp.make_variants``: the call spec (gateway ``CallSpec`` plus lane, timeout, validators, fallback)."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.make_variants.fallback import fallback
from mde_compass.calls.make_variants.schema import VariantsIn, VariantsOut
from mde_compass.calls.make_variants.validate import check_variants
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.make_variants"
_PROFILE = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[VariantsIn, VariantsOut] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=VariantsIn,
        output_model=VariantsOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_PROFILE.route,
        reasoning_effort=_PROFILE.effort,
        max_output_tokens=_PROFILE.max_output_tokens,
        mock_handler="mde_compass.calls.make_variants.mock:handle",
        untrusted_inputs=("reason", "current_value_json", "context"),
        cacheable=False,
        repair=True,
        fallback="compass.make_variants",
    ),
    lane=_PROFILE.lane,
    timeout_s=_PROFILE.timeout_s,
    fallback=fallback,
    validators=(check_variants,),
)
