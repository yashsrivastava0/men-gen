"""PreToolUse hook (Bash and PowerShell): block commands that would start live LLM calls by accident.

Blocks (exit code 2, reason on stderr) any command that

* assigns ``LLM_MODE`` the value ``live`` in any shell, quoting or obfuscation form
  (``LLM_MODE=live``, ``LLM_MODE=li''ve``, ``$env:LLM_MODE='live'``, ``${env:LLM_MODE}="live"``,
  ``set LLM_MODE=live``, ``export LLM_MODE="live"``, ``Set-Item Env:LLM_MODE -Value live``,
  ``setx LLM_MODE live``, ``[Environment]::SetEnvironmentVariable('LLM_MODE','live')``,
  ``os.environ['LLM_MODE']='live'``, ``os.environ.setdefault('LLM_'+'MODE','live')``);
* assigns ``LLM_MODE`` a value it cannot prove is not live (``$(echo live)``, a backtick
  substitution, ``$X`` or ``${X:-live}``): a dynamic value is treated as live;
* runs ``just live-*`` or the live eval runner directly,
* runs a Research agent live script (``mde_research_agent.live_*``, by module name or by file path)
  without an offline flag (``--dry-run``, ``--mock``, ``--replay``),

unless the *environment* already has ``ALLOW_LIVE_LLM=1`` (the lead's live session). The command is
first normalised (case-folded, invisible characters, quotes, backticks, carets, backslashes,
braces and ``+`` string concatenation removed, whitespace around ``=`` collapsed), so quoting tricks
do not hide the assignment. The runner *path* is matched on a second reading in which every
backslash is a forward slash instead (in a path it is a separator, not an escape), so the Windows
spelling of the batch runner's path is caught too. The hook sees only the command text: it cannot look inside a file that
a command sources or an ``--env-file``.

Deliberately NOT blocked: ``$env:ALLOW_LIVE_LLM='1'; uv run pytest tests/live -m live`` (the lead's
live batches). It assigns no ``LLM_MODE`` and runs no ``just live-*``; the real guard for live tests
is the root ``conftest.py``, which skips every ``live`` test unless ``ALLOW_LIVE_LLM=1`` is set in
the outer environment (and then switches ``LLM_MODE=live`` itself). Rule 3 therefore rests on the
agent protocol plus those two layers; this hook stops the accidental and the obvious.

Registered in ``.claude/settings.json`` for both the ``Bash`` and the ``PowerShell`` tools. To
enable live batches for the lead / live-runner session, put {"env": {"ALLOW_LIVE_LLM": "1"}} in the
gitignored ``.claude/settings.local.json``. Hook errors never block anything (exit 0) except a
positive match.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata

MESSAGE = (
    "Blocked by .claude/hooks/guard_live.py: this command would enable live LLM calls "
    "(LLM_MODE=live, a dynamic LLM_MODE value, `just live-*` or a `mde_research_agent.live_*` script "
    "without --dry-run/--mock/--replay) but ALLOW_LIVE_LLM=1 is not set "
    "in the environment. Rule 3: only the lead or `live-runner` runs live batches, within the "
    "live cap, CLAUDE.md rule 3 (run `just budget` first). Subagents must use mock or replay mode."
)

# --- patterns over the NORMALISED command (see ``normalise``) ---------------------------------
# ``LLM_MODE=``, ``environ[LLM_MODE]=``, ``$env:LLM_MODE=``: the value follows the equals sign.
_ASSIGN = re.compile(r"llm_mode[\])]*=")
# ``Set-Item Env:LLM_MODE -Value live``, ``setx LLM_MODE live``,
# ``SetEnvironmentVariable(LLM_MODE,live)``, ``setdefault(LLM_MODE,live)``,
# ``update({LLM_MODE: live})``: the value is the next token.
_ARGS = re.compile(r"llm_mode[\])]*[ ,:]+(?:-\w+ )*live(?![\w./\\-])")
_JUST_LIVE = re.compile(r"(?:^|[;&|(\s])just\b[^;&|\r\n]*?\slive-[a-z]")
# one or more separators between the parts: ``evals\runners\live_batch``, ``evals//runners/live_batch``
_LIVE_RUNNER = re.compile(r"evals[./\\]+runners[./\\]+live_batch")
_VALUE_TOKEN = re.compile(r"[^\s;&|)]*")
_HARMLESS_DYNAMIC = ("$null", "$false")
# The Research agent's live scripts (``python -m mde_research_agent.live_bakeoff``, ``.live_runs``,
# ``.live_smoke``) and their file paths. They carry their own preflight (as ``mde_compass.live_batches``
# does), but they are named here so the hook knows them: their live forms need the second key in the
# environment, while the offline forms (``--dry-run``, ``--mock``, ``--replay``) stay allowed.
_RESEARCH_LIVE = re.compile(r"mde_research_agent[./\\]+live_[a-z]")
_OFFLINE_FLAG = re.compile(r"(?:^|\s)--(?:dry-run|mock|replay)(?=\s|$|=|;)")

# Kept for callers that import the old name; matching now goes through ``is_live_command``.
LIVE_PATTERNS = (_ASSIGN, _ARGS, _JUST_LIVE, _LIVE_RUNNER, _RESEARCH_LIVE)


def normalise(command: str, *, paths: bool = False) -> str:
    """The command in the form the patterns are matched against.

    Case-folded; NFKC; invisible characters, quotes, backticks, carets, backslashes, braces and
    ``+`` (with the whitespace around it) removed, so ``li''ve``, ``li`ve``, ``"LLM_""MODE"`` and
    ``'LLM_' + 'MODE'`` collapse to their plain spelling; newlines become ``;``; whitespace runs
    collapse and the whitespace around ``=`` goes. A backtick right after ``=`` (bash command substitution) is kept as ``$`` so the
    value reads as dynamic.

    With ``paths=True`` a backslash becomes a forward slash instead of vanishing (on Windows it is
    the path separator, not a shell escape): that reading is for matching file paths.
    """
    text = unicodedata.normalize("NFKC", command).casefold()
    text = "".join(
        ch
        for ch in text
        if ch in "\r\n\t" or unicodedata.category(ch) not in {"Cf", "Cc"}  # invisible characters
    )
    text = re.sub(r"=\s*`", "=$", text)
    text = re.sub(r"\s*\+\s*", "", text)  # 'li' + 've'  and  'LLM_'+'MODE'  (string concatenation)
    if paths:
        text = text.replace("\\", "/")  # a Windows path: keep the separators
    for junk in ("`", "'", '"', "^", "\\", "{", "}"):
        text = text.replace(junk, "")
    text = re.sub(r"[\r\n]+", ";", text)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s*=\s*", "=", text)


def _assigns_live(text: str) -> bool:
    for match in _ASSIGN.finditer(text):
        value = text[match.end() :]
        if value.startswith(("$", "(")):  # $X, $(cmd), ${X:-live}: cannot prove it is not live
            if not value.startswith(_HARMLESS_DYNAMIC):
                return True
            continue
        token = _VALUE_TOKEN.match(value)
        if token is not None and token.group().rstrip(",") == "live":
            return True
    return False


def _runs_research_live(command: str, text: str) -> bool:
    """A ``mde_research_agent.live_*`` script without an offline flag (``--dry-run``/``--mock``/``--replay``)."""
    # Per command segment (split on ``;`` ``&`` ``|``): an offline flag only counts in the SAME command
    # as the script, so ``...live_runs; echo --mock`` is still live.
    for reading in (
        text,
        normalise(command, paths=True),
    ):  # the second reads agents\research\... as a path
        for segment in re.split(r"[;&|]+", reading):
            if _RESEARCH_LIVE.search(segment) and _OFFLINE_FLAG.search(segment) is None:
                return True
    return False


def is_live_command(command: str) -> bool:
    """True when ``command`` would turn live LLM calls on (see the module docstring)."""
    text = normalise(command)
    return (
        _assigns_live(text)
        or _ARGS.search(text) is not None
        or _JUST_LIVE.search(text) is not None
        or _LIVE_RUNNER.search(text) is not None
        or _LIVE_RUNNER.search(normalise(command, paths=True)) is not None  # evals\runners\...
        or _runs_research_live(command, text)
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        tool_input = payload.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return 0
        if os.environ.get("ALLOW_LIVE_LLM") == "1":
            return 0
        if is_live_command(command):
            sys.stderr.write(MESSAGE + "\n")
            return 2
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
