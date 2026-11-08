"""S1.web_search: one hosted web search, free-text answer, the only call with a tool (brief 7.7).

The answer text and its citations are UNTRUSTED downstream: the research engine cleans them and
hands them to C7 (no tools) as untrusted input. This call itself reads nothing untrusted: its input
is a generic query built by code, so ``untrusted_inputs`` is empty.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from mde_contracts.calls import S1Input
from mde_contracts.gateway import CallSpec, WebSearchTool

SPEC: CallSpec[S1Input, BaseModel] = CallSpec(
    id="S1.web_search",
    version=1,
    input_model=S1Input,
    output_model=None,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="low",
    max_output_tokens=1500,
    mock_handler="mde_prompts.calls.s1_web_search.mock:handle",
    tools=(WebSearchTool(max_tool_calls=1, search_context_size="low"),),
    untrusted_inputs=(),
    cacheable=False,
    repair=False,
    fallback="s1_skip_query",
)
