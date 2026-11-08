"""C8.synthesize_research: Research Note drafts from the extracted, cross-checked facts (spec 4.2).

Large-tier work in the spec, kept on the standard route with medium effort (starting value, tune with
evals). The facts originate from web text, so the whole ``facts`` list is rendered inside untrusted
blocks (second-order injection defence); the call has no tools. The engine validates the output in
code: every fact id must exist, urls must come from the facts, ``verified`` is inherited from the
facts, ids (``RA-###``) are assigned by code.
"""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C8Input, C8Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C8Input, C8Output] = CallSpec(
    id="C8.synthesize_research",
    version=1,
    input_model=C8Input,
    output_model=C8Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=4000,
    mock_handler="mde_prompts.calls.c8_synthesize_research.mock:handle",
    tools=(),
    untrusted_inputs=("facts",),
    cacheable=False,
    repair=True,
    fallback="c8_deterministic_suggestions",
)
