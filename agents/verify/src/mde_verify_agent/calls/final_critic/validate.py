"""Local checks of the final critic's output."""

from __future__ import annotations

from mde_verify_agent.calls.final_critic.schema import FinalIn, FinalOut
from mde_verify_agent.digest import confirm_item_id, ids_in, lines_of, parse_confirm_lines

MAX_FINDINGS = 25
MAX_DIFFERS = 30


def known_ids(inp: FinalIn) -> set[str]:
    text = "\n".join(
        [
            *lines_of(inp.digest),
            *lines_of(inp.facts),
            *lines_of(inp.coverage),
            *lines_of(inp.confirm),
            *lines_of(inp.code_findings),
        ]
    )
    return ids_in(text)


def validate(inp: FinalIn, out: FinalOut) -> list[str]:
    errors: list[str] = []
    known = known_ids(inp)
    if len(out.findings) > MAX_FINDINGS:
        errors.append(
            f"too many findings ({len(out.findings)} > {MAX_FINDINGS}); keep the most serious"
        )
    if len(out.differs_from_brief) > MAX_DIFFERS:
        errors.append(f"too many differences ({len(out.differs_from_brief)} > {MAX_DIFFERS})")
    seen: set[tuple[str, str, str]] = set()
    for i, f in enumerate(out.findings):
        if f.node not in known:
            errors.append(f"findings[{i}]: node {f.node!r} is not an id in the data")
        if not f.change.strip() or not f.reason.strip():
            errors.append(f"findings[{i}]: change and reason must not be empty")
        key = (f.node, f.kind, f.field)
        if key in seen:
            errors.append(f"findings[{i}]: duplicate of an earlier finding on {f.node}")
        seen.add(key)
    items = parse_confirm_lines(lines_of(inp.confirm))
    ids = {str(c["id"]) for c in items}
    for i, m in enumerate(out.confirm_moves):
        if confirm_item_id(m.item, ids) is None:
            errors.append(f"confirm_moves[{i}]: {m.item!r} is not an item of the confirm list")
        if not 1 <= m.to_rank <= max(len(items), 1):
            errors.append(f"confirm_moves[{i}]: to_rank {m.to_rank} is outside 1..{len(items)}")
    return errors
