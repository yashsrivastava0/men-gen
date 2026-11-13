"""Deterministic fallback of PROD-JOURNEYS: the completed draft (moments, unhappy path, emotions, requested variants)."""

from __future__ import annotations

from .baseline import complete
from .schema import JourneysIn, JourneysOut


def fallback(inp: JourneysIn) -> JourneysOut:
    return complete(inp)
