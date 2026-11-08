"""Canonical JSON and sha256 helpers. Hashes bind approvals to exact versions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


def _default(value: object) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)  # type: ignore[type-var]
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Stable JSON: sorted keys, no whitespace, UTF-8 characters kept as-is."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default
    )


def sha256_hex(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def hash_json(value: object) -> str:
    return sha256_hex(canonical_json(value))


def hash_model(model: BaseModel, *, exclude: Iterable[str] = ()) -> str:
    """Hash a Pydantic model's JSON form, optionally leaving out fields such as ``hash``."""
    data = model.model_dump(mode="json", exclude=set(exclude))
    return hash_json(data)
