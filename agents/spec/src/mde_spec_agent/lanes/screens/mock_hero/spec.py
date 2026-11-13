"""spec.mock_hero: the content of the hero records. Route `standard`; no tools; the entity and glossary text is the
user's, so it is untrusted data. Related entities go in the SAME call (hero.fill_plan groups them)."""

from __future__ import annotations

from pathlib import Path

from mde_contracts.gateway import CallSpec
from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.lanes.screens.common import schema_version, text_version
from mde_spec_agent.lanes.screens.mock_hero.fallback import fallback
from mde_spec_agent.lanes.screens.mock_hero.schema import HeroIn, HeroOut
from mde_spec_agent.lanes.screens.mock_hero.validate import validate

CALL_ID = "spec.mock_hero"
VERSION = 1
HERE = Path(__file__).parent

SPEC: SpecCallSpec[HeroIn, HeroOut] = SpecCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=VERSION,
        input_model=HeroIn,
        output_model=HeroOut,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=HERE,
        route="standard",
        reasoning_effort="low",
        max_output_tokens=16000,
        mock_handler="mde_spec_agent.lanes.screens.mock_hero.mock:handle",
        untrusted_inputs=("facts", "glossary", "entities", "context", "allowed_names"),
        cacheable=False,
        repair=True,
        fallback="spec.mock_hero",
    ),
    kind="MOCK-HERO",
    timeout_s=240.0,
    fallback=fallback,
    validators=(validate,),
)


def prompt_version() -> str:
    return text_version(HERE / "system.jinja", HERE / "user.jinja")


def out_schema_version() -> str:
    return schema_version(HeroOut)
