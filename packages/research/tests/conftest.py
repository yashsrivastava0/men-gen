"""Make the test helpers importable (tests run with ``--import-mode=importlib``)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
