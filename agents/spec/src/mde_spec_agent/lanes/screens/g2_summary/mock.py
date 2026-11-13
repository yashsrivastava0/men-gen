"""Mock handler of G2-SUMMARY: deterministic. One paragraph built only from the numbers and lines of the input."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.g2_summary.schema import G2In, G2Out


def handle(inp: G2In) -> G2Out:
    n = {x.name: x.value for x in inp.numbers}
    parts = [
        f"This release has {n.get('screens', 0)} screens across {n.get('apps', 0)} app(s), "
        f"{n.get('entities', 0)} kinds of data and {n.get('operations', 0)} operations."
    ]
    if n.get("later", 0):
        parts.append(f"{n['later']} feature(s) are planned for later.")
    if n.get("assumed", 0) or n.get("degraded", 0):
        parts.append(
            f"{n.get('assumed', 0)} item(s) rest on an assumption and {n.get('degraded', 0)} were written by a safe default; both are marked."
        )
    if n.get("confirm", 0):
        parts.append(f"Please confirm the {n['confirm']} item(s) listed first before you approve.")
    return G2Out(paragraph=" ".join(parts))
