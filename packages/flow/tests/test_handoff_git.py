"""``LocalGitProjectRepo`` against real temporary git repos, and ``NoopFrontendEngine``."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mde_contracts.events import SpecReady
from mde_core.errors import ConfigError, Conflict
from mde_core.hashing import sha256_hex
from mde_flow.errors import HandoffFailed
from mde_flow.handoff import (
    LOCK_FILE,
    SPEC_FILE,
    LocalGitProjectRepo,
    NoopFrontendEngine,
    validate_name,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

TEXT = "# APP_SPEC\n\nhello é\r\nworld\n"


def git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    out = subprocess.run(
        [shutil.which("git") or "git", "-C", str(repo), *args],
        capture_output=True, check=True, env=env, text=True, encoding="utf-8",
    )  # fmt: skip
    return out.stdout.strip()


async def commit(
    repo: LocalGitProjectRepo,
    *,
    version: int = 1,
    text: str = TEXT,
    tenant: str = "tenant-a",
    app: str = "app_1",
):  # type: ignore[no-untyped-def]
    return await repo.commit_spec(
        tenant_id=tenant, app_id=app, spec_text=text, version=version, spec_hash=sha256_hex(text),
        message=f"Approve spec v{version}",
    )  # fmt: skip


async def test_commit_writes_the_files_and_a_commit(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    info = await commit(repo)
    repo_dir = tmp_path / "tenant-a" / "app_1"
    assert info.repo_dir == str(repo_dir) and info.path == SPEC_FILE
    assert (repo_dir / SPEC_FILE).read_bytes() == TEXT.encode("utf-8")  # byte for byte
    assert sha256_hex((repo_dir / SPEC_FILE).read_bytes()) == sha256_hex(TEXT)
    lock = json.loads((repo_dir / LOCK_FILE).read_text(encoding="utf-8"))
    assert lock == {"version": 1, "hash": sha256_hex(TEXT)}
    assert git(repo_dir, "rev-parse", "HEAD") == info.sha
    assert git(repo_dir, "rev-list", "--count", "HEAD") == "1"
    assert git(repo_dir, "tag", "--list") == "spec-v1"
    assert git(repo_dir, "show", "-s", "--format=%B", "HEAD").startswith("Approve spec v1")
    assert f"Spec-Hash: {sha256_hex(TEXT)}" in git(repo_dir, "show", "-s", "--format=%B", "HEAD")


async def test_the_commit_uses_the_fixed_identity_not_the_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "global.gitconfig"
    cfg.write_text(
        "[user]\n\tname = Somebody Else\n\temail = else@example.com\n"
        "[commit]\n\tgpgsign = true\n[core]\n\thooksPath = /definitely/not/here\n"
        "[init]\n\tdefaultBranch = trunk\n",
        encoding="utf-8",
    )
    for var in ("GIT_CONFIG_GLOBAL",):
        monkeypatch.setenv(var, str(cfg))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Leaked Author")  # inherited GIT_* must not leak in
    repo = LocalGitProjectRepo(
        tmp_path / "projects", author_name="Ctx Engine", author_email="ctx@x.invalid"
    )
    await commit(repo)
    repo_dir = tmp_path / "projects" / "tenant-a" / "app_1"
    fmt = git(repo_dir, "log", "-1", "--format=%an|%ae|%cn|%ce")
    assert fmt == "Ctx Engine|ctx@x.invalid|Ctx Engine|ctx@x.invalid"
    assert git(repo_dir, "symbolic-ref", "--short", "HEAD") == "main"


async def test_hooks_never_run(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    await commit(repo, version=1)
    hook = tmp_path / "tenant-a" / "app_1" / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    if os.name != "nt":
        hook.chmod(0o755)
    await commit(repo, version=2, text=TEXT + "more\n")  # a failing hook would abort this
    assert git(tmp_path / "tenant-a" / "app_1", "rev-list", "--count", "HEAD") == "2"


async def test_idempotent_for_the_same_version_and_hash(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    first = await commit(repo)
    again = await commit(repo)
    assert again.sha == first.sha
    assert git(tmp_path / "tenant-a" / "app_1", "rev-list", "--count", "HEAD") == "1"


async def test_replaying_an_old_version_does_not_revert_the_working_tree(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    v1 = await commit(repo, version=1)
    v2 = await commit(repo, version=2, text=TEXT + "v2\n")
    assert v2.sha != v1.sha
    replay = await commit(repo, version=1)
    assert replay.sha == v1.sha
    repo_dir = tmp_path / "tenant-a" / "app_1"
    assert (repo_dir / SPEC_FILE).read_bytes() == (TEXT + "v2\n").encode("utf-8")
    assert git(repo_dir, "rev-list", "--count", "HEAD") == "2"
    assert git(repo_dir, "tag", "--list").split() == ["spec-v1", "spec-v2"]


async def test_same_version_with_a_different_hash_is_a_conflict(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    await commit(repo)
    with pytest.raises(Conflict):
        await commit(repo, version=1, text=TEXT + "tampered\n")


async def test_apps_and_tenants_get_their_own_repos(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    a = await commit(repo, tenant="t1", app="app_a")
    b = await commit(repo, tenant="t2", app="app_a")
    c = await commit(repo, tenant="t1", app="app_b")
    assert len({a.repo_dir, b.repo_dir, c.repo_dir}) == 3


async def test_concurrent_commits_to_one_repo_are_serialised(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path)
    results = await asyncio.gather(
        *(commit(repo, version=v, text=f"{TEXT}{v}\n") for v in range(1, 6))
    )
    assert len({r.sha for r in results}) == 5
    assert git(tmp_path / "tenant-a" / "app_1", "rev-list", "--count", "HEAD") == "5"


@pytest.mark.parametrize(
    "bad",
    ["", ".", "..", "../x", "a/b", "a\\b", "/abs", "C:evil", "con", "NUL", "com1.txt", "x..y", "trail.", " lead",
     "a" * 200, "naughty\x00", "-rf", "x y"],
)  # fmt: skip
def test_unsafe_names_are_refused(bad: str) -> None:
    with pytest.raises(ConfigError):
        validate_name(bad, "tenant_id")


@pytest.mark.parametrize("good", ["tenant-a", "ten_01H", "app_1", "a.b-c_d", "T1"])
def test_plain_names_are_accepted(good: str) -> None:
    assert validate_name(good, "x") == good


async def test_path_traversal_through_tenant_or_app_is_blocked(tmp_path: Path) -> None:
    repo = LocalGitProjectRepo(tmp_path / "projects")
    for tenant, app in [("..", "app_1"), ("tenant-a", ".."), ("../../etc", "x"), ("t", "../../x")]:
        with pytest.raises(ConfigError):
            await commit(repo, tenant=tenant, app=app)
    assert not (tmp_path / "x").exists() and not (tmp_path / "etc").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
async def test_a_symlink_cannot_lead_out_of_the_projects_dir(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "t").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "t" / "app_1").symlink_to(outside, target_is_directory=True)
    repo = LocalGitProjectRepo(root)
    with pytest.raises(ConfigError):
        await commit(repo, tenant="t", app="app_1")
    assert list(outside.iterdir()) == []


async def test_a_missing_git_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(ConfigError):
        LocalGitProjectRepo(tmp_path)
    broken = LocalGitProjectRepo(tmp_path, git_executable=str(tmp_path / "no-such-git"))
    with pytest.raises(HandoffFailed):
        await commit(broken)


async def test_noop_frontend_engine_records_notifications() -> None:
    frontend = NoopFrontendEngine()
    ev = SpecReady(
        event_id="e", session_id="s", app_id="a", version=1, hash="h", git_sha="sha",
        path="APP_SPEC.md", repo_dir=".",
    )  # fmt: skip
    await frontend.notify(ev)
    assert frontend.notifications == [ev]


# ----------------------------------------------------------------------------- names and links


@pytest.mark.parametrize("bad", ["acme\n", "acme\r\n", "\nacme", "ac\nme", "acme\n\n"])
def test_validate_name_newline_and_case(bad: str) -> None:
    """``$`` matches before a trailing newline: a name that ends in one is not a plain name."""
    with pytest.raises(ConfigError):
        validate_name(bad, "tenant_id")


async def test_names_that_differ_only_by_case_never_share_a_directory(tmp_path: Path) -> None:
    """On a case-insensitive file system ``Acme`` and ``acme`` are one directory: two tenants (or
    two apps) would share a repository. The second name is refused, on every platform."""
    repo = LocalGitProjectRepo(tmp_path)
    await commit(repo, tenant="acme", app="app_1")
    with pytest.raises(ConfigError, match="without regard to case"):
        await commit(repo, tenant="Acme", app="app_1")
    with pytest.raises(ConfigError, match="without regard to case"):
        await commit(repo, tenant="ACME", app="app_2")
    await commit(repo, tenant="acme", app="app_1", version=2, text=TEXT + "more\n")  # the same name
    await commit(repo, tenant="acme", app="app_2")  # another app of the same tenant
    with pytest.raises(ConfigError, match="without regard to case"):
        await commit(repo, tenant="acme", app="APP_2")
    assert visible_names(tmp_path) == ["acme"]


def visible_names(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir() if not p.name.startswith("."))


def _make_link(link: Path, target: Path) -> None:
    """A directory link that needs no privilege: a junction on Windows, a symlink elsewhere."""
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True, capture_output=True,
        )  # fmt: skip
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("level", ["tenant", "app"])
async def test_a_directory_link_cannot_lead_out_of_the_projects_dir(
    tmp_path: Path, level: str
) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if level == "tenant":
        _make_link(root / "t", outside)
    else:
        (root / "t").mkdir()
        _make_link(root / "t" / "app_1", outside)
    repo = LocalGitProjectRepo(root)
    with pytest.raises(ConfigError):
        await commit(repo, tenant="t", app="app_1")
    assert visible_names(outside) == []  # nothing was written through the link
