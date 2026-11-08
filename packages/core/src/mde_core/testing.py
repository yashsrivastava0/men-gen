"""Importable test helpers.

Multi-process tests must put their worker functions in a real package module (like this one):
with ``--import-mode=importlib`` a spawned child cannot import the test module by name.
"""

from __future__ import annotations

from pathlib import Path

from mde_core.filelock import FileLock


def lock_counter_worker(lock_path: str, counter_path: str, n: int) -> None:
    """Increment a counter file ``n`` times under a :class:`FileLock` (lost updates = bug)."""
    for _ in range(n):
        with FileLock(lock_path):
            p = Path(counter_path)
            cur = int(p.read_text()) if p.exists() else 0
            p.write_text(str(cur + 1))
