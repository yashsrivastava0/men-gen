"""Small test doubles and builders for the interview engine's units (no model, no store, no clock).

* ``make_card`` / ``make_fact`` / ``make_qa`` build valid domain objects with sensible defaults.
* ``StubPlanner`` implements ``engine.ports.QuestionPlanner`` and ``StubAuditor`` implements
  ``engine.ports.QuestionAuditor``: they return what the test queued and record every request, so a unit test of
  ``question_pipeline`` can say exactly what the model "answered" and check what the pipeline asked.

They live in a real package module (not in a test file) because pytest runs with ``--import-mode=importlib``:
test modules cannot import each other (D-017).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime

from mde_compass.domain.cards import Card, CardOption
from mde_compass.domain.interview import (
    AuditVerdict,
    Fact,
    FactEffect,
    NeedStatus,
    PlannedBatch,
    PlannerMode,
    QaRecord,
    stored_mode,
)
from mde_compass.engine.ports import (
    AuditRequest,
    AuditResult,
    PlanContext,
    PlanRequest,
    PlanResult,
    TurnContext,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
TERMS: frozenset[str] = frozenset({"tiffin", "pune", "office", "riders", "lunch"})


def make_card(
    card_id: str = "Q-002",
    question: str = "Who orders your tiffins, and what do they want from the app?",
    *,
    intent: str | None = None,
    needs: Sequence[str] = ("audience.segments",),
    type: str = "open_prompt",
    origin: str = "planner",
    group_id: str = "G1",
    parent: str | None = None,
    depth: int = 0,
    options: Sequence[str] = (),
    recommended: int | None = None,
    examples: Sequence[str] | None = None,
    batch_id: str | None = None,
) -> Card:
    """A planned question. An open prompt gets an example hint (the validators require one)."""
    opts = [
        CardOption(
            id=chr(ord("A") + i),
            label=label,
            basis="suggestion",
            recommended=recommended == i,
            reason="a short reason" if recommended == i else "",
        )
        for i, label in enumerate(options)
    ]
    hints = list(examples) if examples is not None else (["Office workers"] if not opts else [])
    return Card(
        id=card_id,
        frame="planned",
        slots=[n for n in needs if not n.startswith("domain:")],
        type=type,  # type: ignore[arg-type]
        question=question,
        why_we_ask="It decides what the screens must do.",
        options=opts,
        intent=intent if intent is not None else f"learn: {question}",
        need_ids=list(needs),
        answer_spec="a short specific answer",
        examples=hints,
        parent_card_id=parent,
        depth=depth,
        origin=origin,  # type: ignore[arg-type]
        group_id=group_id,
        batch_id=batch_id,
    )


def make_batch(
    cards: Iterable[Card], *, mode: PlannerMode = "initial", **extra: object
) -> PlannedBatch:
    """Cards grouped by their ``group_id`` in order, as a ``PlannedBatch``."""
    from mde_compass.domain.interview import BatchGroup

    order: list[str] = []
    by_group: dict[str, list[Card]] = {}
    for card in cards:
        key = card.group_id or "G1"
        if key not in by_group:
            order.append(key)
            by_group[key] = []
        by_group[key].append(card)
    groups = [BatchGroup(id=k, title=f"Group {k}", cards=by_group[k]) for k in order]
    return PlannedBatch(batch_id="BAT-000", mode=mode, groups=groups, **extra)  # type: ignore[arg-type]


def make_fact(
    number: int = 1,
    statement: str = "Customers are office workers",
    *,
    quote: str = "office workers",
    status: str = "active",
    origin: str = "stated",
    unverified: bool = False,
    topics: Sequence[str] = ("audience",),
    effects: Sequence[FactEffect] = (),
    aspects: Sequence[str] = (),
    card: str | None = None,
    consumers: Sequence[str] = ("all",),
) -> Fact:
    return Fact(
        id=f"FCT-{number:03d}",
        type="context",
        statement=statement,
        topics=list(topics),
        consumers=list(consumers),
        confidence=0.8,
        status=status,  # type: ignore[arg-type]
        origin=origin,  # type: ignore[arg-type]
        source_ref="evt_1",
        qa_card_id=card,
        quote=quote,
        unverified_quote=unverified,
        effects=list(effects),
        aspects=list(aspects),
        not_used_reason="small talk" if status == "not_used" else "",
        created_at=NOW,
        updated_at=NOW,
    )


def make_qa(
    card: Card, status: str = "answered", *, version: int = 1, asked: bool = True
) -> QaRecord:
    return QaRecord(
        card_id=card.id,
        version=version,
        card=card,
        status=status,  # type: ignore[arg-type]
        asked_at=NOW if asked else None,
        answered_at=NOW if status == "answered" else None,
    )


def make_status(
    need_id: str, state: str = "missing", *, impact: int = 5, level: str = "must"
) -> NeedStatus:
    return NeedStatus(need_id=need_id, state=state, impact=impact, level=level)  # type: ignore[arg-type]


def turn_context() -> TurnContext:
    return TurnContext(session_id="ses_01", tenant_id="t1")


def plan_request(
    mode: PlannerMode = "initial", *, want: tuple[int, int] = (3, 7), **extra: object
) -> PlanRequest:
    return PlanRequest(
        mode=mode,
        context=PlanContext(),
        project_terms=sorted(TERMS),
        want_min=want[0],
        want_max=want[1],
        **extra,  # type: ignore[arg-type]
    )


class StubPlanner:
    """Queued ``PlanResult`` values for ``plan`` and one for ``fallback``; every request is recorded."""

    def __init__(self, *results: PlanResult, fallback: PlanResult | None = None) -> None:
        self.queue = list(results)
        self.fallback_result = fallback
        self.requests: list[PlanRequest] = []
        self.fallback_requests: list[PlanRequest] = []

    async def plan(self, ctx: TurnContext, request: PlanRequest) -> PlanResult:
        self.requests.append(request)
        if not self.queue:
            raise AssertionError("the pipeline asked the planner more often than the test queued")
        return self.queue.pop(0)

    async def fallback(self, ctx: TurnContext, request: PlanRequest) -> PlanResult:
        self.fallback_requests.append(request)
        return self.fallback_result or PlanResult(
            batch=PlannedBatch(batch_id="BAT-000", mode=stored_mode(request.mode)),
            used_fallback=True,
        )


Verdicts = dict[str, dict[str, object]]


class StubAuditor:
    """Passes every question (specificity 5) unless the test overrides a card id's verdict fields."""

    def __init__(
        self,
        overrides: Verdicts | None = None,
        *,
        used_fallback: bool = False,
        omit: Iterable[str] = (),
        per_call: Callable[[int], Verdicts] | None = None,
    ) -> None:
        self.overrides = overrides or {}
        self.used_fallback = used_fallback
        self.omit = set(omit)
        self.per_call = per_call
        self.requests: list[AuditRequest] = []

    async def audit(self, ctx: TurnContext, request: AuditRequest) -> AuditResult:
        self.requests.append(request)
        over = self.per_call(len(self.requests)) if self.per_call else self.overrides
        verdicts = [
            AuditVerdict.model_validate({"card_id": c.id, "specific": 5, **over.get(c.id, {})})
            for c in request.cards
            if c.id not in self.omit
        ]
        return AuditResult(verdicts=verdicts, used_fallback=self.used_fallback)
