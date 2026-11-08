"""Security and hygiene scans over the whole repo (brief 9.1). Offline, never prints a secret.

* No key-like patterns, and not the actual keys from ``.env`` (compared in memory), in any
  tracked or not-ignored file (``docs/`` included: a fixture there needs an explicit
  ``secret-scan: allow`` marker on its line), nor in runtime data (ledger, projects).
* ``eval``/``exec`` are never called in source (rule 9).
* Only ``mde_llm_gateway`` imports ``openai`` (rule 2); no agent frameworks; no SQLite.
"""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from dotenv import dotenv_values

from mde_core.paths import git_executable
from mde_core.redact import SECRET_PATTERNS, normalize_for_scan

ROOT = Path(__file__).resolve().parents[2]
HIGH_SIGNAL = {
    "openai_key", "anthropic_key", "aws_access_key", "github_token", "slack_token",
    "google_api_key", "stripe_key", "private_key_block",
}  # fmt: skip
# Files that intentionally contain fake secret-looking fixtures. ``docs/`` is NOT here: prose and
# examples there are scanned too, and a deliberate fixture carries ``secret-scan: allow``.
ALLOW_PREFIXES = ("packages/core/tests/", "tests/security/")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2"}
FORBIDDEN_FRAMEWORKS = {
    "langchain", "langgraph", "llama_index", "crewai", "autogen", "semantic_kernel", "dspy",
    "instructor", "agents", "sqlite3", "aiosqlite",
}  # fmt: skip


def _repo_files() -> list[Path]:
    git = git_executable()  # absolute path from PATH: never a ``git.exe`` planted in the cwd
    try:
        if git is None:
            raise FileNotFoundError("git not found on PATH")
        out = subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()  # fmt: skip
    except Exception:  # not a git checkout: walk instead (skipping the heavy ignored folders)
        skip = {".git", ".venv", "node_modules", "__pycache__", "data"}
        out = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*")
            if p.is_file() and not skip & set(p.relative_to(ROOT).parts)
        ]
    files = [ROOT / p for p in out if (ROOT / p).is_file()]
    for runtime in ("data/.budget", "data/projects"):
        base = ROOT / runtime
        if base.exists():
            files += [p for p in base.rglob("*") if p.is_file()]
    return files


def _text(path: Path) -> str | None:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _env_secret_values() -> list[str]:
    env = ROOT / ".env"
    if not env.exists():
        return []
    values = [v for v in dotenv_values(env).values() if v and len(v) >= 12]
    return [v for v in values if v not in {"mock", "replay", "live", "gpt-6-luna"}]


def _is_allowed(rel: str) -> bool:
    return rel.startswith(ALLOW_PREFIXES) or rel == ".env"


def _line_offenders(rel: str, text: str) -> list[str]:
    """Key-like lines of ``text`` (names only, never values); invisible-character tricks folded."""
    if _is_allowed(rel):
        return []
    patterns = [(n, p) for n, p in SECRET_PATTERNS if n in HIGH_SIGNAL]
    found: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if "secret-scan: allow" in line:
            continue
        folded = normalize_for_scan(line)
        for name, pat in patterns:
            if pat.search(folded):
                found.append(f"{rel}:{lineno} ({name})")
    return found


def test_no_key_like_patterns_in_repo() -> None:
    offenders: list[str] = []
    for path in _repo_files():
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
        text = _text(path)
        if text is not None:
            offenders += _line_offenders(rel, text)
    assert not offenders, "key-like text found (values not shown): " + ", ".join(offenders[:20])


def test_docs_are_scanned_and_fixtures_need_an_explicit_marker() -> None:
    """``docs/`` used to be exempt wholesale; now only a line marked ``secret-scan: allow`` is."""
    fake = "sk-" + "proj-" + "A1b2C3d4E5" * 4  # built at runtime: this file is allow-listed anyway
    assert not any(prefix.startswith("docs") for prefix in ALLOW_PREFIXES)
    assert _line_offenders("docs/guide.md", f"use {fake} here")
    assert _line_offenders("docs/guide.md", f"hidden {fake[:12]}\u200b{fake[12:]}")  # ZW-split
    assert not _line_offenders("docs/guide.md", f"use {fake} here  <!-- secret-scan: allow -->")
    assert not _line_offenders("tests/security/x.py", f"use {fake} here")
    assert any(p.relative_to(ROOT).parts[0] == "docs" for p in _repo_files())


def test_actual_env_secrets_never_appear_in_repo_files() -> None:
    secrets = _env_secret_values()
    if not secrets:
        reason = (
            "SKIPPED, the real-key comparison did NOT run: "
            + (
                "there is no .env in this checkout"
                if not (ROOT / ".env").exists()
                else ".env holds no value of 12+ characters"
            )
            + " (expected in CI and in agent worktrees; run `just scan-secrets` in a checkout "
            "that has the lead's .env to enable it)"
        )
        print(reason)
        pytest.skip(reason)
    leaked: list[str] = []
    for path in _repo_files():
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
        if rel == ".env":
            continue
        text = _text(path)
        if text is None:
            continue
        if any(s in text for s in secrets):
            leaked.append(rel)
    assert not leaked, f"real secret value found in: {leaked} (value not shown)"


def _python_sources() -> list[Path]:
    roots = [*ROOT.glob("packages/*/src"), *ROOT.glob("apps/*/src")]
    return [p for r in roots for p in r.rglob("*.py")]


def test_no_eval_or_exec_in_source() -> None:
    bad: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec"}
            ):
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert not bad, f"eval/exec is forbidden (rule 9): {bad}"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return mods


def test_only_gateway_imports_openai_and_no_forbidden_frameworks() -> None:
    bad_openai: list[str] = []
    bad_fw: list[str] = []
    for path in [*_python_sources(), *ROOT.glob("tests/**/*.py"), *ROOT.glob("scripts/*.py")]:
        rel = path.relative_to(ROOT).as_posix()
        mods = _imports(path)
        in_gateway = rel.startswith("packages/llm-gateway/")
        if "openai" in mods and not in_gateway:
            bad_openai.append(rel)
        if mods & FORBIDDEN_FRAMEWORKS and not rel.startswith("tests/security/"):
            bad_fw.append(rel)
    assert not bad_openai, f"only mde_llm_gateway may import openai: {bad_openai}"
    assert not bad_fw, f"forbidden framework / SQLite import: {bad_fw}"


def test_no_agent_framework_dependencies_declared() -> None:
    banned = re.compile(
        r"^(langchain|langgraph|llama[-_]index|crewai|autogen|semantic[-_]kernel|dspy|instructor"
        r"|openai[-_]agents)",
        re.I,
    )
    projects = [
        ROOT / "pyproject.toml",
        *ROOT.glob("packages/*/pyproject.toml"),
        *ROOT.glob("apps/*/pyproject.toml"),
    ]
    for pyproject in projects:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        deps: list[str] = list(data.get("project", {}).get("dependencies", []))
        for group in data.get("dependency-groups", {}).values():
            deps += [d for d in group if isinstance(d, str)]
        bad = [d for d in deps if banned.match(d.strip())]
        assert not bad, f"{pyproject}: forbidden dependency {bad}"
