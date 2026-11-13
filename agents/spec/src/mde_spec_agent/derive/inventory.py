"""R-INV: the screen inventory (doc 15 4.2, 4.3). The count is derived, never fixed.

Compass's structure is a PROPOSAL (grade ``default``). Code turns it into one list of :class:`ScreenProposal` by rules:

1. one proposal per Compass screen (a screen Compass lists for several apps is ONE shared screen, specified once and
   built in the first app; ``shared_apps`` names them all), screen types from the closed vocabulary through the exact-synonym table; without a structure (Compass format 1)
   screens are derived from the Brief's features;
2. a create screen and an edit screen of one entity become ONE form with a mode (the old id stays as an alias);
3. entities: a managed entity gets a form, a viewed entity at least one screen; join and log entities none;
4. every flow step resolves to a screen (a step that does not is reported);
5. every role has a home or a reason;
6. integrations: a payment need has a pay step;
7. the peer check from Research's screens lane: a screen pattern common among peers and absent here is added as Default or
   left out with the reason (the list is returned as ``peers_usually_have``);
8. the SYSTEM SET (``libraries.system_screens_for``) per app; a Compass screen that already is one satisfies it;
   a legal screen whose library entry allows a link out is a link, not a screen.

No layout words; no model. Word lists live in ``data/inventory.yaml``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from mde_compass_intake import ResearchView, SpecInput
from mde_compass_intake.spec_input import App as InApp
from mde_compass_intake.spec_input import Entity as InEntity
from mde_compass_intake.text import stem_set
from mde_spec_core import libraries
from mde_spec_core.config import SpecConfig, default_config
from mde_spec_core.ids import IdRegistry
from mde_spec_core.policy import default_policy_sheet, policy_ref

from ._data import inventory_data
from .text import first_sentences, has_word, plural

ENTITY_PRODUCER_LEVELS = ("create", "edit", "manage")


# ---------------------------------------------------------------- result types


@dataclass
class ScreenProposal:
    """One screen of the inventory. Release and seed read it; neither changes the Compass structure itself."""

    id: str
    name: str
    screen_type: str
    app: str  # where it is built (the first app that uses it)
    module: str | None
    role_ids: tuple[str, ...]
    feature_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    reasons: list[str] = field(default_factory=list)
    shared_apps: tuple[
        str, ...
    ] = ()  # every app that uses it (a shared screen is specified once, doc 19 2.3)
    system: bool = False
    compass_id: str | None = None
    purpose: str = ""
    phase: str = ""  # Compass's ``core`` / ``extended``; ``derived`` for a screen code added; ``brief`` when derived from the Brief
    entity_uses: tuple[tuple[str, str], ...] = ()
    source: str = "compass"  # compass | merged | derived | system | peer
    system_id: str | None = None
    variants: tuple[str, ...] = ()
    aliases: list[str] = field(default_factory=list)
    mode: str | None = None  # ``create_edit`` for a merged form
    provenance: tuple[tuple[str, str], ...] = ()  # (kind, ref) pairs for the graph's Prov


@dataclass(frozen=True)
class AppInfo:
    id: str
    name: str
    surface: str  # phone | tablet | web
    role_ids: tuple[str, ...]
    source_id: str | None  # the Compass app id (None for an app code had to add)
    note: str = ""


@dataclass(frozen=True)
class RoleInfo:
    id: str
    name: str
    goal: str
    app_ids: tuple[str, ...]
    assumed: bool
    grade: str  # locked | default | open
    aliases: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class PeerGap:
    """A screen pattern Research found common among peers and absent here (doc 15 4.2 rule 8)."""

    name: str
    evidence: str
    level: str
    item_ids: tuple[str, ...]
    added: bool
    reason: str
    screen_id: str | None = None


@dataclass(frozen=True)
class MergeNote:
    kept: str
    removed: str
    why: str


@dataclass
class Inventory:
    screens: list[ScreenProposal]
    apps: list[AppInfo]
    roles: dict[str, RoleInfo]
    role_alias: dict[str, str | None]
    feature_alias: dict[str, str]
    screen_alias: dict[str, str]
    compass_instances: dict[str, list[str]]  # Compass screen id -> proposal ids (one per app)
    system_module: (
        tuple[str, str] | None
    )  # (module id, name) when the system screens need a module of their own
    derived_modules: list[
        tuple[str, str, str]
    ]  # (module id, name, app id) when Compass has no modules
    links_out: list[str]  # system screens satisfied by a link out (legal pages)
    peers_usually_have: list[PeerGap]
    merges: list[MergeNote]
    notes: list[str]
    registry: IdRegistry
    facts: set[str]
    profile: str
    structure_derived: bool = False

    def proposal(self, pid: str) -> ScreenProposal | None:
        pid = self.screen_alias.get(pid, pid)
        for p in self.screens:
            if p.id == pid:
                return p
        return None

    def by_app(self, app_id: str) -> list[ScreenProposal]:
        return [p for p in self.screens if p.app == app_id]


# ---------------------------------------------------------------- roles and apps


def _surface(text: str) -> str:
    words = inventory_data().surface_words
    for surface in ("web", "tablet"):
        if has_word(text, words.get(surface, [])):
            return surface
    return "phone"


def _clean_apps(si: SpecInput, notes: list[str]) -> tuple[list[InApp], dict[str, str | None]]:
    """The apps that exist: real ones, plus a non-real one that exclusively owns a screen (a role-named app such as
    "Manager side" with its own screens is an app; "Fleet maintenance screens", listed beside a real app on every
    screen, is a module label and is dropped). Returns the kept apps and an id map (a dropped app maps to None)."""
    by_id = {a.id: a for a in si.apps}
    amap: dict[str, str | None] = {}
    for a in si.apps:
        if a.junk:
            amap[a.id] = None
        elif a.duplicate_of:
            amap[a.id] = a.duplicate_of
    exclusive: set[str] = set()
    for s in si.structure.screens:
        apps = [x for x in s.apps if x in by_id and amap.get(x, x) is not None]
        if len(apps) == 1:
            exclusive.add(apps[0])
    kept: list[InApp] = []
    for a in si.apps:
        if a.id in amap:
            continue
        if a.is_real or a.id in exclusive:
            kept.append(a)
            amap[a.id] = a.id
            if not a.is_real:
                notes.append(
                    f"app {a.id} {a.name!r} looks like a role name but owns screens of its own: kept as an app (assumed)"
                )
        else:
            amap[a.id] = None
            notes.append(f"app {a.id} {a.name!r} is not an app (a module or role label): dropped")
    return kept, amap


def _resolve(amap: dict[str, Any], key: str) -> Any:
    seen: set[str] = set()
    cur: Any = key
    while cur in amap and amap[cur] != cur and amap[cur] is not None and cur not in seen:
        seen.add(cur)
        cur = amap[cur]
    return None if amap.get(cur, cur) is None else cur


def _strip_suffix(name: str) -> str:
    words = inventory_data().role_name_suffix_words
    parts = name.split()
    while len(parts) > 1 and parts[-1].lower() in words:
        parts.pop()
    return " ".join(parts)


def _roles(
    si: SpecInput, app_ids: set[str], notes: list[str]
) -> tuple[dict[str, RoleInfo], dict[str, str | None]]:
    ralias: dict[str, str | None] = {}
    for r in si.roles:
        if r.duplicate_of:
            ralias[r.id] = r.duplicate_of
    for m in si.merges:
        if m.kind == "role":
            ralias[m.from_id] = m.into_id
    clash_app = {c.role_id: c.app_id for c in si.name_clashes}
    roles: dict[str, RoleInfo] = {}
    for r in si.roles:
        if r.id in ralias:
            continue
        if r.junk:
            ralias[r.id] = None
            notes.append(f"role {r.id} {r.name!r} is junk: dropped")
            continue
        name = r.name
        assumed = r.assumed
        note = ""
        if r.is_app_name:
            if clash_app.get(r.id) in app_ids:
                name = _strip_suffix(r.name)
                assumed = True
                note = f"named like an app ({r.name!r}); split into the role {name!r} and the app (assumed)"
                notes.append(f"role {r.id}: {note}")
            else:
                ralias[r.id] = None
                notes.append(f"role {r.id} {r.name!r} is an app label with no app: dropped")
                continue
        aliases = tuple(sorted(k for k, v in ralias.items() if v == r.id))
        roles[r.id] = RoleInfo(
            id=r.id,
            name=name,
            goal=r.goal,
            app_ids=tuple(r.apps),
            assumed=assumed,
            grade=r.trust.grade,
            aliases=aliases,
            note=note,
        )
    return roles, ralias


def _role_id(ralias: dict[str, str | None], roles: dict[str, RoleInfo], rid: str) -> str | None:
    seen: set[str] = set()
    cur: str | None = rid
    while cur is not None and cur in ralias and cur not in seen:
        seen.add(cur)
        cur = ralias[cur]
    return cur if cur in roles else None


# ---------------------------------------------------------------- the derivation


def _screen_type(pattern: str, name: str, notes: list[str], sid: str) -> str:
    canon = libraries.canonical("screen_type", pattern) if pattern else None
    if canon:
        return canon
    notes.append(
        f"screen {sid} {name!r}: pattern {pattern!r} is not in the vocabulary: typed detail"
    )
    return "detail"


def _plural_name(entity: InEntity | None) -> str:
    return plural((entity.name if entity else "item").lower())


def _entity_classes(si: SpecInput) -> dict[str, str]:
    """primary / secondary / join / log (by name words and by shape: mostly references)."""
    cls = inventory_data().entity_classes
    out: dict[str, str] = {}
    for e in si.structure.entities:
        label = f"{e.key} {e.name}"
        refs = sum(1 for f in e.fields if f.type == "ref")
        if has_word(label, cls.log_words):
            out[e.id] = "log"
        elif has_word(label, cls.join_words) or (
            len(e.fields) >= 2 and refs >= 2 and refs >= len(e.fields) - 1
        ):
            out[e.id] = "join"
        elif len(e.states) >= 3:
            out[e.id] = "primary"
        else:
            out[e.id] = "secondary"
    return out


def build_inventory(
    spec_input: SpecInput,
    research_view: ResearchView | None,
    facts: set[str],
    profile: str,
    config: SpecConfig | None = None,
    *,
    registry: IdRegistry | None = None,
) -> Inventory:
    _ = config or default_config()  # caps reach the release planner, not the inventory
    reg = registry or IdRegistry()
    notes: list[str] = []
    si = spec_input

    kept_apps, amap = _clean_apps(si, notes)
    if not kept_apps:
        # nothing to build an app from: one assumed app, so the rest of the derivation has somewhere to put screens
        app_id = reg.mint("APP")
        kept_info = [
            AppInfo(app_id, "The app", "phone", (), None, "no app in the Brief: one is assumed")
        ]
        notes.append("the Brief names no app: one app is assumed")
    else:
        kept_info = []
        for a in kept_apps:
            reg.carry(a.id) if a.id not in reg.used else None
            kept_info.append(
                AppInfo(a.id, a.name, _surface(f"{a.surface} {a.name}"), tuple(a.roles), a.id)
            )
    app_ids = [a.id for a in kept_info]
    app_set = set(app_ids)

    roles, ralias = _roles(si, app_set, notes)
    for rid in roles:
        if rid not in reg.used:
            reg.carry(rid)
    falias: dict[str, str] = {}
    for f in si.features:
        if f.duplicate_of:
            falias[f.id] = f.duplicate_of
    for m in si.merges:
        if m.kind == "feature":
            falias[m.from_id] = m.into_id

    def feat(fid: str) -> str:
        seen: set[str] = set()
        while fid in falias and fid not in seen:
            seen.add(fid)
            fid = falias[fid]
        return fid

    live_features = {
        f.id for f in si.features if f.duplicate_of is None and f.scope_status != "out"
    }

    entities = {e.id: e for e in si.structure.entities}
    eclass = _entity_classes(si)
    modules = {m.id: m for m in si.modules}

    # --- role -> apps: declared, plus the single-app screens a role is listed on ---------------------------------
    role_apps: dict[str, set[str]] = defaultdict(set)
    for rid, r in roles.items():
        role_apps[rid] |= {a for a in (_resolve(amap, x) for x in r.app_ids) if a in app_set}
    for s in si.structure.screens:
        sapps = [a for a in (_resolve(amap, x) for x in s.apps) if a in app_set]
        if len(set(sapps)) == 1:
            for rid in s.roles:
                rr = _role_id(ralias, roles, rid)
                if rr:
                    role_apps[rr].add(sapps[0])
    for a in kept_info:  # app.roles from Compass
        for rid in a.role_ids:
            rr = _role_id(ralias, roles, rid)
            if rr:
                role_apps[rr].add(a.id)
    for rid in roles:
        if not role_apps[rid]:
            role_apps[rid] = set(app_ids)  # unknown: assumed to work in every app
            notes.append(
                f"role {rid} {roles[rid].name!r}: no app is named for it, assumed in every app"
            )

    app_roles: dict[str, tuple[str, ...]] = {
        a: tuple(sorted(r for r in roles if a in role_apps[r])) for a in app_ids
    }

    # --- features reached through flows and modules (a screen that serves nothing borrows them) -------------------
    flow_feats_of_screen: dict[str, set[str]] = defaultdict(set)
    for fl in si.structure.flows:
        for st in fl.steps:
            flow_feats_of_screen[st.screen] |= {feat(x) for x in fl.features}

    # --- 1. Compass screens, one proposal per app -------------------------------------------------------------------
    screens: list[ScreenProposal] = []
    instances: dict[str, list[str]] = {}
    structure_derived = not si.structure.screens

    def new_id(compass_id: str | None, first: bool) -> str:
        if compass_id and first:
            return reg.carry(compass_id)
        return reg.mint("SCR")

    for s in si.structure.screens:
        sapps = [a for a in dict.fromkeys(_resolve(amap, x) for x in s.apps) if a in app_set]
        if not sapps:
            mod = modules.get(s.module)
            sapps = [
                a
                for a in dict.fromkeys(_resolve(amap, x) for x in (mod.apps if mod else ()))
                if a in app_set
            ]
        if not sapps:
            sapps = [app_ids[0]]
            notes.append(f"screen {s.id} {s.name!r}: no app, put in {app_ids[0]} (assumed)")
        stype = _screen_type(s.pattern, s.name, notes, s.id)
        serves = {feat(x) for x in s.serves if feat(x) in live_features}
        reasons = ["Compass screen"]
        if not serves:
            borrowed = {x for x in flow_feats_of_screen.get(s.id, set()) if x in live_features}
            if not borrowed and s.module in modules:
                borrowed = {feat(x) for x in modules[s.module].features if feat(x) in live_features}
            if borrowed:
                serves = borrowed
                reasons.append(
                    "serves no feature in Compass: takes the features of its flow or module"
                )
        entity_uses = tuple((u.entity, u.use) for u in s.entities if u.entity in entities)
        app = sapps[0]
        pid = new_id(s.id, True)
        union_roles = tuple(
            sorted({rr for x in s.roles if (rr := _role_id(ralias, roles, x))})
        ) or tuple(sorted({r for a in sapps for r in app_roles.get(a, ())}))
        if len(sapps) > 1:
            reasons.append(
                f"Compass lists it for {len(sapps)} apps: one shared screen, specified once and used by each ({', '.join(sapps)})"
            )
        screens.append(
            ScreenProposal(
                id=pid,
                name=s.name,
                screen_type=stype,
                app=app,
                module=s.module if s.module in modules else None,
                role_ids=union_roles,
                feature_ids=tuple(sorted(serves)),
                entity_ids=tuple(sorted({e for e, _ in entity_uses})),
                reasons=reasons,
                shared_apps=tuple(sapps),
                compass_id=s.id,
                purpose=first_sentences(s.purpose, 2),
                phase=s.phase,
                entity_uses=entity_uses,
                provenance=(("compass", s.id),),
            )
        )
        ids_for_this = [pid]
        instances[s.id] = ids_for_this

    derived_modules: list[tuple[str, str, str]] = []
    if structure_derived:
        _derive_from_brief(
            si,
            screens,
            instances,
            reg,
            app_ids,
            app_roles,
            live_features,
            feat,
            notes,
            derived_modules,
        )

    screen_alias: dict[str, str] = {}
    merges: list[MergeNote] = []

    # --- 2. merge hints: create + edit of one entity in one module and app -> one form with a mode -----------------
    _merge_forms(screens, entities, reg, screen_alias, merges, instances)

    # --- 3. entity coverage ---------------------------------------------------------------------------------------
    _cover_entities(
        si,
        screens,
        entities,
        eclass,
        modules,
        reg,
        app_ids,
        app_roles,
        roles,
        app_set,
        amap,
        notes,
        live_features,
        feat,
        ralias,
    )

    # --- 4. flows: every step must resolve to a screen --------------------------------------------------------------
    for fl in si.structure.flows:
        for st in fl.steps:
            if not st.screen:
                continue
            if st.screen not in instances and st.screen not in screen_alias:
                notes.append(
                    f"flow {fl.id} step names {st.screen}, which is not a screen of the structure"
                )

    # --- 5. integrations: a payment need has a pay step -------------------------------------------------------------
    if "payments" in facts:
        pay_words = ["pay", "payment", "payments", "checkout", "invoice", "invoices", "billing"]
        if not any(p.screen_type == "checkout" or has_word(p.name, pay_words) for p in screens):
            host = next(
                (
                    p
                    for p in screens
                    if any(has_word(f_name(si, x), pay_words) for x in p.feature_ids)
                ),
                None,
            ) or (screens[0] if screens else None)
            if host is not None:
                pid = reg.mint("SCR")
                screens.append(
                    ScreenProposal(
                        id=pid,
                        name="Pay",
                        screen_type="checkout",
                        app=host.app,
                        module=host.module,
                        role_ids=host.role_ids,
                        feature_ids=host.feature_ids,
                        entity_ids=host.entity_ids,
                        reasons=[
                            "payments are needed and no screen takes a payment: a pay step is added (SPC-IN-09)"
                        ],
                        phase="derived",
                        entity_uses=host.entity_uses,
                        source="derived",
                        provenance=(("assumed", "payments need a pay step"),),
                    )
                )

    # --- 6. role homes: a role with no screen at all gets one ---------------------------------------------------------
    for rid, r in sorted(roles.items()):
        if not any(rid in p.role_ids for p in screens):
            app = sorted(role_apps[rid])[0]
            pid = reg.mint("SCR")
            screens.append(
                ScreenProposal(
                    id=pid,
                    name=f"{r.name} home",
                    screen_type="dashboard",
                    app=app,
                    module=None,
                    role_ids=(rid,),
                    feature_ids=(),
                    entity_ids=(),
                    reasons=[f"role {rid} has no screen: a home is added"],
                    phase="derived",
                    source="derived",
                    provenance=(("assumed", f"home of role {rid}"),),
                )
            )

    # --- 7. peer check ------------------------------------------------------------------------------------------------
    peers = _peer_check(research_view, screens, reg, app_ids, notes)

    # --- 8. the system set per app ------------------------------------------------------------------------------------
    system_module = _system_set(
        si, screens, kept_info, app_roles, facts, profile, reg, modules, links_out := [], notes
    )

    screens.sort(key=lambda p: (app_ids.index(p.app), p.id))
    return Inventory(
        screens=screens,
        apps=kept_info,
        roles=roles,
        role_alias=ralias,
        feature_alias=falias,
        screen_alias=screen_alias,
        compass_instances=instances,
        system_module=system_module,
        derived_modules=derived_modules,
        links_out=links_out,
        peers_usually_have=peers,
        merges=merges,
        notes=notes,
        registry=reg,
        facts=facts,
        profile=profile,
        structure_derived=structure_derived,
    )


def f_name(si: SpecInput, fid: str) -> str:
    return next((f.name for f in si.features if f.id == fid), "")


# ---------------------------------------------------------------- pieces


def _derive_from_brief(
    si: SpecInput,
    screens: list[ScreenProposal],
    instances: dict[str, list[str]],
    reg: IdRegistry,
    app_ids: list[str],
    app_roles: dict[str, tuple[str, ...]],
    live_features: set[str],
    feat: Any,
    notes: list[str],
    derived_modules: list[tuple[str, str, str]],
) -> None:
    """Compass format 1 has no structure: a list and a detail screen per in-scope feature, a form where the feature
    says something is created or added. Flagged ``derived``; the person confirms the structure at G1.5."""
    create_words = [
        "add",
        "create",
        "new",
        "book",
        "order",
        "register",
        "submit",
        "request",
        "post",
        "list",
    ]
    notes.append(
        "Compass has no structure: screens are derived from the features (more calls, flagged)"
    )
    module_of: dict[str, str] = {}
    for f in si.in_scope_features():
        if f.id not in live_features or f.scope_status != "in":
            continue
        # a feature that names no app is drafted once, in the first app (the person confirms it at G1.5)
        targets = [a for a in app_ids if a in set(f.apps)] or app_ids[:1]
        if not f.apps:
            notes.append(
                f"feature {f.id} {f.name!r} names no app: its screens are drafted in {targets[0]}"
            )
        kinds = ["list", "detail"] + (
            ["form"] if has_word(f"{f.name} {f.intent}", create_words) else []
        )
        for app in targets:
            if app not in module_of:
                module_of[app] = reg.mint("MOD")
                derived_modules.append((module_of[app], "Main", app))
            for kind in kinds:
                pid = reg.mint("SCR")
                screens.append(
                    ScreenProposal(
                        id=pid,
                        name=f"{f.name}" if kind == "list" else f"{f.name}: {kind}",
                        screen_type=kind,
                        app=app,
                        module=module_of[app],
                        role_ids=app_roles.get(app, ()),
                        feature_ids=(feat(f.id),),
                        entity_ids=(),
                        reasons=["derived from the Brief's features (no Compass structure)"],
                        phase="brief",
                        source="derived",
                        purpose=first_sentences(f.intent, 2),
                        provenance=(("user", f.id),),
                    )
                )


def _merge_forms(
    screens: list[ScreenProposal],
    entities: dict[str, InEntity],
    reg: IdRegistry,
    screen_alias: dict[str, str],
    merges: list[MergeNote],
    instances: dict[str, list[str]],
) -> None:
    groups: dict[tuple[str, str | None, str], list[ScreenProposal]] = defaultdict(list)
    for p in screens:
        if p.screen_type in ("form", "wizard") and len(p.entity_uses) == 1:
            ent, use = p.entity_uses[0]
            if use in ("create", "edit"):
                groups[(p.app, p.module, ent)].append(p)
    for (_app, _mod, eid), group in sorted(groups.items(), key=lambda kv: kv[0][2]):
        uses = [p.entity_uses[0][1] for p in group]
        if sorted(uses) != ["create", "edit"]:
            continue
        group.sort(key=lambda p: p.id)
        base = group[0]
        ent_name = entities[eid].name
        for other in group[1:]:
            reg.alias(other.id, base.id)
            screen_alias[other.id] = base.id
            base.aliases.append(other.id)
            base.feature_ids = tuple(sorted(set(base.feature_ids) | set(other.feature_ids)))
            base.role_ids = tuple(sorted(set(base.role_ids) | set(other.role_ids)))
            base.reasons.append(
                f"merged {other.id} {other.name!r}: create and edit are one form with a mode"
            )
            merges.append(
                MergeNote(
                    base.id, other.id, "create and edit of one entity are one form with a mode"
                )
            )
            screens.remove(other)
            for ids in instances.values():
                for i, x in enumerate(ids):
                    if x == other.id:
                        ids[i] = base.id
        if any(p.phase == "core" for p in group):
            base.phase = "core"  # one of the two was core, so the merged form is
        base.entity_uses = ((eid, "create"), (eid, "edit"))
        base.mode = "create_edit"
        base.source = "merged"
        base.name = f"{ent_name} form"


def _cover_entities(
    si: SpecInput,
    screens: list[ScreenProposal],
    entities: dict[str, InEntity],
    eclass: dict[str, str],
    modules: dict[str, Any],
    reg: IdRegistry,
    app_ids: list[str],
    app_roles: dict[str, tuple[str, ...]],
    roles: dict[str, RoleInfo],
    app_set: set[str],
    amap: dict[str, Any],
    notes: list[str],
    live_features: set[str],
    feat: Any,
    ralias: dict[str, str | None],
) -> None:
    """An entity nobody can see gets a screen; an entity a role can create or edit gets a form."""
    used: dict[str, list[ScreenProposal]] = defaultdict(list)
    for p in screens:
        for e, _ in p.entity_uses:
            used[e].append(p)
    levels_order = ["none", "view", "create", "edit", "manage"]
    for eid, ent in sorted(entities.items()):
        if eclass.get(eid) in ("join", "log"):
            continue
        acc: dict[str, str] = {}
        for rid, lv in ent.access.items():
            rr = _role_id(ralias, roles, rid)
            if rr and levels_order.index(
                lv if lv in levels_order else "none"
            ) >= levels_order.index(acc.get(rr, "none")):
                acc[rr] = lv
        viewers = {r for r, lv in acc.items() if lv != "none"}
        makers = {r for r, lv in acc.items() if lv in ENTITY_PRODUCER_LEVELS}
        if ent.access and not viewers:
            continue  # data only: nobody may see it
        mod = modules.get(ent.module)
        if mod is None:
            continue
        mod_apps = [
            a for a in dict.fromkeys(_resolve(amap, x) for x in mod.apps) if a in app_set
        ] or app_ids[:1]
        feats = tuple(sorted({feat(x) for x in mod.features if feat(x) in live_features}))

        def pick(who: set[str], pool: list[str]) -> tuple[str, tuple[str, ...]] | None:
            for a in pool:
                mine = tuple(sorted(who & set(app_roles.get(a, ()))))
                if mine:
                    return a, mine
            return None

        has_screen = bool(used.get(eid))
        has_producer = any(
            u in ("create", "edit") for p in used.get(eid, []) for e, u in p.entity_uses if e == eid
        )
        if not has_screen:
            chosen = (
                pick(viewers, mod_apps)
                if ent.access
                else (mod_apps[0], app_roles.get(mod_apps[0], ()))
            )
            chosen = chosen or pick(viewers, app_ids)
            if chosen is None:
                notes.append(
                    f"entity {eid} {ent.name!r} has no screen and no role of an app may see it"
                )
            else:
                pid = reg.mint("SCR")
                screens.append(
                    ScreenProposal(
                        id=pid,
                        name=plural(ent.name),
                        screen_type="list",
                        app=chosen[0],
                        module=ent.module,
                        role_ids=chosen[1],
                        feature_ids=feats,
                        entity_ids=(eid,),
                        reasons=[
                            f"entity {eid} {ent.name!r} has no screen that shows it: a list is added"
                        ],
                        phase="derived",
                        entity_uses=((eid, "view"),),
                        source="derived",
                        provenance=(("assumed", f"entity {eid} has no screen"),),
                    )
                )
        if makers and not has_producer:
            chosen = pick(makers, mod_apps) or pick(makers, app_ids)
            if chosen is None:
                continue
            pid = reg.mint("SCR")
            screens.append(
                ScreenProposal(
                    id=pid,
                    name=f"{ent.name} form",
                    screen_type="form",
                    app=chosen[0],
                    module=ent.module,
                    role_ids=chosen[1],
                    feature_ids=feats,
                    entity_ids=(eid,),
                    reasons=[
                        f"a role can create or edit {ent.name!r} and no screen does: one form with a create and edit mode is added"
                    ],
                    phase="derived",
                    entity_uses=((eid, "create"), (eid, "edit")),
                    source="derived",
                    mode="create_edit",
                    provenance=(("assumed", f"entity {eid} has no form"),),
                )
            )


def _norm_name(text: str) -> frozenset[str]:
    return stem_set(text)


def peer_rank(name: str, research_view: ResearchView | None) -> int:
    """How common a feature or screen is among peers (Research norm level, 0 to 3); 0 when unknown."""
    if research_view is None or research_view.thin:
        return 0
    want = _norm_name(name)
    if not want:
        return 0
    best = 0
    ranks = inventory_data().peers.level_rank
    for n in research_view.norms:
        if n.area not in ("screen", "feature"):
            continue
        have = _norm_name(n.label or n.key)
        if have and len(want & have) / len(want | have) >= 0.5:
            best = max(best, ranks.get(str(n.prevalence.get("level", "")), 0))
    return best


def _peer_check(
    research_view: ResearchView | None,
    screens: list[ScreenProposal],
    reg: IdRegistry,
    app_ids: list[str],
    notes: list[str],
) -> list[PeerGap]:
    out: list[PeerGap] = []
    if research_view is None or (research_view.thin and not research_view.norms):
        return out
    peers = inventory_data().peers
    have = [_norm_name(p.name) for p in screens]
    for n in sorted(research_view.norms, key=lambda x: x.label or x.key):
        if n.area != "screen" or n.matches_brief:
            continue
        label = n.label or n.key
        want = _norm_name(label)
        if not want or any(h and len(want & h) / len(want | h) >= 0.5 for h in have):
            continue
        level = str(n.prevalence.get("level", ""))
        items = [i for i in research_view.items if i.id in n.item_ids]
        evidence = items[0].evidence if items else "unverified"
        best = max(
            (i.evidence for i in items),
            key=lambda e: (
                ["unverified", "single_source", "corroborated", "official"].index(e)
                if e in ("unverified", "single_source", "corroborated", "official")
                else 0
            ),
            default="unverified",
        )
        evidence = best
        if evidence not in peers.add_when_evidence:
            out.append(
                PeerGap(
                    label,
                    evidence,
                    level,
                    n.item_ids,
                    False,
                    f"evidence is {evidence}: left out, listed at G2",
                )
            )
        elif level not in peers.add_when_level:
            out.append(
                PeerGap(
                    label,
                    evidence,
                    level,
                    n.item_ids,
                    False,
                    f"only {level or 'rarely'} among peers: left out, listed at G2",
                )
            )
        else:
            host = next((p for p in screens if not p.system), None)
            if host is None:
                out.append(
                    PeerGap(
                        label,
                        evidence,
                        level,
                        n.item_ids,
                        False,
                        "no screen to anchor it on: left out",
                    )
                )
                continue
            pid = reg.mint("SCR")
            screens.append(
                ScreenProposal(
                    id=pid,
                    name=label[:1].upper() + label[1:],
                    screen_type=peers.peer_screen_type,
                    app=host.app,
                    module=host.module,
                    role_ids=host.role_ids,
                    feature_ids=(),
                    entity_ids=(),
                    reasons=[f"peers usually have it ({level}, {evidence}): added as Default"],
                    phase="derived",
                    source="peer",
                    provenance=(("research", n.item_ids[0] if n.item_ids else ""),),
                )
            )
            out.append(PeerGap(label, evidence, level, n.item_ids, True, "added as Default", pid))
    return out


def _system_set(
    si: SpecInput,
    screens: list[ScreenProposal],
    apps: list[AppInfo],
    app_roles: dict[str, tuple[str, ...]],
    facts: set[str],
    profile: str,
    reg: IdRegistry,
    modules: dict[str, Any],
    links_out: list[str],
    notes: list[str],
) -> tuple[str, str] | None:
    """The system set, once for the program: shared screens are specified once and used by every app (doc 19 2.3),
    built in the first app. A Compass screen that already is one satisfies it; a legal screen that may be a link out is
    a link, not a screen."""
    data = inventory_data()
    wanted = libraries.system_screens_for(facts, profile)
    order = list(data.system_match)
    sheet = default_policy_sheet()
    account_modules = {m.id: m for m in modules.values() if m.kind == "account"}
    system_module: tuple[str, str] | None = None
    home = apps[0].id
    all_apps = tuple(a.id for a in apps)
    all_roles = tuple(sorted({r for a in apps for r in app_roles.get(a.id, ())}))

    mine = [p for p in screens if not p.system]
    claimed: dict[str, ScreenProposal] = {}
    taken: set[str] = set()
    for sid in order:
        m = data.system_match[sid]
        for p in mine:
            if p.id in taken:
                continue
            if p.screen_type in m.types and has_word(p.name, m.words):
                claimed[sid] = p
                taken.add(p.id)
                break
    for sc in wanted:
        variants = tuple(libraries.active_variants(sc, facts))
        hit = claimed.get(sc.id)
        if hit is not None:
            hit.system = True
            hit.system_id = sc.id
            hit.variants = variants
            hit.reasons.append(f"is the system screen {sc.id!r}")
            continue
        if sc.link_out_allowed:
            links_out.append(sc.id)
            continue
        module = next(
            (mid for mid, m in sorted(account_modules.items()) if home in m.apps),
            None,
        )
        if module is None and system_module is None:
            system_module = (reg.mint("MOD"), "Account and help")
        if module is None and system_module is not None:
            module = system_module[0]
        rule = data.system_policy.get(sc.id)
        prov: tuple[tuple[str, str], ...]
        if sc.provenance.kind == "policy" and rule:
            prov = (("policy", policy_ref(rule, sheet)),)
        else:
            prov = (("library", f"system_screens.{sc.id}"),)
        pid = reg.mint("SCR")
        screens.append(
            ScreenProposal(
                id=pid,
                name=sc.name,
                screen_type=sc.screen_type,
                app=home,
                module=module,
                role_ids=all_roles,
                feature_ids=(),
                entity_ids=(),
                reasons=[
                    f"system set: {sc.id} appears when its condition holds; shared by every app"
                ],
                shared_apps=all_apps,
                system=True,
                purpose=sc.purpose,
                phase="system",
                source="system",
                system_id=sc.id,
                variants=variants,
                provenance=prov,
            )
        )
    # derived screens with no module (a home for a role) join a system module
    orphans = [p for p in screens if p.module is None]
    if orphans:
        if system_module is None:
            system_module = (reg.mint("MOD"), "Account and help")
        for p in orphans:
            p.module = system_module[0]
    return system_module
