"""The deterministic decision of ``cmp.decide_answers`` (hard rule 4): no model, no network, no clock.

Per item: the planner's own recommendation, else the first option whose basis is ``typical``, else the first option;
an open question gets an EMPTY text (the engine turns it into the catalog default or the hypothesis). A decision made
here has an empty rationale (the engine words it from the catalog strings) and a low confidence, and the result that
carries it is flagged ``used_fallback`` (a fallback is a failure, counted).
"""

from __future__ import annotations

from mde_compass.calls.decide_answers.schema import (
    DecideInput,
    DecideOutput,
    DecisionWire,
    ItemWire,
    OptionWire,
)

SINGLE_TYPES: frozenset[str] = frozenset({"choose_one", "confirm", "look_signals"})
"""Card types that take exactly one option; every other type with options (pick_many, priority) takes one or more."""
CONF_RECOMMENDED = 0.6
CONF_TYPICAL = 0.5
CONF_FIRST = 0.3
CONF_OPEN = 0.0


def is_open(item: ItemWire) -> bool:
    """An open question has no options: the answer is a text."""
    return not item.options


def pick_option(item: ItemWire) -> tuple[OptionWire, float] | None:
    """The deterministic choice for an item that has options, with how confident it is."""
    by_id = {o.id: o for o in item.options}
    if item.recommended_id in by_id:
        return by_id[item.recommended_id], CONF_RECOMMENDED
    typical = next((o for o in item.options if o.basis == "typical"), None)
    if typical is not None:
        return typical, CONF_TYPICAL
    if item.options:
        return item.options[0], CONF_FIRST
    return None


def fallback_decision(item: ItemWire) -> DecisionWire:
    chosen = pick_option(item)
    if chosen is None:
        return DecisionWire(
            card_id=item.card_id, option_ids=[], answer_text="", rationale="", confidence=CONF_OPEN
        )
    option, confidence = chosen
    return DecisionWire(
        card_id=item.card_id,
        option_ids=[option.id],
        answer_text="",
        rationale="",
        confidence=confidence,
    )


def wire_fallback(inp: DecideInput) -> DecideOutput:
    return DecideOutput(decisions=[fallback_decision(item) for item in inp.items])
