"""C1.understand: read one typed message, return intent, slot updates, references and flags."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C1Input, C1Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C1Input, C1Output] = CallSpec(
    id="C1.understand",
    version=1,
    input_model=C1Input,
    output_model=C1Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=600,
    mock_handler="mde_prompts.calls.c1_understand.mock:handle",
    untrusted_inputs=("attachment_text",),
    cacheable=False,
    repair=True,
    fallback="discovery.keyword_understander",
)
