"""Repository-root resolution, the git lookup, and secret-safe Settings (hardening M4, low items)."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from mde_core import paths
from mde_core.logging import configure_logging, get_logger, register_secrets
from mde_core.paths import anchor_to_main_repo, git_executable, main_repo_root
from mde_core.settings import Settings, describe_presence

DB_PASSWORD = "S3cret" + "Passw0rd"  # synthetic
DB_URL = f"postgresql+psycopg://appuser:{DB_PASSWORD}@db.internal/prod"


# ---------------------------------------------------------------- main repository root
def _fake_repo(root: Path) -> tuple[Path, Path]:
    """``root`` is a main checkout with one linked worktree; returns (main, worktree)."""
    main = root / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = root / "wt"
    (worktree / "pkg" / "src").mkdir(parents=True)
    gitdir = main / ".git" / "worktrees" / "wt"
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return main, worktree


def test_a_worktree_resolves_to_the_main_checkout(tmp_path: Path) -> None:
    main, worktree = _fake_repo(tmp_path)
    assert main_repo_root(main) == main.resolve()
    assert main_repo_root(worktree) == main.resolve()
    assert main_repo_root(worktree / "pkg" / "src") == main.resolve()


def test_relative_paths_anchor_to_the_main_checkout_from_any_worktree_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, worktree = _fake_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    expected = main.resolve() / "data" / ".budget" / "ledger.jsonl"
    for start in (main, worktree, worktree / "pkg"):
        assert anchor_to_main_repo("data/.budget/ledger.jsonl", start=start) == expected
    absolute = tmp_path / "abs" / "ledger.jsonl"
    assert anchor_to_main_repo(absolute, start=worktree) == absolute


def test_outside_any_repository_the_cwd_is_the_last_resort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    monkeypatch.chdir(lonely)
    monkeypatch.setattr(paths, "main_repo_root", lambda start=None: None)
    assert anchor_to_main_repo("data/x.jsonl") == lonely / "data" / "x.jsonl"


def test_this_checkout_resolves_to_a_real_repository_root() -> None:
    root = main_repo_root()
    assert root is not None and (root / ".git").exists()


# ---------------------------------------------------------------- git lookup
def test_git_is_found_on_path_only_never_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "git.exe" if os.name == "nt" else "git"
    planted = tmp_path / "cwd"
    planted.mkdir()
    (planted / name).write_text("not git", encoding="utf-8")
    real = tmp_path / "bin"
    real.mkdir()
    (real / name).write_text("#!/bin/sh\n", encoding="utf-8")
    (real / name).chmod(0o755)
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", str(planted.name), str(real)]))
    git_executable.cache_clear()
    try:
        found = git_executable()
        assert found == str(real / name)
        assert found is not None and Path(found).is_absolute()
        monkeypatch.setenv("PATH", os.pathsep.join(["", "."]))
        git_executable.cache_clear()
        assert git_executable() is None  # '.' and '' are the current directory: ignored
    finally:
        git_executable.cache_clear()


# ---------------------------------------------------------------- Settings stay secret-safe
def test_settings_repr_and_dumps_never_show_the_database_password() -> None:
    settings = Settings(_env_file=None, database_url=DB_URL, openai_api_key="sk-" + "x" * 30)  # type: ignore[call-arg]
    assert DB_PASSWORD not in repr(settings)
    assert DB_PASSWORD not in str(settings)
    assert DB_PASSWORD not in str(settings.model_dump())
    assert DB_PASSWORD not in settings.model_dump_json()
    assert "x" * 30 not in repr(settings) + settings.model_dump_json()  # the key was always masked
    assert settings.database_url == DB_URL  # consumers still read it directly
    assert describe_presence(settings)["DATABASE_URL"] == "set"  # the report is unaffected


def test_the_database_password_is_scrubbed_from_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, database_url=DB_URL)  # type: ignore[call-arg]
    assert DB_PASSWORD in settings.secret_values()
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)
    register_secrets(settings.secret_values())
    log = get_logger("hardening")
    log.info("db", url=settings.database_url, password=DB_PASSWORD)
    log.info("conn", dsn="postgresql://bob:hunter22@host/db")
    out = buffer.getvalue()
    assert DB_PASSWORD not in out and "hunter22" not in out


def test_a_url_without_a_password_adds_no_secret_value() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u@h/db")  # type: ignore[call-arg]
    assert settings.secret_values() == [settings.api_dev_token.get_secret_value()]


# ---------------------------------------------------------------- shared-data paths and cassette settings
def test_projects_dir_and_cassette_dir_anchor_to_the_main_checkout_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = main_repo_root()
    assert root is not None
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # a different cwd must not matter
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.projects_dir == root / "data" / "projects"
    assert settings.cassette_dir == root / "tests" / "cassettes"
    assert settings.llm_budget_ledger == root / "data" / ".budget" / "ledger.jsonl"


def test_relative_overrides_are_anchored_and_absolute_ones_are_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = main_repo_root()
    assert root is not None
    monkeypatch.setenv("PROJECTS_DIR", "out/handoff")
    monkeypatch.setenv("CASSETTE_DIR", "my/cassettes")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.projects_dir == root / "out" / "handoff"
    assert settings.cassette_dir == root / "my" / "cassettes"
    absolute = tmp_path / "abs"
    monkeypatch.setenv("PROJECTS_DIR", str(absolute / "projects"))
    monkeypatch.setenv("CASSETTE_DIR", str(absolute / "cassettes"))
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.projects_dir == absolute / "projects"
    assert settings.cassette_dir == absolute / "cassettes"
    assert Settings(_env_file=None, projects_dir=tmp_path).projects_dir == tmp_path  # type: ignore[call-arg]


def test_a_worktree_shares_one_projects_dir_with_the_main_checkout(tmp_path: Path) -> None:
    main, worktree = _fake_repo(tmp_path)
    assert anchor_to_main_repo("data/projects", start=worktree) == (
        anchor_to_main_repo("data/projects", start=main)
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("yes", True), ("0", False), ("false", False), ("", False)],
)
def test_record_cassettes_follows_mde_record_cassettes(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    assert Settings(_env_file=None).record_cassettes is False  # type: ignore[call-arg]
    monkeypatch.setenv("MDE_RECORD_CASSETTES", raw)
    assert Settings(_env_file=None).record_cassettes is expected  # type: ignore[call-arg]
    assert Settings(_env_file=None, record_cassettes=True).record_cassettes is True  # type: ignore[call-arg]
