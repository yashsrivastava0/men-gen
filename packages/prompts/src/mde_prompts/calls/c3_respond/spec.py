"""C3.respond: a short, warm reply to a question or chit-chat, from the knowledge base only."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C3Input, C3Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C3Input, C3Output] = CallSpec(
    id="C3.respond",
    version=1,
    input_model=C3Input,
    output_model=C3Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="low",
    max_output_tokens=500,
    mock_handler="mde_prompts.calls.c3_respond.mock:handle",
    cacheable=False,
    repair=True,
    fallback="discovery.canned_reply",
)
