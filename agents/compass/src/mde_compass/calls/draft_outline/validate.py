"""Structural check on the outline. Everything finer (a pointer that does not resolve, a feature no module delivers) is
decided in code by ``engine/structure_draft`` and repaired or reported, so one slip never voids a whole outline; this
validator only refuses an output that cannot be used at all."""

from __future__ import annotations

from mde_compass.calls.draft_outline.schema import OutlineInput, OutlineOutput


def structural(inp: OutlineInput, out: OutlineOutput) -> list[str]:
    problems: list[str] = []
    wanted = [f for f in inp.features if f.scope_status == "in"]
    if wanted and not out.modules:
        problems.append("no modules for a request that has in-scope features")
    for name, refs in (
        ("module", [m.ref for m in out.modules]),
        ("entity", [e.ref for e in out.entities]),
        ("journey", [j.ref for j in out.journeys]),
    ):
        if len(set(refs)) != len(refs):
            problems.append(f"two {name}s share a ref")
        if any(not r.strip() for r in refs):
            problems.append(f"a {name} has an empty ref")
    if any(not m.name.strip() for m in out.modules):
        problems.append("a module has no name")
    return problems
