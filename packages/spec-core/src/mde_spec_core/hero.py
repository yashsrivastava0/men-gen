"""The hero skeleton (doc 18 5, AD-87, FN-12, FN-13, FN-32): code mints the records, the model fills the words.

At the Freeze, for every entity, code mints as many records as the entity has lifecycle states or the count of its
class, whichever is larger (a primary entity 12 to 20, up to 30 in deep, the others 3 to 5, compact 8 to 12; read
from `config.profiles`), assigns the relations between records and gives every record a stable id. A hero call then
fills only the content fields code left open; it never mints ids. A value that appears in two entities (an amount, a
person's name) is DERIVED by code from the parent record and never written twice; related entities are filled in
the same call (`fill_plan`). Records are marked synthetic; they never hold URLs, real brands, ID numbers or an
unmasked phone number (only image BRIEFS are written; sourcing and licences are the front end's).

Hero ids are `HERO-<entity id>-<nn>`: they depend only on the entity and the position, so one order has one id in
every app of the program.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import yaml

from .config import DATA_DIR, SpecConfig, default_config
from .graph import Entity, FieldDef, HeroRecord, SpecGraph
from .mock import (
    _is_future_key,
    _text_kind,
    find_unsafe,
    mix_pools,
    mock_tables,
    rel_time,
    status_key,
)

SHARED_PATH = DATA_DIR / "pools" / "shared_values.yaml"
_SKIP_WRITE_TYPES = {"id", "secret", "file", "list", "ref"}
_FIELD_RULES = {
    "phone": "masked only, like +91 98••• ••210; never a whole number",
    "email": "name@example.com; no other domain",
    "code": "a short code with letters and fewer than 9 digits in a row",
    "money": "an amount in the app's currency format",
    "location": "a place name in the app's language",
    "choice": "one of the allowed values",
    "enum": "one of the allowed values",
    "toggle": "true or false",
    "number": "a plain number, fewer than 9 digits in a row",
    "quantity": "a plain number, fewer than 9 digits in a row",
    "text": "a believable value in the app's language; no URLs, no real brands, no ID numbers",
}


def hero_id(entity_id: str, n: int) -> str:
    return f"HERO-{entity_id}-{n:02d}"


def _scope_entities(graph: SpecGraph) -> list[Entity]:
    """Entities that need hero records: those in scope (a `later` entity only serves `later` features)."""
    return [e for _, e in sorted(graph.entities.items()) if not e.later and e.status == "in"]


def hero_count(entity: Entity, profile: str, rnd: random.Random, cfg: SpecConfig) -> int:
    """States or the class count, whichever is larger (doc 18 5)."""
    if entity.klass == "primary":
        lo, hi = cfg.profiles.hero_per_primary_entity[profile]
    else:
        lo, hi = cfg.profiles.hero_per_other_entity
    return max(rnd.randint(lo, hi), len(entity.lifecycle.states))


def mint_hero_skeleton(
    graph: SpecGraph, rnd: random.Random, *, config: SpecConfig | None = None
) -> dict[str, HeroRecord]:
    """The records of every entity at the Freeze: count, state, relations, stable id; content left for the model.

    Every lifecycle state of every entity has at least one record. Code sets the fields it owns: the status, and
    dates relative to now. Each entity draws from its own seeded stream, so adding an entity does not change the
    records of the others. Does not change the graph; the caller stores the result in `graph.hero`."""
    cfg = config or default_config()
    base = rnd.getrandbits(64)
    entities = _scope_entities(graph)
    spread = int(mock_tables()["date_spread_days_hero"])
    records: dict[str, HeroRecord] = {}
    ids: dict[str, list[str]] = {}
    states_of: dict[str, list[str | None]] = {}
    for e in entities:
        r = random.Random(f"{base}:{e.id}:count")  # noqa: S311 - seeded, not security
        n = hero_count(e, graph.meta.profile, r, cfg)
        ids[e.id] = [hero_id(e.id, i + 1) for i in range(n)]
        if e.lifecycle.states:
            weights = [1.0 / (1 + i) for i in range(len(e.lifecycle.states))]
            extra = r.choices(e.lifecycle.states, weights=weights, k=n - len(e.lifecycle.states))
            states: list[str | None] = [*e.lifecycle.states, *extra]
            r.shuffle(states)
        else:
            states = [None] * n
        states_of[e.id] = states
    in_scope = set(ids)
    for e in entities:
        r = random.Random(f"{base}:{e.id}:rel")  # noqa: S311
        skey = status_key(e)
        rels: dict[str, list[str]] = {}
        for f in e.fields:
            if f.ref_entity and f.type != "list" and f.ref_entity in in_scope:
                rels[f.key] = _assign_relations(e.id, f.ref_entity, ids, r)
        for i, hid in enumerate(ids[e.id]):
            content: dict[str, str] = {}
            if skey and states_of[e.id][i]:
                content[skey] = str(states_of[e.id][i])
            for f in e.fields:
                if f.type == "date":
                    off = r.randint(1, 30) if _is_future_key(f.key) else -r.randint(0, spread)
                    content[f.key] = rel_time(off)
            records[hid] = HeroRecord(
                id=hid,
                entity=e.id,
                state=states_of[e.id][i],
                relations={k: v[i] for k, v in sorted(rels.items())},
                content=dict(sorted(content.items())),
                synthetic=True,
            )
    return records


def _assign_relations(
    entity_id: str, parent_id: str, ids: Mapping[str, list[str]], r: random.Random
) -> list[str]:
    """The parent record of each child record. Every parent gets a child before any gets a second one."""
    own = ids[entity_id]
    parents = ids[parent_id]
    if parent_id == entity_id:  # a tree: each record after the first hangs under an earlier one
        return ["" if i == 0 else own[r.randrange(i)] for i in range(len(own))]
    order = list(parents)
    r.shuffle(order)
    out = [order[i % len(order)] for i in range(len(own))]
    r.shuffle(out)
    return out


# ------------------------------------------------------------------ values shared by two entities (FN-32)


@dataclass(frozen=True)
class SharedValue:
    entity: str  # the child entity that would repeat the value
    field: str
    from_entity: str  # the parent entity that owns it
    from_field: str
    via: str  # the child's reference field that points at the parent
    kind: str  # amount | person_name | ...


@cache
def _shared_groups() -> list[dict[str, Any]]:
    return list(yaml.safe_load(SHARED_PATH.read_text(encoding="utf-8"))["groups"])


def _group_of(f: FieldDef) -> dict[str, Any] | None:
    for g in _shared_groups():
        if f.type == g["type"] and f.key.lower() in g["keys"]:
            return g
    return None


def shared_value_plan(graph: SpecGraph) -> list[SharedValue]:
    """Which child fields are derived from a parent record: a child with a reference to a parent, whose field is in
    the same shared group (same kind and type, `data/pools/shared_values.yaml`) as a field of the parent."""
    ents = {e.id: e for e in _scope_entities(graph)}
    out: list[SharedValue] = []
    for e in ents.values():
        for rf in e.fields:
            parent = ents.get(rf.ref_entity or "")
            if parent is None or parent.id == e.id or rf.type == "list":
                continue
            for cf in e.fields:
                grp = _group_of(cf)
                if grp is None or cf.key == rf.key:
                    continue
                cands = [pf for pf in parent.fields if _group_of(pf) is grp]
                if not cands:
                    continue
                same = [pf for pf in cands if pf.key == cf.key]
                pf = (same or cands)[0]
                if not any(sv.entity == e.id and sv.field == cf.key for sv in out):
                    out.append(SharedValue(e.id, cf.key, parent.id, pf.key, rf.key, grp["kind"]))
    return sorted(out, key=lambda s: (s.entity, s.field))


def localise_hero(graph: SpecGraph) -> int:
    """Write the people and places of the hero records in the languages the app offers (BUG-MK-1, doc 17 4.6).

    An app with several languages shows names and places in each script: the records of one entity take the pools of
    ``mock.mix_pools`` in turn (source language first), so a Nashik marketplace in English, Marathi and Hindi has Marathi and
    Hindi names beside English ones. Only fields that hold a person or a place are rewritten (the key words are data, ``text_key_hints``
    in mock_tables.yaml); a record the model or code left without that field is untouched. Seeded by record and field, so the
    same graph gives the same names; call it BEFORE ``derive_shared_values`` so a copied name follows its parent. Returns the
    number of values written. A one-language app changes nothing."""
    pools = mix_pools(graph)
    if len(pools) < 2:
        return 0
    by_entity: dict[str, list[HeroRecord]] = {}
    for rid in sorted(graph.hero):
        by_entity.setdefault(graph.hero[rid].entity, []).append(graph.hero[rid])
    written = 0
    for eid, recs in sorted(by_entity.items()):
        ent = graph.entities.get(eid)
        if ent is None:
            continue
        kinds: dict[str, str] = {}
        for f in ent.fields:
            if f.type == "location":
                kinds[f.key] = "place"
            elif f.type == "text" and (kind := _text_kind(f.key)):
                kinds[f.key] = kind
        for i, rec in enumerate(recs):
            pool = pools[i % len(pools)]
            for key, kind in sorted(kinds.items()):
                if key not in rec.content:
                    continue
                rnd = random.Random(f"{rec.id}:{key}:locale")  # noqa: S311 - seeded, not security
                pick = rnd.choice(pool.people if kind == "person" else pool.places)
                if rec.content[key] != pick:
                    rec.content[key] = pick
                    written += 1
    return written


def derive_shared_values(graph: SpecGraph) -> list[dict[str, str]]:
    """Copy each shared value from the parent record into its child records (graph.hero is changed in place).
    A value a filler wrote for such a field is overwritten, never kept twice. Returns what was derived."""
    plan = shared_value_plan(graph)
    derived: dict[tuple[str, str], dict[str, str]] = {}
    for _ in range(len(plan) + 1):  # chains (line -> order -> customer) settle in a few passes
        changed = False
        for sv in plan:
            for rec in graph.hero.values():
                if rec.entity != sv.entity:
                    continue
                parent = graph.hero.get(rec.relations.get(sv.via, ""))
                if parent is None:
                    continue
                val = parent.content.get(sv.from_field)
                if val is not None and rec.content.get(sv.field) != val:
                    rec.content[sv.field] = val
                    derived[(rec.id, sv.field)] = {
                        "record": rec.id,
                        "field": sv.field,
                        "from": parent.id,
                        "value": val,
                    }
                    changed = True
        if not changed:
            break
    return [derived[k] for k in sorted(derived)]


# ------------------------------------------------------------------ the fill plan (related entities in one call)


@dataclass
class FillGroup:
    id: str
    entities: list[str]
    record_ids: dict[str, list[str]]
    write_fields: dict[str, list[dict[str, str]]]
    code_set: dict[str, list[str]]
    derived: dict[str, list[dict[str, str]]]
    image_brief_for: dict[str, list[str]]
    context_entities: list[str] = field(default_factory=list)
    split_from_larger: bool = False


def _components(entities: list[Entity]) -> list[list[str]]:
    ids = {e.id for e in entities}
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for e in entities:
        for f in e.fields:
            if f.ref_entity in ids and f.type != "list":
                adj[e.id].add(f.ref_entity)  # type: ignore[arg-type]
                adj[f.ref_entity].add(e.id)  # type: ignore[index]
    seen: set[str] = set()
    comps: list[list[str]] = []
    for start in sorted(ids):
        if start in seen:
            continue
        stack, comp = [start], []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            stack.extend(sorted(adj[cur] - seen))
        comps.append(sorted(comp))
    return comps


def _parent_first(ents: dict[str, Entity], comp: list[str]) -> list[str]:
    order: list[str] = []
    visiting: set[str] = set()

    def visit(i: str) -> None:
        if i in order or i in visiting:
            return
        visiting.add(i)
        for f in ents[i].fields:
            if f.ref_entity in comp and f.ref_entity != i and f.type != "list":
                visit(f.ref_entity)  # type: ignore[arg-type]
        visiting.discard(i)
        order.append(i)

    for i in comp:
        visit(i)
    return order


def fill_plan(graph: SpecGraph, *, config: SpecConfig | None = None) -> list[FillGroup]:
    """Groups of related entities that MOCK-HERO fills in the SAME call (so two calls cannot disagree on a shared
    record). A group is split only when it would exceed `fusion.entities_per_call_max`; parents come first and the
    later groups see their parents as read-only context. Each entity lists the fields the model writes (not the
    status, the dates, the relations or the derived values, which code owns)."""
    cfg = config or default_config()
    ents = {e.id: e for e in _scope_entities(graph)}
    plan = shared_value_plan(graph)
    ids_by_entity: dict[str, list[str]] = {}
    for hid in sorted(graph.hero):
        ids_by_entity.setdefault(graph.hero[hid].entity, []).append(hid)
    groups: list[FillGroup] = []
    cap = cfg.fusion.entities_per_call_max
    for comp in _components(list(ents.values())):
        order = _parent_first(ents, comp)
        chunks = [order[i : i + cap] for i in range(0, len(order), cap)]
        for chunk in chunks:
            groups.append(
                _group(graph, ents, plan, ids_by_entity, chunk, len(groups) + 1, len(chunks) > 1)
            )
    return groups


def _group(
    graph: SpecGraph,
    ents: dict[str, Entity],
    plan: list[SharedValue],
    ids_by_entity: dict[str, list[str]],
    chunk: list[str],
    n: int,
    split: bool,
) -> FillGroup:
    derived_keys = {(sv.entity, sv.field) for sv in plan}
    write: dict[str, list[dict[str, str]]] = {}
    code_set: dict[str, list[str]] = {}
    derived: dict[str, list[dict[str, str]]] = {}
    briefs: dict[str, list[str]] = {}
    context: set[str] = set()
    for eid in chunk:
        e = ents[eid]
        skey = status_key(e)
        rows: list[dict[str, str]] = []
        cs: list[str] = []
        for f in e.fields:
            if f.key == "id" or f.type == "id":
                continue
            if f.ref_entity and f.type != "list":
                cs.append(f.key)
                if f.ref_entity not in chunk and f.ref_entity in ents:
                    context.add(f.ref_entity)
            elif f.key == skey or f.type == "date":
                cs.append(f.key)
            elif (eid, f.key) in derived_keys or f.type in _SKIP_WRITE_TYPES:
                continue
            else:
                rows.append(
                    {
                        "key": f.key,
                        "type": f.type,
                        "rule": _FIELD_RULES.get(f.type, _FIELD_RULES["text"]),
                    }
                )
        write[eid] = rows
        code_set[eid] = sorted(cs)
        derived[eid] = [
            {
                "field": sv.field,
                "from_entity": sv.from_entity,
                "from_field": sv.from_field,
                "via": sv.via,
            }
            for sv in plan
            if sv.entity == eid
        ]
        briefs[eid] = [f.key for f in e.fields if f.media]
    return FillGroup(
        id=f"HEROGRP-{n:02d}",
        entities=chunk,
        record_ids={eid: ids_by_entity.get(eid, []) for eid in chunk},
        write_fields=write,
        code_set=code_set,
        derived=derived,
        image_brief_for=briefs,
        context_entities=sorted(context),
        split_from_larger=split,
    )


# ------------------------------------------------------------------ applying a fill


@dataclass
class FillResult:
    applied: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(
        default_factory=dict
    )  # record id -> problems (the rest of the record is kept)
    dropped_code_owned: dict[str, list[str]] = field(
        default_factory=dict
    )  # keys code owns, ignored
    derived: int = 0


def apply_fill(
    graph: SpecGraph,
    filled: Mapping[str, Mapping[str, Any]],
    *,
    allowed_names: Iterable[str] = (),
) -> FillResult:
    """Merge a hero call's content into `graph.hero`. `filled` maps a record id to `{"content": {...},
    "image_brief": "..."}` (a plain content mapping is accepted too). The call may fill only the fields of its fill
    plan: unknown ids are rejected, keys code owns (status, dates, relations, derived values) are ignored, and a
    value that holds a URL, a real brand, an ID number, an unmasked phone number or an email outside example.com is
    rejected on its own while the rest of the record is kept. Shared values are then derived from the parents."""
    res = FillResult()
    plan = {(sv.entity, sv.field) for sv in shared_value_plan(graph)}
    for hid in sorted(filled):
        rec = graph.hero.get(hid)
        if rec is None:
            res.rejected[hid] = ["no such hero record: the call may not mint ids"]
            continue
        entity = graph.entities.get(rec.entity)
        if entity is None:
            res.rejected[hid] = [f"entity {rec.entity} is gone"]
            continue
        body = filled[hid]
        content = (
            body.get("content", body) if isinstance(body.get("content", body), Mapping) else {}
        )
        writable = {
            f.key
            for f in entity.fields
            if f.type not in _SKIP_WRITE_TYPES
            and not f.ref_entity
            and f.type != "date"
            and f.key != status_key(entity)
            and (entity.id, f.key) not in plan
        }
        problems: list[str] = []
        for key, value in sorted(content.items()):
            if key in ("content", "image_brief") and key not in {f.key for f in entity.fields}:
                continue
            if key not in writable:
                owned = key in {f.key for f in entity.fields}
                if owned:
                    res.dropped_code_owned.setdefault(hid, []).append(key)
                else:
                    problems.append(f"{key} is not a field of {entity.id}")
                continue
            if not isinstance(value, str):
                problems.append(f"{key}: the value must be text")
                continue
            bad = find_unsafe([value], allowed_names=allowed_names)
            if bad:
                problems.append(f"{key}: {bad[0].kind}")
                continue
            rec.content[key] = value
        brief = body.get("image_brief") if "image_brief" in body else None
        if isinstance(brief, str) and brief:
            bad = find_unsafe([brief], allowed_names=allowed_names)
            if bad:
                problems.append(f"image_brief: {bad[0].kind}")
            else:
                rec.image_brief = brief
        rec.synthetic = True
        rec.filled = True
        res.applied.append(hid)
        if problems:
            res.rejected[hid] = problems
    res.derived = len(derive_shared_values(graph))
    return res


def hero_ref_index(graph: SpecGraph) -> dict[str, list[str]]:
    """`ENT-006:payment_pending` -> hero ids. Cards cite a hero record by entity and state; the packager resolves
    the citation here at assemble time. A record without a state is listed under `ENT-006`."""
    out: dict[str, list[str]] = {}
    for hid in sorted(graph.hero):
        rec = graph.hero[hid]
        out.setdefault(rec.entity, []).append(hid)
        if rec.state:
            out.setdefault(f"{rec.entity}:{rec.state}", []).append(hid)
    return out
