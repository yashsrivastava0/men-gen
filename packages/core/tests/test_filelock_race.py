"""The first-acquire race of ``FileLock`` on Windows (found by a flaky full-suite run).

Two processes open the same, still empty lock file. The first writes the lock byte and locks it;
the second then tries to write the same byte, and Windows refuses a write into a locked range with
``PermissionError``. That used to escape ``acquire`` (the write sat outside the retry loop); it
must be a wait for the lock instead.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import IO

import pytest

from mde_core.filelock import FileLock, LockTimeout

pytestmark = pytest.mark.skipif(os.name != "nt", reason="byte-range locks refuse writes on Windows")


def _hold_byte_zero(path: Path) -> IO[bytes]:
    """Lock byte 0 of an EMPTY file from another handle: the state the loser of the race sees."""
    import msvcrt

    fh = open(path, "a+b", buffering=0)  # noqa: SIM115 - held until released by the test
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    return fh


def _release(fh: IO[bytes]) -> None:
    import msvcrt

    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    fh.close()


def test_a_locked_empty_file_is_a_wait_not_a_permission_error(tmp_path: Path) -> None:
    path = tmp_path / "race.lock"
    holder = _hold_byte_zero(path)
    try:
        assert path.stat().st_size == 0  # the byte is not written yet: the racy state
        with pytest.raises(LockTimeout):
            FileLock(path, timeout=0.2, poll=0.01).acquire()
    finally:
        _release(holder)


def test_the_waiter_gets_the_lock_once_the_holder_lets_go(tmp_path: Path) -> None:
    path = tmp_path / "race2.lock"
    holder = _hold_byte_zero(path)
    timer = threading.Timer(0.15, _release, args=(holder,))
    timer.start()
    try:
        lock = FileLock(path, timeout=5.0, poll=0.01)
        lock.acquire()  # waits for the timer, then takes the lock
        lock.release()
    finally:
        timer.join()
