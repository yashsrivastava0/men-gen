"""Verify tests: put ``tests/`` on the import path so the doubles import as ``verify_support.*``."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = str(Path(__file__).resolve().parent)
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import pytest  # noqa: E402

from mde_spec_core.config import default_config  # noqa: E402
from mde_spec_core.graph import SpecGraph  # noqa: E402
from mde_spec_core.pack import Pack, assemble  # noqa: E402
from mde_spec_core.synth import make_graph  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return default_config()


@pytest.fixture(scope="session")
def big_graph() -> SpecGraph:
    return make_graph("big")


@pytest.fixture(scope="session")
def big_pack(big_graph: SpecGraph) -> Pack:
    return assemble(big_graph)
