"""Property-test settings: deterministic (derandomized, no example database) so the suite never flakes.

``COMPASS_PROPERTIES=thorough`` runs far more examples (see ``scenarios.sessions``); use it before a release, not
in the normal run."""

from __future__ import annotations

import logging

import pytest
import structlog
from hypothesis import HealthCheck, Phase, settings

# A whole interview per example: shrinking a failure would replay hundreds of them, so a failing example is
# reported as it was found (the script is printed) and replayed by hand.
settings.register_profile(
    "compass",
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=list(HealthCheck),
    phases=(Phase.explicit, Phase.generate),
    print_blob=True,
)
settings.load_profile("compass")


@pytest.fixture(autouse=True)
def _quiet_logs() -> None:
    """Hundreds of mock calls per example would drown the report in llm_call lines."""
    logging.disable(logging.CRITICAL)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))


@pytest.fixture(autouse=True)
def _engine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPASS_INTERVIEW_ENGINE", raising=False)
