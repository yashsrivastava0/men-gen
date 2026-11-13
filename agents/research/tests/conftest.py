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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _research_data_dir_is_private(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test gets its own ``RESEARCH_DATA_DIR``: the provider-credit ledger (``provider-credits.json``) and the
    other runtime files of a service or an adapter built from the default settings land in a temporary folder, never
    in the owner's ``agents/research/.runtime``."""
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path_factory.mktemp("research-data")))
