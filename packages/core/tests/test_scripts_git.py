"""Scripts resolve ``git`` through ``mde_core.paths.git_executable`` (PATH only), never a bare name.

On Windows ``subprocess`` resolves a bare ``git`` in the CURRENT DIRECTORY first, so a ``git.exe``
planted in a checkout would run. ``dev_db.py`` has its own ``find_exe`` (pinned in
``tests/security/test_hardening_static.py``); every other script uses the shared helper.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from mde_core import paths

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
GIT_NAME = "git.exe" if os.name == "nt" else "git"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"script_under_test_{name}", SCRIPTS / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_git_calls(source: str) -> list[int]:
    """Line numbers of calls that start a command with the literal ``"git"``."""
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.List)
            and node.elts
            and isinstance(node.elts[0], ast.Constant)
            and node.elts[0].value == "git"
        ):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("script", sorted(p.name for p in SCRIPTS.glob("*.py")))
def test_no_script_starts_a_command_with_a_bare_git(script: str) -> None:
    source = (SCRIPTS / script).read_text(encoding="utf-8")
    assert _bare_git_calls(source) == [], f"{script} runs a bare 'git' (use git_executable())"


def test_the_scanner_sees_a_bare_git() -> None:
    assert _bare_git_calls("import subprocess\nsubprocess.run(['git', 'status'])\n") == [2]
    assert _bare_git_calls("import subprocess\nsubprocess.run([git, 'status'])\n") == []


def test_docs_check_runs_the_git_that_git_executable_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs_check = _load("docs_check.py")
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="README.md\n", stderr="")

    monkeypatch.setattr(docs_check, "git_executable", lambda: "/abs/path/to/git")
    monkeypatch.setattr(docs_check.subprocess, "run", fake_run)
    docs_check.files()
    assert seen and seen[0][0] == "/abs/path/to/git"


def test_docs_check_never_runs_a_git_planted_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_check = _load("docs_check.py")
    planted = tmp_path / GIT_NAME
    planted.write_text("not git", encoding="utf-8")
    planted.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative/bin"]))
    paths.git_executable.cache_clear()
    ran: list[object] = []
    monkeypatch.setattr(docs_check.subprocess, "run", lambda *a, **k: ran.append(a))
    try:
        with pytest.raises(SystemExit, match="git was not found on PATH"):
            docs_check.files()
    finally:
        paths.git_executable.cache_clear()
    assert ran == []  # nothing was executed at all


def test_docs_check_still_lists_the_repository_files() -> None:
    docs_check = _load("docs_check.py")
    if paths.git_executable() is None:
        pytest.skip("git is not installed")
    listed = {p.name for p in docs_check.files()}
    assert {"docs_check.py", "CLAUDE.md"} <= listed
