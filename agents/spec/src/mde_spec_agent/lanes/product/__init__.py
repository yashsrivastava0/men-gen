"""The product lane: its call specs (CALLS) and its pipeline steps (STEPS), filled in by the lane's author."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

CALLS: list[Any] = []  # SpecCallSpec objects registered with the gateway
STEPS: dict[str, Callable[[Any], Awaitable[None]]] = {}  # step name -> async fn(RunContext)
