"""Mock handler of PROD-MODULE: deterministic, derived from the input slice only, valid and useful offline.

It starts from the completed draft (acceptance and a story for every screen) and adds what code can read from the draft
itself: a `text` field whose key names money, a phone, a place or a file gets that type; every lifecycle transition of a
primary or secondary entity becomes a CUSTOM operation (a domain action CRUD cannot express) with typed response, errors
from the closed list and the roles that may move the status. Nothing here states a number or names a provider.
"""

from __future__ import annotations

from mde_spec_agent.derive.text import plural, slug
from mde_spec_agent.lanes.product.kit import vocab

from .baseline import complete
from .schema import ModuleIn, ModuleOut, OperationRec, OpErrorRec, OpFieldRec

MAX_OPS_PER_ENTITY = 4


def _retype(out: ModuleOut) -> None:
    words = vocab.hints()["field_type_words"]
    media = set(vocab.hints()["media_words"])
    for f in out.fields:
        tokens = set(f.key.split("_"))
        if f.type != "text":
            continue
        for new_type, ws in words.items():
            if tokens & set(ws):
                f.type = new_type
                if new_type == "file" and tokens & media:
                    f.media = True
                break


def handle(inp: ModuleIn) -> ModuleOut:
    out = complete(inp)
    _retype(out)
    names = {e.id: e.name for e in out.entities}
    klass = {e.id: e.klass for e in out.entities}
    initial = {s.entity: s.state for s in out.states if s.initial}
    count: dict[str, int] = {}
    for t in out.transitions:
        if klass.get(t.entity) not in ("primary", "secondary") or not t.roles:
            continue
        if t.to_state == initial.get(t.entity) or count.get(t.entity, 0) >= MAX_OPS_PER_ENTITY:
            continue
        count[t.entity] = count.get(t.entity, 0) + 1
        opid = f"NEW-op_{slug(names[t.entity])}_{slug(t.to_state)}"
        if any(o.id == opid for o in out.operations):
            continue
        noun = names[t.entity].lower()
        out.operations.append(
            OperationRec(
                id=opid,
                name=f"Mark {noun} {t.to_state.replace('_', ' ')}",
                entity=t.entity,
                method="POST",
                path=f"/{plural(slug(names[t.entity]))}/{{id}}/{slug(t.to_state)}".replace(
                    "_", "-"
                ),
                roles=sorted(t.roles),
                idempotent=True,
                prov=[f"assumed:lifecycle transition {t.from_state} to {t.to_state} of {t.entity}"],
            )
        )
        out.op_fields += [
            OpFieldRec(op=opid, side="response", key="id", type="id", required=True),
            OpFieldRec(op=opid, side="response", key="status", type="enum", required=True),
        ]
        out.op_errors += [
            OpErrorRec(op=opid, code="unauthenticated", recovery="sign_in_again"),
            OpErrorRec(op=opid, code="forbidden", recovery="stay"),
            OpErrorRec(op=opid, code="not_found", recovery="go_back"),
            OpErrorRec(op=opid, code="conflict", recovery="reload"),
        ]
        t.on = opid
    return out
