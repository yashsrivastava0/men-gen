"""Validator 1, ``schema``: every block is valid and survives render -> parse.

The block models validate on construction; what can still be wrong is the structure *between*
fields (unique ids, enum and entity references inside the data model, state machines, colour
codes, build-sequence references) and the fidelity of the file itself: each block must read back
as exactly what was written.
"""

from __future__ import annotations

import re

from mde_contracts.calls import ValidatorFinding
from mde_contracts.common import SPEC_SCHEMA_ID
from mde_contracts.spec import AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_builder.validators._common import duplicates, finding
from mde_spec_loader.errors import SpecParseError
from mde_spec_loader.fragments import walk_nav
from mde_spec_loader.graph import build_ref_graph
from mde_spec_loader.parse import normalize_summary, parse_spec

NAME = "schema"
_HEX = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")

_BLOCKS = (
    ("app", "APP"),
    ("design", "DESIGN"),
    ("navigation", "NAV"),
    ("data", "DATA"),
    ("api", "API"),
    ("roles", "ROLES"),
    ("integrations", "INTEGRATIONS"),
    ("behavior", "BEHAVIOR"),
    ("build", "BUILD"),
    ("acceptance", "ACCEPT"),
    ("assumptions", "ASSUME"),
    ("revision", "REV"),
)


def _uniqueness(spec: AppSpec) -> list[ValidatorFinding]:
    groups = (
        ("screen", [s.id for s in spec.screens]),
        ("flow", [f.id for f in spec.flows]),
        ("entity", [e.id for e in spec.data.entities]),
        ("operation", [o.id for o in spec.api.operations]),
        ("role", [r.id for r in spec.roles.roles]),
        ("integration", [i.id for i in spec.integrations.items]),
        ("navigation node", [n.id for n in walk_nav(spec.navigation.root)]),
        ("enum", [e.name for e in spec.data.enums]),
        ("research addition", [a.id for a in spec.assumptions.research_additions]),
    )
    out: list[ValidatorFinding] = []
    for label, ids in groups:
        for dup in duplicates(ids):
            out.append(finding(NAME, f"Duplicate {label} id {dup}: every id must be unique", dup))
    return out


def _header(spec: AppSpec) -> list[ValidatorFinding]:
    out: list[ValidatorFinding] = []
    if spec.header.schema_id != SPEC_SCHEMA_ID:
        out.append(
            finding(
                NAME,
                f"Unknown spec schema {spec.header.schema_id!r}; expected {SPEC_SCHEMA_ID!r}",
                "HEADER",
            )
        )
    if spec.revision.version != spec.header.spec_version:
        out.append(
            finding(
                NAME,
                f"The revision block says version {spec.revision.version} but the header says "
                f"{spec.header.spec_version}",
                "REV",
                "HEADER",
            )
        )
    return out


def _data_model(spec: AppSpec) -> list[ValidatorFinding]:
    out: list[ValidatorFinding] = []
    enums = {e.name: e for e in spec.data.enums}
    for enum in spec.data.enums:
        if not enum.values:
            out.append(finding(NAME, f"Enum {enum.name} has no values", "DATA"))
        for dup in duplicates(enum.values):
            out.append(
                finding(
                    NAME, f"Enum {enum.name} lists the value {dup!r} twice", "DATA", severity="warn"
                )
            )
    for entity in spec.data.entities:
        names = {f.name for f in entity.fields}
        for dup in duplicates(f.name for f in entity.fields):
            out.append(finding(NAME, f"Entity {entity.id} has two fields named {dup!r}", entity.id))
        for field in entity.fields:
            if field.type == "ref" and field.ref is None:
                out.append(
                    finding(
                        NAME,
                        f"Field {field.name} of {entity.id} is a reference but names no entity",
                        entity.id,
                    )
                )
            if field.type == "enum" and field.enum is None:
                out.append(
                    finding(
                        NAME,
                        f"Field {field.name} of {entity.id} is an enum but names no enum",
                        entity.id,
                    )
                )
            if field.enum is not None and field.enum not in enums:
                out.append(
                    finding(
                        NAME,
                        f"Field {field.name} of {entity.id} uses the enum {field.enum}, "
                        "which is not defined",
                        entity.id,
                    )
                )
            if field.type != "ref" and field.ref is not None:
                out.append(
                    finding(
                        NAME,
                        f"Field {field.name} of {entity.id} names an entity but is not a reference",
                        entity.id,
                        severity="warn",
                    )
                )
        machine = entity.state_machine
        if machine is None:
            continue
        if machine.field not in names:
            out.append(
                finding(
                    NAME,
                    f"The state machine of {entity.id} tracks field {machine.field!r}, "
                    "which the entity does not have",
                    entity.id,
                )
            )
        if not machine.states:
            out.append(finding(NAME, f"The state machine of {entity.id} has no states", entity.id))
        for transition in machine.transitions:
            for state in (transition.source, transition.target):
                if state not in machine.states:
                    out.append(
                        finding(
                            NAME,
                            f"A transition of {entity.id} uses the state {state!r}, which is not "
                            "one of its states",
                            entity.id,
                        )
                    )
        tracked = next((f for f in entity.fields if f.name == machine.field), None)
        if tracked is not None and tracked.enum in enums:
            unknown = [s for s in machine.states if s not in enums[tracked.enum].values]
            if unknown:
                out.append(
                    finding(
                        NAME,
                        f"States {unknown} of {entity.id} are not values of enum {tracked.enum}",
                        entity.id,
                        severity="warn",
                    )
                )
    return out


def _design(spec: AppSpec) -> list[ValidatorFinding]:
    out: list[ValidatorFinding] = []
    tokens = spec.design.tokens
    for label, colors in (("light", tokens.colors_light), ("dark", tokens.colors_dark)):
        for name, value in colors.items():
            if not _HEX.match(value):
                out.append(
                    finding(
                        NAME,
                        f"Colour token {name} ({label}) is not a hex colour: {value!r}",
                        "DESIGN",
                    )
                )
    missing = sorted(set(tokens.colors_light) ^ set(tokens.colors_dark))
    if missing:
        out.append(
            finding(
                NAME,
                f"Colour tokens {missing} exist in only one of light and dark",
                "DESIGN",
                severity="warn",
            )
        )
    return out


def _references(spec: AppSpec) -> list[ValidatorFinding]:
    out: list[ValidatorFinding] = []
    graph = build_ref_graph(spec)
    for edge in graph.edges_of("research"):
        if graph.is_unresolved(edge):
            out.append(
                finding(
                    NAME,
                    f"{edge.src} refers to research addition {edge.dst}, which does not exist",
                    edge.src,
                )
            )
    for edge in graph.edges_of("affects"):
        if graph.is_unresolved(edge):
            out.append(
                finding(
                    NAME,
                    f"Research addition {edge.src} says it affects {edge.dst}, which does not exist",
                    edge.src,
                    severity="warn",
                )
            )
    for edge in graph.edges_of("step_screen", "step_flow"):
        if graph.is_unresolved(edge):
            out.append(
                finding(
                    NAME,
                    f"Build step {edge.src} refers to {edge.dst}, which does not exist",
                    edge.src,
                )
            )
    return out


def _drift(spec: AppSpec, parsed: AppSpec) -> list[str]:
    """Ids of the blocks whose parsed form differs from what was rendered."""
    blank = {"hash": ""}
    drifted: list[str] = []
    if spec.header.model_copy(update=blank) != parsed.header.model_copy(update=blank):
        drifted.append("HEADER")
    if normalize_summary(spec.summary) != parsed.summary:
        drifted.append("SUMMARY")
    drifted.extend(
        block_id for attr, block_id in _BLOCKS if getattr(spec, attr) != getattr(parsed, attr)
    )
    original = {s.id: s for s in spec.screens}
    for screen in parsed.screens:
        if original.get(screen.id) != screen:
            drifted.append(screen.id)
    original_flows = {f.id: f for f in spec.flows}
    for flow in parsed.flows:
        if original_flows.get(flow.id) != flow:
            drifted.append(flow.id)
    if spec.backlog != parsed.backlog:
        drifted.append("BACKLOG")
    return drifted


def _round_trip(spec: AppSpec, rendered: str | None) -> list[ValidatorFinding]:
    text = rendered if rendered is not None else render_spec(spec)
    try:
        parsed = parse_spec(text)
    except SpecParseError as exc:
        return [finding(NAME, f"The rendered file cannot be read back: {exc.message}")]
    out = [
        finding(
            NAME,
            f"Block {block_id} does not read back as written (it changes in the file)",
            block_id,
        )
        for block_id in _drift(spec, parsed)
    ]
    if len(parsed.screens) != len(spec.screens) or len(parsed.flows) != len(spec.flows):
        out.append(finding(NAME, "Screens or flows are lost between render and parse"))
    return out


def schema(spec: AppSpec, *, rendered: str | None = None) -> list[ValidatorFinding]:
    """Validator 1: unique ids, model integrity, and every block re-validates after render -> parse."""
    return [
        *_uniqueness(spec),
        *_header(spec),
        *_data_model(spec),
        *_design(spec),
        *_references(spec),
        *_round_trip(spec, rendered),
    ]
