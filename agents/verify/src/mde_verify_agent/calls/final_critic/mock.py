"""Mock handler: deterministic and plausible, derived from the input only.

Coverage gaps the self-check listed become class B findings (a lane call writes the missing part); a Locked fact whose
words under half appear in the digest is a brief mismatch; the confirm list is re-ranked in code terms.
"""

from __future__ import annotations

import re

from mde_verify_agent.calls.final_critic.analyse import confirm_moves, differs
from mde_verify_agent.calls.final_critic.schema import FinalIn, FinalOut, FindingOut
from mde_verify_agent.digest import lines_of

_WORD = re.compile(r"[a-z]{5,}")
_GAP = re.compile(r"^GAP (?P<id>\S+?): (?P<problem>.*?)(?: \[(?P<kind>[^\]]*)\])?$")
_FACT = re.compile(r"^FACT (?P<id>\S+) -> (?P<node>[A-Za-z0-9\-]+)(?:\.\w+)?: (?P<text>.*)$")


def handle(inp: FinalIn) -> FinalOut:
    findings: list[FindingOut] = []
    code = "\n".join(lines_of(inp.code_findings))
    for line in lines_of(inp.coverage):
        m = _GAP.match(line)
        if m and m["id"] not in code:
            findings.append(
                FindingOut(
                    kind="coverage_gap",
                    node=m["id"],
                    field="",
                    change="write the missing part",
                    reason=m["problem"],
                    severity="warning",
                    cls="B",
                )
            )
    digest_words = set(_WORD.findall("\n".join(lines_of(inp.digest)).lower()))
    for line in lines_of(inp.facts):
        m = _FACT.match(line)
        words = set(_WORD.findall(m["text"].lower())) if m else set()
        if m and words and len(words & digest_words) / len(words) < 0.5 and m["id"] not in code:
            findings.append(
                FindingOut(
                    kind="brief_mismatch",
                    node=m["node"],
                    field="",
                    change=f"bring back what {m['id']} says",
                    reason=f"the Brief says {m['text']!r} but nothing in the spec carries it",
                    severity="blocker",
                    cls="B",
                )
            )
    findings = findings[:25]
    return FinalOut(
        findings=findings,
        confirm_moves=confirm_moves(inp),
        differs_from_brief=differs(inp),
        summary=f"Final pass: {len(findings)} finding(s) beyond the code checks.",
    )
