"""The patch a PROD-MODULE answer makes: pure, from the answer, the call input and the graph snapshot. Nodes the model
rewrote keep their tier, aliases and provenance (it only ADDS provenance); nothing is ever removed; a new node gets its
tier from its evidence and is never Locked; ids that start with NEW- are minted here, in the batch's own id range."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from mde_spec_agent.lanes.product.kit.common import (
    ProvAllowed,
    merge_prov,
    safe_prov,
    tier_and_assumed,
)
from mde_spec_agent.lanes.product.kit.env import Env, input_hash_of
from mde_spec_agent.lanes.product.kit.gate import Minter
from mde_spec_agent.run.patch import Patch
from mde_spec_core.graph import (
    Acceptance,
    Entity,
    FieldDef,
    Lifecycle,
    Operation,
    OpError,
    Rule,
    Story,
    Transition,
)
from mde_spec_core.ids import parse_id

from .repair import repair
from .schema import ModuleIn, ModuleOut, StoryRec

NEW = "NEW-"


def is_new(ident: str) -> bool:
    return ident.startswith(NEW)


def build_module_patch(
    env: Env, inp: ModuleIn, out: ModuleOut, minter: Minter, flagged: bool
) -> Patch:
    g = env.graph
    allowed = ProvAllowed.of(inp)
    ih = input_hash_of(inp)
    out, rcode = repair(
        inp, out
    )  # R-code: an invented non-join, non-log entity is dropped (logged)

    def safe(
        strings: Any, node_id: str, *, claims_field: bool, tier: str = "default"
    ) -> tuple[list[Any], list[str], list[dict[str, str]]]:
        """Provenance the V-36 check accepts: research is recorded in claims_used or downgraded to assumed (logged)."""
        prov, claims, rows = safe_prov(
            strings,
            env.quote_of,
            allowed.labels,
            node_id,
            claims_field=claims_field,
            tier=tier,
            allowed=allowed,
            graph=env.graph,
        )
        rcode.extend(rows)
        return prov, claims, rows

    ids: dict[str, str] = {}  # NEW-key (entity, rule, operation, story) -> minted id
    for e in out.entities:
        ids[e.id] = minter.mint("ENT") if is_new(e.id) else e.id
    for r in out.rules:
        ids[r.id] = minter.mint("RUL") if is_new(r.id) else r.id
    for o in out.operations:
        ids[o.id] = minter.mint("OP") if is_new(o.id) else o.id
    for s in out.stories:
        ids[s.id] = minter.mint("US") if is_new(s.id) else s.id

    def rid(ref: str) -> str:
        return ids.get(ref, ref)

    fields_of: dict[str, list[Any]] = defaultdict(list)
    for f in out.fields:
        fields_of[f.entity].append(f)
    states_of: dict[str, list[Any]] = defaultdict(list)
    for s in out.states:
        states_of[s.entity].append(s)
    trans_of: dict[str, list[Any]] = defaultdict(list)
    for t in out.transitions:
        trans_of[t.entity].append(t)

    tables: dict[str, dict[str, Any]] = defaultdict(dict)
    module = g.modules[inp.module_id]
    new_entity_ids: list[str] = []

    # ---- entities ------------------------------------------------------------------------------------------------
    for rec in out.entities:
        eid = ids[rec.id]
        base = g.entities.get(eid)
        prov = safe(rec.prov, eid, claims_field=False, tier=base.tier if base else "default")[0]
        if base is None:
            tier, assumed = tier_and_assumed(prov, allowed.labels)
            node = Entity(
                id=eid,
                tier=tier,  # type: ignore[arg-type]
                assumed=assumed,
                provenance=prov,
                name=rec.name,
                owner_module=inp.module_id,
                klass=rec.klass,
            )
            new_entity_ids.append(eid)
        else:
            node = base.model_copy(deep=True)
            node.provenance = merge_prov(node.provenance, prov)
            if rec.klass != node.klass:
                node.klass = rec.klass
        if rec.typical_count > 0:
            node.typical_count = rec.typical_count
        if rec.max_count > 0:
            node.max_count = rec.max_count
        node.input_hash = ih
        node.degraded = node.degraded or flagged
        # fields: the draft's fields stay (retyped or constrained when the answer says so), new fields are appended
        by_key = {f.key: f for f in node.fields}
        order = [f.key for f in node.fields]
        for fr in fields_of.get(rec.id, []):
            fd = FieldDef(
                key=fr.key,
                type=fr.type,
                required=fr.required,
                enum_values=list(fr.enum_values),
                ref_entity=rid(fr.ref_entity) if fr.ref_entity else None,
                derived=rid(fr.derived_rule) if fr.derived_rule else None,
                constraint=fr.constraint,
                media=fr.media,
            )
            if fr.key not in by_key:
                order.append(fr.key)
            by_key[fr.key] = fd
        node.fields = [by_key[k] for k in order]
        # lifecycle
        srows = states_of.get(rec.id, [])
        if srows:
            states = [s.state for s in srows]
            initial = next((s.state for s in srows if s.initial), states[0])
            transitions = [
                Transition(
                    from_state=t.from_state,
                    to_state=t.to_state,
                    roles=list(t.roles),
                    on=rid(t.on) if t.on else "",
                )
                for t in trans_of.get(rec.id, [])
            ]
            node.lifecycle = Lifecycle(states=states, transitions=transitions, initial=initial)
            for i, f in enumerate(node.fields):
                if f.key == "status" and f.type == "enum":  # enums come from lifecycles
                    node.fields[i] = f.model_copy(update={"enum_values": list(states)})
        tables["entities"][eid] = node.model_dump(mode="json")
    if new_entity_ids:
        mod = module.model_copy(deep=True)
        mod.entity_ids = sorted({*mod.entity_ids, *new_entity_ids})
        tables["modules"][mod.id] = mod.model_dump(mode="json")

    # ---- rules -----------------------------------------------------------------------------------------------------
    for rr in out.rules:
        nid = ids[rr.id]
        base = g.rules.get(nid)
        prov, claims, _ = safe(
            rr.prov, nid, claims_field=True, tier=base.tier if base else "default"
        )
        if base is None:
            tier, assumed = tier_and_assumed(prov, allowed.labels)
            rule = Rule(
                id=nid,
                tier=tier,  # type: ignore[arg-type]
                assumed=assumed,
                provenance=prov,
                subject=rr.subject,
                statement=rr.statement,
                condition=rr.condition,
                value=rr.value or None,
                unit=rr.unit or None,
                confirm=rr.confirm or bool(claims),
                module_id=inp.module_id,
                claims_used=claims,
            )
        else:
            rule = base.model_copy(deep=True)
            rule.module_id = inp.module_id
            rule.confirm = rule.confirm or rr.confirm
            rule.provenance = merge_prov(rule.provenance, prov)
            rule.claims_used = sorted({*rule.claims_used, *claims})
        rule.input_hash = ih
        rule.degraded = rule.degraded or flagged
        tables["rules"][nid] = rule.model_dump(mode="json")

    # ---- custom operations -------------------------------------------------------------------------------------
    for orec in out.operations:
        nid = ids[orec.id]
        prov = safe(orec.prov, nid, claims_field=False)[0]
        tier, assumed = tier_and_assumed(prov, allowed.labels)
        path = "/" + orec.path.strip().strip("/")
        op = Operation(
            id=nid,
            tier=tier,  # type: ignore[arg-type]
            assumed=assumed,
            provenance=prov,
            name=orec.name,
            entity=rid(orec.entity) if orec.entity else None,
            method=orec.method,
            path=path,
            kind="custom",
            request=[
                FieldDef(key=f.key, type=f.type, required=f.required)
                for f in out.op_fields
                if f.op == orec.id and f.side == "request"
            ],
            response=[
                FieldDef(key=f.key, type=f.type, required=f.required)
                for f in out.op_fields
                if f.op == orec.id and f.side == "response"
            ],
            errors=[
                OpError(code=e.code, recovery=e.recovery) for e in out.op_errors if e.op == orec.id
            ],
            roles=sorted(set(orec.roles)),
            idempotent=orec.idempotent,
            paged=orec.paged,
            app_tags=sorted(module.app_ids),
        )
        op.input_hash = ih
        op.degraded = flagged
        tables["operations"][nid] = op.model_dump(mode="json")

    # ---- stories ---------------------------------------------------------------------------------------------------
    acc_of: dict[str, list[Acceptance]] = defaultdict(list)
    for a in out.acceptance:
        acc_of[a.story].append(Acceptance(given=a.given, when=a.when, then=a.then))
    for srec in out.stories:
        nid = ids[srec.id]
        base = g.stories.get(nid)
        prov = safe(srec.prov, nid, claims_field=False, tier=base.tier if base else "default")[0]
        story = _story(base, nid, srec, prov, allowed.labels)
        story.acceptance = acc_of.get(srec.id, [])
        if (
            story.assumed and story.voice == "must"
        ):  # an assumed story is never voice must (V-40): code makes it usually
            story.voice = "usually"
            rcode.append(
                {
                    "kind": "downgrade_locked_to_default",
                    "node_id": nid,
                    "what": "an assumed story cannot be voice must: made usually",
                    "before": "must",
                    "after": "usually",
                }
            )
        story.input_hash = ih
        story.degraded = story.degraded or flagged
        tables["stories"][nid] = story.model_dump(mode="json")

    # a story's screens also cite the story (V-40 reads either side); screens are not rewritten here
    patch: Patch = {
        "tables": {t: dict(sorted(n.items())) for t, n in tables.items()},
        "replace": True,
    }
    if rcode:
        patch["rcode"] = sorted(rcode, key=lambda r: (r["node_id"], r["before"]))
    return patch


def _story(base: Story | None, nid: str, srec: StoryRec, prov: list[Any], labels: Any) -> Story:
    if base is None:
        tier, assumed = tier_and_assumed(prov, labels)
        return Story(
            id=nid,
            tier=tier,  # type: ignore[arg-type]
            assumed=assumed,
            provenance=prov,
            role_id=srec.role,
            want=srec.want,
            so_that=srec.so_that,
            feature_ids=list(srec.feature_ids),
            policy_id=srec.policy_id or None,
            screen_ids=list(srec.screen_ids),
            voice=srec.voice,  # type: ignore[arg-type]
        )
    story = base.model_copy(deep=True)
    story.want = srec.want
    story.so_that = srec.so_that
    story.feature_ids = list(srec.feature_ids)
    story.policy_id = srec.policy_id or None
    story.screen_ids = list(srec.screen_ids)
    story.voice = srec.voice  # type: ignore[assignment]
    story.provenance = merge_prov(story.provenance, prov)
    return story


def minted_kind(ident: str) -> str | None:
    p = parse_id(ident)
    return p.kind if p else None
