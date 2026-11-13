"""ver.structure_critic: the Freeze critic's model half. Route `standard`; no tools; the Brief, the facts and the
digest are untrusted data. The route is a name, never a model: switching vendors is configuration."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_core.verify_port import PortCallSpec
from mde_verify_agent.calls.structure_critic.fallback import fallback
from mde_verify_agent.calls.structure_critic.schema import StructureIn, StructureOut
from mde_verify_agent.calls.structure_critic.validate import validate

CALL_ID = "ver.structure_critic"

SPEC = PortCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=StructureIn,
        output_model=StructureOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=6000,
        mock_handler="mde_verify_agent.calls.structure_critic.mock:handle",
        untrusted_inputs=("brief", "facts", "digest", "code_findings"),
        cacheable=False,
        repair=True,
        fallback="verify.structure_critic",
    ),
    kind="VERIFY-STRUCTURE",
    timeout_s=120.0,
    fallback=fallback,
    validators=(validate,),
)
