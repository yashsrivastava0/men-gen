"""spec.dsgn: the direction call. Route `standard`; no tools; the Brief's words and names are untrusted data.
The route is a name, never a model: switching vendors is configuration (hard rule 14)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.design.dsgn.fallback import fallback
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn, DsgnOut
from mde_spec_agent.lanes.design.dsgn.validate import validate

CALL_ID = "spec.dsgn"
VERSION = 1
MAX_OUTPUT_TOKENS = 6000

SPEC: SpecCallSpec[DsgnIn, DsgnOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=DsgnIn,
        output_model=DsgnOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        mock_handler="mde_spec_agent.lanes.design.dsgn.mock:handle",
        untrusted_inputs=("brief", "audience", "apps", "entities"),
        cacheable=False,
        repair=True,
        fallback="spec.dsgn",
    ),
    kind="DSGN",
    timeout_s=120.0,
    fallback=fallback,
    validators=(validate,),
)
