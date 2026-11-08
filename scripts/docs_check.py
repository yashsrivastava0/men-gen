"""Docs hygiene (brief 5.4, rule 11): markdown only where allowed, index links resolve.

* ``.md`` files may live only in README.md, CLAUDE.md, AGENTS.md (any depth), ``.claude/**``, ``docs/**``.
* Every relative link in docs/README.md must exist (lines containing "planned" are exempt).
* Required docs exist.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from mde_core.paths import git_executable

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_NAMES = {"README.md", "CLAUDE.md", "AGENTS.md"}
REQUIRED = [
    "docs/README.md",
    "docs/implementation/PROGRESS.md",
    "docs/implementation/DECISIONS.md",
    "docs/implementation/BRIEF.md",
    "docs/product/context-engine-spec.md",
    "docs/adr/TEMPLATE.md",
]
LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def files() -> list[Path]:
    # ``git`` is looked up on PATH only: on Windows a bare "git" is resolved in the current
    # directory first, so a planted git.exe in a checkout would run.
    git = git_executable()
    if git is None:
        raise SystemExit(
            "docs-check: git was not found on PATH (relative PATH entries are ignored)"
        )
    out = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    return [ROOT / p for p in out if (ROOT / p).exists()]


def main() -> int:
    problems: list[str] = []
    for path in files():
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() == ".md":
            ok = path.name in ALLOWED_NAMES or rel.startswith(".claude/") or rel.startswith("docs/")
            if not ok:
                problems.append(f"markdown outside allowed locations: {rel}")
    for req in REQUIRED:
        if not (ROOT / req).exists():
            problems.append(f"missing required doc: {req}")
    index = ROOT / "docs" / "README.md"
    if index.exists():
        for lineno, line in enumerate(index.read_text(encoding="utf-8").splitlines(), 1):
            if "planned" in line.lower():
                continue
            for target in LINK_RE.findall(line):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (index.parent / target).resolve().exists():
                    problems.append(f"docs/README.md:{lineno}: broken link {target}")
    for p in problems:
        print("docs-check:", p)
    print(f"docs-check: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
