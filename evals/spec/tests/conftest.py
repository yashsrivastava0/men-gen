"""Proof-tool tests: make the repository root importable (``evals`` lives there; importlib mode adds nothing)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[3])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from evals.spec import packs  # noqa: E402
from evals.spec.common import make_proof_runner  # noqa: E402
from mde_spec_core.synth import make_graph  # noqa: E402


@pytest.fixture(scope="session")
def quick() -> packs.ProofPack:
    return packs.baseline("quick")


@pytest.fixture(scope="session")
def standard() -> packs.ProofPack:
    return packs.baseline("standard")


@pytest.fixture(scope="session")
def thorough() -> packs.ProofPack:
    return packs.baseline("thorough")


@pytest.fixture(scope="session")
def tiny() -> packs.ProofPack:
    """A pack small enough for the whole-pack arm (it fits `whole_pack_tokens`)."""
    return packs.from_graph("tiny", make_graph("tiny"))


@pytest.fixture(scope="session")
def small_graph():
    return make_graph("small")


@pytest.fixture
def runner(tmp_path):
    return make_proof_runner(tmp_path / "runner")
