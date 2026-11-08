"""Validator 5, ``role_gates``: every role gate refers to a defined role."""

from __future__ import annotations

from mde_contracts.calls import ValidatorFinding
from mde_contracts.spec import AppSpec
from mde_spec_builder.validators._common import finding, id_list
from mde_spec_loader.graph import build_ref_graph

NAME = "role_gates"
_LABEL = {"screen": "Screen", "operation": "Operation", "nav": "Navigation node"}


def role_gates(spec: AppSpec) -> list[ValidatorFinding]:
    """Validator 5: ``auth`` values, operation ``roles`` and permissions name defined roles
    (and real screens and operations)."""
    out: list[ValidatorFinding] = []
    graph = build_ref_graph(spec)
    roles = {r.id for r in spec.roles.roles}

    for edge in graph.edges_of("gate"):
        if edge.dst not in roles:
            label = _LABEL.get(graph.kind(edge.src) or "", "Item")
            out.append(
                finding(
                    NAME,
                    f"{label} {edge.src} is gated to {edge.dst}, which is not a defined role",
                    edge.src,
                    edge.dst,
                )
            )
    for edge in graph.edges_of("auth_invalid"):
        out.append(
            finding(
                NAME,
                f"{edge.src} has the auth value {edge.dst!r}; use public, authenticated or a ROLE id",
                edge.src,
            )
        )

    undefined: set[str] = set()
    for edge in graph.edges_of("permits"):
        if edge.src not in roles and edge.src not in undefined:
            undefined.add(edge.src)
            out.append(
                finding(
                    NAME,
                    f"Permissions are given to {edge.src}, which is not a defined role",
                    edge.src,
                )
            )
        if graph.is_unresolved(edge):
            out.append(
                finding(
                    NAME,
                    f"Role {edge.src} is permitted {edge.dst}, which does not exist",
                    edge.src,
                    edge.dst,
                )
            )
    for permission in spec.roles.permissions:
        if permission.role not in roles and permission.role not in undefined:
            undefined.add(permission.role)
            out.append(
                finding(
                    NAME,
                    f"Permissions are given to {permission.role}, which is not a defined role",
                    permission.role,
                )
            )

    role_surfaces = {r.surface for r in spec.roles.roles}
    orphaned = [s.id for s in spec.screens if s.surface not in role_surfaces]
    if orphaned:
        surfaces = sorted({s.surface for s in spec.screens if s.id in orphaned})
        out.append(
            finding(
                NAME,
                f"Screens {id_list(orphaned)} are on surface(s) {surfaces}, but no role belongs to them",
                *orphaned,
                severity="warn",
            )
        )
    return out
