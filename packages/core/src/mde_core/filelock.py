"""Portable inter-process file lock (D-004: ``fcntl.flock`` does not exist on Windows).

Used by the budget ledger so parallel processes and subagents share one truth.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from types import TracebackType
from typing import IO

if os.name == "nt":  # pragma: no cover - exercised on Windows only
    import msvcrt
else:  # pragma: no cover - exercised on POSIX only
    import fcntl


class LockTimeout(TimeoutError):
    pass


class FileLock:
    """Exclusive advisory lock on ``<path>`` (the file is created if missing).

    Usage::

        with FileLock(ledger_path.with_suffix(".lock")):
            ...  # critical section

    The lock is re-entrant per instance only; use one instance per critical section.
    """

    def __init__(self, path: str | Path, *, timeout: float = 30.0, poll: float = 0.02) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.poll = poll
        self._fh: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Unbuffered: a refused write must not leave a pending byte that a later flush or close
        # would retry (and fail) behind our back.
        fh = open(self.path, "a+b", buffering=0)  # noqa: SIM115 - kept open while the lock is held
        if os.name == "nt":  # lock byte 0; make sure the file has at least one byte
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                # Another process may have seen the same empty file, written the byte first and now
                # hold the lock on it: Windows refuses writes into a locked range. The byte then
                # exists, so there is nothing left to create; the retry loop waits for the lock.
                with contextlib.suppress(PermissionError):
                    fh.write(b"\0")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._try_lock(fh)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise LockTimeout(f"could not lock {self.path} in {self.timeout}s") from exc
                time.sleep(self.poll)
        self._fh = fh

    @staticmethod
    def _try_lock(fh: IO[bytes]) -> None:
        if os.name == "nt":  # pragma: no cover
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        fh = self._fh
        if fh is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
            self._fh = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
