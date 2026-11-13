"""Live mode is one switch, ``LLM_MODE=live`` (owner policy, D-140; CLAUDE.md rule 13).

No second live-call key, no extra readiness check for live mode and no shell hook that blocks live mode may come
back, anywhere in the repository. The forbidden spellings are assembled from parts so this file does not
match itself.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN = re.compile(
    "|".join(
        (
            "ALLOW" + "_LIVE_LLM",
            "allow" + "_live",
            "guard" + "_live",
            "second" + r"[ -]key",
            "live" + " switch",
            "live-call" + " hook",
            "live-guard" + " hook",
        )
    ),
    re.IGNORECASE,
)
TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".json", ".yaml", ".yml", ".jinja", ".js", ".ts", ".html", ".css", ".txt",
    ".example", ".cfg", ".ini", "",
}  # fmt: skip


def _repo_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line]


def test_no_second_live_switch_anywhere() -> None:
    hits: list[str] = []
    for path in _repo_files():
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert not hits, "an extra live gate or a live-blocking hook came back (D-140): " + ", ".join(
        hits[:20]
    )


def test_no_hook_gates_shell_commands_on_live_mode() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        for hook in entry.get("hooks", []):
            assert "live" not in hook.get("command", "").lower(), hook
    assert not list((ROOT / ".claude" / "hooks").glob("*live*"))


def test_live_mode_alone_enables_live_calls() -> None:
    from mde_core.settings import Settings

    settings = Settings(_env_file=None, llm_mode="live", forbid_live=False)  # type: ignore[call-arg]
    assert settings.live_enabled
