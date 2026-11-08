"""C14.interpret_change: turn a change request or a gate comment into patch operations."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C14Input, C14Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C14Input, C14Output] = CallSpec(
    id="C14.interpret_change",
    version=1,
    input_model=C14Input,
    output_model=C14Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=700,
    mock_handler="mde_prompts.calls.c14_interpret_change.mock:handle",
    cacheable=False,
    repair=True,
    fallback="discovery.use_the_buttons",
)
