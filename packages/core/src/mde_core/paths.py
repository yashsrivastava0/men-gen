"""Repository-root and tool lookup helpers (standard library only, no subprocess).

* :func:`main_repo_root`: the MAIN checkout of the repository, the same answer as
  ``git rev-parse --git-common-dir`` (its parent) gives, computed from the ``.git`` entries on disk so
  every git worktree and every working directory agree on one location for shared runtime data
  (the budget ledger, the private Postgres cluster).
* :func:`anchor_to_main_repo`: a relative path is resolved against that root, never against the
  current working directory. This is the single helper behind ``LLM_BUDGET_LEDGER``: a second
  worktree or another cwd must not see an empty ledger and a fresh USD 6.
* :func:`git_executable`: the absolute path of ``git``, searched on ``PATH`` only. On Windows a bare
  ``git`` (``subprocess``, ``shutil.which``) is looked up in the current directory first, so a
  planted ``git.exe`` in a checkout would be run.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _common_git_dir(git_entry: Path) -> Path | None:
    """The common git directory behind a ``.git`` directory or worktree pointer file."""
    if git_entry.is_dir():
        return git_entry
    try:
        text = git_entry.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.removeprefix("gitdir:").strip())
    if not gitdir.is_absolute():
        gitdir = git_entry.parent / gitdir
    commondir = gitdir / "commondir"
    try:
        relative = commondir.read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir.resolve()
    return (gitdir / relative).resolve()


def _root_from(start: Path) -> Path | None:
    here = start.resolve()
    for directory in (here, *here.parents):
        entry = directory / ".git"
        if entry.exists():
            common = _common_git_dir(entry)
            if common is None:
                return directory
            return common.parent if common.name == ".git" else directory
    return None


@lru_cache(maxsize=1)
def _default_root() -> Path | None:
    # The code's own location first (it sits inside the checkout, whatever the cwd is); then the
    # cwd (an installed copy running from inside a checkout).
    return _root_from(Path(__file__)) or _root_from(Path.cwd())


def main_repo_root(start: Path | None = None) -> Path | None:
    """The main checkout of the repository containing ``start`` (default: this code), if any."""
    if start is not None:
        return _root_from(start)
    return _default_root()


def anchor_to_main_repo(path: str | Path, *, start: Path | None = None) -> Path:
    """``path`` unchanged when absolute, else resolved against the main repository root.

    Outside any repository the current directory is the last resort (the old behaviour); the
    budget ledger's guard file then still detects a missing or truncated ledger.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    root = main_repo_root(start) or Path.cwd()
    return root / candidate


@lru_cache(maxsize=1)
def git_executable() -> str | None:
    """Absolute path of ``git`` found on ``PATH`` (never the current directory), or ``None``."""
    names = ("git.exe", "git.cmd") if os.name == "nt" else ("git",)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):  # '' and '.' mean "the current directory"
            continue
        for name in names:
            candidate = Path(entry) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None
