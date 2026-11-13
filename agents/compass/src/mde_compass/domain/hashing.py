"""Canonical JSON and hashing (ADR 0013).

``canonical_json`` is sorted-key, compact, UTF-8, with volatile fields removed, so equal content always
hashes equal. The whole-Brief hash covers all content sections plus ``meta`` (minus volatile keys);
section hashes cover one content section each. Approval binds to the whole hash and the section hashes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from mde_compass.domain.brief import OPTIONAL_SECTIONS, SECTION_KEYS, Brief

# Bookkeeping, not content: changing these must not change a hash.
VOLATILE_TOP_LEVEL: frozenset[str] = frozenset({"version", "ids", "readiness"})
VOLATILE_KEYS: frozenset[str] = frozenset({"date", "at", "updated_at", "created_at"})


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def canonical_json(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        _strip(data), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def brief_hash(brief: Brief) -> str:
    data = brief.model_dump(mode="json")
    for key in VOLATILE_TOP_LEVEL:
        data.pop(key, None)
    # who approved which hash is bookkeeping about the hash itself: recording it must not change it
    meta = data.get("meta")
    if isinstance(meta, dict):
        meta.pop("approvals", None)
        meta.pop("interview", None)  # audit counts about the interview, never content (ADR 0015)
        meta.pop(
            "compile_digests", None
        )  # what the compile was last run on: bookkeeping (ADR 0016)
        plan = meta.get("plan")
        if isinstance(plan, dict) and plan.get("interview_budget") is None:
            plan.pop(
                "interview_budget", None
            )  # a Brief that never chose one hashes as it always did
    for key in OPTIONAL_SECTIONS:
        if not data.get(key):
            data.pop(key, None)  # an empty optional section is not part of the content
    return sha256_hex(canonical_json(data))


def section_hashes(brief: Brief) -> dict[str, str]:
    data = brief.model_dump(mode="json")
    return {
        key: sha256_hex(canonical_json(data.get(key, [])))
        for key in SECTION_KEYS
        if not (
            key in OPTIONAL_SECTIONS and not data.get(key)
        )  # absent while empty (the dump may leave the key out); absent == absent below
    }


def changed_sections(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Sections whose hash differs: exactly the approvals a change voids."""
    return [key for key in SECTION_KEYS if before.get(key) != after.get(key)]
