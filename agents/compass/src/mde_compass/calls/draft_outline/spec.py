"""The ``cmp.draft_outline`` call spec (ADR 0017): the modules, entity names and journeys of the app."""

from __future__ import annotations

from pathlib import Path

from mde_compass.calls.base import CompassCallSpec
from mde_compass.calls.draft_outline.fallback import wire_fallback
from mde_compass.calls.draft_outline.schema import OutlineInput, OutlineOutput
from mde_compass.calls.draft_outline.validate import structural
from mde_compass.config import CALL_PROFILES, CallProfile
from mde_contracts.gateway import CallSpec

CALL_ID = "cmp.draft_outline"
# effort low, route standard: the answer is short on purpose (names only), so the serial step stays fast
_P = CALL_PROFILES.get(CALL_ID) or CallProfile(CALL_ID, "low", 4000, "writer", "standard", 55.0)

SPEC: CompassCallSpec[OutlineInput, OutlineOutput] = CompassCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=OutlineInput,
        output_model=OutlineOutput,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path(__file__).parent,
        route=_P.route,
        reasoning_effort=_P.effort,
        max_output_tokens=_P.max_output_tokens,
        mock_handler="mde_compass.calls.draft_outline.mock:handle",
        # the person's words, their documents and the features and roles written from them are data: no tools
        untrusted_inputs=(
            "project_card",
            "features",
            "roles",
            "apps",
            "integrations",
            "org",
            "fact_digest",
            "seeds",
            "pinned",
            "instruction",
        ),
        cacheable=False,
        repair=True,
        fallback="compass.draft_outline",
    ),
    lane=_P.lane,
    timeout_s=_P.timeout_s,
    fallback=wire_fallback,
    validators=(structural,),
)
