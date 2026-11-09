"""Mock handler of ``cmp.decide_answers``: the deterministic choice with a ``[mock]`` rationale, and a labelled text
for an open question (so a mock session never has an empty answer). For offline tests and demos only."""

from __future__ import annotations

from mde_compass.calls.decide_answers.fallback import SINGLE_TYPES, fallback_decision, is_open
from mde_compass.calls.decide_answers.schema import (
    DecideInput,
    DecideOutput,
    DecisionWire,
    ItemWire,
)

TAG = "[mock]"
MOCK_CONFIDENCE = 0.5


def _decide(item: ItemWire) -> DecisionWire:
    if is_open(item):
        topic = item.needs[0] if item.needs else item.card_id
        return DecisionWire(
            card_id=item.card_id,
            option_ids=[],
            answer_text=f"{TAG} a sensible default for {topic}",
            rationale=f"{TAG} typical for this kind of project",
            confidence=MOCK_CONFIDENCE,
        )
    base = fallback_decision(item)
    ids = list(base.option_ids)
    if (
        item.card_type not in SINGLE_TYPES
    ):  # a checklist: the choice and the next option, when there is one
        ids += [o.id for o in item.options if o.id not in ids][:1]
    return base.model_copy(
        update={
            "option_ids": ids,
            "rationale": f"{TAG} a sensible choice",
            "confidence": MOCK_CONFIDENCE,
        }
    )


def handle(inp: DecideInput) -> DecideOutput:
    return DecideOutput(decisions=[_decide(item) for item in inp.items])
