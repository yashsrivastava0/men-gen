"""``SituateAdapter``: the engine's ``Situator`` port over the ``cmp.situate`` call."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.situate.schema import FamilyWire, SituateInput, SituateOutput, StatementWire
from mde_compass.calls.situate.spec import SPEC
from mde_compass.calls.situate.validate import MAX_STATEMENTS, sanitize
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.claims import Statement
from mde_compass.domain.situation import (
    DomainHypothesis,
    GlossaryCandidate,
    HypothesisFeature,
    SituationProfile,
)
from mde_compass.engine.ports import SituationResult, TurnContext
from mde_core.redact import redact_text

MAX_KEYWORDS = 12
MAX_FEATURES_PER_FAMILY = 6


def families_wire(catalog: Catalog, language: str) -> list[FamilyWire]:
    lang = "hi" if language == "hi" else "en"
    return [
        FamilyWire(
            id=f.id,
            label=f.label.get(lang),
            keywords=f.keywords[:MAX_KEYWORDS],
            typical_features=[
                t.name.get(lang) for t in f.typical_features[:MAX_FEATURES_PER_FAMILY]
            ],
        )
        for f in catalog.families.values()
    ]


def build_input(
    catalog: Catalog, ctx: TurnContext, user_text: str, statements: list[Statement] | None
) -> SituateInput:
    return SituateInput(
        user_text=redact_text(user_text)[0],
        statements=[
            StatementWire(type=s.type, text=redact_text(s.text)[0])
            for s in (statements or [])[:MAX_STATEMENTS]
        ],
        language=ctx.language,
        mode=ctx.mode,
        input_mode=ctx.profile.input_mode if ctx.profile else "text",
        families=families_wire(catalog, ctx.language),
    )


def to_result(out: SituateOutput) -> SituationResult:
    p, h = out.profile, out.hypothesis
    return SituationResult(
        profile=SituationProfile(**p.model_dump()),
        hypothesis=DomainHypothesis(
            label="hypothesis",
            category=h.category,
            subcategory=h.subcategory,
            business_model_candidates=h.business_model_candidates,
            likely_families=h.likely_families,
            likely_roles=h.likely_roles,
            likely_features=[HypothesisFeature(**f.model_dump()) for f in h.likely_features],
            hard_parts=h.hard_parts,
            glossary_candidates=[
                GlossaryCandidate(**t.model_dump()) for t in h.glossary_candidates
            ],
            confidence=h.confidence,
        ),
        one_line_idea=out.one_line_idea.strip(),
    )


class SituateAdapter:
    """Implements ``mde_compass.engine.ports.Situator``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def situate(
        self, ctx: TurnContext, *, user_text: str, statements: list[Statement] | None = None
    ) -> SituationResult:
        wire = build_input(self._catalog, ctx, user_text, statements)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out, _notes = sanitize(wire, result.output, self._catalog)
        return to_result(out)
