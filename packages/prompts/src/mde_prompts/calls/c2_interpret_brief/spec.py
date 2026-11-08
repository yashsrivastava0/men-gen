"""C2.interpret_brief: classify the first idea (or a long upload) and flag sensitive domains."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C2Input, C2Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C2Input, C2Output] = CallSpec(
    id="C2.interpret_brief",
    version=1,
    input_model=C2Input,
    output_model=C2Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="low",
    max_output_tokens=900,
    mock_handler="mde_prompts.calls.c2_interpret_brief.mock:handle",
    untrusted_inputs=("document_text",),
    cacheable=True,
    repair=True,
    fallback="discovery.keyword_archetype_match",
)
