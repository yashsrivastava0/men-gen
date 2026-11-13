"""What code can already say about the final pass, shared by the mock handler and the fallback: the confirm list's
re-ranking (money, legal, identity first) and Spec's own list of differences passed through."""

from __future__ import annotations

from mde_verify_agent.calls.final_critic.schema import FinalIn, MoveOut
from mde_verify_agent.digest import lines_of, parse_confirm_lines

_FIRST = ("money", "legal", "identity")


def confirm_moves(inp: FinalIn) -> list[MoveOut]:
    items = parse_confirm_lines(lines_of(inp.confirm))
    first_other = next((c["rank"] for c in items if c["category"] not in _FIRST), None)
    if first_other is None:
        return []
    moves: list[MoveOut] = []
    target = first_other
    for c in items:
        if c["category"] in _FIRST and c["rank"] > first_other:
            moves.append(
                MoveOut(
                    item=str(c["id"]),
                    to_rank=target,
                    reason=f"a {c['category']} item must come before ordinary ones",
                )
            )
            target += 1
    return moves


def differs(inp: FinalIn) -> list[str]:
    return list(dict.fromkeys(ln.strip() for ln in lines_of(inp.differs)))[:30]
