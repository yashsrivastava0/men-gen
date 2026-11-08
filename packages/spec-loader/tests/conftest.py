"""Fixtures for the loader tests.

The sample generator and the renderer live in ``mde_spec_builder`` (a higher layer); the tests of
this package use them as test tools only, the loader itself never imports them.
"""

from __future__ import annotations

import pytest

from mde_contracts.spec import AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex


@pytest.fixture(scope="session")
def spec12() -> AppSpec:
    return make_sample_spec(12)


@pytest.fixture(scope="session")
def text12(spec12: AppSpec) -> str:
    return render_spec(spec12)


@pytest.fixture(scope="session")
def index12(text12: str) -> SpecIndex:
    return SpecIndex.from_text(text12)


@pytest.fixture(scope="session")
def spec50() -> AppSpec:
    return make_sample_spec(50)


@pytest.fixture(scope="session")
def text50(spec50: AppSpec) -> str:
    return render_spec(spec50)


@pytest.fixture(scope="session")
def index50(text50: str) -> SpecIndex:
    return SpecIndex.from_text(text50)
