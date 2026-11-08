"""B5 assemble: deterministic id assignment, the reference graph and id helpers.

The reference graph itself lives in ``mde_spec_loader.graph`` (the loader needs it too and the
loader sits below the builder in the layering); this module re-exports it with spec-level
helpers so the validators, the Plan view and the builder share one implementation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import cast

from mde_contracts.spec import AppSpec, NavNode
from mde_spec_loader.diff import diff_specs
from mde_spec_loader.graph import RefGraph, Refs, build_ref_graph

MAX_ID_NUMBER = 999
_SCREEN_ID = re.compile(r"\bSCR-\d{3}\b")


def screen_id(number: int) -> str:
    """``SCR-###``, zero padded (1..999)."""
    return _numbered("SCR", number)


def flow_id(number: int) -> str:
    return _numbered("FLW", number)


def nav_id(number: int) -> str:
    return _numbered("NAV", number)


def _numbered(prefix: str, number: int) -> str:
    if not 1 <= number <= MAX_ID_NUMBER:
        raise ValueError(f"{prefix} number out of range 1..{MAX_ID_NUMBER}: {number}")
    return f"{prefix}-{number:03d}"


# --------------------------------------------------------------------------- navigation order


def navigation_screen_order(root: NavNode) -> list[str]:
    """Screens (``NavNode.screen`` values) in navigation order: depth-first, parents first."""
    ordered: list[str] = []

    def visit(node: NavNode) -> None:
        if node.screen is not None and node.screen not in ordered:
            ordered.append(node.screen)
        for child in node.children:
            visit(child)

    visit(root)
    return ordered


def assign_screen_ids(order: Iterable[str], extra: Iterable[str] = ()) -> dict[str, str]:
    """Map screen keys to ``SCR-###`` in navigation order (``order``), then ``extra`` keys.

    Duplicates keep their first position. Deterministic: the same keys give the same ids.
    """
    mapping: dict[str, str] = {}
    for key in [*order, *extra]:
        if key not in mapping:
            mapping[key] = screen_id(len(mapping) + 1)
    return mapping


def assign_flow_ids(keys: Iterable[str]) -> dict[str, str]:
    """Map flow keys to ``FLW-###`` in the given order."""
    mapping: dict[str, str] = {}
    for key in keys:
        if key not in mapping:
            mapping[key] = flow_id(len(mapping) + 1)
    return mapping


def number_navigation(root: NavNode, *, start: int = 1) -> NavNode:
    """Re-number every navigation node ``NAV-###`` depth-first (pre-order)."""
    counter = start

    def rebuild(node: NavNode) -> NavNode:
        nonlocal counter
        node_id = nav_id(counter)
        counter += 1
        children = [rebuild(child) for child in node.children]
        return node.model_copy(update={"id": node_id, "children": children})

    return rebuild(root)


def rewrite_screen_ids(value: object, mapping: Mapping[str, str]) -> object:
    """Replace screen ids in every string of a plain data tree (one pass, so swaps are safe)."""
    if isinstance(value, str):
        return _SCREEN_ID.sub(lambda m: mapping.get(m.group(0), m.group(0)), value)
    if isinstance(value, dict):
        items = cast("dict[str, object]", value).items()
        return {k: rewrite_screen_ids(v, mapping) for k, v in items}
    if isinstance(value, list):
        return [rewrite_screen_ids(v, mapping) for v in cast("list[object]", value)]
    return value


def renumber_screens(spec: AppSpec) -> AppSpec:
    """Re-assign ``SCR-###`` in navigation order (screens outside the tree follow in list order)
    and rewrite every reference: navigation, flows, actions, permissions, build sequence,
    research additions, backlog hints and ids mentioned in text. Screens end up sorted by id."""
    in_nav = navigation_screen_order(spec.navigation.root)
    known = {s.id for s in spec.screens}
    order = [s for s in in_nav if s in known]
    mapping = assign_screen_ids(order, (s.id for s in spec.screens))
    data = cast("dict[str, object]", rewrite_screen_ids(spec.model_dump(mode="python"), mapping))
    rewritten = AppSpec.model_validate(data)
    return canonicalize(rewritten)


def canonicalize(spec: AppSpec) -> AppSpec:
    """Screens and flows sorted by id (ids are assigned in navigation order, so this is the
    stable order in the file). Everything else keeps its list order, which is meaningful."""
    return spec.model_copy(
        update={
            "screens": sorted(spec.screens, key=lambda s: s.id),
            "flows": sorted(spec.flows, key=lambda f: f.id),
        }
    )


# --------------------------------------------------------------------------- reference graph


def reference_graph(spec: AppSpec) -> RefGraph:
    """The reference graph: navigation -> screen, screen -> operation -> entity, actions ->
    screens, flows -> screens, integrations, roles. Unresolved targets are in ``.unresolved``."""
    return build_ref_graph(spec)


def neighbors(spec: AppSpec | RefGraph, spec_id: str) -> list[str]:
    """Screens linked to ``spec_id`` by navigation, actions or flow order."""
    graph = spec if isinstance(spec, RefGraph) else build_ref_graph(spec)
    return graph.neighbors(spec_id)


def refs(spec: AppSpec | RefGraph, spec_id: str, *, deep: bool = True) -> Refs:
    """Entities, operations, integrations, roles ... that ``spec_id`` references."""
    graph = spec if isinstance(spec, RefGraph) else build_ref_graph(spec)
    return graph.refs(spec_id, deep=deep)


def spec_ids(spec: AppSpec) -> list[str]:
    """Every addressable id of the spec (screens, flows, entities, operations, roles,
    integrations), in file order; used for the diff since Gate 1."""
    return [
        *(s.id for s in spec.screens),
        *(f.id for f in spec.flows),
        *(e.id for e in spec.data.entities),
        *(o.id for o in spec.api.operations),
        *(r.id for r in spec.roles.roles),
        *(i.id for i in spec.integrations.items),
    ]


def changed_ids(old: AppSpec, new: AppSpec) -> list[str]:
    """Ids that differ between two versions (for ``RevisionBlock.changed_ids``)."""
    return diff_specs(old, new).all_ids
