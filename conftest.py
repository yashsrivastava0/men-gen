"""Root pytest configuration (brief 7.9, 9.1).

* Autouse guard: every test runs in mock mode with no API key and MDE_FORBID_LIVE=1, so any live
  provider construction raises ``LiveCallForbidden``. Only tests marked ``live`` may call the API,
  and only when ``LLM_MODE=live`` is already set in the OUTER environment.
* If ``mde_persistence.testing_plugin`` exists it is registered (ephemeral Postgres fixtures).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

if importlib.util.find_spec("mde_persistence") is not None and (
    importlib.util.find_spec("mde_persistence.testing_plugin") is not None
):
    pytest_plugins = ["mde_persistence.testing_plugin"]


@pytest.fixture(autouse=True)
def _llm_live_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live"):
        if os.environ.get("LLM_MODE") != "live":
            pytest.skip(
                "live test: LLM_MODE=live must be set in the outer environment "
                "(see docs/runbooks/live-llm-budget.md)"
            )
        monkeypatch.delenv("MDE_FORBID_LIVE", raising=False)
        monkeypatch.delenv("MDE_ENV_FILE", raising=False)  # lets .env supply the key
        return
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("MDE_FORBID_LIVE", "1")
    monkeypatch.setenv("MDE_ENV_FILE", "")  # never read .env in offline tests
