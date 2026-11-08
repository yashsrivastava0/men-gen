"""Pydantic model -> OpenAI *strict* JSON schema (brief 7.5).

What OpenAI's strict mode accepts (guide "Supported schemas", read 2026-11-08; see
``docs/architecture/llm-gateway.md`` for the confirmed/unverified list):

* root is an ``object`` (no ``anyOf`` at the root); every object has ``additionalProperties: false``
  and ALL its properties listed in ``required``;
* optional values are expressed as a union with ``null``;
* supported keywords: ``type``, ``properties``, ``required``, ``items``, ``enum``, ``anyOf``,
  ``$ref``/``$defs``, ``description``, string ``pattern`` and ``format`` (a short list), number
  ``minimum``/``maximum``/``exclusiveMinimum``/``exclusiveMaximum``/``multipleOf``, array
  ``minItems``/``maxItems``;
* NOT supported: ``allOf``, ``not``, ``if/then/else``, ``dependent*``, ``patternProperties``; the
  guide also does not list ``minLength``/``maxLength`` or ``const``, so they are dropped / rewritten;
* limits: 5000 properties, 10 nesting levels, 1000 enum values.

This converter is deliberately stricter than the API: ``$defs`` are inlined (recursion is rejected),
nesting is capped at 5 levels (brief), open objects (``dict``), tuples and ``Any`` are rejected with a
path to the offending field, ``oneOf`` becomes ``anyOf``, ``const`` becomes a one-value ``enum`` and
``default``/``title``/``examples`` are dropped.

Decision: a field that has a default but whose Python type does not admit ``None`` stays a REQUIRED,
NON-NULL property. A ``null`` for it would fail local validation, and forcing the model to always
emit the field costs a few tokens. Only fields typed ``X | None`` become ``anyOf [X, null]``. Every
JSON document valid for the produced schema is therefore valid for the model's structure, and every
fully-populated ``model_dump`` is valid for the schema. Constraints the API does not accept are
stripped from the *provider* schema only; the gateway always re-validates locally with the model.
"""

from __future__ import annotations

import re
from typing import cast

from pydantic import BaseModel

from mde_core.errors import ConfigError
from mde_core.hashing import hash_json

JsonDict = dict[str, object]

DEFAULT_MAX_DEPTH = 5

# Keywords copied through unchanged (when the value is well formed).
_KEEP = {
    "type",
    "enum",
    "description",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
}

# string ``format`` values the API accepts.
_FORMATS = {
    "date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid",
}  # fmt: skip
# Keywords that change meaning and cannot be dropped silently.
_FORBIDDEN = {
    "allOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas",
    "patternProperties", "prefixItems", "unevaluatedProperties", "unevaluatedItems",
    "propertyNames", "contains", "$dynamicRef", "$anchor",
}  # fmt: skip
_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]")


class StrictSchemaError(ConfigError):
    """The model cannot be expressed in OpenAI's strict subset. ``path`` points at the field."""

    code = "config_error"

    def __init__(self, message: str, *, path: str = "$") -> None:
        super().__init__(f"{message} (at {path})", details={"path": path})
        self.path = path


def strict_schema_name(spec_id: str) -> str:
    """``text.format.name``: letters, digits, ``_`` and ``-`` only, at most 64 characters."""
    return _NAME_RE.sub("_", spec_id)[:64] or "output"


def schema_hash(schema: JsonDict) -> str:
    return hash_json(schema)


def to_strict_schema(
    model: type[BaseModel], *, max_depth: int = DEFAULT_MAX_DEPTH, keep_pattern: bool = True
) -> JsonDict:
    """Convert ``model`` to a strict-mode JSON schema (a new dict every call).

    Raises :class:`StrictSchemaError` for open objects, ``Any``, recursion, unsupported composition
    or nesting deeper than ``max_depth`` (objects and arrays each count one level).
    """
    raw = cast("JsonDict", model.model_json_schema(mode="validation"))
    defs = cast("dict[str, JsonDict]", raw.get("$defs", {}))
    conv = _Converter(defs, keep_pattern=keep_pattern)
    out = conv.node(raw, "$", ())
    if out.get("type") != "object":
        raise StrictSchemaError("the root of a strict schema must be an object")
    depth = schema_depth(out)
    if depth > max_depth:
        raise StrictSchemaError(
            f"{model.__name__} nests {depth} levels deep; the limit is {max_depth}", path="$"
        )
    problems = check_strict(out, max_depth=max_depth)
    if problems:  # internal consistency: the converter must never emit a non-strict schema
        raise StrictSchemaError("; ".join(problems[:3]))
    return out


class _Converter:
    def __init__(self, defs: dict[str, JsonDict], *, keep_pattern: bool) -> None:
        self.defs = defs
        self.keep_pattern = keep_pattern

    # -- dispatch
    def node(self, node: JsonDict, path: str, stack: tuple[str, ...]) -> JsonDict:
        bad = sorted(set(node) & _FORBIDDEN)
        if bad and not (bad == ["allOf"] and self._single_allof(node)):
            raise StrictSchemaError(f"unsupported keyword(s) {bad}", path=path)
        if "$ref" in node:
            return self._ref(node, path, stack)
        if "allOf" in node:  # single-element allOf: merge (older Pydantic wraps $ref with siblings)
            merged = {k: v for k, v in node.items() if k != "allOf"}
            only = cast("list[JsonDict]", node["allOf"])[0]
            return self.node({**only, **merged}, path, stack)
        if "oneOf" in node or "anyOf" in node:
            return self._any_of(node, path, stack)
        if "const" in node:
            node = {**{k: v for k, v in node.items() if k != "const"}, "enum": [node["const"]]}
        kind = node.get("type")
        if kind == "object" or "properties" in node:
            return self._object(node, path, stack)
        if kind == "array":
            return self._array(node, path, stack)
        if kind is None and "enum" not in node:
            raise StrictSchemaError("unconstrained value (typing.Any is not allowed)", path=path)
        return self._scalar(node, path)

    @staticmethod
    def _single_allof(node: JsonDict) -> bool:
        parts = node.get("allOf")
        return isinstance(parts, list) and len(cast("list[object]", parts)) == 1

    def _ref(self, node: JsonDict, path: str, stack: tuple[str, ...]) -> JsonDict:
        ref = cast("str", node["$ref"])
        name = ref.rsplit("/", 1)[-1]
        if name in stack:
            raise StrictSchemaError(
                f"recursive model {name!r} cannot be a strict schema", path=path
            )
        if name not in self.defs:
            raise StrictSchemaError(f"unresolved reference {ref!r}", path=path)
        body = self.node(self.defs[name], path, (*stack, name))
        siblings = {k: v for k, v in node.items() if k in ("description",)}
        return {**body, **siblings}

    def _any_of(self, node: JsonDict, path: str, stack: tuple[str, ...]) -> JsonDict:
        branches = cast("list[JsonDict]", node.get("anyOf", node.get("oneOf")))
        out: list[JsonDict] = []
        for i, branch in enumerate(branches):
            if branch.get("type") == "null":
                out.append({"type": "null"})
                continue
            conv = self.node(branch, f"{path}.anyOf[{i}]", stack)
            if "anyOf" in conv and len(conv) == 1:  # flatten nested unions
                out.extend(cast("list[JsonDict]", conv["anyOf"]))
            else:
                out.append(conv)
        result: JsonDict = {"anyOf": out}
        if "description" in node:
            result["description"] = node["description"]
        return result

    def _object(self, node: JsonDict, path: str, stack: tuple[str, ...]) -> JsonDict:
        extra = node.get("additionalProperties", False)
        if extra not in (False, None):
            raise StrictSchemaError(
                "open object (dict / additionalProperties): use a model with named fields",
                path=path,
            )
        props = cast("dict[str, JsonDict]", node.get("properties", {}))
        converted: dict[str, object] = {}
        for name, sub in props.items():
            converted[name] = self.node(sub, f"{path}.{name}", stack)
        out: JsonDict = {
            "type": "object",
            "properties": converted,
            "required": list(converted),
            "additionalProperties": False,
        }
        if "description" in node:
            out["description"] = node["description"]
        return out

    def _array(self, node: JsonDict, path: str, stack: tuple[str, ...]) -> JsonDict:
        items = node.get("items")
        if not isinstance(items, dict):
            raise StrictSchemaError("array without a single item schema", path=path)
        out: JsonDict = {
            "type": "array",
            "items": self.node(cast("JsonDict", items), f"{path}[]", stack),
        }
        for key in ("description", "minItems", "maxItems"):
            if key in node:
                out[key] = node[key]
        return out

    def _scalar(self, node: JsonDict, path: str) -> JsonDict:
        out: JsonDict = {}
        for key in node:
            if key in _KEEP:
                out[key] = node[key]
        fmt = node.get("format")
        if isinstance(fmt, str) and fmt in _FORMATS:
            out["format"] = fmt
        pattern = node.get("pattern")
        if self.keep_pattern and isinstance(pattern, str):
            out["pattern"] = pattern
        kind = out.get("type")
        if isinstance(kind, list):
            out["type"] = kind
        elif kind is None and "enum" in out:
            values = cast("list[object]", out["enum"])
            out["type"] = _enum_type(values)
        if out.get("type") in ("array", "object"):  # pragma: no cover - handled by callers
            raise StrictSchemaError("unexpected composite type", path=path)
        return out


def _enum_type(values: list[object]) -> str:
    kinds = {type(v) for v in values}
    if kinds == {str}:
        return "string"
    if kinds == {bool}:
        return "boolean"
    if kinds == {int}:
        return "integer"
    return "string"


# ----------------------------------------------------------------------------- inspection helpers
def schema_depth(node: object) -> int:
    """Nesting depth: every ``object`` and every ``array`` on a path counts one level."""
    if isinstance(node, list):
        return max((schema_depth(x) for x in cast("list[object]", node)), default=0)
    if not isinstance(node, dict):
        return 0
    d = cast("JsonDict", node)
    best = 0
    for key in ("properties",):
        props = d.get(key)
        if isinstance(props, dict):
            best = max(
                best, max((schema_depth(v) for v in cast("JsonDict", props).values()), default=0)
            )
    for key in ("items", "anyOf"):
        if key in d:
            best = max(best, schema_depth(d[key]))
    own = 1 if d.get("type") in ("object", "array") else 0
    return own + best


def check_strict(node: object, *, max_depth: int = DEFAULT_MAX_DEPTH, path: str = "$") -> list[str]:
    """Return every way ``node`` violates the strict subset (empty list: it conforms)."""
    problems: list[str] = []
    if path == "$":
        if not isinstance(node, dict) or cast("JsonDict", node).get("type") != "object":
            return ["root must be an object"]
        if "anyOf" in cast("JsonDict", node):
            problems.append("root must not be anyOf")
        if schema_depth(cast("JsonDict", node)) > max_depth:
            problems.append(f"nesting deeper than {max_depth}")
    if isinstance(node, list):
        for i, item in enumerate(cast("list[object]", node)):
            problems += check_strict(item, max_depth=max_depth, path=f"{path}[{i}]")
        return problems
    if not isinstance(node, dict):
        return problems
    d = cast("JsonDict", node)
    for forbidden in sorted(
        (set(d) & _FORBIDDEN) | ({"default", "const", "$ref", "$defs", "oneOf"} & set(d))
    ):
        problems.append(f"{path}: keyword {forbidden!r} not allowed")
    for dropped in ("minLength", "maxLength"):
        if dropped in d:
            problems.append(f"{path}: keyword {dropped!r} not allowed")
    if d.get("type") == "object":
        props = d.get("properties")
        if not isinstance(props, dict):
            problems.append(f"{path}: object without properties")
        else:
            names = list(cast("JsonDict", props))
            if d.get("required") != names:
                problems.append(f"{path}: required must list every property in order")
            if d.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            for name, sub in cast("JsonDict", props).items():
                problems += check_strict(sub, max_depth=max_depth, path=f"{path}.{name}")
    for key in ("items", "anyOf"):
        if key in d:
            problems += check_strict(d[key], max_depth=max_depth, path=f"{path}.{key}")
    return problems
