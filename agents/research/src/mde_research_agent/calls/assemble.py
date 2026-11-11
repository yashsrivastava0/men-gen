"""RSCH-3: assembly. ONE call orders the items of each Spec lane and writes a short ``use_note`` per item.

CODE builds everything factual first (indexes, norms with prevalence, digests, suggestions); this call only SELECTS
and ORDERS items that already exist and writes a note of at most 15 words saying how Spec should use each. It writes
no new facts (RV-05): code drops a note that adds a number, name, date or URL absent from the item it annotates, an id
that was not in the input, and any note over 15 words. One call, not one per lane (LangChain found parallel writing
disjoint). The input text comes from web pages through extraction, so it is wrapped as untrusted and the call has no
tools. Mock handler and fallback: a deterministic ordering and the default notes from ``data/use_notes.yaml``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.catalog.loader import DATA_DIR

CALL_ID = "rsch.assemble"
MAX_NOTE_WORDS = 15
LEVEL_RANK = {"core": 0, "common": 1, "emerging": 2, "advanced": 3, "limited_evidence": 4}


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemIn(W):
    id: str
    kind: str
    label: str
    claim: str
    level: str
    impact: str
    flags: list[str]


class LaneIn(W):
    lane: str
    limit: int
    items: list[ItemIn]


class AssembleIn(W):
    language: str
    lanes: list[LaneIn]


class NoteOut(W):
    id: str
    use_note: str


class LaneOut(W):
    lane: str
    order: list[str]
    notes: list[NoteOut]


class AssembleOut(W):
    lanes: list[LaneOut]


@lru_cache(maxsize=1)
def default_notes() -> dict[str, str]:
    raw = yaml.safe_load((DATA_DIR / "use_notes.yaml").read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in raw.items()}


def rank_key(item: ItemIn) -> tuple[int, int, int, str]:
    return (
        LEVEL_RANK.get(item.level, 5),
        0 if item.impact == "high" else 1,
        1 if "single-source" in item.flags or "unchecked" in item.flags else 0,
        item.id,
    )


def deterministic_assembly(inp: AssembleIn) -> AssembleOut:
    notes = default_notes()
    lanes: list[LaneOut] = []
    for lane in inp.lanes:
        ranked = sorted(lane.items, key=rank_key)[: lane.limit]
        lanes.append(
            LaneOut(
                lane=lane.lane,
                order=[i.id for i in ranked],
                notes=[
                    NoteOut(id=i.id, use_note=notes.get(i.kind, ""))
                    for i in ranked
                    if notes.get(i.kind)
                ],
            )
        )
    return AssembleOut(lanes=lanes)


def handle(inp: AssembleIn) -> AssembleOut:
    return deterministic_assembly(inp)


def validate(inp: AssembleIn, out: AssembleOut) -> list[str]:
    lanes = {lane.lane: {i.id for i in lane.items} for lane in inp.lanes}
    errors = [f"unknown lane {lo.lane!r}" for lo in out.lanes if lo.lane not in lanes]
    for lo in out.lanes:
        known = lanes.get(lo.lane, set())
        errors += [f"id {i} not in lane {lo.lane}" for i in lo.order if i not in known]
    return errors[:5]


SPEC: ResearchCallSpec[AssembleIn, AssembleOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=AssembleIn,
        output_model=AssembleOut,
        system_template="assemble.system.jinja",
        user_template="assemble.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="deep",
        reasoning_effort="medium",
        max_output_tokens=12000,
        mock_handler="mde_research_agent.calls.assemble:handle",
        untrusted_inputs=("lanes",),
        cacheable=False,
        repair=True,
        fallback="research.assemble",
    ),
    timeout_s=120.0,
    fallback=deterministic_assembly,
    validators=(validate,),
)
