"""spec.copy_l: locale passes. Route `fast`; no tools; the source copy and glossary derive from the user's words, so they
are untrusted data. At most config.fusion.strings_per_locale_call strings per call."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.screens.common import schema_version, text_version
from mde_spec_agent.lanes.screens.copy_l.fallback import fallback
from mde_spec_agent.lanes.screens.copy_l.schema import CopyLIn, CopyLOut
from mde_spec_agent.lanes.screens.copy_l.validate import validate

CALL_ID = "spec.copy_l"
VERSION = 1
HERE = Path(__file__).parent

SPEC: SpecCallSpec[CopyLIn, CopyLOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=CopyLIn,
        output_model=CopyLOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=HERE,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=32000,
        mock_handler="mde_spec_agent.lanes.screens.copy_l.mock:handle",
        untrusted_inputs=("glossary", "facts", "strings"),
        cacheable=False,
        repair=True,
        fallback="spec.copy_l",
    ),
    kind="COPY-L",
    timeout_s=240.0,
    fallback=fallback,
    validators=(validate,),
)


def prompt_version() -> str:
    return text_version(HERE / "system.jinja", HERE / "user.jinja")


def out_schema_version() -> str:
    return schema_version(CopyLOut)
