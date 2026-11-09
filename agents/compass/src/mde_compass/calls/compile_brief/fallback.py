"""The flagged fallback of ``cmp.compile_brief`` (a fallback is a failure: counted, zero in live batches).

``wire_fallback`` is a pure function of the wire input, with no model. It is also the mock handler.

It is the **safe floor**: nothing is lost and nothing is invented. Every fact of the ledger is placed (topic from
its ``[type|topic]`` tag, consumers ``["all"]`` so every downstream agent still sees it), no Brief item is produced,
nothing is marked not used, and the summary is the first fact's statement cut to 120 characters. An empty ``items``
list is correct here: the slot values are already in the Brief, written by the projector from each fact's own
effects when the facts were stored. What the fallback gives up is only the model's reading of the ledger (cleaner
wording of values, the pitch, routing of facts to the agents that need them).
"""

from __future__ import annotations

from mde_compass.calls.compile_brief.ledger import ALL, join_text, parse_ledger, short_summary
from mde_compass.calls.compile_brief.schema import CompileInput, CompileOutput, PlacementWire


def wire_fallback(inp: CompileInput) -> CompileOutput:
    facts = parse_ledger(join_text(inp.ledger))
    return CompileOutput(
        items=[],
        placements=[
            PlacementWire(fact_id=f.fact_id, topic=f.topic, consumers=[ALL]) for f in facts
        ],
        not_used=[],
        summary=short_summary(facts),
        pitch="",
    )
