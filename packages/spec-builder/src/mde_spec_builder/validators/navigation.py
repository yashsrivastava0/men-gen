"""Validator 2, ``navigation_targets``: every target exists and every screen is reachable."""

from __future__ import annotations

from collections import defaultdict

from mde_contracts.calls import ValidatorFinding
from mde_contracts.spec import AppSpec
from mde_spec_builder.validators._common import finding
from mde_spec_loader.fragments import walk_nav
from mde_spec_loader.graph import build_ref_graph

NAME = "navigation_targets"


def navigation_targets(spec: AppSpec) -> list[ValidatorFinding]:
    """Validator 2: navigation points at real screens; no screen is orphaned; routes are unique."""
    out: list[ValidatorFinding] = []
    graph = build_ref_graph(spec)

    for edge in graph.edges_of("nav_shows"):
        if graph.is_unresolved(edge):
            out.append(
                finding(
                    NAME,
                    f"Navigation node {edge.src} points at screen {edge.dst}, which does not exist",
                    edge.src,
                    edge.dst,
                )
            )
    for node in walk_nav(spec.navigation.root):
        if node.kind in ("screen", "modal") and node.screen is None:
            out.append(
                finding(
                    NAME,
                    f"Navigation node {node.id} ({node.route}) is a {node.kind} entry "
                    "but names no screen",
                    node.id,
                )
            )
        elif (
            node.kind in ("tabs", "stack", "auth_gate")
            and not node.children
            and node.screen is None
        ):
            out.append(
                finding(
                    NAME,
                    f"Navigation node {node.id} ({node.kind}) has nothing inside it",
                    node.id,
                    severity="warn",
                )
            )

    by_route: dict[str, list[str]] = defaultdict(list)
    for screen in spec.screens:
        by_route[screen.route].append(screen.id)
    for route, ids in by_route.items():
        if len(ids) > 1:
            out.append(finding(NAME, f"Screens {', '.join(ids)} share the route {route}", *ids))

    reachable = set(graph.reachable_screens())
    for screen in spec.screens:
        if screen.id not in reachable:
            out.append(
                finding(
                    NAME,
                    f"Screen {screen.id} ({screen.route}) cannot be reached: no navigation entry "
                    "and no action of a reachable screen goes to it",
                    screen.id,
                )
            )
    return out
