"""The ``cmp.update_summary`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.update_summary.fallback import fallback
from mde_compass.calls.update_summary.schema import SummaryInput, SummaryOutput
from mde_compass.calls.update_summary.validate import structural
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.update_summary"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[SummaryInput, SummaryOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=SummaryInput,
        output_model=SummaryOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.update_summary.mock:handle",
        # the facts and the earlier summary are the person's words, shortened: data, never instructions
        untrusted_inputs=("previous_topics", "new_facts_digest"),
        cacheable=False,
        repair=True,
        fallback="compass.update_summary",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(structural,),
)
