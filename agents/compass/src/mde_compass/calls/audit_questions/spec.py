"""The ``cmp.audit_questions`` call spec."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.audit_questions.fallback import wire_fallback
from mde_compass.calls.audit_questions.schema import AuditInput, AuditOutput
from mde_compass.calls.audit_questions.validate import structural
from mde_compass.calls.base import CompassCallSpec
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.audit_questions"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[AuditInput, AuditOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=3,
        input_model=AuditInput,
        output_model=AuditOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.audit_questions.mock:handle",
        # the earlier-questions list carries the person's words; the questions under review are model text
        # that the system prompt also declares to be data
        untrusted_inputs=("asked_list",),
        cacheable=False,
        repair=True,
        fallback="compass.audit_questions",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
