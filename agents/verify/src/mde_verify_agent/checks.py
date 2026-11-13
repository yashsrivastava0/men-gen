"""The pure-code half of Verify: zero model cost (doc 19 3.2).

* `code_findings(graph, check_report)` turns the self-check's validator findings (the same code Verify re-runs; it owns
  no different validators) into `Defect`s with repair classes: A code fix, B lane call, C needs decision.
* `structure_code_defects(graph, locked)` is the code half of the structure critic at the Freeze: the structure
  validators plus the checks no validator owns (role and entity mismatch, cross-app lifecycle disagreement, a Locked
  fact dropped or changed against the Brief). The model half only adds what code cannot judge.
* `confirm_rank_defects(confirm)` checks the confirm list's ranking (money, legal, identity first) in code.

Nothing here writes into the graph: a defect says what to change; Spec applies it.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mde_spec_core.graph import Entity, SpecGraph
from mde_spec_core.render import bound_fields
from mde_spec_core.validators.base import Finding, run_all
from mde_spec_core.validators.context import make_context
from mde_spec_core.verify_port import Defect, LockedFact, Patch

# The validators that look at STRUCTURE (what exists and how it connects): what the Freeze can judge on the graph
# alone. The model-free half of the structure critic runs exactly these, plus the S-checks below.
STRUCTURE_CODES: frozenset[str] = frozenset(
    {
        "V-02",
        "V-03",
        "V-04",
        "V-05",
        "V-07",
        "V-08",
        "V-09",
        "V-11",
        "V-17",
        "V-19",
        "V-28",
        "V-29",
        "V-30",
        "V-32",
        "V-33",
    }
)

_SEVERITY_ORDER = {"blocker": 0, "warning": 1, "info": 2}
# A finding of one family on one node is reported once; the more specific S-check wins over the general validator.
_FAMILY = {"V-11": "same_entity_name", "S-XAPP": "same_entity_name"}


def _split_where(where: str) -> tuple[str, str]:
    """`SCR-004.key` -> (`SCR-004`, `key`); a file path or plain id stays whole."""
    if "/" in where or where.endswith((".md", ".json")):
        return where, ""
    head, _, tail = where.partition(".")
    return head, tail


def slug(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


def defect_from_finding(f: Finding) -> Defect:
    node, fld = _split_where(f.where)
    return Defect(
        id=f"{f.code}:{f.where}:{slug(f.why)}",
        cls=f.repair,
        node=node,
        field=fld,
        change=f.fix,
        reason=f.why,
        severity=f.severity,
        source="code",
        code=f.code,
    )


def sort_defects(defects: Iterable[Defect]) -> list[Defect]:
    return sorted(defects, key=lambda d: (_SEVERITY_ORDER[d.severity], d.node, d.code, d.id))


def dedupe(defects: Iterable[Defect], *, prefer: Sequence[str] = ()) -> list[Defect]:
    """Unique by id; then one finding per (node, family) when a family is defined, `prefer` codes winning."""
    seen: dict[str, Defect] = {}
    for d in defects:
        seen.setdefault(d.id, d)
    by_family: dict[tuple[str, str], Defect] = {}
    rest: list[Defect] = []
    for d in seen.values():
        fam = _FAMILY.get(d.code)
        if fam is None:
            rest.append(d)
            continue
        key = (d.node, fam)
        cur = by_family.get(key)
        if cur is None or (d.code in prefer and cur.code not in prefer):
            by_family[key] = d
    return sort_defects([*rest, *by_family.values()])


def code_findings(
    graph: SpecGraph,
    check_report: Any = None,
    *,
    only: Iterable[str] | None = None,
    **ctx_kwargs: Any,
) -> list[Defect]:
    """Validator findings as Defects. With a `CheckReport` its findings are converted as they are (Verify re-runs the
    same code, never different validators); without one the whole suite runs on the graph through `make_context`.
    Builder needs still missing count as blockers of class C (a need nobody can fill without a decision)."""
    if check_report is not None:
        findings: list[Finding] = list(check_report.findings)
        if only is not None:
            keep = set(only)
            findings = [f for f in findings if f.code in keep]
        out = [defect_from_finding(f) for f in findings if f.severity != "info"]
        for need in getattr(check_report, "missing", []) or []:
            out.append(
                Defect(
                    id=f"BN:{need.id}",
                    cls="C",
                    node=str(need.id),
                    change="supply what the builder needs",
                    reason=f"{need.title}: still missing: {need.why}",
                    severity="blocker",
                    code="BN",
                )
            )
        return dedupe(out)
    ctx = make_context(graph, **ctx_kwargs)
    found = run_all(ctx, gate=3, only=set(only) if only is not None else None)
    return dedupe(defect_from_finding(f) for f in found if f.severity != "info")


# ------------------------------------------------------------------------------------- structure, in code


def _norm_name(name: str) -> str:
    n = " ".join(name.casefold().split())
    return n[:-1] if n.endswith("s") and len(n) > 3 else n


def _entity_apps(graph: SpecGraph, e: Entity) -> list[str]:
    mod = graph.modules.get(e.owner_module or "")
    return sorted(mod.app_ids) if mod else []


def cross_app_defects(graph: SpecGraph) -> list[Defect]:
    """BG-10: entities that share a name but disagree on their statuses. The canonical one is the lowest id; the
    patch goes to ITS owning module (the module that owns the entity writes the lifecycle once), class B."""
    groups: dict[str, list[Entity]] = defaultdict(list)
    for eid in sorted(graph.entities):
        e = graph.entities[eid]
        if e.status == "in" and not e.later:
            groups[_norm_name(e.name)].append(e)
    out: list[Defect] = []
    for ents in groups.values():
        if len(ents) < 2:
            continue
        canon = ents[0]
        for other in ents[1:]:
            a, b = set(canon.lifecycle.states), set(other.lifecycle.states)
            if a == b:
                continue
            owner = canon.owner_module or canon.id
            apps = sorted(set(_entity_apps(graph, canon)) | set(_entity_apps(graph, other)))
            where = f" (apps {', '.join(apps)})" if apps else ""
            out.append(
                Defect(
                    id=f"S-XAPP:{other.id}:{canon.id}",
                    cls="B",
                    node=owner,
                    field=f"entities.{other.id}.lifecycle.states",
                    change=(
                        f"one lifecycle for {canon.name!r}: align {other.id} with {canon.id} "
                        f"({' > '.join(canon.lifecycle.states)}) or merge them"
                    ),
                    reason=(
                        f"{canon.id} and {other.id} are both {canon.name!r} but disagree on statuses: "
                        f"only in {canon.id}: {sorted(a - b)}; only in {other.id}: {sorted(b - a)}{where}"
                    ),
                    severity="blocker",
                    code="S-XAPP",
                )
            )
            # the duplicate entity is where the disagreement lives; V-11 on it is folded into this finding
            out.append(
                Defect(
                    id=f"S-XAPP-NODE:{other.id}",
                    cls="B",
                    node=other.id,
                    field="lifecycle",
                    change=f"take the statuses of {canon.id}",
                    reason=f"same name as {canon.id} with different statuses",
                    severity="blocker",
                    code="S-XAPP",
                )
            )
    return out


def role_entity_defects(graph: SpecGraph) -> list[Defect]:
    """A role that reaches a screen showing an entity the entity's own access table DENIES it (an explicit `none`
    row). A role that is merely absent from the table is V-08's business (default deny), not flagged here: the
    unmodified synthetic and real graphs list only the main roles, and a check that fires on every clean pack is noise.
    Class B: the screen or the table is wrong; a lane call."""
    out: list[Defect] = []
    for sid in sorted(graph.screens):
        s = graph.screens[sid]
        if s.status != "in":
            continue
        bound = {k for k in bound_fields(s) if k != "<ops>"}
        for eid in sorted(bound):
            e = graph.entities.get(eid)
            if e is None or not e.access:
                continue
            table = {row.role_id: set(row.actions) for row in e.access}
            for rid in sorted(s.role_ids):
                actions = table.get(rid)
                if actions is not None and actions <= {"none"}:
                    out.append(
                        Defect(
                            id=f"S-ROLE-ENT:{sid}:{rid}:{eid}",
                            cls="B",
                            node=sid,
                            field="role_ids",
                            change=f"remove {rid} from {sid} or give {rid} access to {eid}",
                            reason=f"{rid} sees {eid} on {sid} but {eid}'s access table denies it",
                            severity="blocker",
                            code="S-ROLE-ENT",
                        )
                    )
    return out


def locked_fact_defects(graph: SpecGraph, locked: Iterable[LockedFact]) -> list[Defect]:
    """A Locked fact of the Brief that the graph dropped (node gone: B, the node is re-written from the Brief),
    no longer holds as Locked (C: a decision, never repaired into a Locked fact) or changed its value (A: the Brief's
    own value is put back, deterministic)."""
    out: list[Defect] = []
    for fact in locked:
        node = graph.find(fact.node)
        if node is None:
            out.append(
                Defect(
                    id=f"S-LOCKED:{fact.id}:dropped",
                    cls="B",
                    node=fact.node,
                    field=fact.field,
                    change=f"write {fact.node} again from the Brief fact {fact.id}",
                    reason=f"the Locked fact {fact.id} has no node {fact.node} any more",
                    severity="blocker",
                    code="S-LOCKED",
                )
            )
            continue
        if node.tier != "locked":
            out.append(
                Defect(
                    id=f"S-LOCKED:{fact.id}:tier",
                    cls="C",
                    node=fact.node,
                    field="tier",
                    change=f"decide: keep {fact.node} Locked as the Brief says, or tell the person it changed",
                    reason=f"the Brief says {fact.id} is Locked but {fact.node} is {node.tier}",
                    severity="blocker",
                    code="S-LOCKED",
                )
            )
        if fact.field and fact.value:
            current = getattr(node, fact.field, None)
            if current is not None and str(current).strip() != fact.value.strip():
                out.append(
                    Defect(
                        id=f"S-LOCKED:{fact.id}:value",
                        cls="A",
                        node=fact.node,
                        field=fact.field,
                        change=f"set {fact.field} back to {fact.value!r}",
                        reason=f"the Locked fact {fact.id} says {fact.value!r} but {fact.node}.{fact.field} is {str(current)!r}",
                        severity="blocker",
                        code="S-LOCKED",
                    )
                )
    return out


def structure_code_defects(
    graph: SpecGraph, locked: Iterable[LockedFact] = (), **ctx_kwargs: Any
) -> list[Defect]:
    """The code half of the structure critic: structure validators, plus the S-checks no validator owns."""
    validators = code_findings(graph, only=STRUCTURE_CODES, **ctx_kwargs)
    own = [
        *cross_app_defects(graph),
        *role_entity_defects(graph),
        *locked_fact_defects(graph, locked),
    ]
    return dedupe([*own, *validators], prefer=("S-XAPP",))


# ------------------------------------------------------------------------------------- the confirm list


def confirm_rank_defects(confirm: Sequence[Mapping[str, Any]]) -> list[Defect]:
    """The confirm list is ranked by code: money, legal, identity first, the rest after. An item of a first-class
    category ranked below an `other` item is mis-ranked. Class A: a deterministic re-sort."""
    order = {"money": 0, "legal": 1, "identity": 2}
    out: list[Defect] = []
    first_other: int | None = None
    for item in confirm:
        rank = int(item.get("rank", 0))
        cat = str(item.get("category", "other"))
        if cat not in order:
            first_other = rank if first_other is None else min(first_other, rank)
            continue
        if first_other is not None and rank > first_other:
            out.append(
                Defect(
                    id=f"S-CONFIRM:{item.get('id', '')}:{rank}",
                    cls="A",
                    node=str(item.get("id", "")),
                    field="confirm_rank",
                    change=f"move above rank {first_other}",
                    reason=f"a {cat} item is ranked {rank}, below an ordinary item at rank {first_other}",
                    severity="warning",
                    source="code",
                    code="S-CONFIRM",
                )
            )
    return out


# ------------------------------------------------------------------------------------- patches


def patch_for(d: Defect) -> Patch:
    return Patch(node=d.node, field=d.field, change=d.change, reason=d.reason, cls=d.cls)


# ------------------------------------------------------------------------------------- the canary


def card_read_paths(task_card: Mapping[str, Any]) -> list[str]:
    """The files a task card lists, in the card's order: prefix, global shards, module, screens."""
    reads = task_card.get("reads", {}) or {}
    paths: list[str] = []
    if reads.get("prefix"):
        paths.append(str(reads["prefix"]))
    paths += [str(p) for p in reads.get("global", []) or []]
    if reads.get("module"):
        paths.append(str(reads["module"]))
    paths += [str(p) for p in reads.get("screens", []) or []]
    return list(dict.fromkeys(paths))


def card_screen_ids(task_card: Mapping[str, Any]) -> list[str]:
    return [
        str(o).rsplit("/", 1)[-1] for o in task_card.get("owns", []) or [] if "/screens/" in str(o)
    ]


def canary_defects(result: Any, task_card: Mapping[str, Any]) -> list[Defect]:
    """What a canary's blocking questions and unsatisfiable criteria mean for the pack: a class C finding on the card's
    first screen (a person or a lane call decides what the missing fact is). Spec routes them like any other finding."""
    screens = card_screen_ids(task_card)
    node = screens[0] if screens else str(task_card.get("id", ""))
    out = [
        Defect(
            id=f"S-CANARY:{result.task_id}:{slug(q)}",
            cls="C",
            node=node,
            field="",
            change="state the missing fact in the card's reads",
            reason=q,
            severity="warning",
            code="S-CANARY",
        )
        for q in result.blocking_questions
    ]
    return out


def canary_rate(results: Iterable[Any], screens: int, max_per_screen: float) -> tuple[float, bool]:
    """Blocking questions per screen over all canaries and whether it is within the configured maximum (0.3)."""
    total = sum(len(r.blocking_questions) for r in results)
    rate = total / screens if screens else 0.0
    return rate, rate <= max_per_screen
