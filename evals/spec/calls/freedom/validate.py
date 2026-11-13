from __future__ import annotations

from evals.spec.calls.freedom.schema import FreedomIn, FreedomOut


def validate(inp: FreedomIn, out: FreedomOut) -> list[str]:
    errors: list[str] = []
    if out.arrangement not in inp.arrangements:
        errors.append(f"arrangement {out.arrangement!r} is not in the closed list")
    if sorted(out.region_order) != sorted(inp.region_keys):
        errors.append("region_order must list every region key of the card exactly once")
    return errors
