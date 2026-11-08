"""Hand-off adapters: the local git project repo and a no-op frontend engine.

``LocalGitProjectRepo`` implements ``mde_contracts.ports.ProjectRepoPort``: it writes the approved
``APP_SPEC.md`` and ``spec.lock.json`` into ``<projects_dir>/<tenant>/<app_id>/`` and commits.

* The repo is created on first use (``git init``); nothing depends on the user's git config: the
  author/committer identity, config files, hooks, signing and line-ending conversion are all fixed
  through the environment and ``-c`` flags, and commits use ``--no-verify``.
* Idempotent for the same ``(version, hash)``: each commit is tagged ``spec-v<version>``; a replay
  returns that commit without writing a new one. The same version with a different hash is a
  ``Conflict`` (an approved version never changes).
* ``tenant_id`` and ``app_id`` are validated as plain names, so a path can never escape
  ``projects_dir`` (``..``, separators, drive letters, reserved Windows names are refused).
* ``APP_SPEC.md`` is written byte-for-byte (UTF-8, no newline conversion) so
  ``compute_spec_hash(file) == spec_hash`` (the loader's blanked-hash rule); ``.gitattributes`` pins that with ``-text``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from mde_contracts.events import SpecReady
from mde_contracts.ports import CommitInfo
from mde_core.errors import ConfigError, Conflict
from mde_core.filelock import FileLock
from mde_flow.errors import HandoffFailed

SPEC_FILE = "APP_SPEC.md"
LOCK_FILE = "spec.lock.json"
_ATTRIBUTES = f"{SPEC_FILE} -text\n{LOCK_FILE} -text\n"
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")  # used with fullmatch
_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
_GIT_TIMEOUT_SECONDS = 60


def validate_name(value: str, what: str) -> str:
    """Accept only plain directory names; refuse anything that could leave ``projects_dir``.

    ``fullmatch``, not ``match`` with ``$``: ``$`` also matches before a trailing newline, and a
    name with a newline in it is not a plain name."""
    stem = value.split(".", 1)[0].lower()
    if (
        _SAFE_NAME.fullmatch(value) is None
        or value.endswith(".")
        or ".." in value
        or stem in _RESERVED
    ):
        raise ConfigError(f"unsafe {what}: {value!r}", details={"what": what})
    return value


def assert_no_case_collision(parent: Path, name: str, what: str) -> None:
    """Refuse a name that differs from an existing sibling only by case.

    On a case-insensitive file system (Windows, macOS by default) ``Acme`` and ``acme`` are the same
    directory: two tenants would share a repository. Names are compared case-folded."""
    if not parent.is_dir():
        return
    folded = name.casefold()
    for entry in os.listdir(parent):
        if entry != name and entry.casefold() == folded:
            raise ConfigError(
                f"{what} {name!r} collides with the existing {entry!r} (names are compared "
                "without regard to case)",
                details={"what": what},
            )


class LocalGitProjectRepo:
    """``ProjectRepoPort`` on a local git repo per app."""

    def __init__(
        self,
        projects_dir: str | Path,
        *,
        git_executable: str | None = None,
        author_name: str = "Mobigen Context Engine",
        author_email: str = "context-engine@mobigen.invalid",
    ) -> None:
        self.root = Path(projects_dir)
        git = git_executable or shutil.which("git")
        if git is None:
            raise ConfigError("git is not installed or not on PATH")
        self._git = git
        self._name = author_name
        self._email = author_email

    # ------------------------------------------------------------------ public

    def repo_dir(self, tenant_id: str, app_id: str) -> Path:
        validate_name(tenant_id, "tenant_id")
        validate_name(app_id, "app_id")
        tenant_dir = self.root / tenant_id
        path = tenant_dir / app_id
        # The names are plain (no separators, no ``..``), so a path can only lead out of the projects
        # directory through a link planted inside it: refuse a symlink or a Windows junction at
        # either level. (Comparing ``resolve()`` results is not safe here: while another commit is
        # creating these directories, Windows resolution can fall back to a short-name form.)
        for part in (tenant_dir, path):
            if part.is_symlink() or part.is_junction():
                raise ConfigError("project path escapes the projects directory")
        return path

    async def commit_spec(
        self,
        *,
        tenant_id: str,
        app_id: str,
        spec_text: str,
        version: int,
        spec_hash: str,
        message: str,
    ) -> CommitInfo:
        repo = self.repo_dir(tenant_id, app_id)
        return await asyncio.to_thread(
            self._commit_sync, repo, spec_text, version, spec_hash, message
        )

    # ------------------------------------------------------------------ git plumbing

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            {
                "GIT_AUTHOR_NAME": self._name,
                "GIT_AUTHOR_EMAIL": self._email,
                "GIT_COMMITTER_NAME": self._name,
                "GIT_COMMITTER_EMAIL": self._email,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return env

    def _run(
        self, repo: Path, *args: str, check: bool = True, hooks: Path | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        flags = [
            "-c", "core.autocrlf=false",
            "-c", "core.safecrlf=false",
            "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false",
            "-c", "init.defaultBranch=main",
        ]  # fmt: skip
        if hooks is not None:
            flags += ["-c", f"core.hooksPath={hooks.as_posix()}"]
        cmd = [self._git, *flags, *args]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed executable, validated names, no shell
                cmd,
                cwd=repo,
                env=self._env(),
                capture_output=True,
                check=False,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HandoffFailed(f"git could not run: {type(exc).__name__}") from exc
        if check and proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
            raise HandoffFailed(
                f"git {args[0]} failed", details={"code": proc.returncode, "stderr": " ".join(tail)}
            )
        return proc

    def _commit_sync(
        self, repo: Path, spec_text: str, version: int, spec_hash: str, message: str
    ) -> CommitInfo:
        assert_no_case_collision(repo.parent.parent, repo.parent.name, "tenant_id")
        assert_no_case_collision(repo.parent, repo.name, "app_id")
        repo.mkdir(parents=True, exist_ok=True)
        with FileLock(repo.parent / f".{repo.name}.lock"):
            if not (repo / ".git").exists():
                self._run(repo, "init")
            hooks = repo / ".git" / "mde-no-hooks"
            hooks.mkdir(exist_ok=True)
            tag = f"spec-v{int(version)}"
            existing = self._tagged_commit(repo, tag, hooks)
            if existing is not None:
                self._assert_same_version(repo, existing, version, spec_hash, hooks)
                return CommitInfo(sha=existing, path=SPEC_FILE, repo_dir=str(repo))

            (repo / SPEC_FILE).write_bytes(spec_text.encode("utf-8"))
            lock = json.dumps({"version": version, "hash": spec_hash}, indent=2, sort_keys=True)
            (repo / LOCK_FILE).write_bytes((lock + "\n").encode("utf-8"))
            (repo / ".gitattributes").write_bytes(_ATTRIBUTES.encode("utf-8"))
            self._run(repo, "add", "--", SPEC_FILE, LOCK_FILE, ".gitattributes", hooks=hooks)
            dirty = self._run(repo, "diff", "--cached", "--quiet", check=False, hooks=hooks)
            if dirty.returncode == 1:
                body = f"{message}\n\nSpec-Version: {version}\nSpec-Hash: {spec_hash}\n"
                self._run(repo, "commit", "--no-verify", "-m", body, hooks=hooks)
            elif dirty.returncode != 0:
                raise HandoffFailed("git diff failed", details={"code": dirty.returncode})
            sha = self._run(repo, "rev-parse", "HEAD", hooks=hooks).stdout.decode().strip()
            self._run(repo, "tag", tag, sha, hooks=hooks)
            return CommitInfo(sha=sha, path=SPEC_FILE, repo_dir=str(repo))

    def _tagged_commit(self, repo: Path, tag: str, hooks: Path) -> str | None:
        proc = self._run(
            repo,
            "rev-parse",
            "-q",
            "--verify",
            f"refs/tags/{tag}^{{commit}}",
            check=False,
            hooks=hooks,
        )
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    def _assert_same_version(
        self, repo: Path, sha: str, version: int, spec_hash: str, hooks: Path
    ) -> None:
        raw = self._run(repo, "show", f"{sha}:{LOCK_FILE}", hooks=hooks).stdout
        lock = json.loads(raw.decode("utf-8"))
        if lock.get("version") != version or lock.get("hash") != spec_hash:
            raise Conflict(
                f"spec version {version} is already committed with a different hash",
                details={"version": version},
            )


class NoopFrontendEngine:
    """``FrontendEnginePort`` that only records what it was told (the real engine comes later)."""

    def __init__(self) -> None:
        self.notifications: list[SpecReady] = []

    async def notify(self, event: SpecReady) -> None:
        self.notifications.append(event)
