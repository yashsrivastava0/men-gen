"""Shared fixtures of the view tests: the three real exports derived, assembled and checked once per session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import read_export
from mde_spec_agent.derive import derive_graph
from mde_spec_agent.derive.pipeline import Derived
from mde_spec_agent.views.screen import mock_profiles
from mde_spec_core.check import CheckReport, spec_check
from mde_spec_core.config import default_config
from mde_spec_core.pack import Pack, assemble
from mde_spec_core.profiles import ConsumerProfile, load_profiles
from mde_spec_core.rcode import run_rcode

ROOT = Path(__file__).resolve().parents[4]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
THREE = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
}


@dataclass
class Built:
    derived: Derived
    pack: Pack
    check: CheckReport
    mocks: dict[str, dict[str, Any]]
    rcode_rows: list[dict[str, str]]

    @property
    def graph(self):
        return self.derived.graph


@cache
def build(key: str) -> Built:
    path = EXPORTS / f"{THREE[key]}.json"
    if not path.exists():
        pytest.skip(f"export {key} is not in the repository")
    derived = derive_graph(read_export(json.loads(path.read_text(encoding="utf-8"))))
    log = run_rcode(derived.graph, derived.facts)
    pack = assemble(derived.graph)
    check = spec_check(derived.graph, pack, rcode_log=log)
    return Built(derived, pack, check, mock_profiles(derived.graph), log.rows())


@pytest.fixture(scope="session", params=sorted(THREE))
def built(request: pytest.FixtureRequest) -> Built:
    return build(request.param)


@pytest.fixture(scope="session")
def quick() -> Built:
    return build("quick")


@pytest.fixture(scope="session")
def thorough() -> Built:
    return build("thorough")


@pytest.fixture(scope="session")
def writer() -> ConsumerProfile:
    return load_profiles().get("weak-default")


@pytest.fixture(scope="session")
def config():
    return default_config()


_ID_KEYS = {
    "id",
    "screen_id",
    "to",
    "from",
    "content_hash",
    "example_id",
    "rule",
    "source",
    "check",
    "code",
    "key",
}


def owner_strings(obj: Any, skip: set[str] | None = None) -> list[str]:
    """Every string a person reads in a view: all string values except machine keys (ids, hashes, links, codes)."""
    skip = _ID_KEYS | (skip or set())
    out: list[str] = []

    def walk(x: Any, key: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str) and key not in skip:
            out.append(x)

    walk(obj)
    return out


@pytest.fixture(scope="session")
def strings_of():
    return owner_strings
