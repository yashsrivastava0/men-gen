"""Event-loop factory for Windows (D-042).

psycopg's async mode cannot run on Windows' default ``ProactorEventLoop``; it needs a selector loop.
The policy must be set BEFORE any loop exists, so every entry point of this package imports this
module first (``python -m mde_context_api``, ``python -m mde_context_api.worker``) and passes
``selector_loop_factory`` to uvicorn (``loop="mde_context_api.loop:selector_loop_factory"``), which
also works with ``--reload`` (the child process imports it by name).

``selector_loop_factory`` is shaped to suit both uvicorn conventions: newer versions use the imported
callable directly as the loop factory (called with no argument), older ones call a custom "loop
setup" with ``use_subprocess=...`` and ignore the result; either way the policy is set first.
"""

from __future__ import annotations

import asyncio

from mde_persistence.db import ensure_selector_loop_policy

LOOP_FACTORY_PATH = "mde_context_api.loop:selector_loop_factory"

ensure_selector_loop_policy()  # importing this module is enough on Windows; no-op elsewhere


def selector_loop_factory(use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    """A new selector event loop (the only kind psycopg's async mode supports on Windows)."""
    ensure_selector_loop_policy()
    return asyncio.SelectorEventLoop()
