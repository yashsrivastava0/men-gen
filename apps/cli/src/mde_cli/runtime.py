"""A persistent event loop driven from synchronous code.

Why not one ``asyncio.run`` around the whole chat: ``asyncio.run`` turns the first Ctrl+C into a task
cancellation, which cannot interrupt a blocking ``input()``, and reading the keyboard in a worker
thread leaves a thread that blocks the interpreter at exit. Instead the chat is a plain loop on the
main thread: ``input()`` blocks (and Ctrl+C works as everyone expects), and each step is one
``run_until_complete`` on a loop that lives for the whole command. A Ctrl+C while waiting for the
plan stops the wait, not the plan.

The loop is a ``SelectorEventLoop`` on every platform: psycopg's async mode cannot run on Windows'
default ``ProactorEventLoop`` (D-042). It is created directly, so no global policy is touched.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class LoopRunner:
    def __init__(self) -> None:
        self.loop = asyncio.SelectorEventLoop()

    def run(self, coro: Awaitable[T]) -> T:
        return self.loop.run_until_complete(coro)

    def run_interruptible(self, coro: Coroutine[Any, Any, T]) -> tuple[bool, T | None]:
        """``(True, result)``, or ``(False, None)`` when Ctrl+C stopped it (the task is cancelled
        and awaited, so nothing is left running)."""
        task = self.loop.create_task(coro)
        try:
            return True, self.loop.run_until_complete(task)
        except KeyboardInterrupt:
            task.cancel()
            with contextlib.suppress(BaseException):
                self.loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
            return False, None

    def close(self) -> None:
        loop = self.loop
        if loop.is_closed():
            return
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()
