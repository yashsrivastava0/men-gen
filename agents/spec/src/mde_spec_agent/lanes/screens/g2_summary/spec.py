"""spec.g2_summary: one paragraph for the G2 page. Route `fast`; no tools; the lines carry the app's own words, so they are
untrusted data. If the call fails the page uses the TEMPLATE summary from code (FN-29)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.screens.common import schema_version, text_version
from mde_spec_agent.lanes.screens.g2_summary.fallback import fallback
from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out
from mde_spec_agent.lanes.screens.g2_summary.validate import validate

CALL_ID = "spec.g2_summary"
VERSION = 1
HERE = Path(__file__).parent

SPEC: SpecCallSpec[G2In, G2Out] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=G2In,
        output_model=G2Out,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=HERE,
        route="fast",
        reasoning_effort="low",
        max_output_tokens=1500,
        mock_handler="mde_spec_agent.lanes.screens.g2_summary.mock:handle",
        untrusted_inputs=("lines",),
        cacheable=False,
        repair=True,
        fallback="spec.g2_summary",
    ),
    kind="G2-SUMMARY",
    timeout_s=90.0,
    fallback=fallback,
    validators=(validate,),
)


def prompt_version() -> str:
    return text_version(HERE / "system.jinja", HERE / "user.jinja")


def out_schema_version() -> str:
    return schema_version(G2Out)
