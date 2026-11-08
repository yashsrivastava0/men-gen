"""Code validators of ``cmp.interpret_change``: path grammar, existence in the digest, value validity.

``value_ok`` is shared with ``cmp.make_variants``. Paths follow ``domain/changes.py``: dict sections are
``<section>.<key>``; list sections are ``<section>`` (add), ``<section>.<ID>`` (a record) and
``<section>.<ID>.<field>`` (a field). All regexes are anchored and bounded (linear).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import JsonValue

from mde_compass.calls.interpret_change.schema import (
    MAX_OPS,
    ChangeIn,
    ChangeOut,
    DigestItem,
    OpOut,
)
from mde_compass.domain.brief import DICT_SECTIONS, LIST_SECTIONS, FeatureStatus, Priority

_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
MAX_PATH_CHARS = 120
MAX_VALUE_TEXT = 500
MAX_OPTION_CHARS = 80

FieldKind = tuple[Literal["text", "text_list", "enum"], tuple[str, ...]]

# Editable fields of each addressable list section (what ``<section>.<ID>.<field>`` may name).
FIELD_KINDS: dict[str, dict[str, FieldKind]] = {
    "scope": {
        "name": ("text", ()),
        "intent": ("text", ()),
        "apps": ("text_list", ()),
        "scope_status": ("enum", get_args(FeatureStatus)),
        "priority": ("enum", get_args(Priority)),
    },
    "apps": {
        "name": ("text", ()),
        "purpose": ("text", ()),
        "surface": ("text", ()),
        "audience": ("text", ()),
        "roles": ("text_list", ()),
    },
    "roles": {
        "name_in_user_words": ("text", ()),
        "goal": ("text", ()),
        "rough_permissions": ("text_list", ()),
    },
    "assumptions": {
        "claim": ("text", ()),
        "decision": ("enum", ("proposed", "accepted", "changed", "rejected")),
    },
}
NAME_FIELD: dict[str, str] = {
    "scope": "name",
    "apps": "name",
    "roles": "name_in_user_words",
    "assumptions": "claim",
}
ADD_SECTIONS: tuple[str, ...] = ("scope", "apps", "roles")


@dataclass(frozen=True)
class ParsedPath:
    section: str
    record: str | None = None  # list sections: record id
    field: str | None = None  # list sections: field name
    key: str | None = None  # dict sections: the key (may hold dots, e.g. ``brand.name``)


def parse_path(path: str) -> ParsedPath | None:
    """Parse a PatchOp path, or ``None`` when it does not follow the grammar."""
    if not path or len(path) > MAX_PATH_CHARS:
        return None
    parts = path.split(".")
    if any(not _SEGMENT.match(p) for p in parts):
        return None
    section = parts[0]
    if section in DICT_SECTIONS:
        return ParsedPath(section, key=".".join(parts[1:])) if len(parts) >= 2 else None
    if section in LIST_SECTIONS and len(parts) <= 3:
        return ParsedPath(
            section,
            record=parts[1] if len(parts) > 1 else None,
            field=parts[2] if len(parts) > 2 else None,
        )
    return None


def parse_json(text: str) -> tuple[bool, JsonValue]:
    """``(ok, value)``; an empty string counts as JSON ``null``."""
    if not text.strip():
        return True, None
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def _is_text_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def value_ok(kind: str, options: list[str] | tuple[str, ...], value: JsonValue) -> str | None:
    """``None`` when ``value`` is valid for a slot of ``kind`` (with ``options`` for enums), else why not."""
    opts = list(options)
    if kind == "text":
        if isinstance(value, str) and value.strip() and len(value) <= MAX_VALUE_TEXT:
            return None
        return "expected a non-empty short text"
    if kind == "enum":
        if not isinstance(value, str) or not value.strip():
            return "expected one option as text"
        return None if not opts or value in opts else f"{value!r} is not one of the options"
    if kind == "enum_list":
        if not _is_text_list(value) or not value:
            return "expected a non-empty list of options"
        assert isinstance(value, list)
        bad = [v for v in value if opts and v not in opts]
        return None if not bad else f"{bad[:2]} not among the options"
    if kind == "text_list":
        return None if _is_text_list(value) and value else "expected a non-empty list of texts"
    if kind == "object_list":
        ok = isinstance(value, list) and value and all(isinstance(v, dict) for v in value)
        return None if ok else "expected a non-empty list of objects"
    if kind == "object":
        return None if isinstance(value, dict) and value else "expected an object"
    if kind == "bool":
        return None if isinstance(value, bool) else "expected true or false"
    if kind == "geo":
        ok = (isinstance(value, str) and value.strip()) or (isinstance(value, dict) and value)
        return None if ok else "expected a place as text or an object"
    if kind == "locales":
        if _is_text_list(value) and value:
            return None
        if isinstance(value, dict) and set(value) <= {"chat", "app", "right_to_left"} and value:
            return None
        return "expected a list of language codes or {chat, app, right_to_left}"
    return None  # unknown kinds (record, ...): the caller decides


def _field_ok(section: str, field: str, value: JsonValue) -> str | None:
    spec = FIELD_KINDS.get(section, {}).get(field)
    if spec is None:
        return f"field {field!r} is not editable in {section}"
    kind, options = spec
    return value_ok(kind, options, value)


def owner_item(items: dict[str, DigestItem], path: str) -> DigestItem | None:
    """The digest item an op path belongs to: the path itself or its record (``scope.FTR-004.priority``)."""
    if path in items:
        return items[path]
    head, _, _ = path.rpartition(".")
    return items.get(head)


def _check_add(inp: ChangeIn, op: OpOut, pp: ParsedPath) -> list[str]:
    if pp.record or pp.section not in inp.add_sections:
        return [f"add: {op.path!r} is not an allowed list section"]
    ok, value = parse_json(op.value_json)
    if not ok or not isinstance(value, dict):
        return ["add: value must be a JSON object"]
    errors = [f"add: {e}" for k, v in value.items() if (e := _field_ok(pp.section, k, v))]
    name = value.get(NAME_FIELD[pp.section])
    if not isinstance(name, str) or not name.strip():
        errors.append(f"add: needs a {NAME_FIELD[pp.section]}")
    return errors


def _check_op(inp: ChangeIn, items: dict[str, DigestItem], op: OpOut) -> list[str]:
    pp = parse_path(op.path)
    if pp is None:
        return [f"bad path {op.path!r}"]
    ok_old, _ = parse_json(op.old_value_json)
    if not ok_old:
        return ["old_value_json is not JSON"]
    if op.op == "add":
        return _check_add(inp, op, pp)
    owner = owner_item(items, op.path)
    if owner is None:
        return [f"{op.path!r} is not among the targeted items"]
    ok, value = parse_json(op.value_json)
    if not ok:
        return [f"{op.path}: value_json is not JSON"]
    whole = op.path in items
    if op.op in ("remove", "clear"):
        if value is not None:
            return [f"{op.op}: value_json must be null"]
        if op.op == "remove" and not (pp.record and not pp.field and whole):
            return ["remove: path must be a list record such as scope.FTR-004"]
        if op.op == "clear" and pp.record and not pp.field:
            return ["clear: use remove for a whole record"]
        return []
    if whole:
        if owner.value_kind == "record":
            return [f"set: {op.path} is a record; set one of its fields"]
        err = value_ok(owner.value_kind, owner.options, value)
        return [f"set {op.path}: {err}"] if err else []
    if pp.field is None:
        return [f"set: {op.path!r} is not a known item"]
    err = _field_ok(pp.section, pp.field, value)
    return [f"set {op.path}: {err}"] if err else []


def check_change(inp: ChangeIn, out: ChangeOut) -> list[str]:
    """Errors that make the model output unusable; an empty list means it may become a proposal."""
    errors: list[str] = []
    if len(out.options) > 3 or any(len(o) > MAX_OPTION_CHARS or not o.strip() for o in out.options):
        errors.append("options: at most three short choices")
    if not out.understood:
        if out.ops:
            errors.append("ops returned although not understood")
        if not out.clarification.strip():
            errors.append("not understood but no clarification")
        return errors
    if out.clarification.strip():
        errors.append("understood but also asks a question")
    if not out.ops:
        errors.append("understood without operations")
    if len(out.ops) > MAX_OPS:
        errors.append(f"more than {MAX_OPS} operations")
    items = {i.path: i for i in inp.items}
    seen: set[tuple[str, str]] = set()
    owners: set[str] = set()
    for op in out.ops:
        errors.extend(_check_op(inp, items, op))
        if (op.op, op.path) in seen:
            errors.append(f"duplicate operation on {op.path}")
        seen.add((op.op, op.path))
        owner = owner_item(items, op.path)
        if owner is not None:
            owners.add(owner.path)
    if inp.target_kind == "unknown" and len(owners) > 1:
        errors.append("ambiguous target: operations touch several items; ask instead")
    return errors
