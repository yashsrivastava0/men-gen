"""``cmp.merge_claims``: dedupe, contradictions, coverage and TBD items over the statements of one source."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.merge_claims.fallback import fallback
from mde_compass.calls.merge_claims.schema import MergeInput, MergeOutput
from mde_compass.calls.merge_claims.validate import validate_output
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.merge_claims"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[MergeInput, MergeOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=MergeInput,
        output_model=MergeOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.merge_claims.mock:handle",
        tools=(),
        untrusted_inputs=("statements",),
        cacheable=False,
        repair=True,
        fallback="compass.merge_claims",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(validate_output,),
)
