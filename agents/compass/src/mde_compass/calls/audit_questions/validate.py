"""Structural checks on the auditor's output: one verdict per input question, honest scores, issues that explain
every low score or flag, and a ``fix`` that is a hint, not a rewritten question. Whether a verdict means
"reject" is code elsewhere (the engine); this only refuses output that is not an audit at all, so the gateway's
one repair or the deterministic heuristic takes over."""

from __future__ import annotations

from mde_compass.calls.audit_questions.schema import AuditInput, AuditOutput, VerdictWire

MIN_SPECIFIC = 1
MAX_SPECIFIC = 5
ACCEPT_SPECIFIC = 3  # below this a question is generic
MAX_FIX_WORDS = 60
_QUESTION_MARKS = ("?", "？", "؟")  # ASCII, fullwidth, Arabic: a question mark means a rewrite


def is_flagged(v: VerdictWire) -> bool:
    """Any boolean flag that makes a question unacceptable."""
    return (
        v.leading
        or v.jargon
        or bool(v.duplicate_of.strip())
        or not v.answerable
        or not v.maps_to_need
    )


def structural(inp: AuditInput, out: AuditOutput) -> list[str]:
    errors: list[str] = []
    wanted = [q.card_id for q in inp.questions]
    got = [v.card_id for v in out.verdicts]
    if len(got) != len(set(got)):
        errors.append("a card id has more than one verdict")
    missing = [i for i in wanted if i not in got]
    extra = [i for i in dict.fromkeys(got) if i not in wanted]
    if missing:
        errors.append(f"no verdict for {', '.join(missing)}")
    if extra:
        errors.append(f"verdict for unknown question {', '.join(extra)}")
    for v in out.verdicts:
        where = f"verdict {v.card_id!r}"
        if not MIN_SPECIFIC <= v.specific <= MAX_SPECIFIC:
            errors.append(f"{where}: specific must be {MIN_SPECIFIC} to {MAX_SPECIFIC}")
        if (v.specific < ACCEPT_SPECIFIC or is_flagged(v)) and not any(i.strip() for i in v.issues):
            errors.append(f"{where}: a low score or a flag needs at least one issue")
        if v.duplicate_of.strip() == v.card_id:
            errors.append(f"{where}: a question cannot duplicate itself")
        if any(mark in v.fix for mark in _QUESTION_MARKS):
            errors.append(f"{where}: fix looks like a rewritten question (it asks something)")
        if len(v.fix.split()) > MAX_FIX_WORDS:
            errors.append(f"{where}: fix is not a short hint")
    return errors
