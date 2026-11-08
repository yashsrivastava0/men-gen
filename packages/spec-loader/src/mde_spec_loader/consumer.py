"""The consumer simulator: walks a spec the way the frontend engine will (a contract test).

It slices every build step and every screen, measures the slices, and reports slices over the
budget, references that do not resolve, screens that no step builds (or builds twice) and a hash
that does not verify. ``scripts/simulate_frontend_consumer.py`` is a thin command-line wrapper.
"""

from __future__ import annotations

from mde_spec_loader.errors import UnknownIdError
from mde_spec_loader.index import SpecIndex
from mde_spec_loader.models import ConsumerReport, SliceReport

DEFAULT_BUDGET_TOKENS = 10_000


def simulate_consumer(
    index: SpecIndex, *, budget_tokens: int = DEFAULT_BUDGET_TOKENS
) -> ConsumerReport:
    spec = index.spec
    reports: list[SliceReport] = []
    problems: list[str] = []

    def measure(target: str, kind: str) -> None:
        try:
            sliced = index.slice(target)
        except UnknownIdError as exc:
            problems.append(f"cannot slice {target}: {exc}")
            return
        over = sliced.token_estimate > budget_tokens
        reports.append(
            SliceReport(
                id=target,
                kind="step" if kind == "step" else "screen",
                tokens=sliced.token_estimate,
                over_budget=over,
            )
        )
        if over:
            problems.append(
                f"slice {target} is {sliced.token_estimate} tokens, over the {budget_tokens} budget"
            )

    for step in spec.build.steps:
        measure(step.id, "step")
    for screen in spec.screens:
        measure(screen.id, "screen")

    scheduled: dict[str, int] = {}
    for step in spec.build.steps:
        for screen_id in step.screens:
            scheduled[screen_id] = scheduled.get(screen_id, 0) + 1
        for group in step.parallel:
            for screen_id in group.screens:
                scheduled[screen_id] = scheduled.get(screen_id, 0) + 1
    unscheduled = [s.id for s in spec.screens if scheduled.get(s.id, 0) != 1]
    for screen_id in unscheduled:
        count = scheduled.get(screen_id, 0)
        problems.append(
            f"screen {screen_id} is built by no step"
            if count == 0
            else f"screen {screen_id} is built by {count} steps"
        )

    unresolved = index.unresolved_refs()
    problems.extend(f"unresolved reference: {u}" for u in unresolved)

    hash_ok = index.verify()
    if not hash_ok:
        problems.append("the embedded hash does not match the file content")

    return ConsumerReport(
        budget_tokens=budget_tokens,
        slices=reports,
        unresolved=unresolved,
        unscheduled=unscheduled,
        hash_ok=hash_ok,
        problems=problems,
    )
