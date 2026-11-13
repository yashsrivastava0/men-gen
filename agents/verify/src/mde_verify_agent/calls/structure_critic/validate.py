"""Local checks of the structure critic's output (code says no; the runner re-asks once, then falls back)."""

from __future__ import annotations

from mde_verify_agent.calls.structure_critic.schema import StructureIn, StructureOut
from mde_verify_agent.digest import ids_in, lines_of

MAX_FINDINGS = 25


def known_ids(inp: StructureIn) -> set[str]:
    text = "\n".join([*lines_of(inp.digest), *lines_of(inp.facts), *lines_of(inp.code_findings)])
    return ids_in(text)


def validate(inp: StructureIn, out: StructureOut) -> list[str]:
    errors: list[str] = []
    known = known_ids(inp)
    if len(out.findings) > MAX_FINDINGS:
        errors.append(
            f"too many findings ({len(out.findings)} > {MAX_FINDINGS}); keep the most serious"
        )
    seen: set[tuple[str, str, str]] = set()
    for i, f in enumerate(out.findings):
        where = f"findings[{i}]"
        if f.node not in known:
            errors.append(f"{where}: node {f.node!r} is not an id in the digest")
        if not f.change.strip() or not f.reason.strip():
            errors.append(f"{where}: change and reason must not be empty")
        key = (f.node, f.kind, f.field)
        if key in seen:
            errors.append(f"{where}: duplicate of an earlier finding on {f.node}")
        seen.add(key)
    return errors
