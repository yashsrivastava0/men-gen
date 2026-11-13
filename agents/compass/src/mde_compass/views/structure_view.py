"""The structure page (ADR 0017): the records of the Brief laid out for a person to review, and the pieces other views
reuse (module rows for the Brief page, the structure block of the consumer views and the export).

Pure functions of a ``Brief`` and a catalog: no I/O, no turn. Names of roles, features, apps and screens are the person's
own words as stored; nothing leaks provenance, quotes or history (the consumer views use ``derive.values``, this page shows
only ``tier`` and ``decision`` because the person is reviewing).
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog
from mde_compass.catalog.size_bands import BandId, get_band
from mde_compass.domain.brief import Brief
from mde_compass.engine import structure_actions as actions
from mde_compass.engine import structure_checks as checks
from mde_compass.engine import structure_derive as derive
from mde_compass.views.models import (
    StructureAccessView,
    StructureBandView,
    StructureCounts,
    StructureEntityUse,
    StructureEntityView,
    StructureFieldView,
    StructureFlowView,
    StructureIssueView,
    StructureMatrix,
    StructureMatrixRow,
    StructureModuleView,
    StructureScreenView,
    StructureStepView,
    StructureViewModel,
)

MAX_ISSUES = 40


def _names(brief: Brief) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    roles = {r.id: r.name_in_user_words for r in brief.roles}
    features = {f.id: f.name for f in brief.scope}
    apps = {a.id: a.name for a in brief.apps}
    return roles, features, apps


def band_view(
    catalog: Catalog, language: str, band: BandId | str, source: str
) -> StructureBandView:
    if band not in ("small", "medium", "large"):
        return StructureBandView(id="")
    b = get_band(band)
    return StructureBandView(
        id=b.id,
        label=catalog.t(f"size.{b.id}", language),
        source=source,
        screens=[b.screens[0], b.screens[1]],
        open_upper=b.open_upper,
        review=b.review,
        research_depth=b.research.depth,
    )


def build_structure_view(
    brief: Brief,
    *,
    catalog: Catalog,
    language: str,
    band: BandId | str,
    source: str,
    enabled: bool,
    session_id: str = "",
    suggested: str = "",
) -> StructureViewModel:
    roles, features, apps = _names(brief)
    phases = derive.phase_of(brief)
    screen_name = {s.id: s.name for s in brief.screens}
    module_name = {m.id: m.name for m in brief.modules}
    entity_name = {e.id: e.name for e in brief.entities}
    screens_by_module: dict[str, list[StructureScreenView]] = {}
    for s in brief.screens:
        screens_by_module.setdefault(s.module, []).append(
            StructureScreenView(
                id=s.id,
                name=s.name,
                purpose=s.purpose,
                pattern=s.pattern,
                roles=[roles.get(r, r) for r in s.roles],
                serves=[features.get(f, f) for f in s.serves],
                entities=[
                    StructureEntityUse(
                        entity_id=u.entity, entity=entity_name.get(u.entity, u.entity), use=u.use
                    )
                    for u in s.entities
                ],
                phase=phases.get(s.id, "extended"),
                decision=s.decision,
                tier=s.tier,
            )
        )
    modules = [
        StructureModuleView(
            id=m.id,
            name=m.name,
            kind=m.kind,
            purpose=m.purpose,
            apps=[apps.get(a, a) for a in m.apps],
            roles=[roles.get(r, r) for r in m.roles],
            features=[features.get(f, f) for f in m.features],
            entry=screen_name.get(m.entry, ""),
            screens=screens_by_module.get(m.id, []),
            decision=m.decision,
            tier=m.tier,
        )
        for m in brief.modules
    ]
    screen_module = {s.id: s.module for s in brief.screens}
    flows: list[StructureFlowView] = []
    for f in brief.flows:
        passes: list[str] = []
        for st in f.steps:
            name = module_name.get(screen_module.get(st.screen, ""), "")
            if name and (not passes or passes[-1] != name):
                passes.append(name)
        flows.append(
            StructureFlowView(
                id=f.id,
                name=f.name,
                role=roles.get(f.role, f.role),
                goal=f.goal,
                steps=[
                    StructureStepView(
                        screen_id=st.screen,
                        screen=screen_name.get(st.screen, st.screen),
                        action=st.action,
                    )
                    for st in f.steps
                ],
                acceptance=list(f.acceptance),
                alternates=list(f.alternates),
                modules=passes,
                decision=f.decision,
                tier=f.tier,
            )
        )
    entities = [
        StructureEntityView(
            id=e.id,
            key=e.key,
            name=e.name,
            purpose=e.purpose,
            module=module_name.get(e.module, ""),
            fields=[
                StructureFieldView(
                    key=fld.key,
                    name=fld.name,
                    type=fld.type,
                    required=fld.required,
                    ref=entity_name.get(fld.ref, ""),
                    values=list(fld.values),
                )
                for fld in e.fields
            ],
            states=list(e.states),
            access=[
                StructureAccessView(role_id=r, role=roles.get(r, r), level=lv)
                for r, lv in e.access.items()
            ],
            decision=e.decision,
            tier=e.tier,
        )
        for e in brief.entities
    ]
    matrix_roles = [r.id for r in brief.roles]
    by_role = derive.role_matrix(brief)
    matrix = StructureMatrix(
        roles=[roles[r] for r in matrix_roles],
        rows=[
            StructureMatrixRow(
                module_id=m.id,
                module=m.name,
                cells=[len(by_role.get(r, {}).get(m.id, [])) for r in matrix_roles],
            )
            for m in brief.modules
        ],
    )
    drafted = derive.has_structure(brief)
    c = derive.counts(brief)
    band_id = band if band in ("small", "medium", "large") else ""
    issues: list[StructureIssueView] = []
    verdict = "fits"
    review_blocks = False
    unreviewed = 0
    if band_id and drafted:
        b = get_band(band_id)
        verdict = derive.size_verdict(brief, b)
        review_blocks = b.review == "hard"
        unreviewed = actions.unreviewed_modules(brief)
        for i in checks.run_checks(brief, b, facts=brief.domain_facts, lang=language)[:MAX_ISSUES]:
            issues.append(
                StructureIssueView(code=i.code, severity=i.severity, text=i.detail, ids=list(i.ids))
            )
    return StructureViewModel(
        language=language,
        session_id=session_id,
        enabled=enabled,
        drafted=drafted,
        band=band_view(catalog, language, band_id, source),
        counts=StructureCounts(
            modules=c.modules, screens=c.screens, flows=c.flows, entities=c.entities
        ),
        verdict=verdict,  # type: ignore[arg-type]
        suggested_band=suggested,
        unreviewed=unreviewed,
        review_blocks=review_blocks,
        modules=modules,
        flows=flows,
        entities=entities,
        matrix=matrix,
        issues=issues,
    )


# ---------------------------------------------------------------------------------- consumer-view pieces


def structure_block(brief: Brief, view: str) -> dict[str, JsonValue]:
    """What a consumer view carries of the structure, values only. Empty for a Brief without one, so views of such a
    Brief do not change. ``blueprint`` gets the outline and a slice per module; ``design`` the screens; ``verify`` the
    journeys with their acceptance and which must-have features each covers; ``research`` the names to look up."""
    if not derive.has_structure(brief):
        return {}
    if view == "blueprint":
        routing = derive.route_facts(brief)
        return {
            "structure": {
                "outline": derive.outline(brief),
                "modules": [
                    derive.module_slice(brief, m.id, routing=routing) for m in brief.modules
                ],
            }
        }
    if view == "design":
        rows: list[JsonValue] = []
        phases = derive.phase_of(brief)
        for s in brief.screens:
            row = derive.values(s)
            row.pop("entities", None)
            row["phase"] = phases.get(s.id, "extended")
            rows.append(row)
        return {"screens": rows, "modules": [derive.values(m) for m in brief.modules]}  # type: ignore[dict-item]
    if view == "verify":
        flows = [derive.values(f) for f in brief.flows]
        covered = {fid for f in brief.flows if f.acceptance for fid in f.features}
        return {
            "flows": flows,  # type: ignore[dict-item]
            "must_features_without_acceptance": [  # type: ignore[dict-item]
                f.id
                for f in brief.scope
                if f.priority == "must" and f.scope_status == "in" and f.id not in covered
            ],
        }
    if view == "research":
        return {
            "modules": [m.name for m in brief.modules],  # type: ignore[dict-item]
            "entities": [e.name for e in brief.entities],  # type: ignore[dict-item]
        }
    return {}


def research_guidance_block(brief: Brief) -> dict[str, JsonValue]:
    """The research guidance (category, band, depth, focus) when the Brief recorded a band; else nothing, so a Brief
    from before the structure existed keeps its research view."""
    block = brief.meta.interview
    if block is None or block.size_band not in ("small", "medium", "large"):
        return {}
    return {"guidance": derive.research_guidance(brief, block.size_band)}


def structure_export(
    brief: Brief, mapping: Mapping[str, JsonValue] | None = None
) -> dict[str, JsonValue]:
    """The structure as one export block: band, counts, outline, a slice per module, the role x module matrix and the
    issues that were left. Empty for a Brief without a structure."""
    if not derive.has_structure(brief):
        return {}
    block = brief.meta.interview
    band_id: BandId | str = block.size_band if block is not None else ""
    c = derive.counts(brief)
    routing = derive.route_facts(brief)
    out: dict[str, JsonValue] = {
        "band": band_id,
        "source": block.size_source if block is not None else "",
        "counts": {
            "modules": c.modules,
            "screens": c.screens,
            "flows": c.flows,
            "entities": c.entities,
        },
        "outline": derive.outline(brief),
        "slices": [derive.module_slice(brief, m.id, routing=routing) for m in brief.modules],
        "role_matrix": {
            role: {mod: list(ids) for mod, ids in per.items()}
            for role, per in derive.role_matrix(brief).items()
        },
        "navigation": [list(e) for e in derive.nav_edges(brief)],
    }
    if band_id in ("small", "medium", "large"):
        b = get_band(band_id)
        out["issues"] = [
            {"code": i.code, "severity": i.severity, "ids": list(i.ids), "detail": i.detail}
            for i in checks.run_checks(brief, b, facts=brief.domain_facts)
        ]
        out["research"] = derive.research_guidance(brief, band_id)
    return out
