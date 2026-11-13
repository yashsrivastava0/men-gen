"""The review of the compiled Brief by ``cmp.scan_ambiguity`` (ADR 0016), as the service calls it after the compile.

Findings never change a value (the scanner has no way to). What they become:

* what does not add up (a contradiction, an unrealistic claim, a missing dependency, numbers or dates that do not fit):
  an assumption without a slot (basis ``review``) that the person decides at Gate 1. It is ``high`` impact, so it
  blocks approval until decided, only when the touched slot has impact 4 or more or must be confirmed;
* what is merely unclear (a vague word, an unexplained term): a NON-BLOCKING open question, at most three, as before.

Old review assumptions that no longer apply are dropped while still undecided; decided ones stay as the audit trail
(``assumptions.merge_derived``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mde_compass.domain.brief import OpenQuestion
from mde_compass.engine.assumptions import merge_derived
from mde_compass.engine.compile_types import ReviewFinding, ReviewScanner
from mde_compass.engine.derived_assumptions import ASSUMPTION_KINDS, review_from_findings

if TYPE_CHECKING:  # pragma: no cover
    from mde_compass.engine.service import Service, _Turn

MAX_OPEN_QUESTIONS = 3
QUESTION_CHARS = 200


async def scan_findings(svc: Service, turn: _Turn) -> list[ReviewFinding]:
    """The scanner's findings, as the rich type whichever port the scanner implements."""
    assert svc.ports is not None
    scanner = svc.ports.ambiguity_scanner
    ctx = svc._ctx(turn)
    if isinstance(scanner, ReviewScanner):
        return await scanner.scan_review(ctx, turn.brief)
    return [
        ReviewFinding(slot_id=f.slot_id, text=f.text, kind=f.kind)
        for f in await scanner.scan(ctx, turn.brief)
    ]


async def flag_review(
    svc: Service, turn: _Turn, findings: list[ReviewFinding] | None = None
) -> list[ReviewFinding]:
    """Record the scanner's findings (assumptions and open questions); returns them. ``findings`` may have been
    read earlier, in parallel with research seeding (the scan only needs the compiled Brief)."""
    if findings is None:
        findings = await scan_findings(svc, turn)
    fresh = review_from_findings(findings, svc.catalog, turn.lang)
    turn.brief = merge_derived(turn.brief, fresh, ("review",))
    known = {q.text.strip().lower() for q in turn.brief.open_questions}
    added = 0
    for finding in findings:
        text = finding.text.strip()
        if finding.kind in ASSUMPTION_KINDS or not text or text.lower() in known:
            continue
        if added >= MAX_OPEN_QUESTIONS:
            break
        turn.brief.open_questions.append(
            OpenQuestion(
                id=turn.brief.ids.allocate("Q"),
                text=text[:QUESTION_CHARS],
                blocking=False,
                owner="user",
                slot_id=finding.slot_id,
            )
        )
        known.add(text.lower())
        added += 1
    return findings
