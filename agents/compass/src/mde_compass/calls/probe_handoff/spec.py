"""``cmp.probe_handoff`` call spec (profile from ``mde_compass.config.CALL_PROFILES``).

One spec for the three probes; the input field ``kind`` selects the planner. Pass or fail is decided by
code (``rules.evaluate``) from the plan, so the spec has no output validator: a failing plan is a result.
"""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.probe_handoff.fallback import fallback
from mde_compass.calls.probe_handoff.schema import ProbeHandoffInput, ProbeHandoffOutput
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.probe_handoff"
_P = CALL_PROFILES[CALL_ID]

_CORE: CallSpec[ProbeHandoffInput, ProbeHandoffOutput] = CallSpec(
    id=CALL_ID,
    version=2,
    input_model=ProbeHandoffInput,
    output_model=ProbeHandoffOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route=_P.route,
    reasoning_effort=_P.effort,
    max_output_tokens=_P.max_output_tokens,
    mock_handler="mde_compass.calls.probe_handoff.mock:handle",
    untrusted_inputs=("digest",),
    cacheable=False,
    repair=True,
    fallback="compass.probe_handoff",
)

SPEC: CompassCallSpec[ProbeHandoffInput, ProbeHandoffOutput] = CompassCallSpec(
    spec=_CORE,
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(),
)
