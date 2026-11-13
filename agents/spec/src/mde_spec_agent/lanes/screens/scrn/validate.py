"""Local checks of a SCRN output (code says no; the runner re-asks once per node, then falls back and flags).

`validate_shape` needs only the call's input and output. The graph-aware checks (every problem `assemble` finds, then
Gate 1: the validators of the touched screens on a COPY of the graph) are built per run by `graph_validators`, because
they read the frozen graph, which a static call spec cannot hold."""

from __future__ import annotations

from mde_spec_agent.calls.base import Validator
from mde_spec_agent.lanes.screens.common import Gate1, LaneRules
from mde_spec_agent.lanes.screens.scrn.build import assemble, screens_patch
from mde_spec_agent.lanes.screens.scrn.schema import ScrnIn, ScrnOut
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph


def validate_shape(inp: ScrnIn, out: ScrnOut) -> list[str]:
    errors: list[str] = []
    ids = [s.id for s in inp.slots]
    got = [c.screen for c in out.cards]
    for sid in ids:
        if got.count(sid) != 1:
            errors.append(f"{sid}: exactly one card is needed, got {got.count(sid)}")
    for sid in ids:
        if not any(r.screen == sid for r in out.regions):
            errors.append(f"{sid}: a card needs at least one region")
    return errors


def graph_validators(
    graph: SpecGraph, cfg: SpecConfig, rules: LaneRules, gate1: Gate1
) -> tuple[Validator, ...]:
    def assembled(inp: ScrnIn, out: ScrnOut) -> list[str]:
        return assemble(graph, inp, out, cfg, rules).errors

    def gate_one(inp: ScrnIn, out: ScrnOut) -> list[str]:
        built = assemble(graph, inp, out, cfg, rules)
        if built.errors:
            return []  # already reported word for word
        return gate1.errors(screens_patch(built, graph.meta.source_language), built.screens)

    return (assembled, gate_one)
