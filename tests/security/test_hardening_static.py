"""Static hardening scans and the live-guard hook (security review hardening).

* Source never uses ``yaml.load``, ``pickle`` and friends, builtin ``compile``, a non-sandboxed
  jinja2 ``Environment``, ``shell=True``, ``os.system``/``os.popen`` or ``subprocess`` with a string
  command (``packages/*/src``, ``apps/*/src``, ``agents/*/src``, ``scripts``). A justified exception carries the marker
  comment ``# hardening: allow <reason>`` on a line of the call.
* ``.claude/hooks/guard_live.py`` blocks the obvious and the obfuscated ways of turning live LLM mode
  on, for both the Bash and the PowerShell tool, and still lets the lead's live batches through.
* The CI workflow is read-only (least privilege).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MARKER = "hardening: allow"


# =============================================================================== static scanner
_YAML_LOADERS = {"load", "load_all", "unsafe_load", "unsafe_load_all", "full_load", "full_load_all"}
_PICKLE_LIKE = {"pickle", "cpickle", "_pickle", "dill", "marshal", "shelve", "cloudpickle"}
_SUBPROCESS_CALLS = {"run", "call", "check_call", "check_output", "Popen"}
_JINJA_UNSAFE = {"Environment", "Template"}  # only the sandboxed environments may be built


def _is_string_expr(node: ast.expr) -> bool:
    """A command given as ONE string: a literal, an f-string, ``"..." % x``, ``"...".format()``."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, (ast.JoinedStr, ast.BinOp)):
        return isinstance(node, ast.JoinedStr) or _is_string_expr(node.left)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
    )


def scan_source(source: str, rel: str = "<memory>") -> list[str]:
    """Banned constructs in ``source`` (``"rel:line what"``), minus marked exceptions."""
    tree = ast.parse(source, filename=rel)
    lines = source.splitlines()
    from_yaml: set[str] = set()  # names imported from yaml (``from yaml import load``)
    from_jinja: set[str] = set()
    from_subprocess: set[str] = set()
    problems: list[tuple[int, int, str]] = []  # (first line, last line, what)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].lower() in _PICKLE_LIKE:
                    problems.append((node.lineno, node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            root = node.module.split(".")[0]
            if root.lower() in _PICKLE_LIKE:
                problems.append((node.lineno, node.lineno, f"from {node.module} import"))
            for alias in node.names:
                if root == "yaml" and alias.name in _YAML_LOADERS:
                    from_yaml.add(alias.asname or alias.name)
                if node.module == "jinja2" and alias.name in _JINJA_UNSAFE:
                    from_jinja.add(alias.asname or alias.name)
                if root == "subprocess" and alias.name in _SUBPROCESS_CALLS:
                    from_subprocess.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        first, last = node.lineno, node.end_lineno or node.lineno
        func = node.func
        owner = (
            func.value.id
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            else None
        )
        attr = func.attr if isinstance(func, ast.Attribute) else None
        name = func.id if isinstance(func, ast.Name) else None

        if (owner == "yaml" and attr in _YAML_LOADERS) or name in from_yaml:
            problems.append((first, last, "yaml.load (use yaml.safe_load)"))
        if name == "compile":
            problems.append((first, last, "builtin compile()"))
        if (owner == "jinja2" and attr in _JINJA_UNSAFE) or name in from_jinja:
            problems.append((first, last, "plain jinja2 Environment/Template (use the sandbox)"))
        if owner == "os" and attr in {"system", "popen"}:
            problems.append((first, last, f"os.{attr}()"))
        if owner == "pickle" or (owner in _PICKLE_LIKE and attr in {"load", "loads"}):
            problems.append((first, last, "pickle"))
        for keyword in node.keywords:
            if keyword.arg == "shell" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            ):
                problems.append((first, last, "shell=True"))
        is_subprocess = (owner == "subprocess" and attr in _SUBPROCESS_CALLS) or (
            name in from_subprocess
        )
        if is_subprocess:
            command = node.args[0] if node.args else None
            for keyword in node.keywords:
                if keyword.arg == "args":
                    command = keyword.value
            if command is not None and _is_string_expr(command):
                problems.append((first, last, "subprocess with a string command"))

    return [
        f"{rel}:{first} {what}"
        for first, last, what in sorted(set(problems))
        if not any(MARKER in lines[i - 1] for i in range(first, min(last, len(lines)) + 1))
    ]


def _scanned_files() -> list[Path]:
    roots = [
        *ROOT.glob("packages/*/src"),
        *ROOT.glob("apps/*/src"),
        *ROOT.glob("agents/*/src"),
        ROOT / "scripts",
    ]
    return sorted(p for r in roots if r.is_dir() for p in r.rglob("*.py"))


def test_no_banned_constructs_in_source() -> None:
    files = _scanned_files()
    assert len(files) > 50, "the scan found suspiciously few files"
    problems: list[str] = []
    for path in files:
        problems += scan_source(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    assert not problems, (
        "banned construct(s) (mark a justified one with '# hardening: allow'):\n"
        + ("\n".join(problems))
    )


BAD_SNIPPETS = {
    "yaml.load": "import yaml\nyaml.load(text)\n",
    "yaml.unsafe_load": "import yaml\nyaml.unsafe_load(text)\n",
    "from yaml import load": "from yaml import load\nload(text)\n",
    "pickle import": "import pickle\n",
    "pickle from-import": "from pickle import loads\n",
    "marshal import": "import marshal\n",
    "builtin compile": "code = compile(src, 'x', 'exec')\n",
    "jinja2.Environment": "import jinja2\nenv = jinja2.Environment()\n",
    "from jinja2 import Environment": "from jinja2 import Environment\nenv = Environment()\n",
    "jinja2.Template": "import jinja2\njinja2.Template('{{x}}')\n",
    "shell=True": "import subprocess\nsubprocess.run(['ls'], shell=True)\n",
    "shell=variable": "import subprocess\nsubprocess.run(['ls'], shell=flag)\n",
    "os.system": "import os\nos.system('ls')\n",
    "os.popen": "import os\nos.popen('ls')\n",
    "subprocess string": "import subprocess\nsubprocess.run('git status')\n",
    "subprocess f-string": "import subprocess\nsubprocess.check_output(f'git {x}')\n",
    "subprocess % format": "import subprocess\nsubprocess.call('git %s' % x)\n",
    "subprocess .format": "import subprocess\nsubprocess.Popen('git {}'.format(x))\n",
    "subprocess args=": "import subprocess\nsubprocess.run(args='git status')\n",
    "subprocess from-import": "from subprocess import run\nrun('git status')\n",
}


@pytest.mark.parametrize("name", sorted(BAD_SNIPPETS))
def test_scanner_flags_every_banned_construct(name: str) -> None:
    assert scan_source(BAD_SNIPPETS[name]), f"{name} was not flagged"


GOOD_SNIPPETS = {
    "yaml.safe_load": "import yaml\nyaml.safe_load(text)\n",
    "sandboxed jinja": "from jinja2.sandbox import SandboxedEnvironment\nSandboxedEnvironment()\n",
    "re.compile": "import re\nre.compile('x')\n",
    "method named compile": "class A:\n    def compile(self): ...\nA().compile()\n",
    "subprocess list": "import subprocess\nsubprocess.run(['git', 'status'], check=True)\n",
    "subprocess list var": "import subprocess\ncmd = ['git']\nsubprocess.run(cmd)\n",
    "shell=False": "import subprocess\nsubprocess.run(['ls'], shell=False)\n",
    "json": "import json\njson.loads('{}')\n",
    "marked exception": "import os\nos.system('ls')  # hardening: allow fixed literal, reviewed\n",
    "marked multi-line": (
        "import subprocess\nsubprocess.run(\n    'git status',  # hardening: allow fixed literal\n)\n"
    ),
}


@pytest.mark.parametrize("name", sorted(GOOD_SNIPPETS))
def test_scanner_accepts_safe_forms_and_marked_exceptions(name: str) -> None:
    assert scan_source(GOOD_SNIPPETS[name]) == []


# =============================================================================== CI workflow
def test_ci_workflow_is_read_only() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    for name, job in workflow["jobs"].items():
        permissions = job.get("permissions", {})
        assert not any(v == "write" for v in permissions.values()), f"job {name} widens permissions"


# =============================================================================== dev_db: git lookup
def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dev_db_resolves_git_to_an_absolute_path_found_on_path_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_db = _load(ROOT / "scripts" / "dev_db.py", "dev_db_under_test")
    if dev_db.GIT is not None:
        assert Path(dev_db.GIT).is_absolute()
    source = (ROOT / "scripts" / "dev_db.py").read_text(encoding="utf-8")
    assert '["git"' not in source and '"git",' not in source, "a bare 'git' is back in dev_db.py"
    # a planted executable in the current directory is never picked up
    exe = "git.exe" if os.name == "nt" else "git"
    (tmp_path / exe).write_text("planted", encoding="utf-8")
    (tmp_path / exe).chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative/bin"]))
    assert dev_db.find_exe("git") is None
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(tmp_path)]))
    assert dev_db.find_exe("git") == str(tmp_path / exe)  # absolute PATH entries do count


# =============================================================================== the live guard hook
HOOK = ROOT / ".claude" / "hooks" / "guard_live.py"
SETTINGS = ROOT / ".claude" / "settings.json"

# (command, blocked). The same text arrives through the Bash and the PowerShell tool.
CASES: list[tuple[str, bool]] = [
    # --- plain forms, every shell
    ("LLM_MODE=live uv run pytest", True),
    ("$env:LLM_MODE='live'; uv run pytest", True),
    ('$env:LLM_MODE="live"; uv run pytest', True),
    ("$env:LLM_MODE = 'live'", True),
    ("set LLM_MODE=live && pytest", True),
    ("cmd /c set LLM_MODE=live", True),
    ('export LLM_MODE="live"; pytest', True),
    ("env LLM_MODE=live python x", True),
    ("LLM_MODE =  live", True),
    ("LLM_MODE=LIVE", True),
    ("[Environment]::SetEnvironmentVariable('LLM_MODE','live')", True),
    ('[System.Environment]::SetEnvironmentVariable("LLM_MODE","live","Process")', True),
    ("just live-smoke", True),
    ("cd x; just live-eval", True),
    ("just --justfile j live-smoke", True),
    ("uv run just live-smoke", True),
    ("echo hi\nLLM_MODE=live uv run x", True),
    # --- quoting and obfuscation (the review's bypasses)
    ("LLM_MODE=li''ve uv run x", True),
    ('LLM_MODE="li""ve" uv run x', True),
    ("LLM_MODE=li\\ve uv run x", True),
    ("$env:LLM_MODE='li`ve'", True),
    ("$env:LLM_MODE = 'li' + 've'", True),
    ("ju''st live-smoke", True),
    ("LLM_MODE=$(echo live) uv run x", True),
    ("LLM_MODE=`echo live` uv run x", True),
    ("X=live; LLM_MODE=$X uv run x", True),
    ("$m='live'; $env:LLM_MODE=$m", True),
    ("export LLM_MODE=${X:-live}", True),
    ("python -c \"import os;os.environ['LLM_MODE']='live'\"", True),
    ("python -c \"import os;os.environ.setdefault('LLM_'+'MODE','live')\"", True),
    ("python -c \"import os;os.putenv('LLM_MODE', 'live')\"", True),
    ("python -c \"import os;os.environ.update({'LLM_MODE': 'live'})\"", True),
    ("Set-Item -Path Env:LLM_MODE -Value live", True),
    ("Set-Item Env:LLM_MODE live", True),
    ("Set-Item -Path 'Env:LLM_MODE' -Value 'live'", True),
    ("${env:LLM_MODE}='live'", True),
    ('${env:LLM_MODE} = "live"', True),
    ("'LLM_MODE' = 'live'", True),
    ("setx LLM_MODE live", True),
    ("\u200bLLM_MODE\u200b=live", True),  # zero-width characters
    ("\uff2c\uff2c\uff2d_MODE=live", True),  # fullwidth letters
    ("uv run python -m evals.runners.live_batch", True),
    # --- the batch runner spelled as a Windows path (N10: backslashes were stripped, not read as
    # separators, so none of these matched)
    ("uv run python evals\\runners\\live_batch.py --batch L1", True),
    ("python .\\evals\\runners\\live_batch.py", True),
    ('uv run python "evals\\runners\\live_batch.py"', True),
    ("uv run python 'evals\\runners\\live_batch.py' --batch L2", True),
    ("uv run python evals\\\\runners\\\\live_batch.py", True),  # doubled separators
    ("uv run python evals\\runners/live_batch.py", True),  # mixed
    ("uv run python evals/runners\\live_batch.py", True),
    ("uv run python C:\\Users\\me\\Mobigen\\evals\\runners\\live_batch.py", True),
    ("uv run python EVALS\\RUNNERS\\LIVE_BATCH.PY", True),
    ("& uv run python .\\evals\\runners\\live_batch.py", True),
    ("uv run python evals//runners//live_batch.py", True),
    ("uv run python ev''als\\ru''nners\\live_''batch.py", True),  # quoting inside the path too
    ("python -m evals.runners.live_batch", True),
    # --- the Research agent's live scripts: named by module or by path; live forms need the second key
    ("uv run python -m mde_research_agent.live_bakeoff", True),
    ("uv run python -m mde_research_agent.live_runs --run quick", True),
    ("python3 -m mde_research_agent.live_smoke", True),
    ("uv run python -m mde_research_agent.live_runs --allowance-usd 1.50", True),
    ("uv run python agents/research/src/mde_research_agent/live_bakeoff.py", True),
    ("uv run python agents\\research\\src\\mde_research_agent\\live_runs.py", True),
    ("uv run python -m MDE_RESEARCH_AGENT.LIVE_RUNS", True),
    (
        "uv run python -m mde_research_agent.eval planted",
        True,
    ),  # the evaluation harness spends real money
    ("uv run python -m mde_research_agent.eval probe --seed 3", True),
    ("uv run python agents/research/src/mde_research_agent/eval/planted.py", True),
    (
        "uv run python -m mde_research_agent.live_runs; echo --mock",
        True,
    ),  # a flag after `;` is another command
    # --- must NOT be blocked
    ("just test", False),
    ("just ci", False),
    ("just budget", False),
    ("just --list", False),
    ("LLM_MODE=mock uv run pytest", False),
    ("LLM_MODE=replay uv run pytest", False),
    ("LLM_MODE=mock uv run pytest -m live", False),
    ("$env:LLM_MODE='mock'", False),
    ("$env:LLM_MODE=$null", False),
    ("echo delivery", False),
    ("git log --oneline", False),
    ("uv run pytest tests/live -m live", False),
    # the lead's live batches: the conftest guard (outer ALLOW_LIVE_LLM) is what protects them
    ("$env:ALLOW_LIVE_LLM='1'; uv run pytest tests/live -m live", False),
    ("$env:ALLOW_LIVE_LLM='1'; uv run pytest tests/live -m live -k smoke -q", False),
    ("grep -rn LLM_MODE docs/runbooks/live-llm-budget.md", False),
    ("Select-String LLM_MODE docs\\runbooks\\live-llm-budget.md", False),
    ("Get-ChildItem Env:LLM_MODE", False),
    ("echo $env:LLM_MODE", False),
    ("uv run python scripts/doctor.py", False),
    # the Research agent: its offline forms, its server and its headless runner are not live scripts
    ("uv run python -m mde_research_agent.live_bakeoff --dry-run", False),
    ("uv run python -m mde_research_agent.live_runs --mock", False),
    ("uv run python -m mde_research_agent.live_runs --replay --run quick", False),
    ("uv run python -m mde_research_agent.live_smoke --dry-run --offline", False),
    (
        "uv run python -m mde_research_agent --port 8120",
        False,
    ),  # the app: it spends only on a person's click
    ("scripts/research_app start", False),
    ("uv run python -m mde_research_agent.eval planted --dry-run", False),
    ("uv run python -m mde_research_agent.evaluate_something", False),
    ("uv run python -m mde_research_agent.run --export x.json --mock", False),
    ("uv run pytest agents/research -m 'not integration and not live' -q", False),
    ("grep -rn live_runs agents/research/README.md", False),
    # a path that merely looks similar, with either separator, is not the runner
    ("uv run python evals\\runners\\run_mock.py", False),
    ("uv run python evals/runners/mock_batch.py", False),
    ("uv run python evals\\graders\\live_check.py", False),
    ("Get-ChildItem docs\\runbooks", False),
    # the lead's pytest-based live command with the second key inline stays allowed (backslashes in
    # the rest of the command do not change that)
    ("$env:ALLOW_LIVE_LLM='1'; uv run pytest tests\\live -m live", False),
    ("$env:ALLOW_LIVE_LLM='1'; uv run pytest tests\\live\\test_l1_discover.py -m live", False),
]


def _hook() -> ModuleType:
    return _load(HOOK, "guard_live_under_test")


@pytest.mark.parametrize(("command", "blocked"), CASES, ids=[repr(c) for c, _ in CASES])
def test_guard_hook_matchers(command: str, blocked: bool) -> None:
    assert _hook().is_live_command(command) is blocked


def _run_hook(
    payload: str, *, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "ALLOW_LIVE_LLM"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env
    )


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_hook_process_exit_codes(tool: str) -> None:
    def call(command: str, **kw: dict[str, str]) -> subprocess.CompletedProcess[str]:
        payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
        return _run_hook(payload, **kw)  # type: ignore[arg-type]

    blocked = call("$env:LLM_MODE='li''ve'")
    assert blocked.returncode == 2 and "guard_live" in blocked.stderr
    assert call("just live-smoke").returncode == 2
    assert call("just test").returncode == 0
    # the lead's live session: ALLOW_LIVE_LLM=1 in the environment lets everything through
    allowed_env = {"env_extra": {"ALLOW_LIVE_LLM": "1"}}
    assert call("just live-smoke", **allowed_env).returncode == 0
    assert call("$env:LLM_MODE='live'", **allowed_env).returncode == 0
    # ... but only the value 1
    assert call("just live-smoke", env_extra={"ALLOW_LIVE_LLM": "0"}).returncode == 2
    # the legitimate inline batch is not blocked either way
    assert call("$env:ALLOW_LIVE_LLM='1'; uv run pytest tests/live -m live").returncode == 0
    # N10: the Windows spelling of the batch runner's path is blocked like the slash one ...
    runner = "uv run python evals\\runners\\live_batch.py --batch L1"
    assert call(runner).returncode == 2
    assert call(runner.replace("\\", "/")).returncode == 2
    # ... and passes only in the lead's live session
    assert call(runner, **allowed_env).returncode == 0


@pytest.mark.parametrize(
    "payload",
    ["not json", "", "[]", '{"tool_input": 5}', '{"tool_input": {"command": 5}}', "{}"],
    ids=["garbage", "empty", "list", "bad tool_input", "non-string command", "empty object"],
)
def test_guard_hook_errors_never_block(payload: str) -> None:
    assert _run_hook(payload).returncode == 0


def test_guard_hook_is_registered_for_both_shell_tools() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]
    matchers = {m for entry in entries for m in entry["matcher"].split("|")}
    assert {"Bash", "PowerShell"} <= matchers
    for entry in entries:
        commands = [h["command"] for h in entry["hooks"] if h["type"] == "command"]
        assert any("guard_live.py" in c for c in commands), entry["matcher"]
    # the formatter still runs after edits
    assert "Edit" in settings["hooks"]["PostToolUse"][0]["matcher"]


def test_secret_reading_deny_rules_cover_powershell_too() -> None:
    deny = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert "Read(./.env)" in deny
    for rule in ("PowerShell(Get-Content .env*)", "PowerShell(Get-ChildItem env:*)"):
        assert rule in deny
    assert any(r.startswith("PowerShell(") and "OPENAI_API_KEY" in r for r in deny)


def test_format_hook_ignores_non_python_files_and_garbage(tmp_path: Path) -> None:
    fmt = ROOT / ".claude" / "hooks" / "format_py.py"
    note = tmp_path / "note.txt"
    note.write_text("a   b", encoding="utf-8")
    payload = json.dumps({"tool_input": {"file_path": str(note)}})
    result = subprocess.run(
        [sys.executable, str(fmt)], input=payload, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0 and note.read_text(encoding="utf-8") == "a   b"
    garbage = subprocess.run(
        [sys.executable, str(fmt)], input="not json", capture_output=True, text=True, check=False
    )
    assert garbage.returncode == 0
