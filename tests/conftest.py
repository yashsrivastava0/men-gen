"""Cross-package test configuration: make the repository root importable.

``--import-mode=importlib`` adds nothing to ``sys.path``, and ``evals`` (the shared in-process
harness, also the home of ``python -m evals.runners...``) lives at the repository root.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
