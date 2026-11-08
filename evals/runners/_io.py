"""Console output for the runners (they are command-line tools; stdout is their interface)."""

from __future__ import annotations

import sys


def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def warn(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()
