"""C11.write_summary: the one-sentence Brief summary (and, later, the plain-language plan summary)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C11Input, C11Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C11Input, C11Output] = CallSpec(
    id="C11.write_summary",
    version=1,
    input_model=C11Input,
    output_model=C11Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=800,
    mock_handler="mde_prompts.calls.c11_write_summary.mock:handle",
    cacheable=True,  # deterministic from the facts; the plan summary rarely repeats exactly
    repair=True,
    fallback="discovery.template_sentence",
)
