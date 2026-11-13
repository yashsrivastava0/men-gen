"""E2E tests: the owner's journey and the fake app, offline, in private data folders."""

from __future__ import annotations

import importlib.util
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mde_core.settings import load_settings
from mde_spec_agent.api import create_app
from mde_spec_agent.config import load_spec_settings
from mde_spec_agent.service import SpecService

HERE = Path(__file__).resolve().parent
INPUTS = Path(__file__).resolve().parents[4] / "docs" / "compass" / "research-inputs"


@pytest.fixture(autouse=True)
def _private_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_ENV_FILE", "")
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "no-research"))


def load_fake() -> ModuleType:
    spec = importlib.util.spec_from_file_location("spec_fake_app_under_test", HERE / "fake_app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_module() -> ModuleType:
    return load_fake()


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[..., Iterator[TestClient]]:
    """``with make_client(inputs_dir=...) as client``: the real app over the real service, mock mode, private folder."""
    from contextlib import contextmanager

    @contextmanager
    def build(extra_inputs: Path | None = None) -> Iterator[TestClient]:
        kw: dict[str, Any] = {"spec_data_dir": tmp_path / "data"}
        if extra_inputs is not None:
            kw["spec_input_dirs"] = str(extra_inputs)
        settings = load_spec_settings(**kw)
        core = load_settings(llm_mode="mock", openai_api_key=None, anthropic_api_key=None)
        service = SpecService(settings, core, research_runs_dir=tmp_path / "no-research")
        with TestClient(create_app(service, settings=settings)) as client:
            yield client

    return build  # type: ignore[return-value]


def wait_for(
    client: TestClient, run_id: str, until: tuple[str, ...], timeout: float = 90.0
) -> dict[str, Any]:
    end = time.monotonic() + timeout
    snap: dict[str, Any] = {}
    while time.monotonic() < end:
        snap = client.get(f"/api/runs/{run_id}").json()
        if snap["status"] in until:
            return snap
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} stayed {snap.get('status')} ({snap.get('note')})")


@pytest.fixture
def waiter() -> Callable[..., dict[str, Any]]:
    return wait_for
