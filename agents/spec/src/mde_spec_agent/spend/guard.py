"""One wrapper through which EVERY Spec and Verify model call passes (doc 19 5: no bypass).

``SpendGuard.call`` reserves the worst case, runs the call, reconciles with the actual cost, and books the worst case
when the call fails after leaving. The gateway keeps its own budget guard as a second, independent layer; this one is
Spec's own hard USD cap. The guard knows nothing about vendors: prices come from a profile (``Prices``) and the
actual cost from whatever the gateway reports.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from .record import Reservation, SpendRecord, SpendRefused
from .worst_case import Prices, worst_case_micros

T = TypeVar("T")


class StopRequested(RuntimeError):
    """POST /api/stop or SIGTERM: finish what is in flight, checkpoint, end partial."""


class SpendGuard:
    def __init__(self, record: SpendRecord, prices: Prices) -> None:
        self.record = record
        self.prices = prices
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    @property
    def stopping(self) -> bool:
        return self._stop

    def estimate(self, input_tokens: int, max_output_tokens: int) -> int:
        return worst_case_micros(input_tokens, max_output_tokens, self.prices)

    def start_wave(self) -> None:
        """Call before launching a wave of calls: tripwire and stop request are checked here."""
        if self._stop:
            raise StopRequested("stop requested")
        self.record.can_start_wave()

    async def call(
        self,
        run: Callable[[], Awaitable[T]],
        *,
        cost_of: Callable[[T], int],
        input_tokens: int,
        max_output_tokens: int,
        label: str,
        run_id: str = "",
    ) -> T:
        if self._stop:
            raise StopRequested("stop requested")
        res: Reservation = self.record.reserve(
            self.estimate(input_tokens, max_output_tokens), label=label, run_id=run_id
        )
        try:
            result = await run()
        except BaseException:
            # The request may have left: fail closed, book the worst case (never release).
            self.record.book_worst_case(res, note="call failed after reservation")
            raise
        self.record.commit(res, cost_of(result))
        return result


__all__ = ["SpendGuard", "SpendRefused", "StopRequested"]
