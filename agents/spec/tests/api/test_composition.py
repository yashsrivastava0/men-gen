"""The real composition root cannot select a test double: nothing in the app imports a test helper, ``create_app()``
without a service builds the real ``SpecService``, and the service has no constructor argument that swaps the gateway."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from fastapi.testclient import TestClient

import mde_spec_agent
from mde_core.settings import load_settings
from mde_spec_agent.api import create_app
from mde_spec_agent.api.main import main as serve_main
from mde_spec_agent.config import load_spec_settings
from mde_spec_agent.service import SpecService, build_service

PKG = Path(mde_spec_agent.__file__).parent
APP_FILES = [*sorted((PKG / "api").glob("*.py")), PKG / "compose.py", PKG / "service.py"]
DOUBLE_WORDS = ("testing", "tests", "fake", "fakes", "support", "double", "stub", "scripted")


def imported_modules(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def test_the_app_imports_no_test_helper() -> None:
    assert APP_FILES, "the files to scan were not found"
    for path in APP_FILES:
        for module in imported_modules(path):
            parts = module.split(".")
            assert not (set(parts) & set(DOUBLE_WORDS)), f"{path.name} imports {module}"
        text = path.read_text(encoding="utf-8")
        assert "fake_app" not in text and "agents/spec/tests" not in text, path.name


def test_the_service_cannot_be_given_a_gateway_or_a_double() -> None:
    params = set(inspect.signature(SpecService.__init__).parameters) - {"self"}
    assert params == {
        "settings",
        "core",
        "data",
        "profiles",
        "config",
        "research_runs_dir",
        "readiness",
        "clock",
    }
    assert "gateway" not in params and not any(w in p for p in params for w in DOUBLE_WORDS)


def test_create_app_without_a_service_builds_the_real_one(
    tmp_path: Path, monkeypatch: object
) -> None:
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("SPEC_DATA_DIR", str(tmp_path / "real"))
    app = create_app()  # no service: the lifespan composes it
    with TestClient(app) as client:
        svc = app.state.service
        assert type(svc) is SpecService  # exactly the real class, not a subclass
        assert svc.data == tmp_path / "real" and svc.fake is False
        assert client.get("/api/health").json()["fake"] is False
        assert client.get("/api/fake").status_code == 404  # only the test fake app answers it
        assert (tmp_path / "real").is_dir()


def test_build_service_is_the_composition_root() -> None:
    svc = build_service(load_spec_settings(), load_settings())
    assert type(svc) is SpecService


def test_serve_refuses_a_non_loopback_host() -> None:
    assert serve_main(["--host", "0.0.0.0"]) == 2  # noqa: S104 - the point of the test
