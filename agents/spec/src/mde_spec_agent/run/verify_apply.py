"""Spec applies Verify's structured patches (Spec is the single writer; Verify never writes).

Only class A patches (deterministic code fixes) with a recognised form are applied, in at most `class_a_max_passes` passes
that must converge. Forms: ``set <field> back to '<value>'`` (a Locked fact restored to the Brief's own value). Parsing is
plain string work: nothing is evaluated. A class B patch is a lane re-write and a class C patch needs a decision: they are
returned as `skipped` with the reason so the caller marks the node degraded and lists it at G2. An unrecognised class A
form is skipped too, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mde_spec_core.graph import SpecGraph

_SET_BACK = re.compile(
    r"^set (?P<field>[A-Za-z_][A-Za-z0-9_]*) back to (?P<q>['\"])(?P<value>.*)(?P=q)$", re.S
)
_CONFIRM_MOVE = re.compile(r"^move above rank \d+$")


@dataclass
class Applied:
    node: str
    field: str
    before: str
    after: str


@dataclass
class ApplyResult:
    applied: list[Applied] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    passes: int = 0
    degraded_nodes: list[str] = field(default_factory=list)

    @property
    def touched(self) -> list[str]:
        return sorted({a.node for a in self.applied})


def _apply_one(graph: SpecGraph, p: Any) -> tuple[Applied | None, str]:
    m = _SET_BACK.match(p.change.strip())
    if m and p.field and m["field"] == p.field:
        node = graph.find(p.node)
        if node is None:
            return None, "node not found"
        if p.field not in type(node).model_fields:
            return None, f"{p.field} is not a field of {p.node}"
        current = getattr(node, p.field, None)
        if current is not None and not isinstance(current, str):
            return None, "only text fields are restored by code"
        before = "" if current is None else str(current)
        setattr(node, p.field, m["value"])
        return Applied(p.node, p.field, before, m["value"]), ""
    if _CONFIRM_MOVE.match(p.change.strip()):
        return None, "the confirm list is re-ranked by code at G2 (no graph change)"
    return None, "unrecognised class A form"


def apply_patches(graph: SpecGraph, patches: list[Any], *, max_passes: int = 3) -> ApplyResult:
    res = ApplyResult()
    pending = [p for p in patches if p.cls == "A"]
    for p in patches:
        if p.cls != "A":
            res.skipped.append(
                {
                    "node": p.node,
                    "cls": p.cls,
                    "reason": "a lane re-write or a decision: the node is degraded and listed at G2",
                }
            )
            if p.node and p.node not in res.degraded_nodes:
                res.degraded_nodes.append(p.node)
    for _ in range(max_passes):
        if not pending:
            break
        res.passes += 1
        still: list[Any] = []
        progressed = False
        for p in pending:
            done, why = _apply_one(graph, p)
            if done:
                res.applied.append(done)
                progressed = True
            else:
                res.skipped.append({"node": p.node, "cls": "A", "reason": why})
        pending = still
        if not progressed:
            break
    return res
