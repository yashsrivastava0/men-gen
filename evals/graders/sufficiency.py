"""Context sufficiency probe grader (spec section 12): is a slice enough to build a screen?

For a sample of screens it takes the slice the frontend engine would receive (``SpecIndex.slice``),
hands ONLY that text to the probe call (``P1.sufficiency_probe``) with concrete questions, and
compares the answers with the full spec. A wrong or "unknown" answer marks a field the slice does
not carry. Offline the probe is answered by the rule-based reader (``evals.probes.sufficiency.mock``);
in live batch L4 the same call goes to the small model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import cast

from evals.probes.sufficiency.models import ProbeInput, ProbeOutput
from evals.probes.sufficiency.questions import Question, matches, questions_for
from evals.probes.sufficiency.spec import PROBE_ID, SPEC
from mde_contracts.gateway import CallContext, GatewayClient
from mde_core.clock import FakeClock
from mde_core.settings import Settings
from mde_llm_gateway import (
    GatewayConfig,
    InProcessGatewayClient,
    StaticRegistry,
    create_gateway,
)
from mde_llm_gateway.providers.mock import MockProvider
from mde_llm_gateway.registry import AnySpec
from mde_spec_loader import SpecIndex

DEFAULT_SCREENS = 8


@dataclass
class KindStats:
    asked: int = 0
    correct: int = 0
    unknown: int = 0
    wrong: int = 0


@dataclass
class ProbeReport:
    screens: list[str] = field(default_factory=lambda: [])
    asked: int = 0
    correct: int = 0
    unknown: int = 0
    wrong: int = 0
    by_kind: dict[str, KindStats] = field(default_factory=lambda: {})
    failures: list[dict[str, str]] = field(default_factory=lambda: [])  # never holds spec text
    slice_tokens: list[int] = field(default_factory=lambda: [])

    @property
    def accuracy(self) -> float:
        return self.correct / self.asked if self.asked else 0.0

    @property
    def missing_fields(self) -> list[str]:
        """The question kinds that had at least one wrong or unknown answer."""
        return sorted(k for k, s in self.by_kind.items() if s.unknown or s.wrong)

    def as_dict(self) -> dict[str, object]:
        return {
            "screens": self.screens,
            "asked": self.asked,
            "correct": self.correct,
            "unknown": self.unknown,
            "wrong": self.wrong,
            "accuracy": round(self.accuracy, 4),
            "missing_fields": self.missing_fields,
            "by_kind": {k: vars(v) for k, v in sorted(self.by_kind.items())},
            "slice_tokens_max": max(self.slice_tokens, default=0),
        }


def offline_probe_client() -> GatewayClient:
    """The probe call behind the real gateway in mock mode (the rule-based reader answers)."""
    gateway = create_gateway(
        Settings(_env_file=None, llm_mode="mock"),  # type: ignore[call-arg]
        StaticRegistry([cast("AnySpec", SPEC)]),
        provider=MockProvider(),
        clock=FakeClock(),
        config=GatewayConfig(max_untrusted_chars=400_000),  # a slice can be 40k characters
    )
    return InProcessGatewayClient(gateway)


def pick_screens(screen_ids: list[str], limit: int | None) -> list[str]:
    """An even, deterministic sample (``None`` means all)."""
    if limit is None or limit >= len(screen_ids):
        return list(screen_ids)
    step = len(screen_ids) / limit
    return [screen_ids[int(i * step)] for i in range(limit)]


async def run_probe(
    client: GatewayClient,
    spec_text: str,
    *,
    limit: int | None = DEFAULT_SCREENS,
    scope: str = "eval:probe",
    purpose: str = "eval:probe",
) -> ProbeReport:
    index = SpecIndex(spec_text)
    spec = index.spec
    report = ProbeReport()
    by_kind: dict[str, KindStats] = defaultdict(KindStats)
    for n, screen_id in enumerate(pick_screens([s.id for s in spec.screens], limit)):
        sliced = index.slice(screen_id)
        questions: list[Question] = questions_for(spec, screen_id)
        result = await client.run(
            SPEC,
            ProbeInput(slice_text=sliced.text, questions=[q.question for q in questions]),
            CallContext(
                request_id=f"req_probe_{n}",
                budget_scope=scope,
                purpose=purpose,
            ),
        )
        output = cast("ProbeOutput", result.output)
        answers = {a.id: a for a in output.answers}
        report.screens.append(screen_id)
        report.slice_tokens.append(sliced.token_estimate)
        for q in questions:
            stats = by_kind[q.question.kind]
            stats.asked += 1
            report.asked += 1
            got = answers.get(q.question.id)
            if got is not None and got.found and matches(q.expected, got.answer):
                stats.correct += 1
                report.correct += 1
            elif got is None or not got.found or got.answer.strip().lower() == "unknown":
                stats.unknown += 1
                report.unknown += 1
                report.failures.append({"id": q.question.id, "why": "unknown"})
            else:
                stats.wrong += 1
                report.wrong += 1
                report.failures.append({"id": q.question.id, "why": "wrong"})
    report.by_kind = dict(by_kind)
    return report


__all__ = ["PROBE_ID", "ProbeReport", "offline_probe_client", "run_probe"]
