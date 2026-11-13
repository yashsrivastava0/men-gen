"""Mock handler of ``cmp.rephrase_question``: the original question behind a clearly labelled prefix, the
options exactly as they were. For offline tests and demos only; a mock session shows the MOCK MODE banner."""

from __future__ import annotations

from mde_compass.calls.rephrase_question.schema import OptionWire, RephraseInput, RephraseOutput

PREFIX = {"simplify": "[mock] simpler: ", "rephrase": "[mock] rephrased: "}


def handle(inp: RephraseInput) -> RephraseOutput:
    return RephraseOutput(
        question=f"{PREFIX[inp.mode]}{inp.question}",
        why_we_ask=inp.why_we_ask,
        options=[OptionWire(label=o.label, description=o.description) for o in inp.options],
        examples=[],
    )
