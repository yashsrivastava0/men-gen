"""The interactive docs are unauthenticated, so they are off unless ``API_DOCS=1`` (security review),
while the OpenAPI document itself keeps working offline (``scripts/export_schemas.py``)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from mde_context_api import __main__ as entry
from mde_context_api.main import create_app, docs_enabled
from mde_context_api.settings import ApiSettings
from mde_context_api.testing import make_settings

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


async def fetch(app: Any, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)  # deliberately without credentials


@pytest.fixture(autouse=True)
def _no_docs_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_DOCS", raising=False)


@pytest.mark.parametrize("path", DOC_PATHS)
async def test_the_docs_are_not_served_by_default(path: str) -> None:
    assert (await fetch(create_app(), path)).status_code == 404


@pytest.mark.parametrize("path", DOC_PATHS)
async def test_the_docs_are_not_served_for_settings_that_do_not_ask(
    path: str, tmp_path: Path
) -> None:
    assert (await fetch(create_app(make_settings(tmp_path)), path)).status_code == 404


@pytest.mark.parametrize("path", DOC_PATHS)
async def test_the_docs_are_served_when_the_settings_ask(path: str, tmp_path: Path) -> None:
    resp = await fetch(create_app(make_settings(tmp_path, api_docs=True)), path)
    assert resp.status_code == 200


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
async def test_the_environment_switches_them_on_when_there_are_no_settings(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_DOCS", value)
    assert docs_enabled(None)
    resp = await fetch(create_app(), "/openapi.json")
    assert resp.status_code == 200 and resp.json()["info"]["title"] == "Context Engine API"


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_anything_else_leaves_them_off(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_DOCS", value)
    assert not docs_enabled(None)


def test_the_setting_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not ApiSettings().api_docs
    monkeypatch.setenv("API_DOCS", "1")
    assert ApiSettings().api_docs


def test_the_schema_is_still_available_offline_with_the_docs_off() -> None:
    app = create_app()
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    doc = app.openapi()  # what scripts/export_schemas.py writes to docs/api/openapi.json
    assert "/v1/sessions" in doc["paths"] and doc["info"]["title"] == "Context Engine API"


def test_the_docs_flag_reaches_the_reload_child_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_DOCS", "0")  # restored afterwards, whatever main() does to it
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(entry, "configure_logging", lambda *a, **k: None)
    assert entry.main(["--docs"]) == 0
    assert os.environ["API_DOCS"] == "1"
    monkeypatch.setenv("API_DOCS", "0")
    assert entry.main([]) == 0
    assert os.environ["API_DOCS"] == "0"  # off stays off: nothing turns it on by itself
