"""Checkpoint versions of a call: the key carries the call spec, the prompt text and the schema, so editing a template or
a schema (even without a version bump) never replays a stale batch."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from mde_spec_agent.calls.base import SpecCallSpec
from mde_spec_agent.run.checkpoint import Versions


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


def versions_of(base: Versions, cs: SpecCallSpec) -> Versions:  # type: ignore[type-arg]
    root = Path(cs.spec.template_root)
    prompt = _hash(
        (root / cs.spec.system_template).read_text(encoding="utf-8"),
        (root / cs.spec.user_template).read_text(encoding="utf-8"),
    )
    schema = _hash(json.dumps(cs.spec.output_model.model_json_schema(), sort_keys=True))  # type: ignore[union-attr]
    return dataclasses.replace(
        base,
        call_spec=f"{cs.id}@{cs.spec.version}",
        prompt=f"{cs.id}.prompt@{prompt}",
        schema=f"{cs.id}.schema@{schema}",
    )
