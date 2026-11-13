"""Mock handler: deterministic and plausible, derived from the input only.

It looks for the one thing a lexical rule can see: a Locked fact of the Brief whose words (five letters or more)
of which under half appear in the structure digest (a flow the structure does not carry). Code findings already naming the fact
(a dropped or changed node) are not repeated.
"""

from __future__ import annotations

import re

from mde_verify_agent.calls.structure_critic.schema import FindingOut, StructureIn, StructureOut
from mde_verify_agent.digest import lines_of

_FACT = re.compile(
    r"^FACT (?P<id>\S+) -> (?P<node>[A-Za-z0-9\-]+)(?:\.(?P<field>\w+))?: (?P<text>.*)$"
)
_WORD = re.compile(r"[a-z]{5,}")


def handle(inp: StructureIn) -> StructureOut:
    digest = "\n".join(lines_of(inp.digest))
    digest_words = set(_WORD.findall(digest.lower()))
    code = "\n".join(lines_of(inp.code_findings))
    findings: list[FindingOut] = []
    for line in lines_of(inp.facts):
        m = _FACT.match(line)
        if not m or m["id"] in code:
            continue
        words = set(_WORD.findall(m["text"].lower()))
        if words and len(words & digest_words) / len(words) < 0.5:
            findings.append(
                FindingOut(
                    kind="missing_flow",
                    node=m["node"],
                    field=m["field"] or "",
                    change=f"add what {m['id']} asks for",
                    reason=f"the Brief states {m['text']!r} but nothing in the structure covers it",
                    severity="blocker",
                    cls="B",
                )
            )
    summary = (
        f"Checked the Brief against the structure: {len(findings)} thing(s) the code checks cannot see."
        if findings
        else "Nothing further found beyond the code checks."
    )
    return StructureOut(findings=findings, summary=summary)
