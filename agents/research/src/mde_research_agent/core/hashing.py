"""The content hash of ``research.json``: covers everything except the volatile ``run`` block and the
wall-clock budget values, so the same inputs and cassettes give the same hash (determinism)."""

from __future__ import annotations

from typing import Any

from mde_core.hashing import hash_json, sha256_hex
from mde_research_agent.domain.research import content_payload


def content_hash(output: dict[str, Any]) -> str:
    return hash_json(content_payload(output))


def text_hash(text: str) -> str:
    return sha256_hex(text)
