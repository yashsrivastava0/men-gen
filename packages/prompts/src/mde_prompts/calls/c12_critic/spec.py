"""C12.critic: does the spec match what the user asked? contradictions, missing flows or features."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C12Input, C12Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C12Input, C12Output] = CallSpec(
    id="C12.critic",
    version=1,
    input_model=C12Input,
    output_model=C12Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=2500,
    mock_handler="mde_prompts.calls.c12_critic.mock:handle",
    cacheable=False,
    repair=True,
    fallback="builder.critic_pass",
)
