"""Deterministic fallback of PROD-MODULE: the code-made draft, completed (acceptance and a story for every screen).
The runner flags the node `degraded` when this is used."""

from __future__ import annotations

from .baseline import complete
from .schema import ModuleIn, ModuleOut


def fallback(inp: ModuleIn) -> ModuleOut:
    return complete(inp)
