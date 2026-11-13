"""Deterministic fallback of PROD-INTEG: the draft with the library texts a provider-side flow requires."""

from __future__ import annotations

from .baseline import complete
from .schema import IntegIn, IntegOut


def fallback(inp: IntegIn) -> IntegOut:
    return complete(inp)
