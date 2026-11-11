"""Static hardening scans (security review hardening).

* Source never uses ``yaml.load``, ``pickle`` and friends, builtin ``compile``, a non-sandboxed
  jinja2 ``Environment``, ``shell=True``, ``os.system``/``os.popen`` or ``subprocess`` with a string
  command (``packages/*/src``, ``apps/*/src``, ``agents/*/src``, ``scripts``). A justified exception carries the marker
  comment ``# hardening: allow <reason>`` on a line of the call.
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


# ============================================================================= Claude settings
SETTINGS = ROOT / ".claude" / "settings.json"


def test_format_hook_is_registered_after_edits() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
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
