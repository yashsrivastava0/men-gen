"""R6 internal sources (Enterprise): an interface only in v1, with a stub.

Connectors read the company's own documents (PRDs, API specs, brand portals). They never reach the
web and their text never goes to a search provider. ``NullInternalSource`` is the default: it finds
nothing, so R6 is a no-op until a real connector implementation is wired in by the flow.
"""

from __future__ import annotations

from mde_contracts.ports import InternalFact, InternalSourcePort


class NullInternalSource:
    """Stub implementation of :class:`InternalSourcePort`: no connectors, no facts."""

    async def fetch(
        self, query: str, *, tenant_id: str, connectors: list[str]
    ) -> list[InternalFact]:
        return []


__all__ = ["InternalFact", "InternalSourcePort", "NullInternalSource"]
