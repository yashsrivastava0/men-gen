"""The ``cmp.review_structure`` call spec (ADR 0017): a findings-only critic of the drafted structure. It can only REPORT:
code keeps a finding only when it cites a real id and a real fact, and the engine turns the rest into repair issues."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.review_structure.fallback import wire_fallback
from mde_compass.calls.review_structure.schema import ReviewInput, ReviewOutput
from mde_compass.calls.review_structure.validate import structural
from mde_compass.config import CALL_PROFILES, CallProfile
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.review_structure"
_P = CALL_PROFILES.get(CALL_ID) or CallProfile(CALL_ID, "low", 2500, "critic", "standard", 45.0)

SPEC: CompassCallSpec[ReviewInput, ReviewOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=ReviewInput,
        output_model=ReviewOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.review_structure.mock:handle",
        # the structure is model-written from the person's words, the facts are theirs: data, no tools
        untrusted_inputs=("outline", "facts", "permissions"),
        cacheable=False,
        repair=True,
        fallback="compass.review_structure",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
