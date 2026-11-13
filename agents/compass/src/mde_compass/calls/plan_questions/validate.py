"""Structural checks on the planner's output. The real gate is code that has the ledger and the catalog
(``engine.question_validators``) plus the auditor; this only refuses output that is not a batch at all, so
the gateway's one repair or the flagged fallback takes over."""

from __future__ import annotations

import re

from mde_compass.calls.plan_questions.schema import PlanInput, PlanOutput
from mde_compass.engine.question_validators import CLOSING_MIN_OPTIONS

_CHOICE = {"choose_one", "pick_many", "priority"}
_FREE = {"text", "quick_form", "upload_or_link"}
_ID = re.compile(r"^Q-\d{3,}$")


def _closing(out: PlanOutput) -> list[str]:
    """Mode closing: the reserved last question must be there, as a priority question with enough options to
    choose from. Everything finer (the options' source, the cap on suggestions, the needs) is code elsewhere."""
    q = next((q for q in out.questions if q.type == "priority"), None)
    if q is None:
        return ["closing: no priority question (what matters most in the first version)"]
    errors: list[str] = []
    if not q.text.strip() or not q.intent.strip():
        errors.append(f"question {q.id!r}: empty text or intent")
    if len(q.options) < CLOSING_MIN_OPTIONS:
        errors.append(
            f"question {q.id!r}: a closing question needs at least {CLOSING_MIN_OPTIONS} options"
        )
    if any(not o.label.strip() for o in q.options):
        errors.append(f"question {q.id!r}: an option has no label")
    return errors


def structural(inp: PlanInput, out: PlanOutput) -> list[str]:
    errors: list[str] = []
    questions = out.questions
    if inp.mode == "closing":
        return _closing(out)
    if not questions and inp.mode not in ("replan", "deepen", "followup"):
        errors.append("no questions in a mode that must ask something")
    if len(questions) > max(inp.want_max * 2, 6):
        errors.append(f"far too many questions ({len(questions)} for at most {inp.want_max})")
    titles: dict[str, str] = {}
    for q in questions:
        if not q.group_id.strip() or not q.group_title.strip():
            errors.append(f"question {q.id!r}: no group id or title")
            continue
        if titles.setdefault(q.group_id, q.group_title) != q.group_title:
            errors.append(f"group {q.group_id!r} has two different titles")
    # more groups than asked for is not fatal: the engine cuts the batch to size
    seen: set[str] = set()
    for q in questions:
        where = f"question {q.id!r}"
        if not _ID.match(q.id) or q.id in seen:
            errors.append(f"{where}: id is not a unique Q-### number")
        seen.add(q.id)
        if not q.text.strip():
            errors.append(f"{where}: empty text")
        if not q.intent.strip():
            errors.append(f"{where}: empty intent")
        if not q.serves:
            errors.append(f"{where}: serves no need")
        # priority is only an ordering hint: an out-of-range value is clamped by the adapter, never fatal
        if q.type in _CHOICE and len(q.options) < 2:
            errors.append(f"{where}: a {q.type} question needs options")
        # options on a free-answer question are dropped by the adapter (not fatal)
        # a `recommended` that is not one of the options is ignored by the adapter (no option is marked)
        if any(not o.label.strip() for o in q.options):
            errors.append(f"{where}: an option has no label")
    return errors
