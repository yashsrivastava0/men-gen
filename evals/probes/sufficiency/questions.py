"""Concrete questions about a screen, with their ground-truth answers read from the full spec."""

from __future__ import annotations

from dataclasses import dataclass

from evals.probes.sufficiency.models import ProbeQuestion, QuestionKind
from mde_contracts.spec import AppSpec, ScreenBlock

MAX_ACTION_QUESTIONS = 3


@dataclass(frozen=True)
class Question:
    question: ProbeQuestion
    expected: str


def _norm(value: str) -> str:
    return value.strip().strip("`'\"").lower()


def matches(expected: str, answer: str) -> bool:
    """Exact after trimming quotes and case (the answers are ids, routes and names)."""
    return _norm(expected) == _norm(answer)


def _ask(
    screen: ScreenBlock, kind: QuestionKind, text: str, expected: str, detail: str | None = None
) -> Question:
    qid = f"{screen.id}:{kind}" + (f":{detail}" if detail else "")
    return Question(
        ProbeQuestion(id=qid, kind=kind, subject=screen.id, detail=detail, text=text), expected
    )


def questions_for(spec: AppSpec, screen_id: str) -> list[Question]:
    """The questions a builder of ``screen_id`` would need answered, with the right answers."""
    screen = next(s for s in spec.screens if s.id == screen_id)
    sid = screen.id
    out = [
        _ask(screen, "route", f"What is the route of screen {sid}?", screen.route),
        _ask(screen, "recipe", f"Which recipe does screen {sid} use?", screen.recipe),
        _ask(
            screen,
            "visibility",
            f"Who can see screen {sid}: what is its auth value (public, authenticated or a role)?",
            screen.auth,
        ),
        _ask(
            screen,
            "flow",
            f"Which flow id does screen {sid} belong to? Answer none if it has no flow.",
            screen.flow or "none",
        ),
    ]
    if screen.data:
        out.append(
            _ask(
                screen,
                "bound_data",
                f"What is the first data binding of screen {sid} (an API or entity reference)?",
                screen.data[0].bind,
            )
        )
    for action in screen.actions[:MAX_ACTION_QUESTIONS]:
        target = action.go or action.calls or "none"
        out.append(
            _ask(
                screen,
                "action_target",
                f"On screen {sid}, the action '{action.name}': which screen id does it go to, or "
                "which API operation does it call?",
                target,
                detail=action.name,
            )
        )
    return out
