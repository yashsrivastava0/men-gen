"""Validator 4, ``action_targets``: every action resolves to a screen, operation or sheet."""

from __future__ import annotations

from mde_contracts.calls import ValidatorFinding
from mde_contracts.spec import AppSpec
from mde_spec_builder.validators._common import finding
from mde_spec_loader.graph import build_ref_graph

NAME = "action_targets"


def action_targets(spec: AppSpec) -> list[ValidatorFinding]:
    """Validator 4: an action calls an operation, goes to a screen / sheet (a navigation node) /
    route, or is ``back`` / ``close``. An action with none of these is a gap, not a button."""
    out: list[ValidatorFinding] = []
    graph = build_ref_graph(spec)

    for screen in spec.screens:
        for action in screen.actions:
            if action.calls is None and action.go is None:
                out.append(
                    finding(
                        NAME,
                        f"Action {action.name!r} on {screen.id} has no destination: it must call "
                        "an operation or go to a screen",
                        screen.id,
                    )
                )

    for edge in graph.edges_of("calls"):
        if graph.kind(edge.dst) != "operation":
            out.append(
                finding(
                    NAME,
                    f"Action {edge.detail!r} on {edge.src} calls {edge.dst}, "
                    "which is not an operation of the API",
                    edge.src,
                )
            )
    for edge in graph.edges_of("go"):
        if graph.kind(edge.dst) not in ("screen", "nav"):
            out.append(
                finding(
                    NAME,
                    f"Action {edge.detail!r} on {edge.src} goes to {edge.dst}, which is not a "
                    "screen, sheet or route",
                    edge.src,
                )
            )
    return out
