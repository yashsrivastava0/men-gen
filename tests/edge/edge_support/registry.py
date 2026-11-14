"""One test per case ID, a fixture folder per ID, and a link check that fails the build on a mismatch.

Case ids are the document ids lower-cased: `SPC-IN-01` -> `spc-in-01`; doc 18's `FN-07` -> `spc-fn-07`; doc 19's `BG-03` ->
`spc-bg-03`. A fixture is a folder `tests/edge/fixtures/<id>/` holding `case.json` (id, kind: real|mut|syn|mock|cfg, what the
handling rule says, the test that proves it) and any input files the case needs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "architecture-v3"
FIXTURES = ROOT / "tests" / "edge" / "fixtures"
F = TypeVar("F", bound=Callable[..., Any])

REGISTRY: dict[str, list[str]] = {}  # case id -> test function names that prove it


def case(*ids: str) -> Callable[[F], F]:
    """Mark a test as the proof of one or more cases (ids as `spc-in-01`)."""

    def deco(fn: F) -> F:
        for i in ids:
            REGISTRY.setdefault(i.lower(), []).append(fn.__qualname__)
        return fn

    return deco


_SPC = re.compile(r"\|\s*(SPC-(?:IN|RS|SC|MD|FE|CH|OP|UX|CL|MK|ST|DG|SZ)-\d{2})\s*\|")
_FN = re.compile(r"\|\s*(FN-\d{2})\s*\|")
_BG = re.compile(r"\|\s*(BG-\d{2})\s*\|")


def doc_case_ids() -> set[str]:
    """Every case id the design documents define (doc 15, 17, 18, 19), lower-cased with the SPC- prefix."""
    out: set[str] = set()
    for name, rx in (("15-spec-agent-design.md", _SPC), ("17-spec-content-model.md", _SPC)):
        out |= {m.lower() for m in rx.findall((DOCS / name).read_text(encoding="utf-8"))}
    out |= {
        f"spc-{m.lower()}"
        for m in _FN.findall((DOCS / "18-spec-final-architecture.md").read_text(encoding="utf-8"))
    }
    out |= {
        f"spc-{m.lower()}"
        for m in _BG.findall(
            (DOCS / "19-spec-big-apps-bench-and-result-first.md").read_text(encoding="utf-8")
        )
    }
    return out


def fixture_ids() -> set[str]:
    return {p.name for p in FIXTURES.iterdir() if p.is_dir()} if FIXTURES.is_dir() else set()


def registered_ids_by_ast() -> dict[str, list[str]]:
    """Case ids named by `@case(...)` in every tests/edge/test_*.py, read statically (no import needed)."""
    import ast

    found: dict[str, list[str]] = {}
    for path in sorted((ROOT / "tests" / "edge").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "case"
                    ):
                        for a in dec.args:
                            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                                found.setdefault(a.value.lower(), []).append(
                                    f"{path.name}::{node.name}"
                                )
    return found
