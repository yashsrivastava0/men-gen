from __future__ import annotations

from evals.spec.calls.diversity.schema import DiversityIn, DiversityOut


def validate(inp: DiversityIn, out: DiversityOut) -> list[str]:
    errors: list[str] = []
    if not 1 <= out.score <= 5:
        errors.append("score must be between 1 and 5")
    if len(out.notes) > 3:
        errors.append("at most three notes")
    if inp.question not in ("between_app", "within_app"):
        errors.append("unknown question")
    return errors
