"""C9a.screens_nav: proposals for the screens and navigation of the baseline (add a screen, rename, link)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C9aInput, C9aOutput
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C9aInput, C9aOutput] = CallSpec(
    id="C9a.screens_nav",
    version=2,
    input_model=C9aInput,
    output_model=C9aOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=3000,
    mock_handler="mde_prompts.calls.c9a_screens_nav.mock:handle",
    cacheable=False,
    repair=True,
    fallback="builder.no_proposals",
)
