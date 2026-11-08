"""C10.screen_briefs: proposals that finish a batch of 3 to 4 screens (copy, sections, checks, states, actions)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C10Input, C10Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C10Input, C10Output] = CallSpec(
    id="C10.screen_briefs",
    version=2,
    input_model=C10Input,
    output_model=C10Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=4000,
    mock_handler="mde_prompts.calls.c10_screen_briefs.mock:handle",
    cacheable=False,
    repair=True,
    fallback="builder.no_proposals",
)
