"""API tests: a service in a temporary data folder over the gateway's mock mode (no key, no network, no cost).

Every test gets a private data folder, so nothing lands in ``agents/spec/.runtime``. The app is driven through
``TestClient`` used as a context manager (the run tasks live on its event loop between requests).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mde_core.settings import load_settings
from mde_spec_agent.api import create_app
from mde_spec_agent.config import load_spec_settings
from mde_spec_agent.service import SpecService

INPUTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"
DONE = ("awaiting_g2", "paused_g15", "approved", "failed", "partial")


@pytest.fixture(autouse=True)
def _private_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_ENV_FILE", "")  # no dotenv file is read
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "no-research"))


def make_core(**kw: Any) -> Any:
    return load_settings(llm_mode="mock", openai_api_key=None, anthropic_api_key=None, **kw)


@pytest.fixture
def make_service(tmp_path: Path) -> Callable[..., SpecService]:
    def build(**kw: Any) -> SpecService:
        settings_kw = {k: kw.pop(k) for k in list(kw) if k.startswith("spec_")}
        settings = load_spec_settings(spec_data_dir=tmp_path / "data", **settings_kw)
        return SpecService(settings, make_core(), research_runs_dir=tmp_path / "no-research", **kw)

    return build


@pytest.fixture
def service(make_service: Callable[..., SpecService]) -> SpecService:
    return make_service()


@pytest.fixture
def client(service: SpecService) -> Iterator[TestClient]:
    with TestClient(create_app(service, settings=service.settings)) as c:
        yield c


@pytest.fixture
def wait() -> Callable[..., dict[str, Any]]:
    def go(
        client: TestClient, run_id: str, until: tuple[str, ...] = DONE, timeout: float = 90.0
    ) -> dict[str, Any]:
        end = time.monotonic() + timeout
        snap: dict[str, Any] = {}
        while time.monotonic() < end:
            snap = client.get(f"/api/runs/{run_id}").json()
            if snap["status"] in until:
                return snap
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} stayed {snap.get('status')} ({snap.get('note')})")

    return go


@pytest.fixture
def start(client: TestClient, wait: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Start a run and wait until it stops moving; returns the snapshot."""

    def go(compass_id: str = "synth:tiny", **kw: Any) -> dict[str, Any]:
        body = {"compass_id": compass_id, "offline": True, **kw}
        res = client.post("/api/runs", json=body)
        assert res.status_code in (200, 202), res.text
        return wait(client, res.json()["run_id"])

    return go


class PacedService(SpecService):
    """A service that waits before every step so a stop request can arrive mid-run (test only)."""

    pace = 0.25

    async def _before_step(self, ctx: Any, name: str) -> None:
        import asyncio

        await asyncio.sleep(self.pace)


@pytest.fixture
def make_paced(tmp_path: Path) -> Callable[..., PacedService]:
    def build(data: str = "data", pace: float = 0.25) -> PacedService:
        settings = load_spec_settings(spec_data_dir=tmp_path / data)
        svc = PacedService(settings, make_core(), research_runs_dir=tmp_path / "no-research")
        svc.pace = pace
        return svc

    return build
