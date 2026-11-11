"""The bake-off judge: one batched, cheap call that says whether each search result is on topic for its query.

Used only by ``live_bakeoff``: it sees a query and the titles and snippets of the top results of each provider (web text,
so wrapped as untrusted, no tools) and answers one boolean per result. Mock handler and fallback: a deterministic
word-overlap judgement.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.text import STOP_WORDS, stem_set

CALL_ID = "rsch.bakeoff_judge"


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JudgeResult(W):
    title: str
    snippet: str


class JudgeQuery(W):
    id: str
    query: str
    results: list[JudgeResult]


class JudgeIn(W):
    queries: list[JudgeQuery]
    text: str = ""

    @model_validator(mode="after")
    def _build_text(self) -> JudgeIn:
        lines: list[str] = []
        for q in self.queries:
            lines.append(f"[{q.id}] QUERY: {q.query}")
            lines += [
                f"  {n}. {r.title} | {r.snippet[:200]}" for n, r in enumerate(q.results, start=1)
            ]
            lines.append("")
        self.text = "\n".join(lines)
        return self


class JudgeVerdict(W):
    id: str
    on_topic: list[bool]


class JudgeOut(W):
    verdicts: list[JudgeVerdict]


def overlap_judge(inp: JudgeIn) -> JudgeOut:
    out = []
    for q in inp.queries:
        want = stem_set(q.query) - {s for w in STOP_WORDS for s in stem_set(w)}
        flags = []
        for r in q.results:
            have = stem_set(f"{r.title} {r.snippet}")
            flags.append(len(want & have) >= max(1, min(2, len(want) // 3)))
        out.append(JudgeVerdict(id=q.id, on_topic=flags))
    return JudgeOut(verdicts=out)


def handle(inp: JudgeIn) -> JudgeOut:
    return overlap_judge(inp)


def validate(inp: JudgeIn, out: JudgeOut) -> list[str]:
    want = {q.id: len(q.results) for q in inp.queries}
    bad = [v.id for v in out.verdicts if want.get(v.id) != len(v.on_topic)]
    return [f"verdict lengths differ for {bad[:3]}"] if bad else []


SPEC: ResearchCallSpec[JudgeIn, JudgeOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=JudgeIn,
        output_model=JudgeOut,
        system_template="judge.system.jinja",
        user_template="judge.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="fast",
        reasoning_effort="low",
        max_output_tokens=6000,
        mock_handler="mde_research_agent.calls.judge:handle",
        untrusted_inputs=("text",),
        cacheable=True,
        repair=True,
        fallback="research.bakeoff_judge",
    ),
    timeout_s=90.0,
    fallback=overlap_judge,
    validators=(validate,),
)
