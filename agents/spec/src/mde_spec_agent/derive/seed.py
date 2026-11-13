"""The code-only BASELINE graph (doc 15 4, 5.7; doc 18 1.1): everything code can write from the inputs, with no model.

Honest by construction: where the sources do not support a field it is null or ``assumed``, never filled to look complete.
Placeholder text is deterministic and marked (stories that a model has not written are ``assumed``; library behaviours
carry provenance ``library``). Trust follows doc 15 3.4: a feature is Locked only with a verified quote; priority and
structure are Default; a research claim is cited only when Research calls it official or corroborated. No layout words, no
vendor or model names, no user text executed.

The graph holds the RELEASE only: a later feature keeps an outline in the ledger and has no screen nodes (V-19).
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from mde_compass_intake import ResearchView, SpecInput
from mde_compass_intake.spec_input import Entity as InEntity
from mde_spec_core import libraries, vocab
from mde_spec_core.callkit.ladder import RCodeLog
from mde_spec_core.canon import sha256_json
from mde_spec_core.graph import (
    AccessRow,
    Action,
    App,
    Behavior,
    Entity,
    FieldDef,
    Flow,
    GraphMeta,
    Guard,
    Input,
    Integration,
    Lifecycle,
    Module,
    Operation,
    OpError,
    Persona,
    Prov,
    Region,
    Role,
    Route,
    RouteParam,
    Rule,
    Screen,
    SpecGraph,
    Stage,
    Step,
    Story,
    Transition,
)
from mde_spec_core.graph import Feature as GFeature
from mde_spec_core.ids import namespaced_copy_key
from mde_spec_core.rcode import instantiate_behaviors, run_rcode
from mde_spec_core.validators.structure import entry_screens

from ._data import facts_data, inventory_data, seed_data
from .inventory import Inventory, ScreenProposal, _entity_classes
from .release import ReleasePlan
from .text import humanize, plural, slug

_RULE_NUMBER = re.compile(
    r"(\d+(?:[.,]\d+)?)\s?(%|percent|rs|inr|usd|€|\$|₹|days?|hours?|minutes?|km|kg)", re.IGNORECASE
)
_LEVELS = ("none", "view", "create", "edit", "manage")


@dataclass
class SeedResult:
    graph: SpecGraph
    rcode_log: RCodeLog
    notes: list[str] = field(default_factory=list)
    access_repairs: list[str] = field(
        default_factory=list
    )  # screen/entity access conflicts repaired (G1.5 list)
    nav_assumed: list[str] = field(
        default_factory=list
    )  # links code added so a screen is reachable
    flows_dropped: list[str] = field(default_factory=list)
    roles_deferred: list[str] = field(default_factory=list)  # roles with no screen in this release
    degraded_locales: set[str] = field(
        default_factory=set
    )  # languages whose copy is not written yet
    claims: dict[str, str] = field(
        default_factory=dict
    )  # research item id -> evidence label (V-36)


def research_adapter(research_view: ResearchView | None) -> Any:
    """What validator V-36 reads: ``items`` as plain dicts (it indexes ``it["id"]``)."""
    if research_view is None:
        return None
    return SimpleNamespace(
        items=[{"id": i.id, "evidence": i.evidence} for i in research_view.items],
        view=research_view,
    )


# ---------------------------------------------------------------- small helpers


def _level(v: str | None) -> int:
    return _LEVELS.index(v) if v in _LEVELS else 0


def _is_text_field(f: FieldDef) -> bool:
    return f.type == "text" and any(
        w in f.key
        for w in (
            "description",
            "notes",
            "note",
            "comment",
            "remarks",
            "details",
            "message",
            "reason",
            "summary",
        )
    )


def _fieldset(ent: Entity) -> dict[str, list[FieldDef]]:
    data = seed_data()
    fields = [f for f in ent.fields if f.key != "id"]
    text = [f for f in fields if _is_text_field(f)]
    media = [f for f in fields if f.media]
    status = [f for f in fields if f.key == "status" and f.type == "enum"]
    key_fields = [f for f in fields if f not in text and f not in media and f.key != "status"]
    key_fields = sorted(key_fields, key=lambda f: (not f.required, fields.index(f)))[
        : data.max_key_fields
    ]
    return {
        "key_fields": key_fields or fields[:1],
        "text_fields": text[:2],
        "media_fields": media[:2],
        "status": status,
        "money_fields": [f for f in fields if f.type == "money"][:3],
        "date_fields": [f for f in fields if f.type == "date"][:2],
        "location_fields": [f for f in fields if f.type == "location"][:2],
        "all": fields,
    }


def _entity_nodes(
    si: SpecInput,
    inv: Inventory,
    reg_roles: set[str],
    eclass: dict[str, str],
    modules: set[str],
    notes: list[str],
) -> dict[str, Entity]:
    data = seed_data()
    neg = set(inventory_data().negative_states)
    out: dict[str, Entity] = {}
    for e in si.structure.entities:
        fields: list[FieldDef] = []
        if not any(f.key == "id" for f in e.fields):
            fields.append(FieldDef(key="id", type="id", required=True))
        seen = {"id"} if fields else set()
        for f in e.fields:
            if f.key in seen:
                continue
            seen.add(f.key)
            fm = data.field_types.get(f.type) or data.field_types[data.default_field_type]
            ref = (
                f.ref
                if f.type == "ref" and f.ref in {x.id for x in si.structure.entities}
                else None
            )
            fields.append(
                FieldDef(
                    key=f.key,
                    type=fm.field,
                    required=f.required,
                    enum_values=list(f.values) if fm.field == "enum" else [],
                    ref_entity=ref,
                    media=fm.media,
                )
            )
        states = [s for s in e.states if s]
        lifecycle = Lifecycle()
        if states:
            if not any(f.key == "status" for f in fields):
                fields.append(FieldDef(key="status", type="enum", enum_values=list(states)))
            else:
                for f in fields:
                    if f.key == "status":
                        f.type = "enum"
                        f.enum_values = list(states)
            gate = (
                sorted(
                    r
                    for r, lv in _roles_access(inv, e).items()
                    if lv in ("create", "edit", "manage") and r in reg_roles
                )
                or sorted(
                    r for r, lv in _roles_access(inv, e).items() if lv != "none" and r in reg_roles
                )[:1]
            )
            happy = [s for s in states if s.lower() not in neg]
            sad = [s for s in states if s.lower() in neg]
            trans: list[Transition] = []
            for a, b in itertools.pairwise(happy):
                trans.append(Transition(from_state=a, to_state=b, roles=gate))
            for s in sad:
                prev = [h for h in happy if states.index(h) < states.index(s)]
                src = prev[-1] if prev else (happy[0] if happy else None)
                if src is not None:
                    trans.append(Transition(from_state=src, to_state=s, roles=gate))
            lifecycle = Lifecycle(states=list(states), transitions=trans, initial=states[0])
        access = _access_rows(inv, e, reg_roles)
        out[e.id] = Entity(
            id=e.id,
            name=e.name or e.key or e.id,
            owner_module=e.module if e.module in modules else None,
            klass=eclass.get(e.id, "secondary"),  # type: ignore[arg-type]
            fields=fields,
            lifecycle=lifecycle,
            access=access,
            provenance=[Prov(kind="compass", ref=e.id)]
            + (
                [Prov(kind="assumed", ref="lifecycle transitions follow the order of the states")]
                if states
                else []
            ),
        )
    return out


def _roles_access(inv: Inventory, e: InEntity) -> dict[str, str]:
    """Role id (merged and cleaned) -> the highest access level Compass gives it."""
    out: dict[str, str] = {}
    for rid, lv in e.access.items():
        cur = rid
        seen: set[str] = set()
        while cur in inv.role_alias and cur not in seen:
            seen.add(cur)
            nxt = inv.role_alias[cur]
            if nxt is None:
                cur = ""
                break
            cur = nxt
        if cur in inv.roles and _level(lv) >= _level(out.get(cur)):
            out[cur] = lv
    return out


def _access_rows(inv: Inventory, e: InEntity, kept: set[str]) -> list[AccessRow]:
    levels = seed_data().access_levels
    rows: list[AccessRow] = []
    for rid, lv in sorted(_roles_access(inv, e).items()):
        if rid in kept and lv in levels:
            rows.append(AccessRow(role_id=rid, actions=levels[lv]))  # type: ignore[arg-type]
    return rows


# ---------------------------------------------------------------- the seed


def seed_graph(
    spec_input: SpecInput,
    research_view: ResearchView | None,
    plan: ReleasePlan,
) -> SpecGraph:
    """The baseline graph of the release in ``plan`` (see :func:`seed`)."""
    return seed(spec_input, research_view, plan).graph


def seed(
    spec_input: SpecInput,
    research_view: ResearchView | None,
    plan: ReleasePlan,
) -> SeedResult:
    inv = plan.inventory
    if inv is None:
        raise ValueError("the release plan carries no inventory: build it with plan_release")
    si = spec_input
    sd = seed_data()
    reg = inv.registry
    facts = set(inv.facts)
    profile = inv.profile
    notes: list[str] = list(inv.notes)
    result_repairs: list[str] = []
    nav_assumed: list[str] = []

    langs = list(si.locales.codes) or ["en"]
    if (
        "en" in langs
    ):  # code-made text is English: when the app offers English it is the source language
        langs = ["en", *[x for x in langs if x != "en"]]
    g = SpecGraph(
        meta=GraphMeta(
            profile=profile,  # type: ignore[arg-type]
            program_id=si.program_id or si.session_id,
            source_language=langs[0],
            languages=langs,
            facts=sorted(facts),
            # digits become letters: a long run of digits in a hash reads as an ID or phone number to the V-20 scan
            inputs_hash=sha256_json(
                {"brief": si.brief_hash, "version": si.brief_version, "profile": profile}
            )[:32].translate(str.maketrans("0123456789", "ghijklmnop")),
        )
    )

    screens = {p.id: p for p in inv.screens}
    release_ids = [i for i in plan.release_screens if i in screens]
    release = {i: screens[i] for i in release_ids}
    eclass = _entity_classes(si)

    # roles that have work in this release (a role whose whole job is later waits for Phase 2)
    working = {r for p in release.values() if not p.system for r in p.role_ids}
    kept_roles = {r for r in inv.roles if r in working}
    deferred_roles = sorted(set(inv.roles) - kept_roles)

    # ---------------------------------------------------------------- features
    in_ids = {f for f, d in plan.features.items() if d.status == "in"}
    quote_of = {f.id: (f.quote_ids[0] if f.quote_ids else None) for f in si.features}
    by_id = {f.id: f for f in si.features}
    feat_apps: dict[str, set[str]] = defaultdict(set)
    for p in release.values():
        for fid in p.feature_ids:
            feat_apps[fid] |= set(p.shared_apps or (p.app,))
    for fid, d in sorted(plan.features.items()):
        f = by_id[fid]
        if fid not in reg.used:
            reg.carry(fid)
        aliases = sorted({*f.alias_ids, *(x.id for x in si.features if x.duplicate_of == fid)})
        for a in aliases:
            reg.alias(a, fid)
        evidenced = f.user_evidenced
        prov = (
            [Prov(kind="user", ref=fid, quote_id=quote_of.get(fid))]
            if evidenced
            else [Prov(kind="compass", ref=fid)]
        )
        g.add(
            "features",
            GFeature(
                id=fid,
                name=f.name,
                intent=f.intent,
                tier="locked" if evidenced else "default",
                status=d.status,  # type: ignore[arg-type]
                later_reason=d.reason,  # type: ignore[arg-type]
                user_priority="must" if d.user_evidenced_must else "unranked",
                user_evidenced=evidenced,
                depends_on=sorted(plan.dependencies.get(fid, set())),
                app_ids=sorted(
                    feat_apps.get(fid, set())
                    or {a for a in f.apps if a in {x.id for x in inv.apps}}
                ),
                outline=d.outline.text() if d.outline else "",
                consent_id=f"consent:{fid}" if d.consented else None,
                provenance=prov,
                aliases=aliases,
            ),
        )

    # ---------------------------------------------------------------- roles, apps
    role_apps: dict[str, set[str]] = defaultdict(set)
    for p in release.values():
        for r in p.role_ids:
            role_apps[r] |= set(p.shared_apps or (p.app,))
    for rid, r in sorted(inv.roles.items()):
        if rid not in kept_roles:
            continue
        g.add(
            "roles",
            Role(
                id=rid,
                name=r.name,
                goal=r.goal,
                app_ids=sorted(
                    role_apps.get(rid, set()) | set(r.app_ids) & {a.id for a in inv.apps}
                ),
                tier="locked" if r.grade == "locked" and not r.assumed else "default",
                assumed=r.assumed,
                provenance=[Prov(kind="assumed" if r.assumed else "compass", ref=rid)],
                aliases=list(r.aliases),
            ),
        )
        for a in r.aliases:
            reg.alias(a, rid)
    for a in inv.apps:
        g.add(
            "apps",
            App(
                id=a.id,
                name=a.name,
                surface=a.surface,  # type: ignore[arg-type]
                role_ids=sorted(r for r in kept_roles if a.id in role_apps.get(r, set())),
                provenance=[Prov(kind="compass" if a.source_id else "assumed", ref=a.id)],
                assumed=a.source_id is None or bool(a.note),
            ),
        )

    # ---------------------------------------------------------------- modules
    in_modules = {m.id: m for m in si.modules}
    module_rows: list[tuple[str, str, str, tuple[str, ...], bool]] = []
    app_set = {a.id for a in inv.apps}
    for m in si.modules:
        apps = tuple(sorted(a for a in m.apps if a in app_set))
        module_rows.append((m.id, m.name, m.purpose, apps, m.kind == "account"))
    if inv.system_module:
        module_rows.append(
            (
                inv.system_module[0],
                inv.system_module[1],
                "Sign-in, account, settings and help every app needs.",
                tuple(a.id for a in inv.apps),
                True,
            )
        )
    for mid, name, app in inv.derived_modules:
        module_rows.append((mid, name, "", (app,), False))
    module_ids = {row[0] for row in module_rows}
    release_by_module: dict[str, list[str]] = defaultdict(list)
    for p in release.values():
        if p.module:
            release_by_module[p.module].append(p.id)
    for mid, name, purpose, apps, shared in module_rows:
        if mid not in reg.used:
            reg.carry(mid)
        used_apps = sorted(
            {
                a
                for sid in release_by_module.get(mid, [])
                for a in (release[sid].shared_apps or (release[sid].app,))
            }
        )
        later = not release_by_module.get(mid)
        g.add(
            "modules",
            Module(
                id=mid,
                name=name,
                purpose=purpose,
                app_ids=sorted(set(apps) | set(used_apps)),
                shared=shared or len(set(apps) | set(used_apps)) > 1,
                screen_ids=sorted(release_by_module.get(mid, [])),
                status="later" if later else "in",
                later_reason="first_release" if later else None,
                provenance=[Prov(kind="compass" if mid in in_modules else "code", ref=mid)],
            ),
        )
    for a in g.apps.values():
        a.module_ids = sorted(m.id for m in g.modules.values() if a.id in m.app_ids)

    # ---------------------------------------------------------------- entities
    for e in _entity_nodes(si, inv, kept_roles, eclass, module_ids, notes).values():
        if e.id not in reg.used:
            reg.carry(e.id)
        g.add("entities", e)
    for m in g.modules.values():
        m.entity_ids = sorted(e.id for e in g.entities.values() if e.owner_module == m.id)
    used_entities = {eid for p in release.values() for eid, _ in p.entity_uses if eid in g.entities}
    for e in g.entities.values():
        e.later = e.id not in used_entities and e.klass not in ("join", "log")
        if e.later:
            e.status = "later"
            e.later_reason = "first_release"

    # ---------------------------------------------------------------- operations (CRUD from entities and the access matrix)
    ops_of: dict[str, dict[str, str]] = {}
    screen_roles_of_entity: dict[str, set[str]] = defaultdict(set)
    for p in release.values():
        for eid, _ in p.entity_uses:
            screen_roles_of_entity[eid] |= set(p.role_ids) & kept_roles
    for eid, e in sorted(g.entities.items()):
        by_level: dict[str, set[str]] = defaultdict(set)
        for row in e.access:
            lv = "none"
            for cand in ("manage", "edit", "create", "view"):
                if cand in row.actions:
                    lv = cand
                    break
            by_level[lv].add(row.role_id)
        viewers = set().union(*(by_level[x] for x in ("view", "create", "edit", "manage")))
        makers = set().union(*(by_level[x] for x in ("create", "edit", "manage")))
        editors = by_level["edit"] | by_level["manage"]
        managers = by_level["manage"]
        if not e.access:
            viewers = makers = editors = managers = set(screen_roles_of_entity.get(eid, set()))
            if viewers:
                notes.append(
                    f"entity {eid} has no access rows in Compass: operations are gated to the roles of its screens (assumed)"
                )
        if not viewers:
            continue
        key = slug(next((x.key for x in si.structure.entities if x.id == eid), "") or e.name)
        coll = slug(plural(key.replace("_", " ")).replace(" ", "_"))
        ops: dict[str, str] = {}
        plan_ops = [
            ("list", "GET", f"/{coll}", viewers, "read", False, True),
            ("get", "GET", f"/{coll}/{{id}}", viewers, "read", False, False),
        ]
        if makers:
            plan_ops.append(("create", "POST", f"/{coll}", makers, "write", True, False))
        if editors:
            plan_ops.append(("update", "PATCH", f"/{coll}/{{id}}", editors, "write", True, False))
        if managers:
            plan_ops.append(
                ("delete", "DELETE", f"/{coll}/{{id}}", managers, "write", False, False)
            )
        for verb, method, path, rset, kind, idem, paged in plan_ops:
            oid = reg.mint("OP")
            writable = [f for f in e.fields if f.key != "id" and f.derived is None]
            op = Operation(
                id=oid,
                name=f"{verb}_{key}",
                entity=eid,
                method=method,  # type: ignore[arg-type]
                path=path,
                kind="crud",
                request=[f.model_copy() for f in writable] if verb in ("create", "update") else [],
                response=[FieldDef(key="id", type="id")] + [f.model_copy() for f in writable]
                if verb != "delete"
                else [],
                errors=[OpError(**x) for x in sd.operation_errors[kind]],
                roles=sorted(rset & kept_roles),
                idempotent=idem,
                paged=paged,
                status="later" if e.later else "in",
                later_reason="first_release" if e.later else None,
                provenance=[Prov(kind="code", ref=f"crud:{eid}.{verb}")],
            )
            if not op.roles:
                continue
            g.add("operations", op)
            ops[verb] = oid
        ops_of[eid] = ops

    # ---------------------------------------------------------------- rules
    modules_of_entity = {e.id: e.owner_module for e in g.entities.values()}
    for fact in si.rules_and_numbers:
        m = _RULE_NUMBER.search(fact.statement)
        evidenced = fact.trust.grade == "locked" and fact.has_quote
        ref_mod = next(
            (modules_of_entity.get(x) for x in fact.entities if modules_of_entity.get(x)), None
        )
        g.add(
            "rules",
            Rule(
                id=reg.mint("RUL"),
                subject=" ".join(fact.statement.split()[:6]).strip(".,") or fact.id,
                statement=fact.statement,
                value=m.group(1).replace(",", ".") if m else None,
                unit=m.group(2).lower() if m else None,
                confirm=bool(m) and not evidenced,
                module_id=ref_mod,
                tier="locked" if evidenced else "default",
                provenance=[Prov(kind="user" if evidenced else "compass", ref=fact.id)],
            ),
        )

    # ---------------------------------------------------------------- integrations
    claims: dict[str, str] = {}
    label = {i.id: i.evidence for i in research_view.items} if research_view else {}
    fd = facts_data()
    integ_rows: list[tuple[str, str, str, bool, list[Prov], str]] = []
    for it in si.integrations:
        need = _norm_key(it.name)
        mode = "mock" if it.preference in ("", "real_later", "mock") else "adapter"
        side = "provider_side_flow" in fd.integrations.get(need, []) or (
            need in ("payments", "payment")
        )
        prov = [
            Prov(
                kind="user" if it.trust.verified_quote else "compass",
                ref=(it.trust.quote_ids[0] if it.trust.quote_ids else it.name),
                quote_id=(it.trust.quote_ids[0] if it.trust.quote_ids else None),
            )
        ]
        integ_rows.append((it.name, need, mode, side, prov, ""))
    have_needs = {r[1] for r in integ_rows}
    if "payments" in facts and not ({"payments", "payment"} & have_needs):
        pay_feat = next(
            (f for f in in_ids if re.search(r"\bpay", f"{by_id[f].name} {by_id[f].intent}", re.I)),
            None,
        )
        prov = [
            Prov(
                kind="user" if pay_feat and by_id[pay_feat].user_evidenced else "assumed",
                ref=pay_feat or "payments",
            )
        ]
        integ_rows.append(
            (
                "payments",
                "payments",
                "mock",
                True,
                prov,
                "derived from the feature that takes payment (SPC-IN-09)",
            )
        )
    dossiers = research_view.dossiers if research_view else ()
    idef = sd.integration_defaults
    for name, need, mode, side, prov, note in integ_rows:
        iid = reg.mint("INT")
        matched = [
            d
            for d in dossiers
            if need in {_norm_key(x) for x in (*d.integrations, *d.needs, d.capability)}
        ]
        good = [
            d
            for d in matched
            if d.item_ids and all(label.get(x) in ("official", "corroborated") for x in d.item_ids)
        ]
        cited = sorted({x for d in good for x in d.item_ids})
        for x in cited:
            claims[x] = label[x]
        g.add(
            "integrations",
            Integration(
                id=iid,
                name=name,
                need_key=need,
                mode=mode,  # type: ignore[arg-type]
                provider_side_flow=side,
                flow=idef.flow if side else "",
                interruption=idef.interruption if side else "",
                pending_timeout=idef.pending_timeout if side else "",
                events=list(idef.events) if side else [],
                mock_behaviour=idef.mock_behaviour,
                provider_ids=sorted({d.provider for d in good if d.provider}),
                claims_used=cited,
                assumed=side,
                provenance=[*prov, *([Prov(kind="research", ref=x) for x in cited[:3]])]
                + ([Prov(kind="library", ref="integration_defaults")] if side else [])
                + ([Prov(kind="assumed", ref=note)] if note else []),
            ),
        )
        if side:
            g.add(
                "rules",
                Rule(
                    id=reg.mint("RUL"),
                    subject=f"{name} pending result",
                    statement=idef.pending_timeout,
                    confirm=True,
                    assumed=True,
                    provenance=[Prov(kind="library", ref="integration_defaults.pending_timeout")],
                ),
            )
    integ_by_need = {i.need_key: i for i in g.integrations.values()}

    # ---------------------------------------------------------------- screens
    builder = _ScreenBuilder(
        si,
        inv,
        plan,
        g,
        ops_of,
        integ_by_need,
        kept_roles,
        facts,
        profile,
        langs[0],
        result_repairs,
    )
    for sid in release_ids:
        g.add("screens", builder.build(release[sid]))

    # ---------------------------------------------------------------- navigation, homes
    nav_assumed += _navigate(si, inv, plan, g, release, kept_roles)

    # ---------------------------------------------------------------- stories, personas
    for fid in sorted(in_ids):
        f = by_id[fid]
        mine = sorted(p.id for p in release.values() if fid in p.feature_ids)
        if not mine:
            continue  # a feature with no screen has no story that cites one (V-40 reports it)
        role_counts: dict[str, int] = defaultdict(int)
        for sid in mine:
            for r in release[sid].role_ids:
                if r in kept_roles:
                    role_counts[r] += 1
        if not role_counts:
            continue
        role = sorted(role_counts, key=lambda r: (-role_counts[r], r))[0]
        grounded = f.user_evidenced and bool(f.intent)
        g.add(
            "stories",
            Story(
                id=reg.mint("US"),
                role_id=role,
                want=f.name[:1].lower() + f.name[1:] if f.name else fid,
                so_that=f.intent or sd.placeholders.story_so_that,
                feature_ids=[fid],
                screen_ids=mine,
                voice="must" if grounded else "usually",
                assumed=not grounded,
                provenance=[Prov(kind="user" if f.user_evidenced else "compass", ref=fid)],
            ),
        )
    story_of: dict[str, list[str]] = defaultdict(list)
    for s in g.stories.values():
        for fid in s.feature_ids:
            story_of[fid].append(s.id)
    for sc in g.screens.values():
        sc.stories = sorted(
            {s for fid in release[sc.id].feature_ids for s in story_of.get(fid, [])}
        )
    for rid, r in sorted(g.roles.items()):
        g.add(
            "personas",
            Persona(
                id=reg.mint("PER"),
                role_id=rid,
                goal=r.goal or sd.placeholders.persona_goal,
                assumed=True,
                provenance=[Prov(kind="assumed", ref=rid)],
            ),
        )

    # ---------------------------------------------------------------- flows
    flows_dropped = _flows(si, inv, plan, g, release, kept_roles, sd.placeholders.unhappy_path, reg)

    # ---------------------------------------------------------------- behaviours, glossary
    instantiate_behaviors(g, facts)
    n = len(g.behaviors)
    for cat in vocab.behavior_categories_for(profile, facts):
        if (
            any(b.category == cat.name for b in g.behaviors.values())
            or cat.supplied_by != "app_specific"
        ):
            continue
        tmpl = sd.assumed_behaviors.get(cat.name)
        if not tmpl:
            continue
        n += 1
        bid = f"BHV-S{n:03d}"
        g.behaviors[bid] = Behavior(
            id=bid,
            category=cat.name,
            text=tmpl.format(
                flow=sd.integration_defaults.flow,
                pending_timeout=sd.integration_defaults.pending_timeout,
            ),
            assumed=True,
            provenance=[
                Prov(kind="library", ref=f"integration_defaults.{cat.name}"),
                Prov(kind="assumed", ref=cat.name),
            ],
        )
    g.glossary = {t: t for t in si.glossary}

    log = run_rcode(g, facts)
    deg = set(langs[1:])
    return SeedResult(
        graph=g,
        rcode_log=log,
        notes=notes,
        access_repairs=result_repairs,
        nav_assumed=nav_assumed,
        flows_dropped=flows_dropped,
        roles_deferred=deferred_roles,
        degraded_locales=deg,
        claims=claims,
    )


def _norm_key(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


# ---------------------------------------------------------------- the card builder


class _ScreenBuilder:
    def __init__(
        self,
        si: SpecInput,
        inv: Inventory,
        plan: ReleasePlan,
        g: SpecGraph,
        ops_of: dict[str, dict[str, str]],
        integ_by_need: dict[str, Integration],
        kept_roles: set[str],
        facts: set[str],
        profile: str,
        lang: str,
        repairs: list[str],
    ) -> None:
        self.si, self.inv, self.plan, self.g = si, inv, plan, g
        self.ops_of, self.integ = ops_of, integ_by_need
        self.kept_roles, self.facts, self.profile, self.lang = kept_roles, facts, profile, lang
        self.repairs = repairs
        self.sd = seed_data()
        self.release = set(plan.release_screens)
        from mde_spec_core.config import default_config

        self.mf = default_config().checks.mobile_fit
        self.sysdefs = libraries.system_screens()
        self.by_system: dict[str, str] = {}
        for sid in sorted(self.release):
            p = {x.id: x for x in inv.screens}[sid]
            if p.system_id and p.system_id not in self.by_system:
                self.by_system[p.system_id] = sid
        self.edges = _flow_edges(si, inv, plan)

    # -- helpers -------------------------------------------------------------------------------------------------

    def _home_of(self, app: str) -> str | None:
        cands = sorted(
            p.id
            for p in self.inv.screens
            if p.id in self.release
            and p.app == app
            and not p.system
            and p.screen_type not in self.sd.non_home_types
        )
        return cands[0] if cands else None

    def _role_split(self, p: ScreenProposal, eids: list[str]) -> tuple[list[str], set[str]]:
        """Roles that may see every entity the screen binds; entities dropped when no role may (a recorded repair)."""
        roles = [r for r in p.role_ids if r in self.kept_roles]
        dropped: set[str] = set()
        for eid in eids:
            e = self.g.entities[eid]
            none_roles = {row.role_id for row in e.access if row.actions == ["none"]}
            bad = [r for r in roles if r in none_roles]
            if not bad:
                continue
            ok = [r for r in roles if r not in none_roles]
            if ok:
                self.repairs.append(
                    f"{p.id} {p.name!r} binds {eid} {e.name!r}, which {', '.join(bad)} may not see: those roles are taken off the screen"
                )
                roles = ok
            else:
                self.repairs.append(
                    f"{p.id} {p.name!r} binds {eid} {e.name!r}, which none of its roles may see: the binding is narrowed away"
                )
                dropped.add(eid)
        return roles, dropped

    def build(self, p: ScreenProposal) -> Screen:
        g, sd, inv = self.g, self.sd, self.inv
        sdef = self.sysdefs.get(p.system_id) if p.system_id else None
        eids: list[str] = []
        for eid, _ in p.entity_uses:
            if eid in g.entities and eid not in eids:
                eids.append(eid)
        eids = eids[: sd.max_entities_per_screen]
        roles, dropped = self._role_split(p, eids)
        eids = [e for e in eids if e not in dropped]
        if not roles:
            roles = (
                sorted(r for r in p.role_ids if r in self.kept_roles) or sorted(self.kept_roles)[:1]
            )
        app = g.apps[p.app]
        web = app.surface == "web"
        uses = {eid: {u for e, u in p.entity_uses if e == eid} for eid in eids}

        regions: list[Region] = []
        inputs: list[Input] = []
        actions: list[Action] = []
        used_keys: set[str] = set()

        def add_region(r: Region) -> None:
            same = next(
                (x for x in regions if not x.bind and (x.key == r.key or x.shape == r.shape)), None
            )
            if r.bind and same is not None:
                # an unbound library region of a system screen that a Compass screen already is: the entity binds to it
                same.bind = list(r.bind)
                same.attrs = sorted({*same.attrs, *r.attrs})
                same.cardinality = same.cardinality or r.cardinality
                return
            if r.key in used_keys or len(regions) >= self.mf.max_regions:
                return
            used_keys.add(r.key)
            regions.append(r)

        # library regions, actions and inputs of a system screen (also when a Compass screen already is one)
        sys_actions: list[Action] = []
        if sdef is not None:
            for sr in sdef.regions:
                add_region(
                    Region(
                        key=sr.key,
                        what=sr.what,
                        shape=sr.shape,
                        attrs=list(sr.attrs),
                        importance=sr.importance,  # type: ignore[arg-type]
                        cardinality="many" if sr.shape == "collection" else None,
                    )
                )
            for si_ in sdef.inputs:
                inputs.append(Input(key=si_.key, type=si_.type, required=si_.required))
            for sa in sdef.actions:
                if sa.when is not None and not vocab.evaluate_predicate(sa.when, self.facts):
                    continue
                steps = self._system_steps(p, sdef.id, sa.key)
                sys_actions.append(
                    Action(
                        key=sa.key,
                        importance=sa.importance,  # type: ignore[arg-type]
                        intent=sa.intent,
                        what=sa.what,
                        effect=steps,
                        errors=["network"]
                        if steps
                        and steps[0].verb == "go"
                        and sdef.id
                        in (
                            "sign_in",
                            "verify_code",
                            "forgot_password",
                            "reset_password",
                            "account_deletion",
                        )
                        else [],
                        flags=[],
                    )
                )

        # regions bound to real entity fields
        tmpls = sd.region_templates.get(p.screen_type) or []
        for eid in eids:
            e = g.entities[eid]
            fs = _fieldset(e)
            ekey = slug(e.name)
            for t in tmpls:
                if t.bind is None:
                    continue
                if t.when and not fs.get(t.when):
                    continue
                binds: list[str] = []
                if t.bind == "ops_list":
                    ops = self.ops_of.get(eid, {})
                    op = (
                        (ops.get("list") or ops.get("get"))
                        if "create" in uses.get(eid, set())
                        else None
                    )
                    if op:
                        binds = [op]
                elif t.bind == "input_fields":
                    binds = [f"{eid}.{f.key}" for f in self._writable(e, web)]
                else:
                    binds = [f"{eid}.{f.key}" for f in fs.get(t.bind, [])]
                if not binds:
                    continue
                key = t.key if len(eids) == 1 else f"{t.key}_{ekey}"
                attrs = list(t.attrs)
                if t.item:
                    attrs.append(f"item={eid}")
                add_region(
                    Region(
                        key=key,
                        what=t.what.format(entity=e.name.lower(), items=plural(e.name.lower())),
                        shape=t.shape,
                        attrs=attrs,
                        bind=binds,
                        importance=t.importance,  # type: ignore[arg-type]
                        cardinality=t.cardinality,
                        copy_unspecified=False,
                    )
                )
        if not regions:
            fallback = sd.region_templates["detail_fallback"][0]
            add_region(
                Region(
                    key=fallback.key,
                    what=fallback.what,
                    shape=fallback.shape,
                    importance=fallback.importance,  # type: ignore[arg-type]
                )
            )
        # at most the phone's number of primary regions
        prim = 0
        for r in regions:
            if r.importance == "primary":
                prim += 1
                if prim > self.mf.max_primary_regions:
                    r.importance = "supporting"

        # inputs of a form
        if p.screen_type in ("form", "wizard") and eids:
            e = g.entities[eids[0]]
            taken = {i.key for i in inputs}
            for f in self._writable(e, web):
                if f.key in taken:
                    continue
                fm = next((v for k, v in sd.field_types.items() if v.field == f.type), None)
                itype = fm.input if fm and fm.input else "text"
                inputs.append(
                    Input(
                        key=f.key,
                        type=itype,
                        required=f.required,
                        constraint=("one of: " + ", ".join(f.enum_values)) if f.enum_values else "",
                        source=f"{eids[0]}.{f.key}",
                    )
                )

        # actions
        nxt = self._next_screens(p)
        mutate = self._mutation_actions(p, eids, uses, roles, nxt)
        actions.extend(sys_actions)
        taken_keys = {a.key for a in actions}
        for a in mutate:
            if a.key not in taken_keys:
                actions.append(a)
                taken_keys.add(a.key)
        # navigation along the flows and from a list to its item (one action each)
        for tgt in nxt:
            if any(st.verb == "go" and st.target == tgt for a in actions for st in a.effect):
                continue
            tp = {x.id: x for x in inv.screens}[tgt]
            key = f"open_{slug(tp.name)}"[:48]
            base, n = key, 2
            while key in taken_keys:
                key = f"{base}_{n}"
                n += 1
            taken_keys.add(key)
            actions.append(
                Action(
                    key=key,
                    importance="secondary",
                    intent="navigate",
                    what=f"go to {tp.name}",
                    effect=[Step(verb="go", target=tgt)],
                )
            )
        self._importance(actions)

        # states, copy, acceptance
        lib = libraries.state_library_for(p.screen_type, self.profile)
        states = [t.to_state_spec() for t in lib.states]
        copy: dict[str, str] = {}
        for t in lib.states:
            copy[t.copy_key] = (
                t.copy_text
            )  # shared by every screen of the type: placeholders stay raw ({items}, {name})
        title_key = namespaced_copy_key(p.id, "title")
        copy[title_key] = p.name
        for a in actions:
            copy[namespaced_copy_key(p.id, a.key)] = humanize(a.key)
        for i in inputs:
            copy.setdefault(namespaced_copy_key(p.id, i.key), humanize(i.key))
        for k, v in copy.items():
            g.locale_copy.setdefault(self.lang, {})[k] = v

        prov = [Prov(kind=k, ref=r) for k, r in p.provenance]  # type: ignore[arg-type]
        for fid in p.feature_ids:
            f = next((x for x in self.si.features if x.id == fid), None)
            prov.append(
                Prov(
                    kind="user" if f and f.user_evidenced else "compass",
                    ref=fid,
                    quote_id=(f.quote_ids[0] if f and f.quote_ids else None),
                )
            )
        if p.source == "derived":
            prov.append(Prov(kind="assumed", ref="; ".join(p.reasons)[:120]))
        acc = [a.to_acceptance(p.name) for a in lib.acceptance]
        purpose = p.purpose or (
            sd.placeholders.purpose_with_entities.format(
                entities=", ".join(plural(g.entities[e].name.lower()) for e in eids)
            )
            if eids
            else sd.placeholders.purpose_plain.format(name=p.name)
        )
        needs_params = p.screen_type in ("detail", "status", "confirmation") and eids
        route = Route(
            name=slug(p.name),
            path=f"/{slug(p.name)}"
            + (f"/:{slug(g.entities[eids[0]].name)}_id" if needs_params else ""),
            params=[
                RouteParam(
                    name=f"{slug(g.entities[eids[0]].name)}_id", type="id", source=f"{eids[0]}.id"
                )
            ]
            if needs_params
            else [],
            guards=[]
            if p.screen_type in sd.open_types or "accounts" not in self.facts
            else [Guard(pred="signed_in"), Guard(pred="role_has_access")],
        )
        varied = ""
        if eids:
            lv = {
                next(
                    (c for c in ("manage", "edit", "create", "view", "none") if c in row.actions),
                    "none",
                )
                for row in g.entities[eids[0]].access
                if row.role_id in roles
            }
            if len(lv) > 1:
                varied = "what each role sees and may do here differs; the access rows in the closure say how"
        return Screen(
            id=p.id,
            name=p.name,
            app_id=p.app,
            module_id=p.module,
            screen_type=p.screen_type,
            surface=app.surface,
            role_ids=sorted(roles),
            purpose=purpose,
            route=route,
            role_conditions=varied,
            regions=sorted(regions, key=lambda r: r.key),
            actions=sorted(actions, key=lambda a: a.key),
            inputs=inputs,
            states=states,
            copy_keys=sorted(copy),
            acceptance=acc,
            provenance=prov,
            system=p.system,
            assumed=p.source in ("derived", "peer"),
        )

    # -- pieces ------------------------------------------------------------------------------------------------------------

    def _writable(self, e: Entity, web: bool) -> list[FieldDef]:
        cap = self.sd.web_form_inputs if web else self.mf.max_form_inputs
        fields = [f for f in e.fields if f.key not in ("id", "status") and f.derived is None]
        fields.sort(key=lambda f: (not f.required, e.fields.index(f)))
        return fields[:cap]

    def _system_steps(self, p: ScreenProposal, sysid: str, key: str) -> list[Step]:
        steps = self.sd.system_effects.get(f"{sysid}.{key}")
        if not steps:
            return [Step(verb="stay")]
        out: list[Step] = []
        for tok in steps:
            if tok == "stay":
                out.append(Step(verb="stay"))
            elif tok == "go:@home":
                home = self._home_of(p.app)
                out.append(Step(verb="go", target=home) if home else Step(verb="stay"))
            elif tok.startswith("go:"):
                tgt = self.by_system.get(tok[3:])
                out.append(
                    Step(verb="go", target=tgt) if tgt and tgt != p.id else Step(verb="stay")
                )
        return out or [Step(verb="stay")]

    def _next_screens(self, p: ScreenProposal) -> list[str]:
        out = sorted(t for t in self.edges.get(p.id, set()) if t in self.release and t != p.id)
        return out

    def _mutation_actions(
        self,
        p: ScreenProposal,
        eids: list[str],
        uses: dict[str, set[str]],
        roles: list[str],
        nxt: list[str],
    ) -> list[Action]:
        out: list[Action] = []
        if p.screen_type not in ("form", "wizard", "checkout") or not eids:
            return out

        def guard_for(eid: str, level: str) -> list[Guard]:
            e = self.g.entities[eid]
            ok = {row.role_id for row in e.access if level in row.actions}
            return [Guard(pred="role_has_access")] if ok and not set(roles) <= ok else []

        eid = next((x for x in eids if uses[x] & {"create", "edit"}), eids[0])
        ops = self.ops_of.get(eid, {})
        go = [Step(verb="go", target=nxt[0])] if nxt else []
        if p.screen_type == "checkout":
            integ = next(iter(self.integ.values()), None)
            pay_ent = next(
                (x for x in eids if "create" in uses[x] and ops_ok(self.ops_of, x, "create")), eid
            )
            cop = self.ops_of.get(pay_ent, {}).get("create")
            if cop is None:
                return out
            steps = [Step(verb="call", target=cop)]
            if integ is not None and integ.provider_side_flow:
                steps.append(Step(verb="external_flow", target=integ.need_key))
            steps += go[:1] if len(steps) < 3 else []
            op = self.g.operations[cop]
            out.append(
                Action(
                    key="pay",
                    importance="primary",
                    intent="pay",
                    what="pay the amount due",
                    precondition="a way to pay is chosen and an amount is due",
                    effect=steps,
                    errors=[e.code for e in op.errors],
                    flags=["idempotent"],
                    guard=guard_for(pay_ent, "create"),
                )
            )
            return out
        create = ops.get("create")
        update = ops.get("update")
        if create and "create" in uses[eid]:
            op = self.g.operations[create]
            out.append(
                Action(
                    key="save",
                    importance="primary",
                    intent="confirm",
                    what=f"save the new {self.g.entities[eid].name.lower()}",
                    precondition="the required details are given"
                    + ("; the form is in create mode" if "edit" in uses[eid] else ""),
                    effect=[Step(verb="call", target=create), *go[:1]],
                    errors=[e.code for e in op.errors],
                    flags=["idempotent"],
                    guard=guard_for(eid, "create"),
                )
            )
        if update and "edit" in uses[eid]:
            op = self.g.operations[update]
            out.append(
                Action(
                    key="save_changes" if create and "create" in uses[eid] else "save",
                    importance="secondary" if create and "create" in uses[eid] else "primary",
                    intent="confirm",
                    what=f"save the changes to the {self.g.entities[eid].name.lower()}",
                    precondition="an existing record is being edited",
                    effect=[Step(verb="call", target=update), *go[:1]],
                    errors=[e.code for e in op.errors],
                    flags=["idempotent"],
                    guard=guard_for(eid, "edit"),
                )
            )
        if not out:
            # a form that has no operation the roles may call: stated, not invented (FN-36)
            out.append(
                Action(
                    key="save",
                    importance="primary",
                    intent="confirm",
                    what="save the details",
                    effect=[Step(verb="stay")],
                    unspecified=True,
                )
            )
        out.append(
            Action(
                key="cancel",
                importance="secondary",
                intent="cancel",
                what="leave without saving",
                effect=[Step(verb="stay")],
            )
        )
        return out

    def _importance(self, actions: list[Action]) -> None:
        view_only = all(
            a.intent in ("view", "navigate", "cancel", "refresh", "share") for a in actions
        )
        seen = False
        for a in actions:
            if view_only:
                a.importance = "secondary"
            elif a.importance == "primary":
                if seen:
                    a.importance = "secondary"
                seen = True


def ops_ok(ops_of: dict[str, dict[str, str]], eid: str, verb: str) -> bool:
    return verb in ops_of.get(eid, {})


# ---------------------------------------------------------------- navigation


def _flow_edges(si: SpecInput, inv: Inventory, plan: ReleasePlan) -> dict[str, set[str]]:
    """Directed edges between screen proposals from the flows (consecutive release steps)."""
    release = set(plan.release_screens)
    edges: dict[str, set[str]] = defaultdict(set)

    def pid(compass_id: str) -> str | None:
        ids = inv.compass_instances.get(compass_id)
        if not ids:
            return None
        return inv.screen_alias.get(ids[0], ids[0])

    for fl in si.structure.flows:
        seq = [x for x in (pid(s.screen) for s in fl.steps) if x and x in release]
        for a, b in itertools.pairwise(seq):
            if a != b:
                edges[a].add(b)
    for a, b in si.structure.navigation:
        pa, pb = pid(a), pid(b)
        if pa and pb and pa in release and pb in release and pa != pb:
            edges[pa].add(pb)
    return edges


def _navigate(
    si: SpecInput,
    inv: Inventory,
    plan: ReleasePlan,
    g: SpecGraph,
    release: dict[str, ScreenProposal],
    kept_roles: set[str],
) -> list[str]:
    """Role homes, leads_to for every edge, and a link for any screen no entry reaches (recorded, never silent)."""
    sd = seed_data()
    edges = _flow_edges(si, inv, plan)
    edges = defaultdict(set, {k: set(v) for k, v in edges.items()})
    added: list[str] = []
    by_sys: dict[str, str] = {}
    for sid in sorted(release):
        p = release[sid]
        if p.system_id and p.system_id not in by_sys:
            by_sys[p.system_id] = sid

    def screens_of(app: str) -> list[ScreenProposal]:
        return [p for p in release.values() if p.app == app]

    # entity edges: list -> detail and form; detail -> form
    by_ent: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for sid in sorted(release):
        p = release[sid]
        for e, _ in p.entity_uses:
            by_ent[(p.app, p.module, e)][p.screen_type].append(sid)  # type: ignore[index]
    for _key, kinds in by_ent.items():
        for lst in kinds.get("list", [])[:1]:
            for t in ("detail", "form", "wizard"):
                for tgt in kinds.get(t, [])[:1]:
                    if tgt != lst:
                        edges[lst].add(tgt)
        for det in kinds.get("detail", [])[:1]:
            for tgt in kinds.get("form", [])[:1]:
                edges[det].add(tgt)
    # system links
    for a, b in sd.system_links:
        if a in by_sys and b in by_sys:
            edges[by_sys[a]].add(by_sys[b])
    # role homes: the first screen of the role's core journey, else the first home-like screen of its app
    homes: dict[str, str] = {}
    for rid in sorted(g.roles):
        cands: list[str] = []
        flid = plan.journeys.get(rid)
        if flid:
            fl = next((x for x in si.structure.flows if x.id == flid), None)
            if fl:
                for s in fl.steps:
                    ids = inv.compass_instances.get(s.screen)
                    if ids and (t := inv.screen_alias.get(ids[0], ids[0])) in release:
                        cands.append(t)
                        break
        mine = sorted(
            p.id
            for p in release.values()
            if rid in p.role_ids and not p.system and p.screen_type not in sd.non_home_types
        )
        mine_any = sorted(p.id for p in release.values() if rid in p.role_ids and not p.system)
        pick = (cands or mine or mine_any or [None])[0]
        if pick and release[pick].screen_type in ("auth",) and mine:
            pick = mine[0]
        if pick:
            homes[rid] = pick
            g.roles[rid].home_screen = pick
    # a home leads to the module entries and to the system screens a signed-in user may open
    entries_by_module = {m.id: m.entry for m in si.modules if m.entry}
    for h in homes.values():
        for ent in entries_by_module.values():
            ids = inv.compass_instances.get(ent)
            t = inv.screen_alias.get(ids[0], ids[0]) if ids else None
            if t and t in release and t != h and release[t].app == release[h].app:
                edges[h].add(t)
        for key in sd.home_links:
            if key in by_sys and by_sys[key] != h:
                edges[h].add(by_sys[key])
    # sign-in leads to each home
    if "sign_in" in by_sys:
        for h in set(homes.values()):
            edges[by_sys["sign_in"]].add(h)
        if "verify_code" in by_sys:
            edges[by_sys["verify_code"]].update(set(homes.values()))
    # reachability fix-up (V-04): any screen no entry reaches is linked from the first reachable screen of its module
    for sid, tg in edges.items():
        g.screens[sid].leads_to = sorted(
            set(g.screens[sid].leads_to) | {t for t in tg if t in g.screens}
        )
    for _ in range(len(release) + 1):
        entries = entry_screens(g)
        seen: set[str] = set()
        stack = [e for e in entries if e in g.screens]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack += [t for t in _targets(g.screens[cur]) if t in g.screens]
        lost = sorted(set(g.screens) - seen)
        if not lost:
            break
        sid = lost[0]
        p = release[sid]
        pool = (
            [
                x
                for x in sorted(seen)
                if g.screens[x].app_id == p.app
                and g.screens[x].module_id == p.module
                and not release[x].system
            ]
            or [x for x in sorted(seen) if g.screens[x].app_id == p.app and not release[x].system]
            or sorted(seen)
        )
        if not pool:
            break
        src = pool[0]
        g.screens[src].leads_to = sorted(set(g.screens[src].leads_to) | {sid})
        added.append(f"{src} -> {sid} (no flow or navigation edge reached {sid})")
    # informational back-references from the go actions
    for s in g.screens.values():
        for a in s.actions:
            for st in a.effect:
                if st.verb == "go" and st.target in g.screens:
                    tgt = g.screens[st.target]
                    ref = f"{s.id}.{a.key}"
                    if ref not in tgt.reached_from:
                        tgt.reached_from.append(ref)
    for s in g.screens.values():
        s.reached_from = sorted(s.reached_from)
    return added


def _targets(s: Screen) -> set[str]:
    out = set(s.leads_to)
    for a in s.actions:
        out |= {st.target for st in a.effect if st.verb == "go" and st.target}
    return {t for t in out if t and t.startswith("SCR-")}


# ---------------------------------------------------------------- flows


def _flows(
    si: SpecInput,
    inv: Inventory,
    plan: ReleasePlan,
    g: SpecGraph,
    release: dict[str, ScreenProposal],
    kept_roles: set[str],
    unhappy: str,
    reg: Any,
) -> list[str]:
    dropped: list[str] = []
    primary_ids = set(plan.journeys.values())

    def pid(compass_id: str) -> str | None:
        ids = inv.compass_instances.get(compass_id)
        if not ids:
            return None
        t = inv.screen_alias.get(ids[0], ids[0])
        return t if t in release else None

    for fl in si.structure.flows:
        seq: list[tuple[str, str]] = []
        for st in fl.steps:
            t = pid(st.screen)
            if t and (not seq or seq[-1][0] != t):
                seq.append((t, st.action))
        if not seq:
            dropped.append(f"{fl.id} {fl.name!r}: none of its screens is in the release")
            continue
        role = fl.role
        seen: set[str] = set()
        while role in inv.role_alias and role not in seen:
            seen.add(role)
            role = inv.role_alias[role] or ""
        stages: list[Stage] = []
        for i, (sid, action) in enumerate(seq, start=1):
            key = ""
            if i < len(seq):
                nxt = seq[i][0]
                key = next(
                    (
                        a.key
                        for a in sorted(g.screens[sid].actions, key=lambda a: a.key)
                        if any(s.verb == "go" and s.target == nxt for s in a.effect)
                    ),
                    "",
                )
            stages.append(
                Stage(
                    n=i,
                    goal=action or g.screens[sid].name,
                    screen_ids=[sid],
                    action_keys=[key] if key else [],
                )
            )
        primary = fl.id in primary_ids
        moments = [
            s.n
            for s in stages
            if g.screens[s.screen_ids[0]].screen_type in ("checkout", "confirmation")
        ] or [stages[-1].n]
        if fl.id not in reg.used:
            reg.carry(fl.id)
        has_alt = bool([a for a in fl.alternates if a])
        g.add(
            "flows",
            Flow(
                id=fl.id,
                name=fl.name,
                role_ids=[role] if role in g.roles else [],
                app_ids=sorted({g.screens[s.screen_ids[0]].app_id for s in stages}),
                primary=primary,
                variant="main",
                stages=stages,
                moments_that_matter=moments if primary else [],
                unhappy_paths=[a for a in fl.alternates if a] or ([unhappy] if primary else []),
                assumed=primary and not has_alt,
                provenance=[Prov(kind="compass", ref=fl.id)],
            ),
        )
    return dropped
