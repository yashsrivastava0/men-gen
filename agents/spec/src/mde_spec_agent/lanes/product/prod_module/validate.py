"""Local checks of a PROD-MODULE answer: closed vocabularies (also enforced by the schema), ids and edges that exist,
provenance present and real, no Locked claim without evidence (code decides the tier), no number the sources do not state,
no layout or widget word the model introduced, lifecycles that make sense, stories that cover what they must.
The runner re-asks once with these messages word for word, then uses the fallback and flags the node."""

from __future__ import annotations

from collections import Counter

from mde_spec_agent.lanes.product.kit.common import (
    Errors,
    invented_numbers,
    new_form_words,
    parse_prov,
    source_numbers,
)
from mde_spec_core.ids import is_valid_key

from .repair import repair
from .schema import ModuleIn, ModuleOut

MAX_NEW_FIELDS_PER_ENTITY = 6


def _evidenced(prov: list[str]) -> bool:
    kinds = {p[0] for raw in prov if (p := parse_prov(raw))}
    return bool(kinds & {"user", "compass", "policy"})


def validate(inp: ModuleIn, out: ModuleOut) -> list[str]:
    out, _ = repair(
        inp, out
    )  # what code repairs mechanically is not an error (the patch makes the same repair)
    e = Errors()
    source = "\n".join([*inp.slice, *inp.facts, *inp.research])
    nums = source_numbers(source, inp.baseline.model_dump_json())
    known_roles = set(inp.role_ids)
    known_screens = set(inp.screen_ids)
    known_features = set(inp.feature_ids)
    in_features = set(inp.in_feature_ids)
    policies = set(inp.policy_refs)

    # ---- entities and fields ------------------------------------------------------------------------------------
    own_entities = set(inp.entity_ids)
    seen = Counter(x.id for x in out.entities)
    for rec in out.entities:
        if seen[rec.id] > 1:
            e.add(f"entity {rec.id}: listed twice")
        if rec.id not in own_entities and not rec.id.startswith("NEW-"):
            e.add(
                f"entity {rec.id}: not an entity of this module; use an id of the draft or NEW-<name>"
            )
        if not rec.name.strip():
            e.add(f"entity {rec.id}: name is empty")
    entity_ids = {r.id for r in out.entities}
    rule_ids = set(inp.rule_ids) | {r.id for r in out.rules}
    dup = Counter((f.entity, f.key) for f in out.fields)
    new_fields: Counter[str] = Counter()
    draft_keys = {(f.entity, f.key) for f in inp.baseline.fields}
    for f in out.fields:
        where = f"field {f.entity}.{f.key}"
        if f.entity not in entity_ids:
            e.add(f"{where}: the entity is not in this answer")
        if not is_valid_key(f.key):
            e.add(f"{where}: key must be lower snake case")
        if dup[(f.entity, f.key)] > 1:
            e.add(f"{where}: listed twice")
        if (f.entity, f.key) not in draft_keys:
            new_fields[f.entity] += 1
        if f.type == "enum" and not f.enum_values:
            e.add(f"{where}: an enum needs its values")
        if f.type == "ref" and not f.ref_entity:
            e.add(f"{where}: a ref needs ref_entity")
        if f.ref_entity and f.ref_entity not in own_entities | entity_ids | set(inp.ref_ids):
            e.add(f"{where}: ref_entity {f.ref_entity} does not exist")
        if f.derived_rule and f.derived_rule not in rule_ids:
            e.add(f"{where}: derived_rule {f.derived_rule} is not a rule of this module")
        for bad in invented_numbers(f.constraint, nums):
            e.add(
                f"{where}: constraint states {bad}, which no source states (never invent a number)"
            )
        for w in new_form_words(f.constraint, source):
            e.add(f"{where}: constraint uses the form word {w!r}")
    for ent, n in new_fields.items():
        if n > MAX_NEW_FIELDS_PER_ENTITY:
            e.add(f"entity {ent}: {n} new fields; add only what a feature, story or fact needs")

    # ---- lifecycles ----------------------------------------------------------------------------------------------
    for rec in out.entities:
        states = [s for s in out.states if s.entity == rec.id]
        if not states:
            continue
        names = [s.state for s in states]
        if len(set(names)) != len(names):
            e.add(f"entity {rec.id}: a status is listed twice")
        init = [s.state for s in states if s.initial]
        if len(init) != 1:
            e.add(f"entity {rec.id}: exactly one status must be initial")
        trans = [t for t in out.transitions if t.entity == rec.id]
        for t in trans:
            if t.from_state not in names or t.to_state not in names:
                e.add(
                    f"entity {rec.id}: transition {t.from_state}->{t.to_state} names an unknown status"
                )
            for r in t.roles:
                if r not in known_roles:
                    e.add(f"entity {rec.id}: transition role {r} is not a role of this app")
            if not t.roles:
                e.add(f"entity {rec.id}: transition {t.from_state}->{t.to_state} names no role")
        if len(names) >= 2 and init:
            reach, stack = set(), [init[0]]
            while stack:
                cur = stack.pop()
                if cur in reach:
                    continue
                reach.add(cur)
                stack += [t.to_state for t in trans if t.from_state == cur]
            for n in names:
                if n not in reach:
                    e.add(f"entity {rec.id}: status {n} is unreachable from {init[0]}")
            if all(any(t.from_state == n for t in trans) for n in names):
                e.add(f"entity {rec.id}: no terminal status (one with no way out)")
        status = [
            f for f in out.fields if f.entity == rec.id and f.key == "status" and f.type == "enum"
        ]
        for f in status:
            if f.enum_values and set(f.enum_values) != set(names):
                e.add(
                    f"entity {rec.id}: the status values must equal the lifecycle statuses {names}"
                )

    # ---- rules ---------------------------------------------------------------------------------------------------
    facts_nums = source_numbers("\n".join(inp.facts), "\n".join(inp.research))
    for r in out.rules:
        where = f"rule {r.id}"
        if not (r.id in inp.rule_ids or r.id.startswith("NEW-")):
            e.add(f"{where}: use an id of the draft or NEW-<key>")
        if not r.statement.strip():
            e.add(f"{where}: statement is empty")
        if r.value:
            if bool(r.unit) is False:
                e.add(f"{where}: a value needs its unit")
            if not _evidenced(r.prov):
                e.add(
                    f"{where}: a value needs a user or Compass quote; without one leave value empty and mark the rule assumed"
                )
            if r.value.replace(",", ".") not in facts_nums | nums:
                e.add(
                    f"{where}: the value {r.value} is not stated by any fact (never invent a number)"
                )
        for bad in invented_numbers(f"{r.statement} {r.condition}", nums):
            e.add(f"{where}: the text states {bad}, which no source states (never invent a number)")
        for w in new_form_words(f"{r.subject} {r.statement} {r.condition}", source):
            e.add(f"{where}: uses the form word {w!r}")
        if r.entity and r.entity not in entity_ids | own_entities:
            e.add(f"{where}: entity {r.entity} does not exist")

    # ---- custom operations ----------------------------------------------------------------------------------------
    op_ids = [o.id for o in out.operations]
    for o in out.operations:
        where = f"operation {o.id}"
        if not o.id.startswith("NEW-") and o.id not in {x.id for x in inp.baseline.operations}:
            e.add(f"{where}: a custom operation is written as NEW-<key>")
        if op_ids.count(o.id) > 1:
            e.add(f"{where}: listed twice")
        if not o.path.startswith("/") or " " in o.path or "//" in o.path:
            e.add(f"{where}: path must start with / and have no spaces")
        if o.entity and o.entity not in entity_ids | own_entities:
            e.add(f"{where}: entity {o.entity} does not exist")
        if not o.roles:
            e.add(f"{where}: name the roles that may call it (default deny)")
        for r in o.roles:
            if r not in known_roles:
                e.add(f"{where}: role {r} is not a role of this app")
        if not any(x.op == o.id for x in out.op_errors):
            e.add(f"{where}: declare its errors (at least one)")
        for w in new_form_words(o.name, source):
            e.add(f"{where}: name uses the form word {w!r}")
    keyed = Counter((o.method, o.path.strip("/")) for o in out.operations)
    for (m, p), n in keyed.items():
        if n > 1:
            e.add(f"operations: {m} /{p} is used twice")
    for f in out.op_fields:
        if f.op not in op_ids:
            e.add(f"op field {f.op}.{f.key}: the operation is not in this answer")
        if not is_valid_key(f.key):
            e.add(f"op field {f.op}.{f.key}: key must be lower snake case")
    for x in out.op_errors:
        if x.op not in op_ids:
            e.add(f"op error {x.op}/{x.code}: the operation is not in this answer")

    # ---- stories -------------------------------------------------------------------------------------------------
    story_ids = Counter(s.id for s in out.stories)
    for s in out.stories:
        where = f"story {s.id}"
        if story_ids[s.id] > 1:
            e.add(f"{where}: listed twice")
        if not (s.id in inp.story_ids or s.id.startswith("NEW-")):
            e.add(f"{where}: use an id of the draft or NEW-<key>")
        if s.role not in known_roles:
            e.add(f"{where}: role {s.role} is not a role of this app")
        if not s.want.strip() or not s.so_that.strip():
            e.add(f"{where}: want and so_that must not be empty")
        for f in s.feature_ids:
            if f not in known_features:
                e.add(f"{where}: feature {f} does not exist")
        if not (
            any(f in in_features for f in s.feature_ids)
            or (s.policy_id and (not policies or s.policy_id in policies))
        ):
            e.add(f"{where}: cite an in feature or a policy id of the sheet")
        for sc in s.screen_ids:
            if sc not in known_screens:
                e.add(f"{where}: screen {sc} does not exist")
        if not any(a.story == s.id for a in out.acceptance):
            e.add(f"{where}: write at least one acceptance (given, when, then)")
        for w in new_form_words(f"{s.want} {s.so_that}", source):
            e.add(f"{where}: uses the form word {w!r}")
    for a in out.acceptance:
        if a.story not in story_ids:
            e.add(f"acceptance for {a.story}: that story is not in this answer")
        if not (a.given.strip() and a.when.strip() and a.then.strip()):
            e.add(f"acceptance for {a.story}: given, when and then must all be written")
        for w in new_form_words(f"{a.given} {a.when} {a.then}", source):
            e.add(f"acceptance for {a.story}: uses the form word {w!r}")
    cited_features = {f for s in out.stories for f in s.feature_ids}
    for f in inp.cover_feature_ids:
        if f not in cited_features:
            e.add(f"feature {f}: no story serves it; write its story")
    cited_screens = {sc for s in out.stories for sc in s.screen_ids}
    for sc in inp.cover_screen_ids:
        if sc not in cited_screens:
            e.add(f"screen {sc}: no story cites it; cite it in the story it serves")
    return e.items
