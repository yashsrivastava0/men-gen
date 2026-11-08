"""Test helpers shared by the Discover call-spec tests (C1, C2, C3, C5, C11, C14).

Kept inside the package because ``--import-mode=importlib`` test modules cannot import siblings
(D-017). Nothing here is used by production code.

* ``render`` mirrors the gateway renderer (sandboxed Jinja, ``StrictUndefined``, the same context
  variables) so a template that renders here renders there.
* ``schema_depth`` is the strict-schema nesting check (the OpenAI limit is 5).
* ``load_cases`` reads ``fixtures/cases.yaml`` of a call folder; ``matches`` compares an expected
  subset with an actual dump.
"""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any

import yaml
from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel

from mde_contracts.gateway import CallSpec

UNTRUSTED_STAND_IN = '<untrusted_content source="test" sha256="x">\n{}\n</untrusted_content>'


def render(spec: CallSpec[Any, Any], inp: BaseModel) -> tuple[str, str]:
    """(system, user) prompt text for ``inp``, rendered like the gateway does."""
    env = SandboxedEnvironment(
        loader=FileSystemLoader(str(spec.template_root)),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["tojson"] = lambda v, indent=None: json.dumps(v, ensure_ascii=False, indent=indent)  # pyright: ignore[reportUnknownMemberType]
    data: dict[str, Any] = inp.model_dump(mode="json")
    for name in spec.untrusted_inputs:
        value = data.get(name)
        if isinstance(value, str):
            data[name] = UNTRUSTED_STAND_IN.format(value)
    context = {**data, "input": data, "input_json": json.dumps(data), "untrusted_notice": ""}
    system = env.get_template(spec.system_template).render(context)
    user = env.get_template(spec.user_template).render(context)
    return system, user


def schema_depth(schema: dict[str, Any], defs: dict[str, Any], seen: tuple[str, ...] = ()) -> int:
    """Nesting depth of objects and arrays; 99 marks an open object or a recursion."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return 99
        return schema_depth(defs[name], defs, (*seen, name))
    if "anyOf" in schema:
        return max(schema_depth(s, defs, seen) for s in schema["anyOf"])
    if schema.get("type") == "object" or "properties" in schema:
        if schema.get("additionalProperties") not in (None, False):
            return 99
        props = schema.get("properties", {})
        return 1 + max((schema_depth(p, defs, seen) for p in props.values()), default=0)
    if schema.get("type") == "array":
        return schema_depth(schema.get("items", {}), defs, seen)
    return 0


def model_depth(model: type[BaseModel]) -> int:
    schema = model.model_json_schema()
    return schema_depth(schema, schema.get("$defs", {}))


def load_cases(spec: CallSpec[Any, Any]) -> list[dict[str, Any]]:
    """Golden cases of a call: ``fixtures/cases.yaml`` next to the spec."""
    path = spec.template_root / "fixtures" / "cases.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def handler(spec: CallSpec[Any, Any]) -> Any:
    module, _, attr = spec.mock_handler.partition(":")
    return getattr(import_module(module), attr)


def matches(expected: Any, actual: Any) -> bool:
    """``expected`` is a subset of ``actual``: dict keys, list items (any order), scalars equal."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(v, actual[k])
            for k, v in expected.items()  # pyright: ignore[reportUnknownVariableType]
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(matches(e, a) for a in actual)  # pyright: ignore[reportUnknownVariableType]
            for e in expected  # pyright: ignore[reportUnknownVariableType]
        )
    if isinstance(expected, str) and expected.startswith(">="):
        return float(actual) >= float(expected[2:])
    if isinstance(expected, str) and expected.startswith("<="):
        return float(actual) <= float(expected[2:])
    return expected == actual
