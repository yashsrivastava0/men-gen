"""Evaluation calls (doc 14 section 6): a stand-in for Spec answers a task, and a judge scores the answer.

Used only by the evaluation harness (the ``eval`` package), never by a run. The stand-in sees the Brief and, in the
research arm, the findings (web-derived text: untrusted, no tools). Every statement must cite finding ids or say ASSUMED.
Mock handler: a deterministic answerer and judge so the harness runs offline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.text import stem_set

PROBE_ID = "rsch.eval_probe"
JUDGE_ID = "rsch.eval_judge"
ID_RX = re.compile(r"\bRI-\d{3,}\b")


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeIn(W):
    task: str
    brief: str
    findings: str = ""  # empty in the Brief-only arm


class Statement(W):
    text: str
    cites: list[str]  # finding ids, or ["ASSUMED"]


class ProbeOut(W):
    statements: list[Statement]


class JudgeEvalIn(W):
    statements: list[Statement]
    findings: str
    text: str = ""

    def model_post_init(self, __context: object) -> None:
        lines = [
            f"[{i}] {s.text} (cites: {', '.join(s.cites)})" for i, s in enumerate(self.statements)
        ]
        self.text = f"FINDINGS:\n{self.findings}\n\nSTATEMENTS:\n" + "\n".join(lines)


class StatementVerdict(W):
    index: int
    verdict: str  # supported | assumed | invented


class JudgeEvalOut(W):
    verdicts: list[StatementVerdict]


def mock_probe(inp: ProbeIn) -> ProbeOut:
    ids = ID_RX.findall(inp.findings)[:6]
    if not ids:
        return ProbeOut(
            statements=[
                Statement(text=f"A reasonable default for: {inp.task[:60]}", cites=["ASSUMED"])
            ]
        )
    return ProbeOut(statements=[Statement(text=f"Use finding {i}", cites=[i]) for i in ids])


def mock_judge(inp: JudgeEvalIn) -> JudgeEvalOut:
    have = set(ID_RX.findall(inp.findings))
    out = []
    for n, s in enumerate(inp.statements):
        if s.cites == ["ASSUMED"]:
            out.append(StatementVerdict(index=n, verdict="assumed"))
        elif s.cites and all(c in have for c in s.cites) and stem_set(s.text):
            out.append(StatementVerdict(index=n, verdict="supported"))
        else:
            out.append(StatementVerdict(index=n, verdict="invented"))
    return JudgeEvalOut(verdicts=out)


def _spec(
    cid: str, inp: Any, out: Any, name: str, handler: str, fallback: Any, untrusted: tuple[str, ...]
) -> ResearchCallSpec[Any, Any]:
    return ResearchCallSpec(
        spec=CallSpec(
            id=cid,
            version=1,
            input_model=inp,
            output_model=out,
            system_template=f"{name}.system.jinja",
            user_template=f"{name}.user.jinja",
            template_root=Path(__file__).parent / "templates",
            route="fast",
            reasoning_effort="low",
            max_output_tokens=6000,
            mock_handler=handler,
            untrusted_inputs=untrusted,
            cacheable=False,
            repair=True,
            fallback=f"research.{name}",
        ),
        timeout_s=120.0,
        fallback=fallback,
        validators=(),
    )


PROBE_SPEC = _spec(
    PROBE_ID,
    ProbeIn,
    ProbeOut,
    "eval_probe",
    "mde_research_agent.calls.eval_probe:mock_probe",
    lambda i: ProbeOut(statements=[]),
    ("findings", "brief"),
)
JUDGE_SPEC = _spec(
    JUDGE_ID,
    JudgeEvalIn,
    JudgeEvalOut,
    "eval_judge",
    "mde_research_agent.calls.eval_probe:mock_judge",
    lambda i: JudgeEvalOut(verdicts=[]),
    ("findings", "text"),
)
