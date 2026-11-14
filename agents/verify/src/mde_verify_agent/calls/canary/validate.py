"""Local checks of the canary's output."""

from __future__ import annotations

from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut
from mde_verify_agent.digest import match_criterion

MAX_QUESTIONS = 12
MAX_OUTLINE = 40


def validate(inp: CanaryIn, out: CanaryOut) -> list[str]:
    errors: list[str] = []
    if len(out.blocking_questions) > MAX_QUESTIONS:
        errors.append(
            f"more than {MAX_QUESTIONS} blocking questions; keep the ones that truly block"
        )
    if len(out.outline) > MAX_OUTLINE:
        errors.append(f"outline longer than {MAX_OUTLINE} steps")
    wanted = list(inp.criteria)
    got: list[str] = []
    for c in out.acceptance_coverage:
        m = match_criterion(c.criterion, wanted)
        if m is None:
            errors.append(
                f"acceptance_coverage names a criterion that is not in the list: {c.criterion[:80]!r}"
            )
        else:
            got.append(m)
    # A criterion the reader did not judge is NOT a reason to re-ask: the port records it as not judged.
    if len(got) != len(set(got)):
        errors.append("acceptance_coverage repeats a criterion")
    for c in out.acceptance_coverage:
        if not c.satisfiable and not out.blocking_questions and not c.how.strip():
            errors.append("an unsatisfiable criterion needs a reason in `how`")
    return errors
