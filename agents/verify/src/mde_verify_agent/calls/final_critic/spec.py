"""ver.final_critic: the spec against the Brief, using the coverage matrix and the freedom checks. Route `standard`;
no tools; every data block is untrusted. The route is a name, never a model."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_core.verify_port import PortCallSpec
from mde_verify_agent.calls.final_critic.fallback import fallback
from mde_verify_agent.calls.final_critic.schema import FinalIn, FinalOut
from mde_verify_agent.calls.final_critic.validate import validate

CALL_ID = "ver.final_critic"

SPEC = PortCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=FinalIn,
        output_model=FinalOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=7000,
        mock_handler="mde_verify_agent.calls.final_critic.mock:handle",
        untrusted_inputs=(
            "brief",
            "facts",
            "digest",
            "coverage",
            "freedom",
            "confirm",
            "differs",
            "code_findings",
        ),
        cacheable=False,
        repair=True,
        fallback="verify.final_critic",
    ),
    kind="VERIFY-FINAL",
    timeout_s=150.0,
    fallback=fallback,
    validators=(validate,),
)
