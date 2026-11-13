"""Graph patches: what a batch produces. A patch is plain JSON (table -> id -> node dict), so it is checkpointed and a
resumed run applies the SAME patches in the SAME order as an uninterrupted one: identical bytes by construction.
"""

from __future__ import annotations

from typing import Any

from mde_spec_core.graph import NODE_TYPES, HeroRecord, SpecGraph

Patch = dict[str, Any]
# Patch shape: {"tables": {"screens": {"SCR-001": {...node...}}}, "locale_copy": {"en": {"key": "text"}},
#               "glossary": {...}, "hero": {id: {...}}, "tokens": {dir_id: {...}}, "meta": {...}}


def apply_patch(graph: SpecGraph, patch: Patch) -> list[str]:
    """Merge a patch into the graph. Existing ids are REPLACED only when the patch says `replace`; a plain duplicate id is
    an error (a duplicate id is never fixed by code). Returns the touched node ids in a stable order."""
    touched: list[str] = []
    replace = bool(patch.get("replace", False))
    for table, nodes in sorted(patch.get("tables", {}).items()):
        if table not in NODE_TYPES:
            raise ValueError(f"unknown table {table}")
        model = NODE_TYPES[table]
        store: dict[str, Any] = getattr(graph, table)
        for nid in sorted(nodes):
            node = model.model_validate(nodes[nid])
            if nid in store and not replace:
                raise ValueError(f"duplicate id {nid} in {table}")
            store[nid] = node
            touched.append(nid)
    for loc, kv in sorted(patch.get("locale_copy", {}).items()):
        graph.locale_copy.setdefault(loc, {}).update(kv)
    for k, v in patch.get("glossary", {}).items():
        graph.glossary[k] = v
    for hid, h in sorted(patch.get("hero", {}).items()):
        graph.hero[hid] = HeroRecord.model_validate(h)
        touched.append(hid)
    for did, tk in sorted(patch.get("tokens", {}).items()):
        graph.tokens[did] = tk
    for k, v in patch.get("meta", {}).items():
        setattr(graph.meta, k, v)
    return touched


def node_patch(table: str, nodes: list[Any], *, replace: bool = False, **extra: Any) -> Patch:
    """Build a patch from node models."""
    out: Patch = {"tables": {table: {n.id: n.model_dump(mode="json") for n in nodes}}, **extra}
    if replace:
        out["replace"] = True
    return out


def merge_patches(patches: list[Patch]) -> Patch:
    out: Patch = {
        "tables": {},
        "locale_copy": {},
        "glossary": {},
        "hero": {},
        "tokens": {},
        "meta": {},
    }
    for p in patches:
        for t, nodes in p.get("tables", {}).items():
            out["tables"].setdefault(t, {}).update(nodes)
        for loc, kv in p.get("locale_copy", {}).items():
            out["locale_copy"].setdefault(loc, {}).update(kv)
        for k in ("glossary", "hero", "tokens", "meta"):
            out[k].update(p.get(k, {}))
        if p.get("replace"):
            out["replace"] = True
    return out
