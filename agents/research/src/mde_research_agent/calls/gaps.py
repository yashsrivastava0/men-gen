"""RSCH-1c: the gap analyst. Proposes follow-up queries for topics whose required slots are still unfilled.

One batched call for all such topics. It sees only the topic, what is missing, the queries already used and the
names of entities found in round 1 (by index, never a URL or domain: the model cannot send the pipeline to an address
of its choosing). Follow-ups must differ from every earlier query (code rejects repeats) and an entity index
must exist; code turns the index into the entity's own domain for a restricted search. The deterministic builder is
the mock handler and the fallback.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.anatomy import is_repeat

CALL_ID = "rsch.gap_analyst"
MAX_PER_TOPIC = 2


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityRef(W):
    index: int
    name: str


class GapTopic(W):
    target_id: str
    job: str
    anchors: list[str]
    context: str
    place: str
    intent: list[str]
    missing: list[str]  # plain descriptions of what is not found yet
    missing_terms: list[str]  # search words for the missing slots
    earlier: list[str]
    entities: list[EntityRef]


class GapIn(W):
    topics: list[GapTopic]


class Followup(W):
    target_id: str
    text: str
    language: str
    exact: bool
    entity_index: int | None = None


class GapOut(W):
    followups: list[Followup]


def deterministic_followups(inp: GapIn) -> GapOut:
    out: list[Followup] = []
    for t in inp.topics:
        used = list(t.earlier)
        anchor = t.anchors[0] if t.anchors else t.context
        terms = " ".join(t.missing_terms[:3])
        candidates: list[Followup] = []
        for ent in t.entities[:2]:
            candidates.append(
                Followup(
                    target_id=t.target_id,
                    text=f"{ent.name} {terms} {' '.join(t.intent[:1])}".strip(),
                    language="en",
                    exact=False,
                    entity_index=ent.index,
                )
            )
        candidates.append(
            Followup(
                target_id=t.target_id,
                text=f"{anchor} {terms} {t.place}".strip(),
                language="en",
                exact=False,
            )
        )
        candidates.append(
            Followup(
                target_id=t.target_id,
                text=f"{anchor} {' '.join(t.intent[1:3])} {' '.join(t.missing_terms[3:5])} {t.place}".strip(),
                language="en",
                exact=False,
            )
        )
        taken = 0
        for c in candidates:
            if taken >= MAX_PER_TOPIC:
                break
            if not is_repeat(c.text, used):
                used.append(c.text)
                out.append(c)
                taken += 1
    return GapOut(followups=out)


def handle(inp: GapIn) -> GapOut:
    return deterministic_followups(inp)


def validate(inp: GapIn, out: GapOut) -> list[str]:
    ids = {t.target_id for t in inp.topics}
    bad = [f.target_id for f in out.followups if f.target_id not in ids]
    return [f"unknown topics: {sorted(set(bad))}"] if bad else []


SPEC: ResearchCallSpec[GapIn, GapOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=GapIn,
        output_model=GapOut,
        system_template="gaps.system.jinja",
        user_template="gaps.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="deep",
        reasoning_effort="low",
        max_output_tokens=4000,
        mock_handler="mde_research_agent.calls.gaps:handle",
        untrusted_inputs=("topics",),
        cacheable=False,
        repair=True,
        fallback="research.gap_analyst",
    ),
    timeout_s=60.0,
    fallback=deterministic_followups,
    validators=(validate,),
)
