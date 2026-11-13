"""The ``cmp.draft_module`` call spec (ADR 0017): screens, journey segments, entity fields and acceptance lines for ONE group
of modules. One call per group, in parallel, on the standard route."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.draft_module.fallback import wire_fallback
from mde_compass.calls.draft_module.schema import ModuleInput, ModuleOutput
from mde_compass.calls.draft_module.validate import structural
from mde_compass.config import CALL_PROFILES, CallProfile
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.draft_module"
_P = CALL_PROFILES.get(CALL_ID) or CallProfile(CALL_ID, "low", 5000, "writer", "standard", 55.0)

SPEC: CompassCallSpec[ModuleInput, ModuleOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=ModuleInput,
        output_model=ModuleOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.draft_module.mock:handle",
        # the person's words, documents and the drafts built from them are data: no tools
        untrusted_inputs=(
            "project_card",
            "modules",
            "other_modules",
            "entities",
            "roles",
            "apps",
            "features",
            "journeys",
            "existing",
            "facts",
            "instruction",
            "issues",
        ),
        cacheable=False,
        repair=True,
        fallback="compass.draft_module",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
