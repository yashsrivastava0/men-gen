"""``cmp.scan_ambiguity``: vague, conflicting or undefined statements in the Brief digest (findings only)."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.scan_ambiguity.fallback import fallback
from mde_compass.calls.scan_ambiguity.schema import ScanInput, ScanOutput
from mde_compass.calls.scan_ambiguity.validate import validate_output
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.scan_ambiguity"
_P = CALL_PROFILES[CALL_ID]

SPEC: CompassCallSpec[ScanInput, ScanOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=2,
        input_model=ScanInput,
        output_model=ScanOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.scan_ambiguity.mock:handle",
        tools=(),
        untrusted_inputs=("items", "glossary_terms", "facts"),
        cacheable=False,
        repair=True,
        fallback="compass.scan_ambiguity",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(validate_output,),
)
