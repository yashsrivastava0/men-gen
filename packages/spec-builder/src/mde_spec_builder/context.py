"""The context each call receives: compact, ordered, and only what that call needs.

Small models do better with a short, stable prompt, so the outline lists ids and one-line purposes
(never whole blocks), C10 sees only its batch's recipes, entities and operations, and research
additions are passed as the trusted summaries the research engine already validated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from mde_content.packs import load_pack
from mde_content.recipes import load_recipes
from mde_contracts.calls import (
    BlockToRepair,
    BriefContext,
    C9aInput,
    C9bInput,
    C9cInput,
    C10Input,
    C12Input,
    C13Input,
    CriticFinding,
    DesignBaseline,
    KeyValue,
    OptionLite,
    OutlineEntity,
    OutlineFlow,
    OutlineItem,
    OutlineOperation,
    OutlineScreen,
    RecipeLite,
    ScreenDraftLite,
    SpecOutline,
    ValidatorFinding,
)
from mde_contracts.engines import BuildRequest
from mde_contracts.research import ResearchSuggestion
from mde_contracts.spec import AppSpec, ScreenBlock
from mde_prompts.calls.c10_screen_briefs.detail import with_detail
from mde_spec_builder.baseline import resolve_pack_id
from mde_spec_builder.design import get_theme
from mde_spec_builder.research_additions import lite
from mde_spec_builder.slots import SlotView, effective_slots
from mde_spec_loader.fragments import block_bodies, item_model, item_text
from mde_spec_loader.graph import parse_binding

PURPOSE_CHARS = 80
DETAIL_CHARS = 240
MAX_BLOCK_CHARS = 1500
MAX_BLOCKS = 6
MAX_FINDINGS = 20
MAX_NEIGHBORS = 6
BATCH_SIZE = 4
_SPACE = re.compile(r"\s+")


def _short(text: str, limit: int) -> str:
    cleaned = _SPACE.sub(" ", text).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- brief


def brief_context(req: BuildRequest, spec: AppSpec) -> BriefContext:
    slots = SlotView(effective_slots(req))
    brief = req.brief
    features: list[OptionLite] = []
    row = brief.row("features")
    if row is not None and row.features:
        features = [OptionLite(id=f.id, label=f.label) for f in row.features if f.on]
    else:
        labels = {f.id: f.label for f in load_pack(resolve_pack_id(slots)).features}
        features = [
            OptionLite(id=i, label=labels.get(i, i)) for i in slots.items("scope.features") or []
        ]
    look_row, login_row = brief.row("look"), brief.row("login")
    try:
        look = get_theme(spec.design.direction).label
    except KeyError:
        look = spec.design.direction
    assets = slots.text("brand.assets")
    return BriefContext(
        app_summary=_short(brief.app_summary, 240),
        archetype=resolve_pack_id(slots),
        language=spec.header.language,
        mode=brief.mode,
        roles=[r.name for r in spec.roles.roles],
        features=features,
        look=_short(look_row.text, 80) if look_row is not None and look_row.text else look,
        login=_short(login_row.text, 80)
        if login_row is not None and login_row.text
        else slots.text("auth.method"),
        regions=slots.items("regions") or [],
        compliance_flags=slots.items("compliance.flags") or [],
        assumptions=[f"{a.label}: {a.value_text}" for a in brief.assumptions][:8],
        brand_name=slots.text("brand.name") or None,
        brand_url=assets if assets.startswith(("http://", "https://")) else None,
    )


# --------------------------------------------------------------------------- outline


def outline(spec: AppSpec) -> SpecOutline:
    """Ids, routes, recipes and one-line purposes: enough to refer to things, nothing more."""
    primary = {f.id for f in spec.flows if f.primary}
    return SpecOutline(
        screens=[
            OutlineScreen(
                id=s.id,
                route=s.route,
                recipe=s.recipe,
                flow=s.flow,
                purpose=_short(s.purpose, PURPOSE_CHARS),
                source=s.source,
            )
            for s in spec.screens
        ],
        entities=[
            OutlineEntity(id=e.id, name=e.name, fields=[f.name for f in e.fields])
            for e in spec.data.entities
        ],
        operations=[
            OutlineOperation(id=o.id, method=o.method, path=o.path) for o in spec.api.operations
        ],
        flows=[
            OutlineFlow(
                id=f.id,
                name=f"{f.name} (primary)" if f.id in primary else f.name,
                screens=list(f.screens),
            )
            for f in spec.flows
        ],
        roles=[OutlineItem(id=r.id, name=r.name) for r in spec.roles.roles],
        integrations=[OutlineItem(id=i.id, name=i.name) for i in spec.integrations.items],
        design_direction=spec.design.direction,
    )


def recipes_lite(ids: Iterable[str] | None = None) -> list[RecipeLite]:
    wanted = set(ids) if ids is not None else None
    return [
        RecipeLite(id=r.id, kind=r.kind, default_sections=list(r.default_sections))
        for r in load_recipes().recipes
        if wanted is None or r.id in wanted
    ]


def design_baseline(spec: AppSpec) -> DesignBaseline:
    tokens = spec.design.tokens
    font = spec.design.component_variants.get("font_family")
    return DesignBaseline(
        direction=spec.design.direction,
        color_tokens_light=[KeyValue(key=k, value=v) for k, v in tokens.colors_light.items()],
        radius=tokens.radius.get("md", 12),
        font_family=font[0] if font else None,
    )


# --------------------------------------------------------------------------- calls


def c9a_input(
    brief: BriefContext, spec: AppSpec, research: Sequence[ResearchSuggestion], *, room: int
) -> C9aInput:
    return C9aInput(
        brief=brief,
        outline=outline(spec),
        recipes=recipes_lite(),
        research=lite(research),
        max_new_screens=max(room, 0),
    )


def c9b_input(
    brief: BriefContext, spec: AppSpec, research: Sequence[ResearchSuggestion]
) -> C9bInput:
    return C9bInput(brief=brief, outline=outline(spec), research=lite(research))


def c9c_input(
    brief: BriefContext,
    spec: AppSpec,
    brand_colors: list[str],
    brand_tone: list[str],
    research: Sequence[ResearchSuggestion],
) -> C9cInput:
    return C9cInput(
        brief=brief,
        baseline=design_baseline(spec),
        brand_colors=brand_colors,
        brand_tone=brand_tone,
        research=lite(research),
    )


def screen_draft(screen: ScreenBlock) -> ScreenDraftLite:
    """The batch view of a screen. A detail the person gave about it (``notes``) rides along in the
    purpose line (``ScreenDraftLite`` has no notes field), marked so the prompt can tell."""
    detail = _short(screen.notes, DETAIL_CHARS) if screen.notes else None
    return ScreenDraftLite(
        id=screen.id,
        route=screen.route,
        recipe=screen.recipe,
        surface=screen.surface,
        purpose=with_detail(_short(screen.purpose, 160), detail),
        sections=list(screen.sections),
        actions=[f"{a.name} -> {a.calls or a.go}" for a in screen.actions],
        copy_keys=list(screen.texts),
    )


def screen_batches(spec: AppSpec, size: int = BATCH_SIZE) -> list[list[ScreenBlock]]:
    """Screens in id order, 3 to 4 per batch, skipping locked ones (hand-edited downstream)."""
    todo = [s for s in spec.screens if not s.locked]
    return [todo[i : i + size] for i in range(0, len(todo), size)]


def c10_input(
    brief: BriefContext,
    spec: AppSpec,
    batch: Sequence[ScreenBlock],
    research: Sequence[ResearchSuggestion],
) -> C10Input:
    """The batch's own context: its recipes, the entities and operations it binds to, and a few
    neighbouring screens (reached by an action) for orientation."""
    ids = {s.id for s in batch}
    operations: list[str] = []
    entities: list[str] = []
    for screen in batch:
        for item in screen.data:
            parsed = parse_binding(item.bind)
            if parsed is None:
                continue
            (operations if parsed.kind == "api" else entities).append(parsed.target)
        operations += [a.calls for a in screen.actions if a.calls]
    ops = {o.id: o for o in spec.api.operations}
    for op_id in operations:
        found = ops.get(op_id)
        if found is not None:
            entities += [e for e in (found.request_entity, found.response_entity) if e]
    neighbor_ids: list[str] = []
    for screen in batch:
        for action in screen.actions:
            if action.go and action.go not in ids and action.go not in neighbor_ids:
                neighbor_ids.append(action.go)
    by_id = {s.id: s for s in spec.screens}
    neighbors = [
        OutlineScreen(
            id=n.id,
            route=n.route,
            recipe=n.recipe,
            flow=n.flow,
            purpose=_short(n.purpose, PURPOSE_CHARS),
            source=n.source,
        )
        for n in (by_id[i] for i in neighbor_ids if i in by_id)
    ][:MAX_NEIGHBORS]
    entity_map = {e.id: e for e in spec.data.entities}
    return C10Input(
        brief=brief,
        screens=[screen_draft(s) for s in batch],
        recipes=recipes_lite({s.recipe for s in batch}),
        entities=[
            OutlineEntity(id=e.id, name=e.name, fields=[f.name for f in e.fields])
            for e in (entity_map[i] for i in dict.fromkeys(entities) if i in entity_map)
        ],
        operations=[
            OutlineOperation(id=o.id, method=o.method, path=o.path)
            for o in (ops[i] for i in dict.fromkeys(operations) if i in ops)
        ],
        neighbors=neighbors,
        research=lite(research[:3]),
    )


def c12_input(brief: BriefContext, spec: AppSpec, findings: Sequence[ValidatorFinding]) -> C12Input:
    return C12Input(
        brief=brief, outline=outline(spec), validator_findings=list(findings)[:MAX_FINDINGS]
    )


def blocks_for(spec: AppSpec, ids: Iterable[str]) -> list[BlockToRepair]:
    """The YAML of the items a finding points at (at most six, each cut to a safe size)."""
    bodies = block_bodies(spec)
    out: list[BlockToRepair] = []
    for item_id in dict.fromkeys(ids):
        if len(out) >= MAX_BLOCKS:
            break
        if item_id in bodies:
            kind, body = bodies[item_id]
        else:
            found = item_model(spec, item_id)
            text = item_text(spec, item_id)
            if found is None or text is None:
                continue
            kind, body = found[0], text
        out.append(BlockToRepair(id=item_id, kind=kind, text=body[:MAX_BLOCK_CHARS]))
    return out


def c13_input(
    brief: BriefContext,
    spec: AppSpec,
    findings: Sequence[ValidatorFinding | CriticFinding],
    *,
    round_number: int,
) -> C13Input:
    shown = list(findings)[:MAX_FINDINGS]
    ids = [i for f in shown for i in f.target_ids]
    return C13Input(
        brief=brief,
        outline=outline(spec),
        findings=shown,
        blocks=blocks_for(spec, ids),
        round=round_number,
    )
