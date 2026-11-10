"""Structural check on a module draft. Finer problems (an unknown role, a pattern outside the list, a step on a screen of
another module) are decided in code by ``engine/structure_draft`` and repaired or reported; this validator only refuses
an output that cannot be used at all: a requested module with no screens, or screens that cannot be told apart."""

from __future__ import annotations

from mde_compass.calls.draft_module.schema import ModuleInput, ModuleOutput


def structural(inp: ModuleInput, out: ModuleOutput) -> list[str]:
    problems: list[str] = []
    per_module: dict[str, int] = {}
    for s in out.screens:
        per_module[s.module] = per_module.get(s.module, 0) + 1
    for module in inp.modules:
        if not per_module.get(module.ref):
            problems.append(f"module {module.ref} has no screens")
    refs = [s.ref for s in out.screens]
    if len(set(refs)) != len(refs):
        problems.append("two screens share a ref")
    if any(not s.name.strip() for s in out.screens):
        problems.append("a screen has no name")
    return problems
