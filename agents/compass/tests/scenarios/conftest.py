from __future__ import annotations

import pytest

from mde_compass.scenarios.runner import new_service


@pytest.fixture(autouse=True)
def _engine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment switch for the question engine must not change which engine a test asks for."""
    monkeypatch.delenv("COMPASS_INTERVIEW_ENGINE", raising=False)


@pytest.fixture
def service():  # type: ignore[no-untyped-def]
    """The real service in mock mode over the in-memory store (no database, no network), on a fake clock.
    The legacy question engine, the default until the lead flips it (use ``new_service("planner")`` otherwise)."""
    return new_service("legacy")
