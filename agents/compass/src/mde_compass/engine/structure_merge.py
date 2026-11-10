"""Merging what the structure calls returned into the records of the Brief (ADR 0017): pure functions on a working copy.

The calls speak refs (an existing id to keep, a short local ref for something new); this module resolves them to real
ids, allocates new ids from the working copy's ledger, keeps what the person pinned (an ``accepted`` or ``Locked``
record is never overwritten), drops what a pointer cannot resolve, and assembles the cross-module flows from journey
legs. It never calls a model and never touches the turn: ``engine/structure_draft`` owns that.

Two merge modes. ``replace``: the group was drafted from scratch (a first draft or a regeneration): unpinned screens of
the group that the model did not return are removed. ``patch``: the call fixed or extended what exists (a repair or an
expansion pass): whatever it did not return stays.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mde_compass.catalog.fact_kinds import overlap_stopwords
from mde_compass.catalog.fact_kinds import words_of as fact_words
from mde_compass.catalog.structure_patterns import load_structure_patterns
from mde_compass.domain.brief import Brief
from mde_compass.domain.common import ItemMeta, Provenance
from mde_compass.domain.interview import Fact
from mde_compass.domain.structure import (
    ACCESS_ORDER,
    EntityField,
    EntityRecord,
    FlowRecord,
    FlowStep,
    ModuleRecord,
    ScreenEntityUse,
    ScreenRecord,
)
from mde_compass.engine.structure_checks import slug_key
from mde_compass.engine.structure_wire import (
    ModuleOutput,
    OutlineOutput,
)

DRAFT_REF = "structure:draft"
SEED_KINDS: frozenset[str] = frozenset({"screen", "flow", "entity"})
_USES = ("view", "create", "edit")
MIN_SEED_WORD_SHARE = 0.6  # a seed must share this part of a record's name to be its evidence


def is_pinned(record: Any) -> bool:
    """Accepted by the person or backed by their words: a regeneration keeps it exactly."""
    return bool(record.decision == "accepted" or record.tier == "Locked")


def fold(text: str) -> str:
    return " ".join(fact_words(text))


def draft_meta(now: datetime) -> dict[str, Any]:
    """What a record the model drafted carries: Default tier, assumed, one non-evidence provenance entry."""
    return {
        "tier": "Default",
        "status": "assumed",
        "confidence": 0.5,
        "provenance": [Provenance(type="model_knowledge", ref=DRAFT_REF, date=now)],
    }


def _ids(values: Iterable[str], valid: set[str], by_name: Mapping[str, str]) -> list[str]:
    """Pointers that resolve: an id that exists, or a name that is one record's name (a model sometimes writes
    "Patient" for ROL-001). Anything else is dropped here, so a made-up id never reaches the Brief."""
    out: list[str] = []
    for v in values:
        key = v.strip()
        hit = key if key in valid else by_name.get(fold(key), "")
        if hit and hit not in out:
            out.append(hit)
    return out


def _one(value: str, valid: set[str], by_name: Mapping[str, str]) -> str:
    found = _ids([value], valid, by_name)
    return found[0] if found else ""


@dataclass
class Lookups:
    apps: set[str]
    roles: set[str]
    features: set[str]
    app_names: dict[str, str]
    role_names: dict[str, str]
    feature_names: dict[str, str]

    @classmethod
    def of(cls, brief: Brief) -> Lookups:
        return cls(
            apps={a.id for a in brief.apps},
            roles={r.id for r in brief.roles},
            features={f.id for f in brief.scope},
            app_names={fold(a.name): a.id for a in brief.apps},
            role_names={fold(r.name_in_user_words): r.id for r in brief.roles},
            feature_names={fold(f.name): f.id for f in brief.scope},
        )


@dataclass
class Journey:
    ref: str
    name: str
    role: str
    goal: str
    legs: list[str]  # module ids, in order (a module may repeat)
    owner: str  # the module id whose call writes the acceptance lines
    flow_id: str = ""  # the existing flow it continues, or ""


@dataclass
class Plan:
    journeys: list[Journey] = field(default_factory=list)
    targets: dict[str, int] = field(
        default_factory=dict
    )  # module id -> how many screens the outline asked for


def _tombstone_screens(work: Brief, gone: set[str]) -> None:
    if not gone:
        return
    work.screens = [s for s in work.screens if s.id not in gone]
    for sid in sorted(gone):
        work.ids.tombstone(sid)


# ------------------------------------------------------------------------------------------------- outline


def merge_outline(work: Brief, out: OutlineOutput, now: datetime) -> tuple[Plan, dict[str, str]]:
    """Resolve the outline to module, entity and journey records. Modules and entities are matched to existing ones by
    id first, then by name, so a regeneration keeps ids; pinned ones stay as they are; the rest of the old ones are
    removed (their ids tombstoned). Returns the journeys to assemble and the map from the call's refs to real ids."""
    look = Lookups.of(work)
    refmap: dict[str, str] = {}
    targets: dict[str, int] = {}

    # --- modules
    mod_by_id = {m.id: m for m in work.modules}
    mod_by_name = {fold(m.name): m for m in work.modules}
    modules: list[ModuleRecord] = []
    used: set[str] = set()
    for mo in out.modules:
        existing = mod_by_id.get(mo.ref) or mod_by_name.get(fold(mo.name))
        if existing is not None and existing.id in used:
            existing = None
        fields: dict[str, Any] = {
            "name": mo.name.strip(),
            "kind": mo.kind.strip(),
            "purpose": mo.purpose.strip(),
            "apps": _ids(mo.apps, look.apps, look.app_names) or sorted(look.apps),
            "features": _ids(mo.features, look.features, look.feature_names),
            "roles": _ids(mo.roles, look.roles, look.role_names) or sorted(look.roles),
        }
        if existing is not None and is_pinned(existing):
            rec = existing
        elif existing is not None:
            rec = existing.model_copy(update=fields)
        else:
            rec = ModuleRecord(id=work.ids.allocate("MOD"), **fields, **draft_meta(now))
        used.add(rec.id)
        refmap[mo.ref] = rec.id
        refmap[rec.id] = rec.id
        targets[rec.id] = mo.screen_target
        modules.append(rec)
    for m in work.modules:  # a pinned module the model left out stays
        if m.id not in used and is_pinned(m):
            used.add(m.id)
            refmap[m.id] = m.id
            modules.append(m)
    gone_modules = {m.id for m in work.modules if m.id not in used}
    for mid in sorted(gone_modules):
        work.ids.tombstone(mid)
    _tombstone_screens(work, {s.id for s in work.screens if s.module in gone_modules})
    work.modules = modules

    # --- entities
    ent_by_id = {e.id: e for e in work.entities}
    ent_by_name = {fold(e.name): e for e in work.entities}
    entities: list[EntityRecord] = []
    used_e: set[str] = set()
    for eo in out.entities:
        existing_e = ent_by_id.get(eo.ref) or ent_by_name.get(fold(eo.name))
        if existing_e is not None and existing_e.id in used_e:
            existing_e = None
        owner = refmap.get(eo.owner_module, "")
        fields_e: dict[str, Any] = {
            "name": eo.name.strip(),
            "purpose": eo.purpose.strip(),
            "module": owner,
        }
        if existing_e is not None and is_pinned(existing_e):
            erec = existing_e
        elif existing_e is not None:
            erec = existing_e.model_copy(update=fields_e)
        else:
            new_id = work.ids.allocate("ENT")
            erec = EntityRecord(
                id=new_id, key=slug_key(eo.name, "entity"), **fields_e, **draft_meta(now)
            )
        used_e.add(erec.id)
        refmap[eo.ref] = erec.id
        refmap[erec.id] = erec.id
        entities.append(erec)
    for e in work.entities:
        if e.id not in used_e and is_pinned(e):
            used_e.add(e.id)
            refmap[e.id] = e.id
            entities.append(e)
    for e in work.entities:
        if e.id not in used_e:
            work.ids.tombstone(e.id)
    work.entities = entities

    # --- journeys (flows are assembled after the modules are drafted)
    flow_by_id = {f.id: f for f in work.flows}
    flow_by_name = {fold(f.name): f for f in work.flows}
    plan = Plan(targets=targets)
    taken: set[str] = set()
    for jo in out.journeys:
        legs = [refmap[m] for m in jo.legs if m in refmap]
        if not legs:
            continue
        old = flow_by_id.get(jo.ref) or flow_by_name.get(fold(jo.name))
        if old is not None and old.id in taken:
            old = None
        if old is not None:
            taken.add(old.id)
        plan.journeys.append(
            Journey(
                ref=jo.ref,
                name=jo.name.strip(),
                role=_one(jo.role, look.roles, look.role_names),
                goal=jo.goal.strip(),
                legs=legs,
                owner=refmap.get(jo.owner_module, legs[-1]),
                flow_id=old.id if old is not None else "",
            )
        )
    return plan, refmap


def plan_from_existing(work: Brief) -> Plan:
    """The journeys of the flows that exist, with their legs read back from the steps (a run of steps on screens of
    one module is one leg), so a module can be redrafted and the other legs kept."""
    screen_module = {s.id: s.module for s in work.screens}
    plan = Plan()
    for flow in work.flows:
        legs = [run[0] for run in _runs(flow.steps, screen_module)]
        if not legs:
            continue
        plan.journeys.append(
            Journey(
                ref=flow.id,
                name=flow.name,
                role=flow.role,
                goal=flow.goal,
                legs=legs,
                owner=legs[-1],
                flow_id=flow.id,
            )
        )
    return plan


def _runs(
    steps: Sequence[FlowStep], screen_module: Mapping[str, str]
) -> list[tuple[str, list[FlowStep]]]:
    """Consecutive steps on screens of one module, as ``(module id, steps)`` pairs."""
    runs: list[tuple[str, list[FlowStep]]] = []
    for st in steps:
        mod = screen_module.get(st.screen, "")
        if runs and runs[-1][0] == mod:
            runs[-1][1].append(st)
        else:
            runs.append((mod, [st]))
    return runs


# ------------------------------------------------------------------------------------------------- modules


@dataclass
class ModuleMerge:
    segments: dict[tuple[str, int], list[tuple[str, str]]] = field(
        default_factory=dict
    )  # (journey, leg) -> (screen, action)
    details: dict[str, tuple[list[str], list[str]]] = field(
        default_factory=dict
    )  # journey -> (acceptance, alternates)
    screens_written: int = 0
    notes: list[str] = field(default_factory=list)


def merge_modules(
    work: Brief,
    out: ModuleOutput,
    group: Sequence[str],
    *,
    patch: bool,
    now: datetime,
) -> ModuleMerge:
    """Resolve one module call's output. ``group`` is the module ids it was asked about."""
    look = Lookups.of(work)
    patterns = load_structure_patterns().patterns
    result = ModuleMerge()
    group_set = set(group)
    screen_by_id = {s.id: s for s in work.screens}
    screen_by_key = {(s.module, fold(s.name)): s for s in work.screens}
    entity_ids = {e.id for e in work.entities}
    refmap: dict[str, str] = {}
    returned: set[str] = set()
    updated: dict[str, ScreenRecord] = {}
    added: list[ScreenRecord] = []

    for so in out.screens:
        module = so.module if so.module in group_set else (group[0] if group else "")
        if not module:
            continue
        existing = screen_by_id.get(so.ref) or screen_by_key.get((module, fold(so.name)))
        fields: dict[str, Any] = {
            "name": so.name.strip(),
            "purpose": so.purpose.strip(),
            "module": module,
            "pattern": so.pattern if so.pattern in patterns else "",
            "apps": _ids(so.apps, look.apps, look.app_names),
            "roles": _ids(so.roles, look.roles, look.role_names),
            "serves": _ids(so.serves, look.features, look.feature_names),
        }
        if existing is not None and existing.id in returned:
            existing = None
        if existing is not None and is_pinned(existing):
            refmap[so.ref] = existing.id
            returned.add(existing.id)
            continue
        if existing is not None:
            rec = existing.model_copy(update=fields)
            updated[rec.id] = rec
        else:
            rec = ScreenRecord(id=work.ids.allocate("SCR"), **fields, **draft_meta(now))
            added.append(rec)
        refmap[so.ref] = rec.id
        returned.add(rec.id)
        result.screens_written += 1

    uses_by_screen: dict[str, list[ScreenEntityUse]] = {}
    for use in out.uses:
        sid = refmap.get(use.screen, "")
        eid = use.entity if use.entity in entity_ids else ""
        if not sid or not eid or use.use not in _USES:
            continue
        mine = uses_by_screen.setdefault(sid, [])
        if all(u.entity != eid for u in mine):
            mine.append(ScreenEntityUse(entity=eid, use=use.use))  # type: ignore[arg-type]
    for sid, uses in uses_by_screen.items():
        if sid in updated:
            updated[sid] = updated[sid].model_copy(update={"entities": uses})
        else:
            for i, rec in enumerate(added):
                if rec.id == sid:
                    added[i] = rec.model_copy(update={"entities": uses})

    kept: list[ScreenRecord] = []
    removed: set[str] = set()
    for s in work.screens:
        if s.id in updated:
            kept.append(updated[s.id])
        elif s.module in group_set and not patch and s.id not in returned and not is_pinned(s):
            removed.add(s.id)  # drafted from scratch and not returned: gone
        else:
            kept.append(s)
    work.screens = [*kept, *added]
    for sid in sorted(removed):
        work.ids.tombstone(sid)

    valid_screens = {s.id for s in work.screens}
    for eo in out.entries:
        sid = refmap.get(eo.screen, "")
        if eo.module in group_set and sid in valid_screens:
            for i, m in enumerate(work.modules):
                if m.id == eo.module and not (is_pinned(m) and m.entry):
                    work.modules[i] = m.model_copy(update={"entry": sid})

    # --- the entities this call wrote: key, states, fields, access
    fields_by_entity: dict[str, list[EntityField]] = {}
    for fo in out.fields:
        if fo.entity not in entity_ids:
            continue
        fields_by_entity.setdefault(fo.entity, []).append(
            EntityField(
                key=fo.key,
                name=fo.name,
                type=fo.type,
                required=fo.required,
                ref=fo.ref if fo.ref in entity_ids else "",
                values=list(fo.values),
            )
        )
    access_by_entity: dict[str, dict[str, Any]] = {}
    for ao in out.access:
        role = _one(ao.role, look.roles, look.role_names)
        if ao.entity in entity_ids and role and ao.level in ACCESS_ORDER:
            access_by_entity.setdefault(ao.entity, {})[role] = ao.level
    drafts = {d.ref: d for d in out.entities}
    for i, e in enumerate(work.entities):
        if is_pinned(e):
            continue
        draft = drafts.get(e.id)
        if draft is None and e.id not in fields_by_entity and e.id not in access_by_entity:
            continue
        update: dict[str, Any] = {}
        if draft is not None:
            update["key"] = draft.key.strip() or e.key
            update["states"] = [s.strip() for s in draft.states if s.strip()]
        if e.id in fields_by_entity:
            update["fields"] = fields_by_entity[e.id]
        if e.id in access_by_entity:
            update["access"] = (
                {**e.access, **access_by_entity[e.id]} if patch else access_by_entity[e.id]
            )
        work.entities[i] = e.model_copy(update=update)

    for so in out.segments:
        steps = [(refmap[st.screen], st.action.strip()) for st in so.steps if st.screen in refmap]
        if steps and so.module in group_set:
            result.segments[(so.journey, so.leg)] = steps
    for jd in out.journeys:
        result.details[jd.ref] = (
            [a.strip() for a in jd.acceptance if a.strip()],
            [a.strip() for a in jd.alternates if a.strip()],
        )
    return result


# ------------------------------------------------------------------------------------------------- flows


def assemble_flows(
    work: Brief,
    plan: Plan,
    segments: Mapping[tuple[str, int], Sequence[tuple[str, str]]],
    details: Mapping[str, tuple[list[str], list[str]]],
    now: datetime,
    *,
    keep_unplanned: bool,
) -> list[str]:
    """Build the flows from the journeys: each leg is a fresh segment when one was written, else the run of steps the
    flow already had for it. A journey with fewer than two usable steps is dropped. Flows the plan does not name are
    removed unless ``keep_unplanned`` (a regeneration of one module leaves the other flows alone). Returns notes."""
    look = Lookups.of(work)
    screen_module = {s.id: s.module for s in work.screens}
    serves = {s.id: s.serves for s in work.screens}
    old_by_id = {f.id: f for f in work.flows}
    notes: list[str] = []
    out: list[FlowRecord] = []
    used: set[str] = set()
    for j in plan.journeys:
        old = old_by_id.get(j.flow_id)
        if old is not None and is_pinned(old):
            out.append(old)
            used.add(old.id)
            continue
        runs = [r for _, r in _runs(old.steps, screen_module)] if old is not None else []
        steps: list[FlowStep] = []
        for i, _module in enumerate(j.legs):
            seg = segments.get((j.ref, i))
            if seg:
                steps += [
                    FlowStep(screen=sid, action=act) for sid, act in seg if sid in screen_module
                ]
            elif i < len(runs):
                steps += [st for st in runs[i] if st.screen in screen_module]
        if len(steps) < 2:
            notes.append(f"journey {j.name!r} has fewer than two steps and was left out")
            continue
        features: list[str] = []
        for st in steps:
            for fid in serves.get(st.screen, []):
                if fid in look.features and fid not in features:
                    features.append(fid)
        acceptance, alternates = details.get(
            j.ref, (list(old.acceptance), list(old.alternates)) if old is not None else ([], [])
        )
        fields: dict[str, Any] = {
            "name": j.name or (old.name if old else "Journey"),
            "goal": j.goal,
            "role": j.role,
            "steps": steps,
            "features": features,
            "acceptance": acceptance,
            "alternates": alternates,
        }
        if old is not None:
            rec = old.model_copy(update=fields)
        else:
            rec = FlowRecord(id=work.ids.allocate("FLW"), **fields, **draft_meta(now))
        used.add(rec.id)
        out.append(rec)
    for f in work.flows:
        if f.id in used:
            continue
        if keep_unplanned or is_pinned(f):
            out.append(f)
        else:
            work.ids.tombstone(f.id)
    work.flows = out
    return notes


# --------------------------------------------------------------------------------------------- evidence


def _significant(text: str) -> set[str]:
    stop = overlap_stopwords()
    return {w for w in fact_words(text) if len(w) >= 3 and w not in stop}


def seed_evidence(work: Brief, facts: Mapping[str, Fact], now: datetime) -> list[str]:
    """What the person (or their document) said about a screen, a flow or an entity is EVIDENCE for the record that
    carries those words: it gets the fact's verified quote and becomes Locked, because the person said it. A record
    needs to share most of its name with the statement; a model-made record with no such fact stays Default. Returns
    the ids that gained evidence."""
    gained: list[str] = []
    seeds = [(df, facts.get(df.id)) for df in work.domain_facts if (df.kind or "") in SEED_KINDS]
    for df, fact in seeds:
        if fact is None or not fact.supports_locked():
            continue
        section = {"screen": "screens", "flow": "flows", "entity": "entities"}[df.kind]
        wanted = _significant(fact.statement)
        best_i, best_score = -1, (0, 0.0)
        records: list[Any] = getattr(work, section)
        for i, rec in enumerate(records):
            name_words = _significant(rec.name)
            if not name_words:
                continue
            shared = len(wanted & name_words)
            share = shared / len(name_words)
            # the record that says most of what the statement says wins; ties go to the one fully covered
            if share >= MIN_SEED_WORD_SHARE and (shared, share) > best_score:
                best_i, best_score = i, (shared, share)
        if best_i < 0:
            continue
        rec = records[best_i]
        if any(p.ref == fact.id for p in rec.provenance):
            continue
        kind = (
            "document"
            if fact.origin == "document"
            else "user_tap"
            if fact.origin == "tap"
            else "user_text"
        )
        evidence = Provenance(type=kind, ref=fact.id, quote=fact.quote, date=now)  # type: ignore[arg-type]
        records[best_i] = rec.model_copy(
            update={
                "tier": "Locked",
                "status": "volunteered",
                "confidence": max(rec.confidence, 0.9),
                "provenance": [*rec.provenance, evidence],
            }
        )
        gained.append(rec.id)
    return gained


def meta_of(record: ItemMeta) -> dict[str, Any]:
    return {
        "tier": record.tier,
        "status": record.status,
        "confidence": record.confidence,
        "provenance": list(record.provenance),
    }
