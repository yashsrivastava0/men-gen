"""The deterministic floor of the structure calls (hard rule 4) and what the mock handlers answer with: a valid draft
built ONLY from the Brief's own features, roles and apps and the templates in ``catalog/structure_patterns.yaml``.

A floor is what the engine falls back to when a model call fails: it is a failure to be fixed, counted and flagged, never
a feature. It is valid by construction (every pointer resolves, every role that opens a screen may do what the screen does
with its entities, every module has screens) but it knows nothing the person did not say, so it is thinner than a model's
draft. Nothing here names a company or an app type; names come from the person's own words and the data file.
"""

from __future__ import annotations

from mde_compass.catalog.loader import load_catalog
from mde_compass.catalog.structure_patterns import load_structure_patterns, text_for
from mde_compass.engine.structure_checks import slug_key
from mde_compass.engine.structure_wire import (
    AccessOut,
    EntityDraft,
    EntityOut,
    EntryOut,
    FeatureWire,
    FieldOut,
    JourneyDetail,
    JourneyOut,
    ModuleCtx,
    ModuleInput,
    ModuleOut,
    ModuleOutput,
    OutlineInput,
    OutlineOutput,
    ScreenOut,
    SegmentOut,
    StepOut,
    UseOut,
)

ACCOUNT_KIND = "account"
SUPPORT_PREFIX = "support:"
NO_SIGN_IN = "no_sign_in"


def _lang(language: str) -> str:
    """Templates exist in English and Hindi; Hindi in Latin letters (hi-Latn) and any other language read English."""
    return "hi" if language == "hi" else "en"


def _family_label(family: str, lang: str) -> str:
    fam = load_catalog().families.get(family)
    return (fam.label.get(lang) or fam.label.get("en") or "") if fam else ""


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 1
    while candidate.casefold() in taken:
        n += 1
        candidate = f"{name} ({n})"
    taken.add(candidate.casefold())
    return candidate


def in_scope(features: list[FeatureWire]) -> list[FeatureWire]:
    """The features that get screens: in scope, the ``must`` ones first (a stable order inside each priority)."""
    order = {"must": 0, "nice": 1, "later": 2}
    kept = [f for f in features if f.scope_status == "in"]
    return sorted(kept, key=lambda f: order.get(f.priority, 1))


# ------------------------------------------------------------------------------------------------- outline


def floor_outline(inp: OutlineInput) -> OutlineOutput:
    data = load_structure_patterns().floor
    lang = _lang(inp.language)
    band = inp.band.id
    per_feature = data.screens_per_feature.get(band, 3)
    all_roles = [r.id for r in inp.roles]
    all_apps = [a.id for a in inp.apps]
    features = in_scope(inp.features)

    groups: list[tuple[str, str, list[FeatureWire]]] = []  # (name, purpose, features)
    by_family: dict[str, list[FeatureWire]] = {}
    if data.group_features_by_family:
        for f in features:
            if f.family:
                by_family.setdefault(f.family, []).append(f)
    grouped = {fid for fs in by_family.values() if len(fs) >= 2 for fid in (f.id for f in fs)}
    emitted: set[str] = set()
    for f in features:
        if f.id in grouped:
            if f.family in emitted:
                continue
            emitted.add(f.family)
            fs = by_family[f.family]
            name = _family_label(f.family, lang) or f.name
            groups.append((name, "; ".join(x.intent or x.name for x in fs)[:200], fs))
        else:
            groups.append((f.name, f.intent or f.name, [f]))

    taken: set[str] = set()
    modules: list[ModuleOut] = []
    entities: list[EntityOut] = []
    journeys: list[JourneyOut] = []

    def ref_of_module() -> str:
        return f"m{len(modules) + 1}"

    if inp.auth_methods and set(inp.auth_methods) != {NO_SIGN_IN}:
        modules.append(
            ModuleOut(
                ref=ref_of_module(),
                kind=ACCOUNT_KIND,
                name=_unique(text_for(data.account_name, lang), taken),
                purpose=text_for(data.account_purpose, lang),
                apps=all_apps,
                features=[],
                roles=all_roles,
                screen_target=len(data.account_screens),
            )
        )
    for name, purpose, fs in groups:
        ref = ref_of_module()
        apps = sorted({a for f in fs for a in f.apps}) or all_apps
        modules.append(
            ModuleOut(
                ref=ref,
                kind="",
                name=_unique(name, taken),
                purpose=purpose,
                apps=apps,
                features=[f.id for f in fs],
                roles=all_roles,
                screen_target=per_feature * len(fs),
            )
        )
        for f in fs:
            entities.append(
                EntityOut(
                    ref=f"e{len(entities) + 1}",
                    name=f.name,
                    purpose=f.intent or f.name,
                    owner_module=ref,
                )
            )
        for f in fs:
            if f.priority == "must":
                journeys.append(
                    JourneyOut(
                        ref=f"j{len(journeys) + 1}",
                        name=f.name,
                        role=all_roles[0] if all_roles else "",
                        goal=f.intent or f.name,
                        legs=[ref],
                        owner_module=ref,
                    )
                )
    for support in data.support_modules:
        if band in support.bands and len(inp.roles) >= support.roles_min:
            modules.append(
                ModuleOut(
                    ref=ref_of_module(),
                    kind=f"{SUPPORT_PREFIX}{support.id}",
                    name=_unique(text_for(support.name, lang), taken),
                    purpose=text_for(support.purpose, lang),
                    apps=all_apps,
                    features=[],
                    roles=all_roles,
                    screen_target=len(support.screens),
                )
            )
    if (
        not journeys and features
    ):  # nothing is a must-have: the first features still get a journey each
        by_id = {m.ref: m for m in modules}
        for m in modules:
            fid = next(iter(m.features), "")
            f = next((x for x in features if x.id == fid), None)
            if f is not None and len(journeys) < 2:
                journeys.append(
                    JourneyOut(
                        ref=f"j{len(journeys) + 1}",
                        name=f.name,
                        role=all_roles[0] if all_roles else "",
                        goal=f.intent or f.name,
                        legs=[by_id[m.ref].ref],
                        owner_module=m.ref,
                    )
                )
    return OutlineOutput(modules=modules, entities=entities, journeys=journeys)


# -------------------------------------------------------------------------------------------------- modules


def _feature_screens(
    inp: ModuleInput, module: ModuleCtx, lang: str
) -> tuple[list[ScreenOut], list[UseOut]]:
    data = load_structure_patterns().floor
    n = data.screens_per_feature.get(inp.band.id, 3)
    by_id = {f.id: f for f in inp.features}
    owned = [e for e in inp.entities if e.owner_module == module.ref]
    entity_of = {e.name: e.ref for e in owned}
    screens: list[ScreenOut] = []
    uses: list[UseOut] = []
    taken: set[str] = set()
    for position, fid in enumerate(module.features):
        feature = by_id.get(fid)
        if feature is None:
            continue
        # the entity named like the feature, else the module's own entities in turn (a model names them freely)
        entity = entity_of.get(feature.name) or (owned[position % len(owned)].ref if owned else "")
        for pattern in data.feature_patterns[:n]:
            ref = f"{module.ref}_s{len(screens) + 1}"
            screens.append(
                ScreenOut(
                    ref=ref,
                    module=module.ref,
                    name=_unique(
                        text_for(data.labels[pattern], lang).format(feature=feature.name), taken
                    ),
                    purpose=text_for(data.purposes[pattern], lang).format(feature=feature.name),
                    pattern=pattern,
                    apps=list(module.apps),
                    roles=list(module.roles),
                    serves=[fid],
                )
            )
            if entity:
                uses.append(
                    UseOut(screen=ref, entity=entity, use="edit" if pattern == "form" else "view")
                )
    return screens, uses


def _template_screens(
    module: ModuleCtx, templates: list[tuple[str, str, str]]
) -> tuple[list[ScreenOut], list[UseOut]]:
    return [
        ScreenOut(
            ref=f"{module.ref}_s{i}",
            module=module.ref,
            name=name,
            purpose=purpose,
            pattern=pattern,
            apps=list(module.apps),
            roles=list(module.roles),
            serves=[],
        )
        for i, (pattern, name, purpose) in enumerate(templates, 1)
    ], []


def _screens_of(
    inp: ModuleInput, module: ModuleCtx, lang: str
) -> tuple[list[ScreenOut], list[UseOut]]:
    data = load_structure_patterns().floor
    if module.kind == ACCOUNT_KIND:
        templates = [
            (s.pattern, text_for(s.name, lang), text_for(s.purpose, lang))
            for s in data.account_screens
            if not s.when_auth or set(s.when_auth) & set(inp.auth_methods)
        ]
        return _template_screens(module, templates)
    if module.kind.startswith(SUPPORT_PREFIX):
        support = next(
            (m for m in data.support_modules if m.id == module.kind[len(SUPPORT_PREFIX) :]), None
        )
        if support is not None:
            return _template_screens(
                module,
                [
                    (s.pattern, text_for(s.name, lang), text_for(s.purpose, lang))
                    for s in support.screens
                ],
            )
    return _feature_screens(inp, module, lang)


def floor_modules(inp: ModuleInput) -> ModuleOutput:
    """Screens, journey segments, entity fields and acceptance lines for one group of modules, from templates."""
    data = load_structure_patterns().floor
    lang = _lang(inp.language)
    home = load_structure_patterns().home_patterns()
    screens: list[ScreenOut] = []
    uses: list[UseOut] = []
    entries: list[EntryOut] = []
    segments: list[SegmentOut] = []
    for module in inp.modules:
        mine, mine_uses = _screens_of(inp, module, lang)
        screens += mine
        uses += mine_uses
        if mine:
            entry = next((s.ref for s in mine if s.pattern in home), mine[0].ref)
            entries.append(EntryOut(module=module.ref, screen=entry))
        for journey in inp.journeys:
            for leg in journey.legs:
                if not leg.here or leg.module != module.ref or not mine:
                    continue
                chosen = [s for s in mine if s.name.startswith(journey.name)] or mine
                steps = [
                    StepOut(
                        screen=s.ref,
                        action=text_for(data.actions.get(s.pattern, data.actions["other"]), lang),
                    )
                    for s in chosen[: max(2, min(4, len(chosen)))]
                ]
                segments.append(
                    SegmentOut(journey=journey.ref, leg=leg.index, module=module.ref, steps=steps)
                )
    entity_drafts: list[EntityDraft] = []
    fields: list[FieldOut] = []
    access: list[AccessOut] = []
    roles_of_module = {m.ref: set(m.roles) for m in inp.modules}
    for entity in inp.entities:
        if not entity.mine:
            continue
        editors = roles_of_module.get(entity.owner_module, set())
        entity_drafts.append(
            EntityDraft(
                ref=entity.ref, key=slug_key(entity.name, f"entity_{entity.ref}"), states=[]
            )
        )
        fields += [
            FieldOut(
                entity=entity.ref,
                key="name",
                name=entity.name,
                type="text",
                required=True,
                ref="",
                values=[],
            ),
            FieldOut(
                entity=entity.ref,
                key="details",
                name=entity.purpose[:60],
                type="text",
                required=False,
                ref="",
                values=[],
            ),
        ]
        access += [
            AccessOut(entity=entity.ref, role=r.id, level="edit" if r.id in editors else "view")
            for r in inp.roles
        ]
    details = [
        JourneyDetail(
            ref=j.ref,
            acceptance=[text_for(data.acceptance, lang).format(goal=j.goal or j.name)],
            alternates=[],
        )
        for j in inp.journeys
        if j.owner
    ]
    return ModuleOutput(
        screens=screens,
        uses=uses,
        entries=entries,
        segments=segments,
        entities=entity_drafts,
        fields=fields,
        access=access,
        journeys=details,
    )
