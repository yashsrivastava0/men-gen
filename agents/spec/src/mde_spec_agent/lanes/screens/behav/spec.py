"""spec.behav: app-specific global behaviours. Route `standard`; no tools; facts, glossary and the app's own records are
untrusted data."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.screens.behav.fallback import fallback
from mde_spec_agent.lanes.screens.behav.schema import BehavIn, BehavOut
from mde_spec_agent.lanes.screens.behav.validate import validate
from mde_spec_agent.lanes.screens.common import schema_version, text_version

CALL_ID = "spec.behav"
VERSION = 1
HERE = Path(__file__).parent

SPEC: SpecCallSpec[BehavIn, BehavOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=BehavIn,
        output_model=BehavOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=HERE,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=8000,
        mock_handler="mde_spec_agent.lanes.screens.behav.mock:handle",
        untrusted_inputs=(
            "facts",
            "glossary",
            "categories",
            "integrations",
            "roles",
            "entities",
            "rules",
        ),
        cacheable=False,
        repair=True,
        fallback="spec.behav",
    ),
    kind="BEHAV",
    timeout_s=180.0,
    fallback=fallback,
    validators=(validate,),
)


def prompt_version() -> str:
    return text_version(HERE / "system.jinja", HERE / "user.jinja")


def out_schema_version() -> str:
    return schema_version(BehavOut)
