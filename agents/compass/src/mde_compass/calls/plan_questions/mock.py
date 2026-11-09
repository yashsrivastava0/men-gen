"""Mock handler of ``cmp.plan_questions``: clearly labelled ``[mock]`` questions built from the idea's own terms
and the Needs Registry (never from a fixed list of frames). For offline tests and demos only: a session running
on it shows the MOCK MODE banner in the UI, so it is never mistaken for the product."""

from __future__ import annotations

import re

from mde_compass.calls.plan_questions.fallback import empty_hypothesis
from mde_compass.calls.plan_questions.schema import (
    AssumedInsteadWire,
    NeedWire,
    OptionWire,
    PlanInput,
    PlanOutput,
    QuestionWire,
)

TAG = "[mock]"
OPEN = ("missing", "partial")


def _asked_intents(asked_list: str) -> list[str]:
    """The intents in the context pack's asked list (``Q-003 | status | intent`` per line), lower-cased."""
    out: list[str] = []
    for line in asked_list.splitlines():
        parts = line.split(" | ", 2)
        if len(parts) == 3:
            out.append(parts[2].strip().casefold())
    return out


def _asked(need: NeedWire, intents: list[str]) -> bool:
    """Was a question about this need already put (by this mock, or a flagged registry card)? A real planner reads
    the asked list and never re-proposes a need; neither does the mock, so a replan never repeats itself."""
    label = need.label.casefold()
    return any(i.startswith(f"learn {label} for ") or i == f"learn: {label}" for i in intents)


def _ordered(inp: PlanInput) -> list[NeedWire]:
    """The open needs (missing or partial) in the context pack, the focus first, then by impact. Outside a
    follow-up each need is proposed once: one that was already asked is left alone. A need marked ``assume`` (a safe
    default, nothing said) is not asked: ``handle`` lists it in ``assumed_instead``."""
    pool = [n for n in inp.needs if n.state in OPEN and not (n.assume and inp.mode != "followup")]
    if inp.mode not in ("followup", "simplify"):
        asked = _asked_intents(inp.asked_list)
        pool = [n for n in pool if not _asked(n, asked) or n.need_id in inp.followup_candidates]
    pool.sort(
        key=lambda n: (
            n.need_id not in inp.focus_needs,
            n.need_id not in inp.followup_candidates,
            -n.impact,
        )
    )  # stable: registry order inside
    return pool


def _question(
    n: NeedWire, term: str, qid: str, group: int, *, followup: bool = False, refines: str = ""
) -> QuestionWire:
    if followup:
        gaps = ", ".join(n.missing_aspects)
        text = f"{TAG} {n.label}: could you say more about {gaps or 'it'}, for your {term} project?"
    else:
        text = f"{TAG} {n.label}, for your {term} project?"
    options: list[OptionWire] = []
    qtype = "text"
    serves = [n.need_id]
    if not followup and n.card_type == "choose_one" and len(n.option_labels) >= 3:
        # The registry card of some list needs (apps, login methods) is a single choice. The mock keeps asking it so,
        # but as a question about how something works, which is what a domain topic next to the need says
        # (``type_fit`` judges only a choose_one whose needs ALL take a list). A mock checklist would be the
        # faithful card, but then the stop pass misses integrations.mode (the Brief compile fills
        # integrations.wanted after the defaults pass; test_interview_followups): an engine gap to close first.
        if n.multi:
            serves.append(f"domain:{n.need_id.replace('.', '_')}_choice")
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
        intent=(
            f"learn more about {n.label.lower()} for {term}"
            if followup
            else f"learn {n.label.lower()} for {term}"
        ),
        serves=serves,
        unlocks=[f"{TAG} Brief: {n.label.lower()}"],
        answer_spec=n.sufficient,
        priority=max(1, min(5, 6 - n.impact)),
        type=qtype,  # type: ignore[arg-type]
        text=text,
        why_we_ask=f"{TAG} This fills in: {n.label.lower()}.",
        options=options,
        recommended=options[0].label if options and qtype == "choose_one" else "",
        examples=[] if options else [f"{TAG} e.g. a short answer about {term}"],
        refines=refines,
    )


MOCK_CLOSING_MAX_OWN = 5
MOCK_CLOSING_MAX_SUGGESTED = 2


def _short(label: str) -> str:
    return " ".join(label.split()[:8])


def _closing(inp: PlanInput) -> PlanOutput:
    """The reserved last question from the closing context: the person's own candidates first (features, then goals,
    then constraints), then, when the person named fewer than three features, the suggested features labelled as
    suggestions (up to the cap the validators allow), then, when there are still fewer than three options, up to two
    generic labelled suggestions. Deterministic and tagged ``[mock]``."""
    term = (list(inp.project_terms) or ["your app"])[0]
    c = inp.closing
    own = list(dict.fromkeys([*c.features, *c.goals, *c.constraints]))[:MOCK_CLOSING_MAX_OWN]
    options = [
        OptionWire(label=f"{TAG} {_short(label)}", description="", basis="your_words", reason="")
        for label in own
    ]
    if len(c.features) < 3:  # complete the list from the typical features of this kind of project
        stated = len(c.features) + len(c.goals) + len(c.constraints)
        cap = max(MOCK_CLOSING_MAX_SUGGESTED, 8 - stated)
        known = {_short(label).casefold() for label in own}
        typical = [
            _short(label)
            for label in dict.fromkeys(c.suggested_features)
            if _short(label).casefold() not in known
        ]
        for label in typical[:cap]:
            if len(options) >= MOCK_CLOSING_MAX_OWN:
                break
            options.append(
                OptionWire(label=f"{TAG} {label}", description="", basis="suggestion", reason="")
            )
    suggestions = (
        f"{TAG} A suggested first-version priority for {term}",
        f"{TAG} Another suggested priority for {term}",
    )
    for label in suggestions[: max(0, min(MOCK_CLOSING_MAX_SUGGESTED, 3 - len(options)))]:
        options.append(OptionWire(label=label, description="", basis="suggestion", reason=""))
    question = QuestionWire(
        id=f"Q-{inp.next_card_n:03d}",
        group_id="G1",
        group_title=f"{TAG} The first version",
        intent=f"learn what matters most in the first version of {term}",
        serves=["scope.priority", "goals.primary"],
        unlocks=[f"{TAG} Brief: first-version priorities"],
        answer_spec=f"{TAG} the few things that must work in the first version of {term}",
        priority=1,
        type="priority",
        text=f"{TAG} What matters most for the first version of your {term} project?",
        why_we_ask=f"{TAG} It decides what to build first.",
        options=options,
        recommended=options[0].label if own else "",
        examples=[f"{TAG} anything else, or what success would look like"],
    )
    return PlanOutput(
        domain_hypothesis=empty_hypothesis(),
        questions=[question],
        covered_needs=["scope.priority", "goals.primary"],
        assumed_instead=[],
        expert_gaps=[],
        backlog=[],
    )


def handle(inp: PlanInput) -> PlanOutput:
    if inp.mode == "closing":
        return _closing(inp)
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
    questions = []
    for i, n in enumerate(picked):
        is_followup = inp.mode == "followup" or n.need_id in inp.followup_candidates
        parent_id = ""
        if is_followup:
            match = re.search(r"\b(Q-\d{3,})\b", inp.asked_list)
            if match:
                parent_id = match.group(1)
        questions.append(
            _question(
                n,
                terms[i % len(terms)],
                f"Q-{inp.next_card_n + i:03d}",
                i // size + 1,
                followup=is_followup,
                refines=parent_id,
            )
        )
    return PlanOutput(
        domain_hypothesis=empty_hypothesis(),
        questions=questions,
        covered_needs=[q.serves[0] for q in questions],
        assumed_instead=[
            AssumedInsteadWire(
                need_id=n.need_id,
                assumption=f"{TAG} the typical default for {n.label.lower()}",
                reason=f"{TAG} a safe default exists and nothing was said about it",
            )
            for n in inp.needs
            if n.assume and inp.mode not in ("followup", "simplify")
        ],
        expert_gaps=[],
        backlog=[],
    )
