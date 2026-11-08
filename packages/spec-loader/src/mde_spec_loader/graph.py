"""The reference graph of an ``AppSpec``.

Nodes are the addressable ids (screens, flows, entities, operations, roles, integrations,
navigation nodes, research additions, enums, build steps). Edges are typed references between them.
An edge whose target is not a node is *unresolved*: the validators turn those into findings and the
consumer simulator fails on them. Everything here is pure and deterministic.

``neighbors`` and ``refs`` are the semantics behind the loader calls of the same name.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from mde_contracts.common import Frozen
from mde_contracts.spec import AppSpec, NavNode, ScreenBlock

NodeKind = Literal[
    "screen",
    "flow",
    "entity",
    "operation",
    "role",
    "integration",
    "nav",
    "research",
    "enum",
    "step",
]

Relation = Literal[
    "nav_shows",  # NAV-### -> SCR
    "nav_child",  # SCR -> SCR: nearest screen-bearing navigation ancestor to descendant
    "go",  # SCR -> SCR | NAV (sheet): an action destination
    "calls",  # SCR -> API: an action calls an operation
    "binds_api",  # SCR -> API
    "binds_entity",  # SCR -> ENT
    "bind_invalid",  # SCR -> raw text of a binding that cannot be parsed (always unresolved)
    "request",  # API -> ENT
    "response",  # API -> ENT
    "entity_ref",  # ENT -> ENT through a ``ref`` field
    "enum_ref",  # ENT -> ENUM:<name>
    "in_flow",  # FLW -> SCR
    "flow_start",  # FLW -> SCR
    "flow_end",  # FLW -> SCR
    "flow_next",  # SCR -> SCR: consecutive screens of a flow
    "screen_flow",  # SCR -> FLW
    "gate",  # SCR | API | NAV -> ROLE: an auth gate
    "auth_invalid",  # any -> raw auth token that is neither public/authenticated nor a role
    "permits",  # ROLE -> SCR | API
    "mentions",  # any -> id found in free text (only recorded when it resolves)
    "research",  # SCR | INT -> RA
    "affects",  # RA -> spec id
    "step_screen",  # S# -> SCR
    "step_flow",  # S# -> FLW
]

_ALWAYS_UNRESOLVED: Final = frozenset({"bind_invalid", "auth_invalid"})
_NOT_REFERENCES: Final = frozenset({"nav_child", "flow_next"})

ID_RE: Final = re.compile(
    r"\b(?:SCR-\d{3}|FLW-\d{3}|ENT-[A-Za-z][A-Za-z0-9]*|API-[A-Za-z][A-Za-z0-9]*"
    r"|ROLE-[a-z][a-z0-9_]*|INT-[a-z][a-z0-9_]*|RA-\d{3})\b"
)
_API_BIND: Final = re.compile(r"^(API-[A-Za-z][A-Za-z0-9]*)(?:\((.*)\))?$", re.DOTALL)
_ENT_BIND: Final = re.compile(
    r"^(ENT-[A-Za-z][A-Za-z0-9]*)(?:\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*))?$"
)
_SCREEN_ID: Final = re.compile(r"^SCR-\d{3}$")
_NAV_ID: Final = re.compile(r"^NAV-[A-Za-z0-9_-]+$")
_AUTH_SPLIT: Final = re.compile(r"[,|\s]+")
_STEP_ID: Final = re.compile(r"^S(?:[0-9]|10)$")
RELATIVE_TARGETS: Final = frozenset({"back", "close"})
PUBLIC_AUTH: Final = frozenset({"public", "authenticated"})


@dataclass(frozen=True, slots=True)
class Binding:
    kind: Literal["api", "entity"]
    target: str  # API-x or ENT-X
    field: str | None  # first segment of ``ENT-X.field.path`` (None for api / bare entity)
    args: tuple[str, ...]  # api arguments as written


def parse_binding(bind: str) -> Binding | None:
    """Parse ``API-getProduct(id)`` / ``ENT-Product.name``; None if it has neither shape."""
    text = bind.strip()
    api = _API_BIND.match(text)
    if api is not None:
        raw_args = api.group(2)
        args = tuple(a.strip() for a in raw_args.split(",") if a.strip()) if raw_args else ()
        return Binding("api", api.group(1), None, args)
    ent = _ENT_BIND.match(text)
    if ent is not None:
        path = ent.group(2)
        return Binding("entity", ent.group(1), path.split(".")[0] if path else None, ())
    return None


def auth_tokens(auth: str) -> list[str]:
    """Split an ``auth`` value (``public``, ``authenticated``, ``ROLE-x``, or a list of them)."""
    return [t for t in _AUTH_SPLIT.split(auth.strip()) if t]


@dataclass(frozen=True, slots=True)
class Edge:
    src: str
    dst: str
    relation: Relation
    detail: str = ""  # action name, field name, binding text, ... (for messages)


class Refs(Frozen):
    """What one id references (sorted, de-duplicated)."""

    entities: list[str] = []
    operations: list[str] = []
    integrations: list[str] = []
    roles: list[str] = []
    screens: list[str] = []
    flows: list[str] = []
    enums: list[str] = []  # enum names
    research: list[str] = []

    def all_ids(self) -> list[str]:
        return [
            *self.entities,
            *self.operations,
            *self.integrations,
            *self.roles,
            *self.screens,
            *self.flows,
            *self.research,
        ]


class RefGraph:
    """Nodes and typed edges between spec ids, with the queries the loader and validators need."""

    def __init__(
        self,
        nodes: dict[str, NodeKind],
        edges: Sequence[Edge],
        screen_order: Sequence[str],
    ) -> None:
        self.nodes: dict[str, NodeKind] = nodes
        self.edges: tuple[Edge, ...] = tuple(edges)
        self.screen_order: tuple[str, ...] = tuple(screen_order)
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}
        for edge in self.edges:
            self._out.setdefault(edge.src, []).append(edge)
            self._in.setdefault(edge.dst, []).append(edge)

    # ------------------------------------------------------------------ basic queries

    def exists(self, node_id: str) -> bool:
        return node_id in self.nodes

    def kind(self, node_id: str) -> NodeKind | None:
        return self.nodes.get(node_id)

    def out_edges(self, node_id: str, *relations: Relation) -> list[Edge]:
        edges = self._out.get(node_id, [])
        return [e for e in edges if not relations or e.relation in relations]

    def in_edges(self, node_id: str, *relations: Relation) -> list[Edge]:
        edges = self._in.get(node_id, [])
        return [e for e in edges if not relations or e.relation in relations]

    def edges_of(self, *relations: Relation) -> list[Edge]:
        return [e for e in self.edges if e.relation in relations]

    def is_unresolved(self, edge: Edge) -> bool:
        return edge.relation in _ALWAYS_UNRESOLVED or edge.dst not in self.nodes

    @property
    def unresolved(self) -> list[Edge]:
        return [e for e in self.edges if self.is_unresolved(e)]

    # ------------------------------------------------------------------ navigation

    def reachable_screens(self) -> list[str]:
        """Screens reachable from the navigation tree, directly or through an action ``go``."""
        seen: set[str] = {
            e.dst for e in self.edges if e.relation == "nav_shows" and e.dst in self.nodes
        }
        queue = deque(sorted(seen))
        while queue:
            current = queue.popleft()
            for edge in self.out_edges(current, "go"):
                target = edge.dst
                if self.nodes.get(target) == "nav":
                    shown = [e.dst for e in self.out_edges(target, "nav_shows")]
                else:
                    shown = [target]
                for nxt in shown:
                    if self.nodes.get(nxt) == "screen" and nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
        return [s for s in self.screen_order if s in seen]

    def neighbors(self, node_id: str) -> list[str]:
        """Screens linked to ``node_id`` by navigation, actions or flow order (both directions).

        For a screen: its screen-to-screen links. For any other id: the screens that touch it.
        """
        found: set[str] = set()
        if self.nodes.get(node_id) == "screen":
            for edge in self.out_edges(node_id, "nav_child", "go", "flow_next"):
                found.update(self._screens_of(edge.dst))
            for edge in self.in_edges(node_id, "nav_child", "go", "flow_next"):
                found.update(self._screens_of(edge.src))
            found.discard(node_id)
        else:
            for edge in [*self.out_edges(node_id), *self.in_edges(node_id)]:
                for other in (edge.src, edge.dst):
                    if other != node_id and self.nodes.get(other) == "screen":
                        found.add(other)
        return sorted(found)

    def _screens_of(self, node_id: str) -> list[str]:
        kind = self.nodes.get(node_id)
        if kind == "screen":
            return [node_id]
        if kind == "nav":
            return [e.dst for e in self.out_edges(node_id, "nav_shows") if e.dst in self.nodes]
        return []

    # ------------------------------------------------------------------ references

    def refs(self, node_id: str, *, deep: bool = True) -> Refs:
        """Entities, operations, integrations, roles, ... that ``node_id`` references.

        ``deep`` also follows operation -> request/response entity and entity -> entity (one hop)
        and collects the enums of every entity reached.
        """
        buckets: dict[NodeKind, set[str]] = {}

        def add(target: str, *, via: Relation | None = None) -> None:
            kind = self.nodes.get(target)
            if kind is None:
                return
            if kind == "screen" and via in ("mentions", None):
                return
            buckets.setdefault(kind, set()).add(target)

        for edge in self.out_edges(node_id):
            if edge.relation in _NOT_REFERENCES or edge.relation == "enum_ref":
                continue
            add(edge.dst, via=edge.relation)
        if self.nodes.get(node_id) == "entity":
            for edge in self.out_edges(node_id, "enum_ref"):
                buckets.setdefault("enum", set()).add(edge.dst)

        if deep:
            for op in sorted(buckets.get("operation", set())):
                for edge in self.out_edges(op, "request", "response"):
                    add(edge.dst)
            for ent in sorted(buckets.get("entity", set())):
                for edge in self.out_edges(ent, "entity_ref"):
                    add(edge.dst)
            for ent in sorted(buckets.get("entity", set())):
                for edge in self.out_edges(ent, "enum_ref"):
                    buckets.setdefault("enum", set()).add(edge.dst)

        def names(kind: NodeKind) -> list[str]:
            return sorted(buckets.get(kind, set()))

        return Refs(
            entities=names("entity"),
            operations=names("operation"),
            integrations=names("integration"),
            roles=names("role"),
            screens=names("screen"),
            flows=names("flow"),
            enums=[e.removeprefix("ENUM:") for e in names("enum")],
            research=names("research"),
        )

    def referenced_by(self, node_id: str) -> list[str]:
        """Ids with an edge pointing at ``node_id`` (nav/flow-order links excluded)."""
        return sorted({e.src for e in self.in_edges(node_id) if e.relation not in _NOT_REFERENCES})


# --------------------------------------------------------------------------- construction


def _walk_nav(node: NavNode) -> Iterable[NavNode]:
    yield node
    for child in node.children:
        yield from _walk_nav(child)


def _screen_texts(spec_screen: ScreenBlock) -> list[str]:
    """Free-text strings of a screen block that may mention ids (``INT-payments`` ...)."""
    texts: list[str] = [spec_screen.purpose, *spec_screen.sections, *spec_screen.checks]
    if spec_screen.notes:
        texts.append(spec_screen.notes)
    texts.extend(spec_screen.texts.values())
    for state in spec_screen.states.values():
        texts.extend(state.values())
    for action in spec_screen.actions:
        if action.on_success:
            texts.append(action.on_success)
    return texts


def build_ref_graph(spec: AppSpec) -> RefGraph:
    nodes: dict[str, NodeKind] = {}
    for screen in spec.screens:
        nodes.setdefault(screen.id, "screen")
    for flow in spec.flows:
        nodes.setdefault(flow.id, "flow")
    for entity in spec.data.entities:
        nodes.setdefault(entity.id, "entity")
    for enum in spec.data.enums:
        nodes.setdefault(f"ENUM:{enum.name}", "enum")
    for op in spec.api.operations:
        nodes.setdefault(op.id, "operation")
    for role in spec.roles.roles:
        nodes.setdefault(role.id, "role")
    for item in spec.integrations.items:
        nodes.setdefault(item.id, "integration")
    for nav in _walk_nav(spec.navigation.root):
        nodes.setdefault(nav.id, "nav")
    for addition in spec.assumptions.research_additions:
        nodes.setdefault(addition.id, "research")
    for step in spec.build.steps:
        nodes.setdefault(step.id, "step")

    routes: dict[str, str] = {}
    for screen in spec.screens:
        routes.setdefault(screen.route, screen.id)

    edges: list[Edge] = []
    seen: set[tuple[str, str, str, str]] = set()

    def link(src: str, dst: str, relation: Relation, detail: str = "") -> None:
        key = (src, dst, relation, detail)
        if key not in seen:
            seen.add(key)
            edges.append(Edge(src, dst, relation, detail))

    def gate(src: str, auth: str) -> None:
        for token in auth_tokens(auth):
            if token in PUBLIC_AUTH:
                continue
            if token.startswith("ROLE-"):
                link(src, token, "gate", "auth")
            else:
                link(src, token, "auth_invalid", auth)

    def mentions(src: str, texts: Iterable[str]) -> None:
        for text in texts:
            for found in ID_RE.findall(text):
                if found != src and found in nodes:
                    link(src, found, "mentions")

    # navigation
    def visit_nav(node: NavNode, parent_screen: str | None) -> None:
        current_screen = parent_screen
        if node.screen is not None:
            link(node.id, node.screen, "nav_shows")
            if parent_screen is not None and parent_screen != node.screen:
                link(parent_screen, node.screen, "nav_child")
            current_screen = node.screen
        gate(node.id, node.auth)
        for child in node.children:
            visit_nav(child, current_screen)

    visit_nav(spec.navigation.root, None)

    # screens
    for screen in spec.screens:
        sid = screen.id
        if screen.flow is not None:
            link(sid, screen.flow, "screen_flow")
        gate(sid, screen.auth)
        for binding in screen.data:
            parsed = parse_binding(binding.bind)
            if parsed is None:
                link(sid, binding.bind, "bind_invalid", binding.bind)
            elif parsed.kind == "api":
                link(sid, parsed.target, "binds_api", binding.bind)
            else:
                link(sid, parsed.target, "binds_entity", binding.bind)
        for action in screen.actions:
            if action.calls is not None:
                link(sid, action.calls, "calls", action.name)
            if action.go is not None:
                target = action.go
                if target in RELATIVE_TARGETS:
                    pass
                elif target.startswith("/"):
                    link(sid, routes.get(target, target), "go", action.name)
                else:
                    link(sid, target, "go", action.name)
            if action.on_success is not None and _SCREEN_ID.match(action.on_success):
                link(sid, action.on_success, "go", f"{action.name}.on_success")
        if screen.research_ref is not None:
            link(sid, screen.research_ref, "research")
        mentions(sid, _screen_texts(screen))

    # flows
    for flow in spec.flows:
        link(flow.id, flow.start, "flow_start")
        link(flow.id, flow.end, "flow_end")
        for screen_id in flow.screens:
            link(flow.id, screen_id, "in_flow")
        for left, right in zip(flow.screens, flow.screens[1:], strict=False):
            if left in nodes and right in nodes:
                link(left, right, "flow_next", flow.id)
        mentions(flow.id, [flow.purpose] if flow.purpose else [])

    # data
    for entity in spec.data.entities:
        for field in entity.fields:
            if field.ref is not None:
                link(entity.id, field.ref, "entity_ref", field.name)
            if field.enum is not None:
                link(entity.id, f"ENUM:{field.enum}", "enum_ref", field.name)

    # api
    for op in spec.api.operations:
        if op.request_entity is not None:
            link(op.id, op.request_entity, "request")
        if op.response_entity is not None:
            link(op.id, op.response_entity, "response")
        for role in op.roles:
            link(op.id, role, "gate", "roles")
        gate(op.id, op.auth)
        mentions(op.id, [op.notes] if op.notes else [])

    # roles and permissions
    for permission in spec.roles.permissions:
        for target in [*permission.screens, *permission.operations]:
            if target != "*":
                link(permission.role, target, "permits")

    # integrations
    for item in spec.integrations.items:
        if item.research_ref is not None:
            link(item.id, item.research_ref, "research")
        mentions(item.id, [item.purpose, *([item.notes] if item.notes else [])])

    # research additions
    for addition in spec.assumptions.research_additions:
        for affected in addition.affects:
            if ID_RE.fullmatch(affected):
                link(addition.id, affected, "affects")

    # build sequence
    for step in spec.build.steps:
        if step.flow is not None:
            link(step.id, step.flow, "step_flow")
        for screen_id in step.screens:
            link(step.id, screen_id, "step_screen")
        for group in step.parallel:
            for screen_id in group.screens:
                link(step.id, screen_id, "step_screen", group.group)

    return RefGraph(nodes, edges, [s.id for s in spec.screens])


def is_step_id(value: str) -> bool:
    return _STEP_ID.match(value) is not None


def is_screen_id(value: str) -> bool:
    return _SCREEN_ID.match(value) is not None


def is_nav_id(value: str) -> bool:
    return _NAV_ID.match(value) is not None
