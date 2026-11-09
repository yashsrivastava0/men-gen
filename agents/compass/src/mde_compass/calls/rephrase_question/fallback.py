"""The flagged fallback of ``cmp.rephrase_question``: the original question, why-we-ask and options, unchanged.

A fallback is a failure (counted, zero in live batches), but here it is also harmless: the person simply sees the
same card again, and the engine can tell them it could not be reworded. The function never invents wording.
"""

from __future__ import annotations

from mde_compass.calls.rephrase_question.schema import OptionWire, RephraseInput, RephraseOutput


def wire_fallback(inp: RephraseInput) -> RephraseOutput:
    return RephraseOutput(
        question=inp.question,
        why_we_ask=inp.why_we_ask,
        options=[OptionWire(label=o.label, description=o.description) for o in inp.options],
        examples=[],
    )
