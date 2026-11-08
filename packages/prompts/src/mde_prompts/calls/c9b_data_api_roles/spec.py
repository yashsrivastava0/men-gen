"""C9b.data_api_roles: proposals for the data, API, roles and integrations of the baseline."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C9bInput, C9bOutput
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C9bInput, C9bOutput] = CallSpec(
    id="C9b.data_api_roles",
    version=2,
    input_model=C9bInput,
    output_model=C9bOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="medium",
    max_output_tokens=8000,
    mock_handler="mde_prompts.calls.c9b_data_api_roles.mock:handle",
    cacheable=False,
    repair=True,
    fallback="builder.no_proposals",
)
