"""Cooperative cancellation. Checked between steps and before every LLM call (brief 8.1, 8.3)."""

from __future__ import annotations

import asyncio

from mde_core.errors import CallCancelled


class CancellationToken:
    """A flag that code polls. ``cancel()`` is idempotent and safe from any task."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    def check(self) -> None:
        """Raise :class:`CallCancelled` if cancellation was requested."""
        if self._event.is_set():
            raise CallCancelled(self._reason or "cancelled")

    async def wait(self) -> None:
        await self._event.wait()


class NeverCancelled(CancellationToken):
    """Token that cannot be cancelled (default for scripts and tests)."""

    def cancel(self, reason: str = "cancelled") -> None:
        return None
