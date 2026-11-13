"""Offline compile check of a call's output schema against a consumer profile (PS-0; FN-21, FN-22).

Computes from a pydantic output model's JSON schema the numbers a provider's structured-output mode
limits (optional or nullable fields, union parameters, nesting depth, largest enum, schema size) and
compares them with ``ConsumerProfile.schema_limits``. A limit that is ``None`` was never verified: it
is NOT checked and is reported as an ``unchecked`` issue, so a missing number is said out loud.

It also enforces the portable shape of ADR 0014 / doc 18 4.4: no open maps, no ``Any``, no tuples, no
recursion, and records inside arrays are closed (``additionalProperties: false``) and flat.

How "nullable" is counted: a property whose schema is a union with ``null`` (``X | None``). The
gateway turns every optional into that union and keeps defaulted non-null fields required, so this is
the number a provider sees (D-172). ``union_params`` counts every union-typed property (nullable or
not). Depth counts every ``object`` and every ``array`` on a path as one level, as the gateway does.

No gateway import (spec-core is the foundation layer): this re-implements the measuring, not the
conversion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel

from mde_spec_core.canon import canonical_json
from mde_spec_core.profiles import ConsumerProfile, ProfileSet, SchemaLimits

Json = dict[str, Any]
Severity = Literal["error", "unchecked"]

_SCALARS = {"string", "integer", "number", "boolean", "null"}


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str
    severity: Severity = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True)
class SchemaMetrics:
    nullable_fields: int = 0
    union_params: int = 0
    depth: int = 0
    max_enum: int = 0
    grammar_chars: int = 0


def combine_metrics(parts: Iterable[SchemaMetrics], *, wrap: bool = True) -> SchemaMetrics:
    """Metrics of a FUSED schema: counts add up, depth is the deepest part (+1 for the wrapper)."""
    plist = list(parts)
    if not plist:
        return SchemaMetrics()
    return SchemaMetrics(
        nullable_fields=sum(p.nullable_fields for p in plist),
        union_params=sum(p.union_params for p in plist),
        depth=max(p.depth for p in plist) + (1 if wrap and len(plist) > 1 else 0),
        max_enum=max(p.max_enum for p in plist),
        grammar_chars=sum(p.grammar_chars for p in plist),
    )


def compare_limits(metrics: SchemaMetrics, limits: SchemaLimits, *, path: str = "$") -> list[Issue]:
    """Compare measured numbers with a profile's limits; ``None`` limits are reported unchecked."""
    pairs: list[tuple[str, int, int | None, str]] = [
        (
            "nullable_fields",
            metrics.nullable_fields,
            limits.max_nullable_fields,
            "max_nullable_fields",
        ),
        ("union_params", metrics.union_params, limits.max_union_params, "max_union_params"),
        ("depth", metrics.depth, limits.max_depth, "max_depth"),
        ("enum_size", metrics.max_enum, limits.max_enum_size, "max_enum_size"),
        ("grammar_chars", metrics.grammar_chars, limits.max_grammar_chars, "max_grammar_chars"),
    ]
    out: list[Issue] = []
    for name, value, limit, limit_name in pairs:
        if limit is None:
            out.append(
                Issue(
                    f"unchecked_{name}",
                    path,
                    f"{limit_name} is unset in the profile; {name}={value} was not checked",
                    "unchecked",
                )
            )
        elif value > limit:
            out.append(
                Issue(
                    f"over_{name}",
                    path,
                    f"{name} is {value}, the profile allows {limit} ({limit_name})",
                )
            )
    return out


# ------------------------------------------------------------------------------------- analysis
def analyse_model(model_cls: type[BaseModel]) -> tuple[SchemaMetrics, list[Issue]]:
    """Measure a model's schema and list every structural (ADR 0014) problem."""
    schema = cast("Json", model_cls.model_json_schema(mode="validation"))
    return analyse_schema(schema)


def analyse_schema(schema: Json) -> tuple[SchemaMetrics, list[Issue]]:
    walker = _Walker(cast("dict[str, Json]", schema.get("$defs", {})))
    root_issues: list[Issue] = []
    resolved = walker.resolve(schema, ())
    if resolved is None or resolved.get("type") != "object" or "properties" not in resolved:
        root_issues.append(
            Issue("root_not_object", "$", "the root of a call schema must be an object")
        )
    depth = walker.node(schema, "$", ())
    try:
        chars = len(canonical_json(schema))
    except (TypeError, ValueError):
        chars = 0
    metrics = SchemaMetrics(
        nullable_fields=walker.nullable,
        union_params=walker.unions,
        depth=depth,
        max_enum=walker.max_enum,
        grammar_chars=chars,
    )
    return metrics, root_issues + walker.issues


class _Walker:
    def __init__(self, defs: dict[str, Json]) -> None:
        self.defs = defs
        self.issues: list[Issue] = []
        self.nullable = 0
        self.unions = 0
        self.max_enum = 0

    def resolve(self, node: Json, stack: tuple[str, ...]) -> Json | None:
        """Follow ``$ref`` (and a single-element ``allOf``); None if unresolved or recursive."""
        seen = stack
        cur = node
        while True:
            if "$ref" in cur:
                name = str(cur["$ref"]).rsplit("/", 1)[-1]
                if name in seen or name not in self.defs:
                    return None
                seen = (*seen, name)
                cur = {**self.defs[name], **{k: v for k, v in cur.items() if k != "$ref"}}
                continue
            allof = cur.get("allOf")
            if isinstance(allof, list) and len(allof) == 1 and isinstance(allof[0], dict):
                cur = {**allof[0], **{k: v for k, v in cur.items() if k != "allOf"}}
                continue
            return cur

    def _stack_for(self, node: Json, stack: tuple[str, ...]) -> tuple[str, ...]:
        cur = node
        seen = stack
        while "$ref" in cur:
            name = str(cur["$ref"]).rsplit("/", 1)[-1]
            seen = (*seen, name)
            cur = self.defs.get(name, {})
        return seen

    def node(self, node: Json, path: str, stack: tuple[str, ...]) -> int:
        """Visit one schema node; return its depth (objects and arrays count one each)."""
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            if name in stack:
                self.issues.append(Issue("recursive", path, f"recursive model {name!r}"))
                return 0
            if name not in self.defs:
                self.issues.append(Issue("unresolved_ref", path, f"unresolved reference {name!r}"))
                return 0
        resolved = self.resolve(node, stack)
        if resolved is None:
            self.issues.append(Issue("recursive", path, "recursive or unresolved reference"))
            return 0
        new_stack = self._stack_for(node, stack)
        self._note_enum(resolved)

        union = resolved.get("anyOf", resolved.get("oneOf"))
        if isinstance(union, list):
            return max(
                (
                    self.node(b, f"{path}|{i}", new_stack)
                    for i, b in enumerate(union)
                    if isinstance(b, dict)
                ),
                default=0,
            )
        kind = resolved.get("type")
        if "prefixItems" in resolved or isinstance(resolved.get("items"), list):
            self.issues.append(Issue("tuple", path, "tuples are not portable: use a record"))
            return 1
        if kind == "object" or "properties" in resolved:
            return 1 + self._object(resolved, path, new_stack)
        if kind == "array":
            items = resolved.get("items")
            if not isinstance(items, dict):
                self.issues.append(Issue("any_type", path, "array without an item schema"))
                return 1
            self._array_record(cast("Json", items), f"{path}[]", new_stack)
            return 1 + self.node(cast("Json", items), f"{path}[]", new_stack)
        if kind is None and "enum" not in resolved and "const" not in resolved:
            self.issues.append(Issue("any_type", path, "unconstrained value (Any is not portable)"))
        return 0

    def _object(self, node: Json, path: str, stack: tuple[str, ...]) -> int:
        extra = node.get("additionalProperties", False)
        props = node.get("properties")
        if "patternProperties" in node or extra not in (False, None):
            self.issues.append(
                Issue(
                    "open_map", path, "open map (dict / additionalProperties): use a closed record"
                )
            )
        elif not isinstance(props, dict) or not props:
            self.issues.append(
                Issue("open_map", path, "object without named fields is an open map")
            )
        deepest = 0
        for name, sub in cast("dict[str, Json]", props if isinstance(props, dict) else {}).items():
            sub_path = f"{path}.{name}"
            sub_res = self.resolve(sub, stack) or {}
            union = sub_res.get("anyOf", sub_res.get("oneOf"))
            if isinstance(union, list):
                self.unions += 1
                if any(isinstance(b, dict) and b.get("type") == "null" for b in union):
                    self.nullable += 1
            deepest = max(deepest, self.node(sub, sub_path, stack))
        return deepest

    def _array_record(self, items: Json, path: str, stack: tuple[str, ...]) -> None:
        rec = self.resolve(items, stack)
        if rec is None or not (rec.get("type") == "object" or "properties" in rec):
            return
        if rec.get("additionalProperties") is not False:
            self.issues.append(
                Issue(
                    "array_record_open",
                    path,
                    "a record inside an array must set additionalProperties false",
                )
            )
        props = rec.get("properties")
        for name, sub in cast("dict[str, Json]", props if isinstance(props, dict) else {}).items():
            if not self._flat_field(sub, stack):
                self.issues.append(
                    Issue(
                        "array_record_not_flat",
                        f"{path}.{name}",
                        "a record inside an array should be flat: scalars, enums, or arrays of scalars",
                    )
                )

    def _flat_field(self, sub: Json, stack: tuple[str, ...]) -> bool:
        res = self.resolve(sub, stack)
        if res is None:
            return False
        union = res.get("anyOf", res.get("oneOf"))
        if isinstance(union, list):
            branches = [b for b in union if isinstance(b, dict) and b.get("type") != "null"]
            return all(self._flat_field(b, stack) for b in branches)
        kind = res.get("type")
        if "enum" in res or "const" in res or kind in _SCALARS:
            return True
        if kind == "array":
            items = res.get("items")
            return isinstance(items, dict) and self._scalar(cast("Json", items), stack)
        return False

    def _scalar(self, node: Json, stack: tuple[str, ...]) -> bool:
        res = self.resolve(node, stack)
        return res is not None and ("enum" in res or "const" in res or res.get("type") in _SCALARS)

    def _note_enum(self, node: Json) -> None:
        enum = node.get("enum")
        if isinstance(enum, list):
            self.max_enum = max(self.max_enum, len(enum))


# ------------------------------------------------------------------------------------------ API
def compile_check(
    model_cls: type[BaseModel], profile: ConsumerProfile, *, require_flat: bool = True
) -> list[Issue]:
    """Issues for one output model on one profile. ``[i for i in r if i.is_error]`` is the verdict."""
    metrics, structural = analyse_model(model_cls)
    if not require_flat:
        structural = [i for i in structural if i.code != "array_record_not_flat"]
    return [*structural, *compare_limits(metrics, profile.schema_limits)]


@dataclass(frozen=True)
class Finding:
    model: str
    profile_id: str
    issue: Issue


@dataclass(frozen=True)
class CompileReport:
    findings: tuple[Finding, ...]
    checked: tuple[tuple[str, str], ...]  # every (model, profile id) pair that was checked

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.issue.is_error]

    def unchecked(self) -> list[Finding]:
        return [f for f in self.findings if not f.issue.is_error]

    @property
    def ok(self) -> bool:
        return not self.errors()


def check_all(
    models: Iterable[type[BaseModel]] | Mapping[str, type[BaseModel]],
    profiles: Iterable[ConsumerProfile] | ProfileSet,
    *,
    require_flat: bool = True,
) -> CompileReport:
    """PS-0: every call model against every profile. Mapping keys name the models in the report."""
    named: list[tuple[str, type[BaseModel]]]
    if isinstance(models, Mapping):
        named = list(cast("Mapping[str, type[BaseModel]]", models).items())
    else:
        named = [(f"{m.__module__}.{m.__qualname__}", m) for m in models]
    plist = profiles.profiles if isinstance(profiles, ProfileSet) else list(profiles)
    findings: list[Finding] = []
    checked: list[tuple[str, str]] = []
    for name, cls in named:
        for prof in plist:
            checked.append((name, prof.id))
            findings += [
                Finding(name, prof.id, i)
                for i in compile_check(cls, prof, require_flat=require_flat)
            ]
    return CompileReport(tuple(findings), tuple(checked))
