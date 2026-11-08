"""C13.repair: proposals that repair the validator and critic findings (fix_reference, remove_item, ...)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.calls import C13Input, C13Output
from mde_contracts.gateway import CallSpec

SPEC: CallSpec[C13Input, C13Output] = CallSpec(
    id="C13.repair",
    version=2,  # v2 (D-083): the repair prompt may also add_entity / add_api_operation
    input_model=C13Input,
    output_model=C13Output,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route="standard",
    reasoning_effort="low",
    max_output_tokens=1800,
    mock_handler="mde_prompts.calls.c13_repair.mock:handle",
    cacheable=False,
    repair=True,
    fallback="builder.no_proposals",
)
