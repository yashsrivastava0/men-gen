"""PostToolUse hook: run `ruff format` on the .py file that was just edited or written.

Reads the hook JSON from stdin. Never fails the tool call: every error path exits 0.
Skips non-.py files, missing files, and machines where ruff is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 20


def _target_file(payload: dict) -> Path | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(raw, str) or not raw.lower().endswith(".py"):
        return None
    path = Path(raw)
    if not path.is_absolute():
        cwd = payload.get("cwd")
        path = Path(cwd if isinstance(cwd, str) and cwd else ".") / path
    return path if path.is_file() else None


def _candidates(path: Path) -> list[list[str]]:
    target = str(path)
    return [
        ["ruff", "format", "--quiet", target],
        [sys.executable, "-m", "ruff", "format", "--quiet", target],
        ["uv", "run", "--no-sync", "--quiet", "ruff", "format", "--quiet", target],
    ]


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        path = _target_file(payload)
        if path is None:
            return 0
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
        for cmd in _candidates(path):
            try:
                result = subprocess.run(
                    cmd,
                    cwd=cwd or None,
                    capture_output=True,
                    timeout=TIMEOUT_SECONDS,
                    check=False,
                )
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                break
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
