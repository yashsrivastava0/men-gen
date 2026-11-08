"""C7.extract: facts from one search result (spec 4.2). No tools; the text is untrusted data.

``search_text`` is the only untrusted field. It arrives inside an ``<untrusted_content>`` block
(stripped of hidden characters, size capped) and the system prompt carries the gateway's security
notice. The research engine validates the output again in code (source urls, length, instruction
look-alikes) and drops every fact of a result when ``injection_suspected`` is set.
"""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C7Input, C7Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C7Input, C7Output] = CallSpec(
    id="C7.extract",
    version=1,
    input_model=C7Input,
    output_model=C7Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=1200,
    mock_handler="mde_prompts.calls.c7_extract.mock:handle",
    tools=(),
    untrusted_inputs=("search_text",),
    cacheable=True,
    repair=True,
    fallback="c7_no_facts",
)
