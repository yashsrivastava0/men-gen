"""Source hygiene of the lane: no model or vendor name (rule 14), no import of another agent or of Verify (layering),
no eval or exec (rule 9), no markdown outside docs, no agent framework."""

from __future__ import annotations

import ast
import re

from .conftest import LANE_SRC

VENDOR_WORDS = re.compile(
    r"openai|anthropic|claude|gpt|gemini|haiku|sonnet|opus|llama|mistral|qwen|deepseek|kimi|moonshot|glm|minimax|"
    r"cohere|grok|bedrock|vertex|azure",
    re.IGNORECASE,
)
FORBIDDEN_IMPORTS = (
    "mde_verify_agent",
    "mde_research_agent",
    "mde_compass",
    "mde_flow",
    "mde_context_api",
)


def _files(*suffixes: str):
    return [p for p in LANE_SRC.rglob("*") if p.suffix in suffixes and "__pycache__" not in p.parts]


def test_no_vendor_or_model_name_anywhere_in_the_lane():
    hits = []
    for p in _files(".py", ".jinja", ".yaml"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if VENDOR_WORDS.search(line):
                hits.append((str(p.relative_to(LANE_SRC)), n, line.strip()[:80]))
    assert hits == []


def test_no_import_of_verify_or_another_agent():
    bad = []
    for p in _files(".py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            bad += [(p.name, m) for m in mods if m.split(".")[0] in FORBIDDEN_IMPORTS]
    assert bad == []


def test_no_eval_exec_framework_or_markdown():
    for p in _files(".py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec"), p.name
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                assert not any(
                    n.split(".")[0] in ("langchain", "langgraph", "crewai", "autogen", "dspy")
                    for n in names
                )
    assert not list(LANE_SRC.rglob("*.md"))


def test_every_call_folder_has_the_adr_0014_files():
    for name in ("plan", "plan_fused", "prod_module", "prod_integ", "prod_journeys"):
        d = LANE_SRC / name
        for f in (
            "spec.py",
            "schema.py",
            "system.jinja",
            "user.jinja",
            "mock.py",
            "fallback.py",
            "validate.py",
        ):
            assert (d / f).is_file(), (name, f)
