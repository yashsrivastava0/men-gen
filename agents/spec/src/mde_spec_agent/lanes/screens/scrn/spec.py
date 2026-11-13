"""spec.scrn: the screen cards. Route `standard`; no tools; the frozen facts, the glossary and the slots carry the user's
and Research's words, so they are untrusted data. The route is a name, never a model: switching vendors is configuration."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.screens.common import schema_version, text_version
from mde_spec_agent.lanes.screens.scrn.fallback import fallback
from mde_spec_agent.lanes.screens.scrn.schema import ScrnIn, ScrnOut
from mde_spec_agent.lanes.screens.scrn.validate import validate_shape

CALL_ID = "spec.scrn"
VERSION = 1
HERE = Path(__file__).parent

SPEC: SpecCallSpec[ScrnIn, ScrnOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=ScrnIn,
        output_model=ScrnOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=HERE,
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=24000,
        mock_handler="mde_spec_agent.lanes.screens.scrn.mock:handle",
        untrusted_inputs=("facts", "glossary", "slots"),
        cacheable=False,
        repair=True,
        fallback="spec.scrn",
    ),
    kind="SCRN",
    timeout_s=240.0,
    fallback=fallback,
    validators=(validate_shape,),
)


def prompt_version() -> str:
    return text_version(HERE / "system.jinja", HERE / "user.jinja")


def out_schema_version() -> str:
    return schema_version(ScrnOut)
