"""C9c.design_tokens: proposals that tune the design tokens (colors, corners, type) and imagery rules."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C9cInput, C9cOutput
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C9cInput, C9cOutput] = CallSpec(
    id="C9c.design_tokens",
    version=2,
    input_model=C9cInput,
    output_model=C9cOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=1000,
    mock_handler="mde_prompts.calls.c9c_design_tokens.mock:handle",
    cacheable=False,
    repair=True,
    fallback="builder.no_proposals",
)
