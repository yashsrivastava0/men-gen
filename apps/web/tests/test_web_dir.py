"""Where the Compass dev server finds the console: ``web_dir()``, ``MDE_WEB_DIR`` and the startup check."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from mde_compass.app import STATIC_DIR, web_dir


def test_the_default_is_the_console_in_this_repository(console_dir: Path, repo_root: Path) -> None:
    assert web_dir() == console_dir == repo_root / "apps" / "web" / "console"
    assert (web_dir() / "index.html").is_file()
    assert web_dir() == STATIC_DIR, "the module-level alias equals the default"


def test_mde_web_dir_with_an_absolute_path_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MDE_WEB_DIR", str(tmp_path))
    assert web_dir() == tmp_path


def test_a_relative_mde_web_dir_is_resolved_against_the_repo_root_not_the_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo_root: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MDE_WEB_DIR", "apps/web/console")
    assert web_dir() == repo_root / "apps" / "web" / "console"
    assert (web_dir() / "index.html").is_file()


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_mde_web_dir_means_the_default(
    monkeypatch: pytest.MonkeyPatch, blank: str, console_dir: Path
) -> None:
    monkeypatch.setenv("MDE_WEB_DIR", blank)
    assert web_dir() == console_dir


def test_web_dir_is_read_each_time_so_the_variable_can_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, console_dir: Path
) -> None:
    assert web_dir() == console_dir
    monkeypatch.setenv("MDE_WEB_DIR", str(tmp_path))
    assert web_dir() == tmp_path
    monkeypatch.delenv("MDE_WEB_DIR")
    assert web_dir() == console_dir


async def test_the_app_serves_the_directory_that_mde_web_dir_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, build_app: Callable[[], FastAPI]
) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Other console</title>", "utf-8")
    (tmp_path / "extra.js").write_text("// extra\n", "utf-8")
    monkeypatch.setenv("MDE_WEB_DIR", str(tmp_path))
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert "Other console" in (await c.get("/ui/")).text
        assert (await c.get("/ui/extra.js")).status_code == 200
        assert (await c.get("/ui/shared/shell.js")).status_code == 404, "only that directory"
        r = await c.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/ui/"
        assert "script-src 'self'" in (await c.get("/ui/")).headers["content-security-policy"]


def test_a_missing_directory_fails_at_app_creation_with_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, build_app: Callable[[], FastAPI]
) -> None:
    gone = tmp_path / "no-such-console"
    monkeypatch.setenv("MDE_WEB_DIR", str(gone))
    with pytest.raises(FileNotFoundError) as err:
        build_app()
    message = str(err.value)
    assert str(gone) in message and "does not exist" in message and "MDE_WEB_DIR" in message


def test_a_directory_without_index_html_fails_at_app_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, build_app: Callable[[], FastAPI]
) -> None:
    (tmp_path / "app.js").write_text("// no page\n", "utf-8")
    monkeypatch.setenv("MDE_WEB_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError) as err:
        build_app()
    message = str(err.value)
    assert str(tmp_path) in message and "index.html" in message and "MDE_WEB_DIR" in message


def test_a_file_where_the_directory_should_be_fails_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, build_app: Callable[[], FastAPI]
) -> None:
    not_a_dir = tmp_path / "index.html"
    not_a_dir.write_text("<!doctype html>", "utf-8")
    monkeypatch.setenv("MDE_WEB_DIR", str(not_a_dir))
    with pytest.raises(FileNotFoundError):
        build_app()


def test_the_agent_package_no_longer_ships_a_ui(repo_root: Path) -> None:
    package = repo_root / "agents" / "compass" / "src" / "mde_compass"
    assert not (package / "static_ui").exists()
    assert not list(package.rglob("*.html")) and not list(package.rglob("*.css"))
    assert not list(package.rglob("*.js"))
