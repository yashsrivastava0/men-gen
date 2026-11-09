"""Mock handler of ``cmp.plan_questions``: clearly labelled ``[mock]`` questions built from the idea's own terms
and the Needs Registry (never from a fixed list of frames). For offline tests and demos only: a session running
on it shows the MOCK MODE banner in the UI, so it is never mistaken for the product."""

from __future__ import annotations

from mde_compass.calls.plan_questions.fallback import empty_hypothesis
from mde_compass.calls.plan_questions.schema import (
    NeedWire,
    OptionWire,
    PlanInput,
    PlanOutput,
    QuestionWire,
)

TAG = "[mock]"
OPEN = ("missing", "partial")


def _ordered(inp: PlanInput) -> list[NeedWire]:
    pool = [n for n in inp.needs if n.state in OPEN]
    pool.sort(
        key=lambda n: (n.need_id not in inp.focus_needs, -n.impact)
    )  # stable: registry order inside
    return pool


def _question(
    n: NeedWire, term: str, qid: str, group: int, *, followup: bool = False
) -> QuestionWire:
    if followup:
        gaps = ", ".join(n.missing_aspects) or "the details"
        text = f"{TAG} About {term}: could you say a little more about {gaps}?"
    else:
        text = f"{TAG} About {term}: what should we know regarding {n.label.lower()}?"
    options: list[OptionWire] = []
    qtype = "text"
    if not followup and n.card_type == "choose_one" and len(n.option_labels) >= 3:
        qtype = "choose_one"
        options = [
            OptionWire(
                label=f"{TAG} {label}",
                description="",
                basis="suggestion",
                reason=f"{TAG} a canned option" if i == 0 else "",
            )
            for i, label in enumerate(n.option_labels[:3])
        ]
    return QuestionWire(
        id=qid,
        group_id=f"G{group}",
        group_title=f"{TAG} Group {group}",
        intent=f"learn {n.label.lower()} for {term}",
        serves=[n.need_id],
        unlocks=[f"{TAG} Brief: {n.label}"],
        answer_spec=n.sufficient,
        depends_on=[],
        priority=max(1, min(5, 6 - n.impact)),
        type=qtype,  # type: ignore[arg-type]
        text=text,
        why_we_ask=f"{TAG} This fills in: {n.label}.",
        options=options,
        recommended=options[0].label if options else "",
        skip_assumes="",
        followup_hint=f"{TAG} ask about {', '.join(n.missing_aspects)}"
        if n.missing_aspects
        else "",
        examples=[] if options else [f"{TAG} e.g. a short answer about {term}"],
    )


def handle(inp: PlanInput) -> PlanOutput:
    terms = list(inp.project_terms) or ["your app"]
    needs = _ordered(inp)
    if inp.mode in ("followup", "simplify"):
        target = next(
            (n for n in needs if n.need_id in inp.focus_needs), needs[0] if needs else None
        )
        picked = [target] if target is not None else []
    else:
        picked = needs[: max(inp.want_min, min(inp.want_max, len(needs)))]
    size = max(1, -(-len(picked) // max(1, min(inp.group_max, 2)))) if picked else 1
    questions = [
        _question(
            n,
            terms[i % len(terms)],
            f"Q-{inp.next_card_n + i:03d}",
            i // size + 1,
            followup=inp.mode == "followup",
        )
        for i, n in enumerate(picked)
    ]
    return PlanOutput(
        domain_hypothesis=empty_hypothesis(),
        questions=questions,
        covered_needs=[q.serves[0] for q in questions],
        assumed_instead=[],
        expert_gaps=[],
        backlog=[],
    )
