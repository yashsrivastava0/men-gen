"""C5.write_card: write one question card when the bank has none for a slot."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C5Input, C5Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C5Input, C5Output] = CallSpec(
    id="C5.write_card",
    version=1,
    input_model=C5Input,
    output_model=C5Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="low",
    max_output_tokens=700,
    mock_handler="mde_prompts.calls.c5_write_card.mock:handle",
    cacheable=False,
    repair=True,
    fallback="discovery.options_card",
)
