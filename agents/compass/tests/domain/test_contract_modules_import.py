"""Every shared contract module imports cleanly and exposes what the other agents rely on."""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "mde_compass.config",
    "mde_compass.budget",
    "mde_compass.domain",
    "mde_compass.store.ports",
    "mde_compass.engine.ports",
    "mde_compass.calls.base",
    "mde_compass.views.models",
    "mde_compass.api.schemas",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_every_call_has_a_profile_and_a_port() -> None:
    from mde_compass.config import CALL_PROFILES
    from mde_compass.engine.ports import Ports

    assert (
        len(CALL_PROFILES) == 17
    )  # twelve from the first build + five for the AI-planned interview
    assert all(cid.startswith("cmp.") for cid in CALL_PROFILES)
    assert len(Ports.__dataclass_fields__) == 17  # type: ignore[attr-defined]


def test_view_model_sections_match_the_prompt() -> None:
    from mde_compass.views.models import SECTION_IDS

    assert len(SECTION_IDS) == 15 and SECTION_IDS[0] == "idea" and SECTION_IDS[-1] == "cannot_build"
