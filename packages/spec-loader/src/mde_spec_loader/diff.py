"""Changed ids between two versions of a spec (``diff(vA, vB)``) and the stale screens they imply."""

from __future__ import annotations

from mde_contracts.spec import AppSpec
from mde_spec_loader.fragments import all_fragments
from mde_spec_loader.graph import RefGraph, build_ref_graph
from mde_spec_loader.models import SpecDiff
from mde_spec_loader.parse import parse_spec

_ITEM_PREFIXES = ("ENT-", "API-", "ROLE-", "INT-", "NAV-", "ENUM:")


def _used_items(graph: RefGraph, screen_id: str, touched: set[str]) -> bool:
    """Does ``screen_id`` reference (or sit under) any id in ``touched`` in ``graph``?"""
    refs = graph.refs(screen_id)
    used = {*refs.all_ids(), *(f"ENUM:{name}" for name in refs.enums)}
    used.update(e.src for e in graph.in_edges(screen_id, "nav_shows"))
    return bool(used & touched)


def diff_specs(a: AppSpec, b: AppSpec) -> SpecDiff:
    """Added, removed and changed ids from version ``a`` to version ``b``.

    Ids are the blocks (``DATA``, ``SCR-014``, ...) and the items inside them (``ENT-Order``,
    ``API-getProduct``, ``NAV-003``, ...). ``HEADER`` and ``REV`` are meta and never listed.
    ``stale_screens`` are the screens of ``b`` whose generated code is out of date: those that
    changed or are new, plus those that use a changed, added or removed item.
    """
    frag_a = all_fragments(a)
    frag_b = all_fragments(b)
    added = sorted(set(frag_b) - set(frag_a))
    removed = sorted(set(frag_a) - set(frag_b))
    changed = sorted(i for i in set(frag_a) & set(frag_b) if frag_a[i] != frag_b[i])

    screens_b = [s.id for s in b.screens]
    screens_a = {s.id for s in a.screens}
    touched = {i for i in (*added, *removed, *changed) if i.startswith(_ITEM_PREFIXES)}
    stale: list[str] = []
    graph_a = build_ref_graph(a) if touched else None
    graph_b = build_ref_graph(b) if touched else None
    for screen_id in screens_b:
        if (
            screen_id in added
            or screen_id in changed
            or (
                graph_a is not None
                and graph_b is not None
                and (
                    _used_items(graph_b, screen_id, touched)
                    or (screen_id in screens_a and _used_items(graph_a, screen_id, touched))
                )
            )
        ):
            stale.append(screen_id)
    return SpecDiff(added=added, removed=removed, changed=changed, stale_screens=stale)


def diff_texts(text_a: str, text_b: str) -> SpecDiff:
    """``diff_specs`` on two rendered files."""
    return diff_specs(parse_spec(text_a), parse_spec(text_b))
