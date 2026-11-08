"""Validator 3, ``data_bindings``: every binding names a real operation, entity and field."""

from __future__ import annotations

import re

from mde_contracts.calls import ValidatorFinding
from mde_contracts.spec import AppSpec
from mde_spec_builder.validators._common import finding
from mde_spec_loader.graph import build_ref_graph, parse_binding

NAME = "data_bindings"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def data_bindings(spec: AppSpec) -> list[ValidatorFinding]:
    """Validator 3: ``API-x(args)`` and ``ENT-X.field`` bindings (and the entities that the API
    and the data model themselves refer to) all resolve."""
    out: list[ValidatorFinding] = []
    graph = build_ref_graph(spec)
    entities = {e.id: e for e in spec.data.entities}
    operations = {o.id: o for o in spec.api.operations}

    for edge in graph.edges:
        relation = edge.relation
        if relation == "bind_invalid":
            out.append(
                finding(
                    NAME,
                    f"Screen {edge.src} has the data binding {edge.detail!r}, which is neither "
                    "API-name(args) nor ENT-Name.field",
                    edge.src,
                )
            )
        elif relation == "binds_api":
            if edge.dst not in operations:
                out.append(
                    finding(
                        NAME,
                        f"Screen {edge.src} binds data to operation {edge.dst}, which does not exist",
                        edge.src,
                    )
                )
        elif relation == "binds_entity":
            entity = entities.get(edge.dst)
            if entity is None:
                out.append(
                    finding(
                        NAME,
                        f"Screen {edge.src} binds data to entity {edge.dst}, which does not exist",
                        edge.src,
                    )
                )
                continue
            binding = parse_binding(edge.detail)
            if (
                binding is not None
                and binding.field is not None
                and binding.field not in {f.name for f in entity.fields}
            ):
                out.append(
                    finding(
                        NAME,
                        f"Screen {edge.src} binds to {edge.detail}, but {edge.dst} has no field "
                        f"{binding.field!r}",
                        edge.src,
                        edge.dst,
                    )
                )
        elif relation in ("request", "response") and edge.dst not in entities:
            out.append(
                finding(
                    NAME,
                    f"Operation {edge.src} has {relation} entity {edge.dst}, which does not exist",
                    edge.src,
                )
            )
        elif relation == "entity_ref" and edge.dst not in entities:
            out.append(
                finding(
                    NAME,
                    f"Field {edge.detail} of {edge.src} refers to entity {edge.dst}, "
                    "which does not exist",
                    edge.src,
                )
            )

    # fields a screen says it shows from an operation must exist on the operation's response
    for screen in spec.screens:
        for binding_item in screen.data:
            parsed = parse_binding(binding_item.bind)
            if parsed is None or parsed.kind != "api":
                continue
            op = operations.get(parsed.target)
            entity = entities.get(op.response_entity) if op and op.response_entity else None
            if entity is None:
                continue
            fields = {f.name for f in entity.fields}
            unknown = [s for s in binding_item.show if _IDENT.match(s) and s not in fields]
            if unknown:
                out.append(
                    finding(
                        NAME,
                        f"Screen {screen.id} shows {unknown} from {parsed.target}, but "
                        f"{entity.id} has no such field",
                        screen.id,
                        entity.id,
                        severity="warn",
                    )
                )
    return out
