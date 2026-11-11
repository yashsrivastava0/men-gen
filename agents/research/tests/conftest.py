"""Research tests: put ``tests/`` on the import path so the doubles in ``tests/support/`` import as ``support.*``.

The doubles (fake search and fetch, the in-memory store, scripted gateway) live ONLY in the test tree: the app's
composition root cannot select them (``tests/test_app_is_live_only.py`` checks that).
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = str(Path(__file__).resolve().parent)
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)
