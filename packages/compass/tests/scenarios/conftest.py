from __future__ import annotations

import pytest

from mde_compass.compose import build_service


@pytest.fixture
def service():
    """The real service in mock mode over the in-memory store (no database, no network)."""
    return build_service(memory=True)
