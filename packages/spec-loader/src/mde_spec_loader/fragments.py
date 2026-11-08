"""Canonical text of every addressable piece of a spec: blocks and the items inside them.

Blocks (``APP``, ``SCR-014``, ``DATA`` ...) are what the file stores. Items (``ENT-Order``,
``API-getProduct``, ``ROLE-admin``, ``INT-payments``, ``NAV-003``, ``S5``) live inside blocks; the
loader can carve them out so a screen task receives only the entities and operations it needs.
Items are re-dumped with the canonical codec, so the same item always has the same text.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from mde_contracts.spec import AppSpec, NavNode
from mde_spec_loader.codec import BacklogBlock, block_data, dump_yaml, model_data
from mde_spec_loader.parse import normalize_summary

ITEM_KINDS = {
    "ENT-": "entity",
    "API-": "operation",
    "ROLE-": "role",
    "INT-": "integration",
    "NAV-": "nav",
}


def fenced(block_id: str, kind: str, body: str) -> str:
    """A fenced YAML block exactly as the renderer writes it (``body`` is ``dump_yaml`` output,
    which ends with its own newline)."""
    return f"```yaml id={block_id} kind={kind}\n{body}```"


def walk_nav(node: NavNode) -> Iterator[NavNode]:
    yield node
    for child in node.children:
        yield from walk_nav(child)


def item_model(spec: AppSpec, item_id: str) -> tuple[str, BaseModel] | None:
    """(kind, model) of an item id, or None if ``item_id`` is not an item of ``spec``."""
    if item_id.startswith("ENT-"):
        for entity in spec.data.entities:
            if entity.id == item_id:
                return "entity", entity
    elif item_id.startswith("API-"):
        for op in spec.api.operations:
            if op.id == item_id:
                return "operation", op
    elif item_id.startswith("ROLE-"):
        for role in spec.roles.roles:
            if role.id == item_id:
                return "role", role
    elif item_id.startswith("INT-"):
        for integration in spec.integrations.items:
            if integration.id == item_id:
                return "integration", integration
    elif item_id.startswith("NAV-"):
        for node in walk_nav(spec.navigation.root):
            if node.id == item_id:
                return "nav", node.model_copy(update={"children": []})
    else:
        for step in spec.build.steps:
            if step.id == item_id:
                return "build_step", step
    return None


def item_text(spec: AppSpec, item_id: str) -> str | None:
    """The fenced canonical text of an item (``kind=entity`` ...), or None if unknown."""
    found = item_model(spec, item_id)
    if found is None:
        return None
    kind, model = found
    return fenced(item_id, kind, dump_yaml(model_data(model)))


def block_bodies(spec: AppSpec) -> dict[str, tuple[str, str]]:
    """Block id -> (kind, canonical YAML body) for every block except the header."""
    out: dict[str, tuple[str, str]] = {}

    def put(block_id: str, kind: str, model: BaseModel) -> None:
        out[block_id] = (kind, dump_yaml(block_data(kind, model)))

    put("APP", "app", spec.app)
    put("DESIGN", "design", spec.design)
    put("NAV", "navigation", spec.navigation)
    put("DATA", "data", spec.data)
    put("API", "api", spec.api)
    put("ROLES", "roles", spec.roles)
    put("INTEGRATIONS", "integrations", spec.integrations)
    put("BEHAVIOR", "behavior", spec.behavior)
    for screen in spec.screens:
        put(screen.id, "screen", screen)
    for flow in spec.flows:
        put(flow.id, "flow", flow)
    put("BUILD", "build_sequence", spec.build)
    put("ACCEPT", "acceptance", spec.acceptance)
    put("ASSUME", "assumptions", spec.assumptions)
    if spec.backlog:
        put("BACKLOG", "backlog", BacklogBlock(items=spec.backlog))
    put("REV", "revision", spec.revision)
    return out


def all_fragments(spec: AppSpec) -> dict[str, str]:
    """Id -> canonical text for blocks and items; the basis of ``diff`` (meta blocks excluded)."""
    out: dict[str, str] = {"SUMMARY": normalize_summary(spec.summary)}
    for block_id, (_kind, body) in block_bodies(spec).items():
        if block_id != "REV":
            out[block_id] = body
    for entity in spec.data.entities:
        out[entity.id] = dump_yaml(model_data(entity))
    for enum in spec.data.enums:
        out[f"ENUM:{enum.name}"] = dump_yaml(model_data(enum))
    for op in spec.api.operations:
        out[op.id] = dump_yaml(model_data(op))
    for role in spec.roles.roles:
        out[role.id] = dump_yaml(model_data(role))
    for integration in spec.integrations.items:
        out[integration.id] = dump_yaml(model_data(integration))
    for node in walk_nav(spec.navigation.root):
        out[node.id] = dump_yaml(model_data(node.model_copy(update={"children": []})))
    return out
