"""``cmp.seed_research`` call spec (profile from ``mde_compass.config.CALL_PROFILES``)."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.seed_research.fallback import fallback
from mde_compass.calls.seed_research.rules import validate
from mde_compass.calls.seed_research.schema import SeedResearchInput, SeedResearchOutput
from mde_compass.config import CALL_PROFILES
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.seed_research"
_P = CALL_PROFILES[CALL_ID]

_CORE: CallSpec[SeedResearchInput, SeedResearchOutput] = CallSpec(
    id=CALL_ID,
    version=1,
    input_model=SeedResearchInput,
    output_model=SeedResearchOutput,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=Path(__file__).parent,
    route=_P.route,
    reasoning_effort=_P.effort,
    max_output_tokens=_P.max_output_tokens,
    mock_handler="mde_compass.calls.seed_research.mock:handle",
    untrusted_inputs=(
        "category",
        "subcategory",
        "geography",
        "audience",
        "business_model",
        "references",
        "glossary",
        "exclusions",
        "unknowns",
        "features",
    ),
    cacheable=False,
    repair=True,
    fallback="compass.seed_research",
)

SPEC: CompassCallSpec[SeedResearchInput, SeedResearchOutput] = CompassCallSpec(
    spec=_CORE,
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=fallback,
    validators=(validate,),
)
