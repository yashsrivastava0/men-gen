"""Contract v0 emitter: the screens' API, derived from the graph by code (doc 15 5.9, doc 18 2.1).

One OpenAPI 3.1 document for the whole program (per-app tags), entity JSON Schemas, a default-deny auth matrix
and an events file. CRUD operations come from the entities and the access matrix (only where the graph has no
operation for that verb yet); custom operations are copied from the graph. Everything is sorted, so the same graph
gives the same bytes. When the single document would pass `contract_resource_cap` tokens, the root document keeps
only the shared parts and every resource goes to its own file.

Zero model cost, no network. `contract_files(graph)` is what `mde_spec_core.pack` merges into the pack.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .canon import normalise_text
from .config import SpecConfig, default_config
from .graph import Entity, FieldDef, Integration, Operation, SpecGraph
from .validators.base import Finding

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# HTTP status per API error code. This is protocol knowledge (what 'not_found' means on the wire), not app content;
# an unknown code is a business-rule failure (422). Codes the client detects itself never reach the server.
_STATUS = {
    "unauthenticated": 401,
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "duplicate": 409,
    "idempotency_conflict": 409,
    "version_conflict": 412,
    "precondition_failed": 412,
    "validation_failed": 422,
    "invalid": 422,
    "payment_declined": 402,
    "payment_required": 402,
    "rate_limited": 429,
    "too_many_requests": 429,
    "unavailable": 503,
    "timeout": 504,
    "internal": 500,
}
_CLIENT_ONLY = {"network", "offline"}
_STANDARD_CODES = (
    "unauthenticated",
    "forbidden",
    "not_found",
    "validation_failed",
    "version_conflict",
    "idempotency_conflict",
    "rate_limited",
    "internal",
)
_STATUS_TEXT = {
    400: "Bad request",
    401: "Not signed in",
    402: "Payment required",
    403: "Not allowed for this role",
    404: "Not found",
    409: "Conflict",
    412: "Version changed since it was read",
    422: "The request cannot be applied",
    429: "Too many requests",
    500: "Server error",
    503: "Service unavailable",
    504: "Timed out",
}
_ALL_ACTIONS = ("view", "create", "edit", "delete", "manage")
_NEEDED = {
    "list": {"view", "create", "edit", "delete", "manage"},
    "get": {"view", "create", "edit", "delete", "manage"},
    "create": {"create", "manage"},
    "update": {"edit", "manage"},
    "delete": {"delete", "manage"},
}
_VERBS_BY_CLASS = {
    "primary": ("list", "get", "create", "update", "delete"),
    "secondary": ("list", "get", "create", "update", "delete"),
    "join": ("list", "create", "delete"),
    "log": ("list", "get", "create"),
}
_FILTER_TYPES = {"enum", "choice", "toggle", "date", "ref", "id", "number", "money", "text"}
_SORT_TYPES = {"text", "number", "money", "date", "enum", "choice"}
_MAX_FILTERS = 8
_MAX_SORTS = 8
_IDEMPOTENT_VERBS = {"create"}


# ---------------------------------------------------------------- result types


@dataclass
class ContractBundle:
    document: dict[str, Any]  # the whole program as one inline OpenAPI 3.1 document
    schemas: dict[str, dict[str, Any]]  # file stem -> JSON Schema (ENT-001, ENT-001.patch)
    auth_matrix: dict[str, Any]
    events: dict[str, Any]
    files: dict[str, str]  # pack path -> text
    operations: list[dict[str, Any]]  # one summary row per operation (derived ones flagged)
    split: bool = False
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- small helpers


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:  # no anchors: every part reads on its own
        return True


def dump_yaml(value: Any) -> str:
    text = yaml.dump(
        value,
        Dumper=_Dumper,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return normalise_text(text)


def dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def dump_json_lines(value: dict[str, Any]) -> str:
    """Machine files that stay small: one line per entry two levels down, sorted keys, LF, final newline."""

    def one(v: Any) -> str:
        return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    parts = []
    for k in sorted(value):
        v = value[k]
        if isinstance(v, dict) and v:
            inner = ",\n".join(f"  {one(ik)}: {one(iv)}" for ik, iv in sorted(v.items()))
            parts.append(f" {one(k)}: {{\n{inner}\n }}")
        elif isinstance(v, list) and len(v) > 1 and all(isinstance(x, dict) for x in v):
            inner = ",\n".join(f"  {one(x)}" for x in v)
            parts.append(f" {one(k)}: [\n{inner}\n ]")
        else:
            parts.append(f" {one(k)}: {one(v)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


def _plural(slug: str) -> str:
    if slug.endswith("s"):
        return slug + "es"
    if slug.endswith("y") and len(slug) > 1 and slug[-2] not in "aeiou":
        return slug[:-1] + "ies"
    return slug + "s"


def _norm_path(path: str) -> str:
    p = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", path.strip())
    return p if p.startswith("/") else "/" + p


def _path_params(path: str) -> list[str]:
    return re.findall(r"{([^}/]+)}", path)


def _pointer(path: str) -> str:
    """JSON-pointer fragment for a path key, URI-escaped."""
    return quote(path.replace("~", "~0").replace("/", "~1"), safe="~")


def _tokens(text: str, factor: float) -> int:
    return math.ceil(len(text) / factor)


def _roles_for(ent: Entity, verb: str) -> list[str]:
    need = _NEEDED[verb]
    out = []
    for row in ent.access:
        acts = set(row.actions) - {"none"}
        if acts & need:
            out.append(row.role_id)
    return sorted(set(out))


# ---------------------------------------------------------------- schemas


def _field_schema(f: FieldDef, ent: Entity) -> dict[str, Any]:
    t = f.type
    s: dict[str, Any]
    if t == "number":
        s = {"type": "number"}
    elif t == "quantity":
        s = {"type": "number", "minimum": 0}
    elif t == "toggle":
        s = {"type": "boolean"}
    elif t == "money":
        s = {
            "type": "object",
            "required": ["amount", "currency"],
            "additionalProperties": False,
            "properties": {
                "amount": {"type": "integer", "description": "minor units, never a float"},
                "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            },
        }
    elif t == "date":
        s = {"type": "string", "format": "date-time", "description": "RFC 3339, UTC"}
    elif t == "phone":
        s = {"type": "string", "pattern": r"^\+[1-9][0-9]{6,14}$", "description": "E.164"}
    elif t == "email":
        s = {"type": "string", "format": "email"}
    elif t == "file":
        s = {"type": "string", "description": "file reference id; bytes go through the media route"}
    elif t == "location":
        s = {
            "type": "object",
            "required": ["lat", "lng"],
            "additionalProperties": False,
            "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lng": {"type": "number", "minimum": -180, "maximum": 180},
            },
        }
    elif t == "secret":
        s = {"type": "string", "writeOnly": True}
    elif t == "list":
        s = {"type": "array", "items": {"type": "string"}}
    elif t in ("enum", "choice"):
        s = {"type": "string"}
        values = list(f.enum_values)
        if not values and f.key in ("status", "state") and ent.lifecycle.states:
            values = list(ent.lifecycle.states)
        if values:
            s["enum"] = values
    elif t == "ref":
        s = {"type": "string"}
        if f.ref_entity:
            s["x-ref"] = f.ref_entity
    else:  # text, code, id, anything the vocabulary adds later: a string is the safe wire form
        s = {"type": "string"}
    if f.constraint:
        s["description"] = (s.get("description", "") + " " + f.constraint).strip()
    if f.derived:
        s["readOnly"] = True
        s["x-derived"] = f.derived
    return s


def entity_schema(ent: Entity, *, fixtures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """JSON Schema (no internal refs, so it reads the same alone and inside the OpenAPI document)."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in sorted(ent.fields, key=lambda x: x.key):
        sch = _field_schema(f, ent)
        if f.key == "id" or f.type == "id":
            sch = {"type": "string", "readOnly": True, "description": "stable opaque id"}
        props[f.key] = sch
        if f.required:
            required.append(f.key)
    if "id" not in props:
        props["id"] = {"type": "string", "readOnly": True, "description": "stable opaque id"}
    if ent.lifecycle.states and "status" not in props:
        props["status"] = {
            "type": "string",
            "enum": list(ent.lifecycle.states),
            "x-derived-from": "lifecycle",
        }
    props.setdefault("created_at", {"type": "string", "format": "date-time", "readOnly": True})
    props.setdefault("updated_at", {"type": "string", "format": "date-time", "readOnly": True})
    if _editable(ent):
        props.setdefault(
            "version",
            {
                "type": "integer",
                "minimum": 1,
                "readOnly": True,
                "description": "optimistic concurrency",
            },
        )
    sch = {
        "title": ent.name,
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": sorted(set(required) | {"id"}),
        "x-entity": ent.id,
        "x-class": ent.klass,
    }
    if ent.lifecycle.states:
        sch["x-lifecycle"] = {
            "states": list(ent.lifecycle.states),
            "initial": ent.lifecycle.initial,
            "transitions": [
                {
                    "from": t.from_state,
                    "to": t.to_state,
                    "roles": sorted(t.roles),
                    "on": t.on,
                }
                for t in sorted(ent.lifecycle.transitions, key=lambda x: (x.from_state, x.to_state))
            ],
        }
    if ent.access:
        sch["x-access"] = {
            r.role_id: sorted(r.actions) for r in sorted(ent.access, key=lambda x: x.role_id)
        }
    if ent.typical_count or ent.max_count:
        sch["x-volumes"] = {"typical": ent.typical_count, "max": ent.max_count}
    if fixtures:
        sch["examples"] = fixtures[:3]
    return sch


def patch_schema(ent: Entity, base: dict[str, Any]) -> dict[str, Any]:
    """The update body: writable properties only, all optional, at least one present."""
    props = {k: v for k, v in base["properties"].items() if not v.get("readOnly")}
    return {
        "title": f"{ent.name} changes",
        "type": "object",
        "additionalProperties": False,
        "minProperties": 1,
        "properties": props,
        "x-entity": ent.id,
    }


def _editable(ent: Entity) -> bool:
    if ent.klass in ("log", "join"):
        return False
    return bool(_roles_for(ent, "update"))


def _fields_schema(fields: list[FieldDef], ent: Entity | None = None) -> dict[str, Any]:
    dummy = ent or Entity(id="ENT-0", name="inline")
    props = {f.key: _field_schema(f, dummy) for f in sorted(fields, key=lambda x: x.key)}
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    req = sorted(f.key for f in fields if f.required)
    if req:
        out["required"] = req
    return out


# ---------------------------------------------------------------- operations (graph + derived)


@dataclass
class _Op:
    id: str
    verb: str  # list get create update delete custom
    method: str
    path: str
    name: str
    entity: str | None
    roles: list[str]
    phase: str
    derived: bool
    idempotent: bool
    paged: bool
    errors: list[tuple[str, str]]  # (code, recovery)
    app_tags: list[str]
    request: list[FieldDef]
    response: list[FieldDef]


def _verb_of(op: Operation) -> str:
    if op.kind == "custom" or not op.entity:
        return "custom"
    if op.method == "GET":
        return "list" if op.paged else "get"
    if op.method in ("PUT", "PATCH"):
        return "update"
    if op.method == "DELETE":
        return "delete"
    return "create"


def _entity_phase(ent: Entity | None) -> str:
    return "later" if ent is not None and (ent.later or ent.status == "later") else "now"


def _resource_slugs(graph: SpecGraph) -> dict[str, str]:
    taken: dict[str, str] = {}
    for eid in sorted(graph.entities):
        slug = _plural(_slug(graph.entities[eid].name))
        if slug in taken:
            slug = f"{slug}-{eid.lower()}"
        taken[slug] = eid
    return {v: k for k, v in taken.items()}


def collect_operations(graph: SpecGraph) -> tuple[list[_Op], list[Finding]]:
    findings: list[Finding] = []
    res = _resource_slugs(graph)
    ops: list[_Op] = []
    seen: dict[tuple[str, str], str] = {}
    covered: dict[str, set[str]] = {}
    for oid in sorted(graph.operations):
        o = graph.operations[oid]
        if o.status == "out":
            continue
        ent = graph.entities.get(o.entity) if o.entity else None
        verb = _verb_of(o)
        path = _norm_path(o.path or f"/{res.get(o.entity or '', 'general')}/{_slug(o.name)}")
        key = (o.method, path)
        if key in seen:
            findings.append(
                Finding(
                    "V-10",
                    "blocker",
                    o.id,
                    f"{o.method} {path} is already taken by {seen[key]}",
                    "give the operation its own path or method",
                    "B",
                )
            )
            continue
        seen[key] = o.id
        if o.entity:
            covered.setdefault(o.entity, set()).add(verb)
        ops.append(
            _Op(
                o.id,
                verb,
                o.method,
                path,
                o.name,
                o.entity,
                sorted(o.roles),
                "later" if o.status == "later" else _entity_phase(ent),
                False,
                o.idempotent,
                o.paged,
                [(e.code, e.recovery) for e in o.errors],
                sorted(o.app_tags),
                list(o.request),
                list(o.response),
            )
        )
    for eid in sorted(graph.entities):
        ent = graph.entities[eid]
        if ent.status == "out":
            continue
        slug = res[eid]
        for verb in _VERBS_BY_CLASS.get(ent.klass, _VERBS_BY_CLASS["secondary"]):
            if verb in covered.get(eid, set()):
                continue
            roles = _roles_for(ent, verb)
            if not roles:
                continue  # default-deny: nobody may do it, so no operation is derived
            method, path = {
                "list": ("GET", f"/{slug}"),
                "create": ("POST", f"/{slug}"),
                "get": ("GET", f"/{slug}/{{id}}"),
                "update": ("PATCH", f"/{slug}/{{id}}"),
                "delete": ("DELETE", f"/{slug}/{{id}}"),
            }[verb]
            if (method, path) in seen:
                continue
            seen[(method, path)] = f"{verb}_{eid}"
            ops.append(
                _Op(
                    f"{verb}_{eid}",
                    verb,
                    method,
                    path,
                    f"{verb} {ent.name}",
                    eid,
                    roles,
                    _entity_phase(ent),
                    True,
                    verb in _IDEMPOTENT_VERBS,
                    verb == "list",
                    [("forbidden", "stay")],
                    [],
                    [],
                    [],
                )
            )
    ops.sort(key=lambda x: (x.path, x.method))
    return ops, findings


def lint_contract(graph: SpecGraph, ops: list[_Op]) -> list[Finding]:
    """What the contract itself can prove: every operation is role-gated and its roles may use the entity."""
    out: list[Finding] = []
    for op in ops:
        if not op.roles:
            out.append(
                Finding(
                    "V-08",
                    "blocker",
                    op.id,
                    "no role may call it, and access defaults to denied",
                    "say which roles may call it",
                    "B",
                )
            )
        if not op.errors and not op.derived:
            out.append(
                Finding(
                    "V-10",
                    "blocker",
                    op.id,
                    "the operation has no error model",
                    "list its error codes with a recovery",
                    "B",
                )
            )
        ent = graph.entities.get(op.entity or "")
        if ent is not None and ent.access and op.verb in _NEEDED:
            allowed = set(_roles_for(ent, op.verb))
            extra = sorted(set(op.roles) - allowed)
            if extra:
                out.append(
                    Finding(
                        "V-30",
                        "warning",
                        op.id,
                        f"roles {', '.join(extra)} are gated in, but the access matrix of {ent.id} "
                        f"does not give them the {op.verb} right",
                        "make the operation roles and the entity access rows agree",
                        "B",
                    )
                )
    return out


# ---------------------------------------------------------------- the document


class _Refs:
    """Reference spelling: inline for one document, relative file refs when the resources are split."""

    def __init__(self, split: bool) -> None:
        self.split = split

    def comp(self, kind: str, name: str) -> dict[str, str]:
        if self.split:
            return {"$ref": f"../components.yaml#/{kind}/{name}"}
        return {"$ref": f"#/components/{kind}/{name}"}

    def schema(self, stem: str) -> dict[str, str]:
        if self.split:
            return {"$ref": f"../schemas/{stem}.json"}
        return {"$ref": f"#/components/schemas/{stem}"}


def _status_for(code: str) -> int | None:
    if code in _CLIENT_ONLY:
        return None
    return _STATUS.get(code, 422)


def _error_codes(ops: list[_Op]) -> list[str]:
    codes: set[str] = set(_STANDARD_CODES)
    for op in ops:
        for c, _ in op.errors:
            if c not in _CLIENT_ONLY:
                codes.add(c)
    return sorted(codes)


def _shared_components(codes: list[str], base: str = "#/components") -> dict[str, Any]:
    err = {
        "type": "object",
        "required": ["code", "message", "request_id"],
        "additionalProperties": False,
        "properties": {
            "code": {"type": "string", "enum": codes},
            "message": {"type": "string", "description": "plain words, safe to show"},
            "request_id": {"type": "string"},
            "retryable": {"type": "boolean"},
            "details": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["field", "issue"],
                    "properties": {"field": {"type": "string"}, "issue": {"type": "string"}},
                },
            },
            "where": {"type": "string"},
            "why": {"type": "string"},
            "fix": {"type": "string"},
        },
        "x-error-shape": "spec.error.v1",
    }
    resp: dict[str, Any] = {}
    for status, name in (
        (401, "Unauthenticated"),
        (403, "Forbidden"),
        (404, "NotFound"),
        (412, "VersionChanged"),
        (422, "Unprocessable"),
        (429, "RateLimited"),
    ):
        resp[name] = {
            "description": _STATUS_TEXT[status],
            "content": {"application/json": {"schema": {"$ref": f"{base}/schemas/Error"}}},
        }
    resp["Default"] = {
        "description": "Any other failure",
        "content": {"application/json": {"schema": {"$ref": f"{base}/schemas/Error"}}},
    }
    return {
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "Error": err,
            "Event": {
                "type": "object",
                "required": ["id", "type", "occurred_at", "data"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "unique per event; consumers dedupe on it",
                    },
                    "type": {"type": "string"},
                    "occurred_at": {"type": "string", "format": "date-time"},
                    "data": {"type": "object"},
                },
            },
        },
        "parameters": {
            "Cursor": {
                "name": "cursor",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": "opaque; take it from next_cursor of the previous page",
            },
            "Limit": {
                "name": "limit",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "IdempotencyKey": {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 8, "maxLength": 128},
                "description": "a client-made key; repeating the call with it returns the first result",
            },
            "IfMatch": {
                "name": "If-Match",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "the ETag from the last read (or the entity version)",
            },
        },
        "responses": resp,
    }


def _params_for(op: _Op, ent: Entity | None, refs: _Refs) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for name in _path_params(op.path):
        params.append({"name": name, "in": "path", "required": True, "schema": {"type": "string"}})
    if op.paged:
        params.append(refs.comp("parameters", "Cursor"))
        params.append(refs.comp("parameters", "Limit"))
        if ent is not None:
            sortable = [
                f.key for f in sorted(ent.fields, key=lambda x: x.key) if f.type in _SORT_TYPES
            ]
            sortable = [*sortable[:_MAX_SORTS], "created_at"]
            vals = sorted({v for k in sortable for v in (k, f"-{k}")})
            params.append(
                {
                    "name": "sort",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string", "enum": vals},
                    "description": "one field; a leading minus sorts descending",
                }
            )
            n = 0
            for f in sorted(ent.fields, key=lambda x: x.key):
                if f.type not in _FILTER_TYPES or f.key == "id" or n >= _MAX_FILTERS:
                    continue
                n += 1
                sch = _field_schema(f, ent)
                sch = {k: v for k, v in sch.items() if k in ("type", "enum", "format")}
                params.append(
                    {"name": f"filter[{f.key}]", "in": "query", "required": False, "schema": sch}
                )
    if op.idempotent and op.method in ("POST", "PATCH", "PUT"):
        params.append(refs.comp("parameters", "IdempotencyKey"))
    if op.verb == "update" and ent is not None and _editable(ent):
        params.append(refs.comp("parameters", "IfMatch"))
    return params


def _error_responses(op: _Op, ent: Entity | None, refs: _Refs) -> tuple[dict[str, Any], list[str]]:
    by_status: dict[int, list[str]] = {}
    client_only: list[str] = []
    recovery: dict[str, str] = {}
    for code, rec in op.errors:
        st = _status_for(code)
        if st is None or rec == "offline":
            client_only.append(code)
            continue
        by_status.setdefault(st, []).append(code)
        recovery[code] = rec or "stay"
    responses: dict[str, Any] = {}
    std: dict[int, str] = {401: "Unauthenticated", 429: "RateLimited"}
    if op.roles or op.derived:
        std[403] = "Forbidden"
    if _path_params(op.path):
        std[404] = "NotFound"
    if op.method in ("POST", "PATCH", "PUT"):
        std[422] = "Unprocessable"
    if op.verb == "update" and ent is not None and _editable(ent):
        std[412] = "VersionChanged"
    for st, name in std.items():
        if st not in by_status:
            responses[str(st)] = refs.comp("responses", name)
    for st, codes in sorted(by_status.items()):
        responses[str(st)] = {
            "description": f"{_STATUS_TEXT.get(st, 'Error')}: {', '.join(sorted(codes))}",
            "content": {"application/json": {"schema": refs.comp("schemas", "Error")}},
            "x-error-codes": sorted(codes),
            "x-recovery": {c: recovery[c] for c in sorted(codes)},
        }
    responses["default"] = refs.comp("responses", "Default")
    return responses, sorted(set(client_only))


def _operation_object(
    op: _Op,
    graph: SpecGraph,
    refs: _Refs,
    app_for_roles: dict[str, set[str]],
    patch_stems: set[str],
) -> dict[str, Any]:
    ent = graph.entities.get(op.entity or "")
    tags = list(op.app_tags)
    if not tags:
        tags = sorted({a for r in op.roles for a in app_for_roles.get(r, set())}) or sorted(
            graph.apps
        )
    errors, client = _error_responses(op, ent, refs)
    body: dict[str, Any] = {}
    if op.method in ("POST", "PATCH", "PUT"):
        sch: dict[str, Any] | None = None
        if op.request:
            sch = _fields_schema(op.request, ent)
        elif op.verb == "create" and ent is not None:
            sch = refs.schema(ent.id)
        elif op.verb == "update" and ent is not None and f"{ent.id}.patch" in patch_stems:
            sch = refs.schema(f"{ent.id}.patch")
        if sch is not None:
            body = {
                "requestBody": {"required": True, "content": {"application/json": {"schema": sch}}}
            }
    ok_code = {"create": "201", "delete": "204"}.get(op.verb, "200")
    ok: dict[str, Any] = {"description": "Success"}
    if ok_code != "204":
        rsch: dict[str, Any] | None
        if op.verb == "list" and ent is not None and not op.response:
            rsch = {
                "type": "object",
                "required": ["items"],
                "properties": {
                    "items": {"type": "array", "items": refs.schema(ent.id)},
                    "next_cursor": {"type": ["string", "null"]},
                },
            }
        elif op.response:
            rsch = _fields_schema(op.response, ent)
        elif op.verb in ("get", "create", "update") and ent is not None:
            rsch = refs.schema(ent.id)
        else:
            rsch = {"type": "object"}
        ok["content"] = {"application/json": {"schema": rsch}}
        if ent is not None and _editable(ent) and op.verb in ("get", "create", "update"):
            ok["headers"] = {"ETag": {"schema": {"type": "string"}}}
    responses = {ok_code: ok, **errors}
    out: dict[str, Any] = {
        "operationId": op.id,
        "summary": op.name,
        "tags": tags,
        "parameters": _params_for(op, ent, refs),
        "responses": responses,
        "security": [{"bearerAuth": []}],
        "x-roles": op.roles,
        "x-phase": op.phase,
        "x-derived": op.derived,
        **body,
    }
    if not out["parameters"]:
        del out["parameters"]
    if op.entity:
        out["x-entity"] = op.entity
    if op.idempotent:
        out["x-idempotent"] = True
    if ent is not None and op.verb == "update" and _editable(ent):
        out["x-concurrency"] = "etag"
    if client:
        out["x-client-errors"] = client
    if not op.roles:
        out["x-deny"] = True
    return out


def _webhooks(graph: SpecGraph, refs: _Refs) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for iid in sorted(graph.integrations):
        i = graph.integrations[iid]
        if not (i.provider_side_flow or i.events) or i.status == "out":
            continue
        out[f"{iid}-callback"] = {
            "post": {
                "operationId": f"callback_{iid}",
                "summary": f"{i.name}: provider callback",
                "description": "Signed by the provider; the receiver dedupes on the event id.",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": refs.comp("schemas", "Event")}},
                },
                "responses": {"200": {"description": "Accepted"}},
                "x-integration": iid,
                "x-phase": "later" if i.status == "later" else "now",
            }
        }
    return out


def _tags(graph: SpecGraph) -> list[dict[str, Any]]:
    return [
        {"name": aid, "description": graph.apps[aid].name, "x-surface": graph.apps[aid].surface}
        for aid in sorted(graph.apps)
    ]


def _info(graph: SpecGraph) -> dict[str, Any]:
    return {
        "title": f"{graph.meta.program_id or 'program'} contract",
        "version": "0.1.0",
        "summary": "Contract v0 derived from the Spec graph; the backend engine builds on it.",
        "x-conventions": {
            "ids": "opaque strings, never reused",
            "timestamps": "RFC 3339, UTC",
            "enums": "lifecycle states are the enum of an entity status",
            "pagination": "cursor: cursor and limit in, next_cursor out",
            "filter_sort": "filter[field]=value and sort=field or -field on list operations",
            "errors": "one shape (spec.error.v1): code, message, request_id, retryable, details",
            "idempotency": "Idempotency-Key header on operations marked x-idempotent",
            "concurrency": "ETag out and If-Match in on editable entities (version field)",
            "auth": "default deny: x-roles lists who may call; see auth-matrix.json",
            "phase": "x-phase later marks operations that exist only for later features",
            "fixtures": "contract/fixtures/<ENT>.json (hook; absent until fixtures are written)",
        },
    }


def build_document(
    graph: SpecGraph,
    ops: list[_Op],
    *,
    split: bool,
    schemas: dict[str, dict[str, Any]],
    patch_stems: set[str],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """(root, resource key -> {path: path item}, shared components file). Inline mode: root holds all paths."""
    refs = _Refs(split)
    app_for_roles: dict[str, set[str]] = {}
    for aid, a in graph.apps.items():
        for r in a.role_ids:
            app_for_roles.setdefault(r, set()).add(aid)
    resources: dict[str, dict[str, Any]] = {}
    for op in ops:
        item = resources.setdefault(op.entity or "general", {}).setdefault(op.path, {})
        item[op.method.lower()] = _operation_object(op, graph, refs, app_for_roles, patch_stems)
    codes = _error_codes(ops)
    shared: dict[str, Any] = {}
    if split:
        shared = _shared_components(codes, "#")
        comps: dict[str, Any] = {"securitySchemes": shared["securitySchemes"]}
    else:
        comps = _shared_components(codes)
    doc: dict[str, Any] = {
        "openapi": "3.1.0",
        "jsonSchemaDialect": SCHEMA_DIALECT,
        "info": _info(graph),
        "servers": [{"url": "/"}],
        "tags": _tags(graph),
        "security": [{"bearerAuth": []}],
        "components": comps,
    }
    hooks = _webhooks(graph, refs)
    if hooks and not split:
        doc["webhooks"] = hooks
    elif hooks:
        resources["webhooks"] = hooks  # its own file; the root points at each callback
    if split:
        doc["paths"] = {}
    else:
        for stem in sorted(schemas):
            comps["schemas"][stem] = schemas[stem]
        paths: dict[str, Any] = {}
        for res in resources.values():
            paths.update(res)
        doc["paths"] = paths
    return doc, resources, shared


# ---------------------------------------------------------------- auth matrix and events


def build_auth_matrix(graph: SpecGraph, ops: list[_Op]) -> dict[str, Any]:
    """Default-deny matrix. Operations point at a role set (many share one), so the file stays small."""
    sets: dict[tuple[str, ...], str] = {}
    for op in sorted(ops, key=lambda x: x.id):
        sets.setdefault(tuple(op.roles), "")
    names = {k: f"RS-{i:02d}" for i, k in enumerate(sorted(sets), start=1)}
    return {
        "schema": "spec.auth-matrix.v1",
        "default": "deny",
        "note": "an operation, role or entity action not listed here is denied; role_sets lists who may call",
        "public_operations": [],
        "roles": {
            rid: {"name": r.name, "apps": sorted(r.app_ids)}
            for rid, r in sorted(graph.roles.items())
        },
        "role_sets": {names[k]: list(k) for k in sorted(names)},
        "operations": {op.id: names[tuple(op.roles)] for op in sorted(ops, key=lambda x: x.id)},
        "later_operations": sorted(op.id for op in ops if op.phase == "later"),
        "entities": {
            eid: {
                row.role_id: sorted(row.actions)
                for row in sorted(e.access, key=lambda x: x.role_id)
            }
            for eid, e in sorted(graph.entities.items())
            if e.access
        },
    }


def roles_allowed(matrix: dict[str, Any], operation_id: str) -> list[str]:
    """Who may call an operation under the matrix (empty = nobody: default deny)."""
    rs = matrix["operations"].get(operation_id)
    return list(matrix["role_sets"].get(rs, [])) if rs else []


def _event_type(i: Integration, name: str) -> str:
    return f"{_slug(i.need_key or i.id)}.{_slug(name)}"


def build_events(graph: SpecGraph) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    callbacks: list[dict[str, Any]] = []
    for iid in sorted(graph.integrations):
        i = graph.integrations[iid]
        if i.status == "out":
            continue
        for ev in sorted(set(i.events)):
            events.append(
                {
                    "type": _event_type(i, ev),
                    "kind": "integration",
                    "source": iid,
                    "delivery": "webhook" if i.provider_side_flow else "internal",
                    "phase": "later" if i.status == "later" else "now",
                    "at_least_once": True,
                }
            )
        if i.provider_side_flow:
            callbacks.append(
                {
                    "integration": iid,
                    "name": i.name,
                    "method": "POST",
                    "path": f"/callbacks/{_slug(i.need_key or iid)}",
                    "signed": True,
                    "dedupe_on": "event id",
                    "interruption": i.interruption,
                    "pending_timeout": i.pending_timeout,
                    "mode": i.mode,
                }
            )
    for eid in sorted(graph.entities):
        e = graph.entities[eid]
        if e.lifecycle.transitions and e.status != "out":
            events.append(
                {
                    "type": f"{_slug(e.name)}.status_changed",
                    "kind": "domain",
                    "source": eid,
                    "delivery": "internal",
                    "phase": _entity_phase(e),
                    "payload": ["id", "from", "to", "version"],
                    "states": list(e.lifecycle.states),
                }
            )
    return {
        "schema": "spec.events.v1",
        "envelope": {
            "required": ["id", "type", "occurred_at", "data"],
            "id": "unique per event; consumers dedupe on it",
            "occurred_at": "RFC 3339, UTC",
        },
        "events": sorted(events, key=lambda x: (x["kind"], x["type"], x["source"])),
        "callbacks": callbacks,
    }


# ---------------------------------------------------------------- validation and the public builder


def validate_openapi(spec: dict[str, Any] | Path) -> str | None:
    """None when valid, else the validator's message (first line). Pure Python, no network."""
    from openapi_spec_validator import validate
    from openapi_spec_validator.readers import read_from_filename

    try:
        if isinstance(spec, Path):
            data, base = read_from_filename(str(spec))
            validate(data, base_uri=base)
        else:
            validate(spec)
    except Exception as exc:
        return (str(exc).strip().splitlines() or [type(exc).__name__])[0][:400]
    return None


def build_contract(
    graph: SpecGraph,
    *,
    config: SpecConfig | None = None,
    chars_per_token: float | None = None,
    fixtures: Mapping[str, list[dict[str, Any]]] | None = None,
    validate: bool = True,
) -> ContractBundle:
    cfg = config or default_config()
    cap = cfg.budgets.contract_resource_cap
    factor = chars_per_token or cfg.budgets.token_factor_chars_per_token
    ops, findings = collect_operations(graph)
    findings += lint_contract(graph, ops)
    hero: dict[str, list[dict[str, Any]]] = {}
    for h in sorted(graph.hero.values(), key=lambda x: x.id):
        if h.filled and h.content:
            hero.setdefault(h.entity, []).append(dict(sorted(h.content.items())))
    schemas: dict[str, dict[str, Any]] = {}
    patch_stems: set[str] = set()
    for eid in sorted(graph.entities):
        ent = graph.entities[eid]
        if ent.status == "out":
            continue
        ex = (fixtures or {}).get(eid) or hero.get(eid)
        base = entity_schema(ent, fixtures=list(ex) if ex else None)
        schemas[eid] = base
        if any(o.verb == "update" and o.entity == eid for o in ops) and _editable(ent):
            schemas[f"{eid}.patch"] = patch_schema(ent, base)
            patch_stems.add(f"{eid}.patch")
    # an update operation of an entity that is not editable still needs a body schema
    for o in ops:
        if (
            o.verb == "update"
            and o.entity
            and f"{o.entity}.patch" not in schemas
            and o.entity in schemas
        ):
            ent = graph.entities[o.entity]
            schemas[f"{o.entity}.patch"] = patch_schema(ent, schemas[o.entity])
            patch_stems.add(f"{o.entity}.patch")

    doc, _, _ = build_document(graph, ops, split=False, schemas=schemas, patch_stems=patch_stems)
    single_text = dump_yaml(doc)
    split = _tokens(single_text, factor) > cap
    files: dict[str, str] = {}
    warnings: list[str] = []
    if not split:
        files["contract/openapi.yaml"] = single_text
    else:
        root, resources, shared = build_document(
            graph, ops, split=True, schemas=schemas, patch_stems=patch_stems
        )
        files["contract/components.yaml"] = dump_yaml(shared)
        paths: dict[str, Any] = {}
        for key in sorted(resources):
            part = resources[key]
            chunks: list[dict[str, Any]] = [{}]
            for p in sorted(part):
                item_tokens = _tokens(dump_yaml({p: part[p]}), factor)
                if item_tokens > cap:
                    warnings.append(
                        f"path {p} is {item_tokens} tokens, cap {cap}: it cannot be split further"
                    )
                if chunks[-1] and _tokens(dump_yaml({**chunks[-1], p: part[p]}), factor) > cap:
                    chunks.append({})
                chunks[-1][p] = part[p]
            for n, chunk in enumerate(chunks, start=1):
                fname = f"resources/{_slug(key)}{'' if len(chunks) == 1 else f'-{n}'}.yaml"
                files[f"contract/{fname}"] = dump_yaml(chunk)
                for p in sorted(chunk):
                    target = root.setdefault("webhooks", {}) if key == "webhooks" else paths
                    target[p] = {"$ref": f"{fname}#/{_pointer(p)}"}
        root["paths"] = paths
        root_text = dump_yaml(root)
        if _tokens(root_text, factor) > cap:
            warnings.append(
                f"contract/openapi.yaml root is {_tokens(root_text, factor)} tokens, cap {cap}: "
                "the program has too many paths for one index"
            )
        files["contract/openapi.yaml"] = root_text
    for stem in sorted(schemas):
        files[f"contract/schemas/{stem}.json"] = dump_json(
            {"$schema": SCHEMA_DIALECT, **schemas[stem]}
        )
    auth = build_auth_matrix(graph, ops)
    events = build_events(graph)
    files["contract/auth-matrix.json"] = dump_json_lines(auth)
    files["contract/events.json"] = dump_json_lines(events)
    for eid in sorted(fixtures or {}):
        files[f"contract/fixtures/{eid}.json"] = dump_json((fixtures or {})[eid])

    if validate:
        msg = validate_openapi(doc)
        if msg is None and split:
            with tempfile.TemporaryDirectory() as tmp:
                for p, t in files.items():
                    dest = Path(tmp) / p
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(t, encoding="utf-8")
                msg = validate_openapi(Path(tmp) / "contract" / "openapi.yaml")
        if msg is not None:
            findings.append(
                Finding(
                    "V-10",
                    "blocker",
                    "contract/openapi.yaml",
                    f"the OpenAPI document does not validate: {msg}",
                    "fix the operation, path or schema it names (a generated file: a Spec bug)",
                    "C",
                )
            )
    rows = [
        {
            "id": o.id,
            "method": o.method,
            "path": o.path,
            "entity": o.entity,
            "verb": o.verb,
            "phase": o.phase,
            "derived": o.derived,
            "roles": o.roles,
        }
        for o in ops
    ]
    return ContractBundle(doc, schemas, auth, events, files, rows, split, findings, warnings)


def contract_files(
    graph: SpecGraph,
    *,
    config: SpecConfig | None = None,
    chars_per_token: float | None = None,
    fixtures: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, str]:
    """Pack path -> text for everything under contract/ that code writes (the pack merges this)."""
    return build_contract(
        graph, config=config, chars_per_token=chars_per_token, fixtures=fixtures, validate=False
    ).files
