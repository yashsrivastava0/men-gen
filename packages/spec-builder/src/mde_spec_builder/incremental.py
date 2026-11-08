"""Incremental builds (spec 3.6): after a change only the affected writers re-run and every other
block of the previous spec is carried over untouched.

``plan_build`` decides between a *full* build (baseline + all writers) and a *patch*:

======================  ==========================================================  =============
``affected_sections``   what is rebuilt                                              writers
======================  ==========================================================  =============
``design``              design tokens (+ app tone and name)                          C9c
``roles``               roles, navigation gates, sign-in screens, their operations   C9b, C10 (sign-in)
``integrations``        integrations (+ the screens that mention them)               C10
``behavior``            behavior block (+ every screen when the language changed)    C10 (if language)
``all``/``screens``/    everything: screens, flows, navigation, data, API            all
``navigation``/``data``
``api``
======================  ==========================================================  =============

A patch is only valid while the baseline structure (the set of baseline screen routes) is the same
as in the previous spec; if a change reshaped it (a feature switched, no sign-in) the build falls
back to a full one. Toggling a research addition off removes exactly the items it created.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Literal

from mde_content.packs import load_pack
from mde_contracts.calls import ChangeOp
from mde_contracts.engines import BuildRequest, SpecSection
from mde_contracts.spec import (
    ApiOperation,
    AppSpec,
    Integration,
    IntegrationsBlock,
    NavNode,
    ResearchAddition,
    ScreenBlock,
)
from mde_spec_builder.assemble import rewrite_screen_ids
from mde_spec_builder.baseline import resolve_pack_id
from mde_spec_builder.pack_expand import auth_recipe_ids
from mde_spec_builder.research_additions import feature_target
from mde_spec_builder.rules import load_rules
from mde_spec_builder.slots import SlotView

PATCHABLE: frozenset[str] = frozenset({"design", "roles", "integrations", "behavior"})

# slot id prefix -> sections it changes (spec 3.6 dependency map)
_SLOT_SECTIONS: tuple[tuple[str, SpecSection], ...] = (
    ("design.", "design"),
    ("brand.", "design"),
    ("auth.", "roles"),
    ("users.roles", "roles"),
    ("pay.", "integrations"),
    ("integrations.list", "integrations"),
    ("behavior.", "behavior"),
    ("locale", "behavior"),
    ("a11y", "behavior"),
    ("compliance.", "behavior"),
    ("data.classification", "behavior"),
    ("release.", "behavior"),
    ("approvers", "behavior"),
)


def sections_for_slots(slot_ids: Iterable[str]) -> list[SpecSection]:
    """The sections to rebuild when these slots change (the dependency map of spec 3.6). Anything
    not listed (``scope.*``, ``idea.*``, ``regions``, ``data.source``) means ``all``."""
    found: list[SpecSection] = []
    for slot_id in slot_ids:
        section: SpecSection = "all"
        for prefix, target in _SLOT_SECTIONS:
            if slot_id.startswith(prefix):
                section = target
                break
        if section not in found:
            found.append(section)
    return ["all"] if "all" in found else found


@dataclass(frozen=True)
class BuildPlan:
    mode: Literal["full", "patch"]
    sections: frozenset[str] = frozenset()  # patch sections
    reason: str = ""


def screen_level(op: ChangeOp) -> bool:
    """Does this change request ask for something on a screen? The other kinds (slots, features,
    research toggles, assumptions) are already carried by the build request's inputs."""
    if op.slot_id is not None:
        return False
    if op.kind == "other":
        return bool(op.instruction)
    return op.kind in ("add_screen", "remove_screen", "patch_screen", "set_copy")


# sections a screen-level change request (an added, removed or annotated screen) is the reason for
_SCREEN_SECTIONS: frozenset[str] = frozenset({"screens", "navigation"})


def plan_build(req: BuildRequest) -> BuildPlan:
    """Full build unless a previous spec exists and only patchable sections are affected.

    ``screens`` and ``navigation`` are what the flow reports for screen-level change requests
    (add, remove, patch a screen, set its copy): the builder applies those itself on the previous
    spec (``change_ops``), so when such a request is in the build they are not a reason to rebuild."""
    if req.previous_spec is None:
        return BuildPlan("full", reason="no previous version")
    sections = set(req.affected_sections)
    if any(screen_level(op) for op in req.change_ops):
        sections -= _SCREEN_SECTIONS
    if "all" in sections or sections - PATCHABLE:
        return BuildPlan("full", reason="the change touches screens, data or scope")
    if _feature_addition_toggled(req, req.previous_spec):
        return BuildPlan("full", reason="a research addition switches a pack feature on or off")
    return BuildPlan("patch", frozenset(sections))


def _feature_addition_toggled(req: BuildRequest, prev: AppSpec) -> bool:
    """Was a research addition that names a pack feature (``scope.features:<id>``) toggled? Such an
    addition reshapes screens, data and navigation, so the build is a full one."""
    note = req.research
    changed = {
        a.id
        for a in [
            *toggles(prev, req.excluded_research_ids).off,
            *toggles(prev, req.excluded_research_ids).on,
        ]
    }
    if note is None or not changed:
        return False
    pack = load_pack(resolve_pack_id(SlotView.of(req)))
    features = {f.id for f in pack.features}
    return any(feature_target(s, features) is not None for s in note.suggestions if s.id in changed)


def is_baseline_screen(screen: ScreenBlock, fresh: ScreenBlock | None) -> bool:
    """Is ``screen`` of the previous spec a screen the baseline produces (``fresh`` is the baseline's
    screen at the same route, or None)?

    The baseline owns the pack's screens (source ``default``), the screens of a pack feature that
    research switched on (source ``research``, tagged with the suggestion that did it) and a pack
    screen the person annotated (source ``custom``, same route). Screens that proposals or change
    requests added (``brief``, ``research`` proposals, ``custom`` at a new route) are not.
    """
    if fresh is None:
        return screen.source == "default"  # a pack screen the baseline no longer has
    if screen.source in ("default", "custom"):
        return True
    return screen.source == "research" and screen.research_ref == fresh.research_ref


def structure_matches(prev: AppSpec, base: AppSpec) -> bool:
    """Do the baseline screens of ``prev`` have exactly the routes of the new baseline ``base``?

    Both sides are read the same way: the baseline's screens (research feature screens included,
    because ``base`` was built with the same research selection) against the screens of ``prev``
    that the baseline produces (``is_baseline_screen``), minus the pack screens the person removed.
    Screens that proposals or change requests added play no part.
    """
    by_route = {s.route: s for s in base.screens}
    wanted = set(by_route) - removed_routes(prev)
    have = {s.route for s in prev.screens if is_baseline_screen(s, by_route.get(s.route))}
    return have == wanted


def removed_routes(prev: AppSpec) -> set[str]:
    """Routes of pack screens the person asked to remove (kept in the backlog, so a later build
    leaves them out too)."""
    reason = load_rules().change_ops.removed_reason
    return {r for item in prev.backlog if item.reason == reason for r in item.screens_hint}


# --------------------------------------------------------------------------- grafts


def _route_ids(prev: AppSpec, base: AppSpec) -> dict[str, str]:
    """Baseline screen id -> the id the same route has in the previous spec."""
    by_route = {s.route: s.id for s in prev.screens}
    return {s.id: by_route[s.route] for s in base.screens if s.route in by_route}


def _regate(node: NavNode, auth_by_route: dict[str, str]) -> NavNode:
    auth = auth_by_route.get(node.route, node.auth)
    return node.model_copy(
        update={"auth": auth, "children": [_regate(c, auth_by_route) for c in node.children]}
    )


def graft_roles(prev: AppSpec, base: AppSpec) -> tuple[AppSpec, set[str]]:
    """Roles, gates and the sign-in screens of ``base`` onto ``prev``; returns the spec and the
    ids of the sign-in screens that were replaced (their briefs are rewritten afterwards)."""
    ids = _route_ids(prev, base)
    auth_by_route = {s.route: s.auth for s in base.screens}
    shared = auth_recipe_ids()
    base_by_route = {s.route: s for s in base.screens}
    replaced: set[str] = set()
    screens: list[ScreenBlock] = []
    for screen in prev.screens:
        fresh = base_by_route.get(screen.route)
        if fresh is None or not is_baseline_screen(screen, fresh):
            screens.append(screen)
            continue
        if screen.recipe in shared or fresh.recipe in shared:
            data = rewrite_screen_ids(fresh.model_dump(mode="python"), ids)
            assert isinstance(data, dict)
            merged = ScreenBlock.model_validate(
                {
                    **data,
                    "id": screen.id,
                    "flow": screen.flow,
                    "source": screen.source,
                    "research_ref": screen.research_ref,
                    "notes": screen.notes,
                }
            )
            screens.append(merged)
            replaced.add(screen.id)
        else:
            screens.append(screen.model_copy(update={"auth": fresh.auth}))
    base_ops = {o.id: o for o in base.api.operations}
    ops: list[ApiOperation] = list(base.api.operations)
    ops += [o for o in prev.api.operations if o.id not in base_ops and o.source != "default"]
    nav = prev.navigation.model_copy(update={"root": _regate(prev.navigation.root, auth_by_route)})
    out = prev.model_copy(
        update={
            "roles": base.roles,
            "screens": screens,
            "navigation": nav,
            "api": prev.api.model_copy(update={"operations": ops}),
            "integrations": _merge_integrations(prev, base),
        }
    )
    return out, replaced


def _merge_integrations(prev: AppSpec, base: AppSpec) -> IntegrationsBlock:
    """The baseline's integrations plus the ones added later (research, proposals) by id."""
    have = {i.id for i in base.integrations.items}
    extras: list[Integration] = [
        i for i in prev.integrations.items if i.id not in have and i.source != "default"
    ]
    return base.integrations.model_copy(update={"items": [*base.integrations.items, *extras]})


def graft_integrations(prev: AppSpec, base: AppSpec) -> AppSpec:
    return prev.model_copy(update={"integrations": _merge_integrations(prev, base)})


def graft_design(prev: AppSpec, base: AppSpec) -> AppSpec:
    return prev.model_copy(update={"design": base.design, "app": base.app})


def graft_behavior(prev: AppSpec, base: AppSpec) -> AppSpec:
    return prev.model_copy(update={"behavior": base.behavior})


def affected_screens(prev: AppSpec, integration_ids: Collection[str]) -> set[str]:
    """Screens that mention an integration (its id stem in the route, a section or an action)."""
    stems = {i.removeprefix("INT-").replace("_", "-").lower() for i in integration_ids}
    found: set[str] = set()
    for screen in prev.screens:
        words = (
            " ".join([screen.route, *screen.sections, *(a.name for a in screen.actions)])
            .lower()
            .replace("_", "-")
        )
        if any(stem and stem.rstrip("s") in words for stem in stems):
            found.add(screen.id)
    return found


def changed_integration_ids(prev: AppSpec, merged: AppSpec) -> list[str]:
    before = {i.id: i for i in prev.integrations.items}
    after = {i.id: i for i in merged.integrations.items}
    return sorted(i for i in {*before, *after} if before.get(i) != after.get(i))


# --------------------------------------------------------------------------- research toggles


@dataclass
class Toggles:
    off: list[ResearchAddition] = field(default_factory=list)  # included before, excluded now
    on: list[ResearchAddition] = field(default_factory=list)  # excluded before, included now


def toggles(prev: AppSpec, excluded: Collection[str]) -> Toggles:
    found = Toggles()
    for addition in prev.assumptions.research_additions:
        if addition.included and addition.id in excluded:
            found.off.append(addition)
        elif not addition.included and addition.id not in excluded:
            found.on.append(addition)
    return found


_REMOVE_ORDER = ("SCR", "FLW", "API", "ENT", "INT", "ROLE")


def created_by(spec: AppSpec, addition: ResearchAddition) -> list[str]:
    """Ids of the items this addition created: screens and integrations tagged with its id, and
    entities / operations it touched whose source is ``research`` (removal order: screens first)."""
    owned: set[str] = {s.id for s in spec.screens if s.research_ref == addition.id}
    owned |= {i.id for i in spec.integrations.items if i.research_ref == addition.id}
    research_items = {e.id for e in spec.data.entities if e.source == "research"} | {
        o.id for o in spec.api.operations if o.source == "research"
    }
    owned |= {i for i in addition.affects if i in research_items}
    return sorted(owned, key=lambda i: (_REMOVE_ORDER.index(i.split("-")[0]), i))
