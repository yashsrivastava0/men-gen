"""Check a slot value (a JSON string from the model) against the slot's catalog ``value_schema``."""

from __future__ import annotations

import json
import re
from typing import Any

from mde_compass.catalog.loader import SlotDef

MAX_VALUE_CHARS = 4_000
_LANG_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class ValueError_(ValueError):
    """The value does not fit the slot."""


def parse_json(value_json: str) -> Any:
    if len(value_json) > MAX_VALUE_CHARS:
        raise ValueError_("value too long")
    try:
        return json.loads(value_json)
    except (ValueError, RecursionError) as exc:
        raise ValueError_("value_json is not JSON") from exc


def _is_text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _check_object(slot: SlotDef, v: Any) -> None:
    if not isinstance(v, dict) or not v:
        raise ValueError_("expected a non-empty object")
    allowed = set(slot.value_schema.fields)
    if allowed and not set(v) <= allowed:
        raise ValueError_(f"unknown fields {sorted(set(v) - allowed)}")


def validate_value(slot: SlotDef, value: Any) -> Any:
    """Return the normalised value or raise ``ValueError_`` when it does not match ``slot.value_schema``."""
    kind = slot.value_schema.kind
    options = slot.value_schema.options
    if kind == "text":
        if not _is_text(value):
            raise ValueError_("expected text")
        return value.strip()
    if kind == "enum":
        if value not in options:
            raise ValueError_("not one of the options")
        return value
    if kind in ("enum_list", "text_list"):
        if not isinstance(value, list) or not value or not all(_is_text(x) for x in value):
            raise ValueError_("expected a non-empty list of strings")
        if kind == "enum_list" and options and not all(x in options for x in value):
            raise ValueError_("value outside the options")
        return [x.strip() for x in value]
    if kind == "object_list":
        if not isinstance(value, list) or not value:
            raise ValueError_("expected a non-empty list of objects")
        for item in value:
            _check_object(slot, item)
        return value
    if kind == "object":
        _check_object(slot, value)
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError_("expected true or false")
        return value
    if kind == "geo":
        _check_object(slot, value)
        if not all(_is_text(x) for x in value.values()):
            raise ValueError_("geo parts must be text")
        return value
    if kind == "locales":
        if not isinstance(value, list) or not value:
            raise ValueError_("expected a list of language codes")
        if not all(isinstance(x, str) and _LANG_CODE.match(x) for x in value):
            raise ValueError_("language codes expected")
        return value
    raise ValueError_(f"unknown kind {kind}")  # pragma: no cover
