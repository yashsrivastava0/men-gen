"""Terminal output: one small sink so every command prints the same way.

Plain text first. ``rich`` only adds colour when the stream is a terminal; markup and highlighting
are off, so text that looks like markup (``[REDACTED]``, a user's own brackets) is printed as is, and
long lines are never hard-wrapped (a transcript saved to a file stays one line per sentence).
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterable
from typing import TextIO

from rich.console import Console


def harden_stdio() -> None:
    """Never crash on a character the console cannot encode (Hindi text on a legacy Windows code
    page, for example): replace it instead."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):  # a closed or exotic stream
                reconfigure(errors="replace")


class Out:
    """Where a command writes. ``stream`` defaults to the CURRENT ``sys.stdout`` (so a test runner
    that swaps it is respected)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def _console(self) -> Console:
        return Console(
            file=self._stream or sys.stdout,
            markup=False,
            highlight=False,
            emoji=False,
            soft_wrap=True,
        )

    @property
    def interactive(self) -> bool:
        stream = self._stream or sys.stdout
        return bool(getattr(stream, "isatty", lambda: False)())

    def line(self, text: str = "", style: str | None = None) -> None:
        self._console().print(text, style=style, soft_wrap=True, markup=False, highlight=False)

    def lines(self, lines: Iterable[str], style: str | None = None) -> None:
        for text in lines:
            self.line(text, style)

    def error(self, text: str) -> None:
        Console(file=sys.stderr, markup=False, highlight=False, emoji=False, soft_wrap=True).print(
            text, style="red", soft_wrap=True, markup=False, highlight=False
        )
