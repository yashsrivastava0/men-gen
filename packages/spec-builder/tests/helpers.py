"""Test helpers for the builder: request factories, a Research Note, and two gateways.

* ``DirectMockGateway`` runs the spec's mock handler (``spec.mock_handler``) in process and wraps
  the output in a ``CallResult``; it records every call.
* ``ScriptedGateway`` answers by call id from queues of outputs or exceptions (and falls back to a
  default gateway for ids that have no queue); it records the ``CallContext`` of every call.

Both implement ``mde_contracts.gateway.GatewayClient`` without importing the gateway package.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mde_content.policies import resolve_policy
from mde_contracts.brief import AssumedItem, Brief, BriefFeature, BriefRow
from mde_contracts.calls import C11Input, C11Output
from mde_contracts.engines import BuildRequest, RunContext
from mde_contracts.gateway import BudgetStatus, CallContext, CallResult, CallSpec
from mde_contracts.policy import Limits, Policy
from mde_contracts.research import (
    BrandFinding,
    CompliancePointer,
    ResearchFact,
    ResearchNote,
    ResearchSuggestion,
    SourceRef,
)
from mde_contracts.slots import SlotRecord
from mde_core.cancel import CancellationToken

TEST_SESSION = "ses_TEST00000000000000000001"


# --------------------------------------------------------------------------- requests


def slot(slot_id: str, value: object, state: str = "confirmed") -> SlotRecord:
    return SlotRecord.model_validate({"slot_id": slot_id, "value": value, "state": state})


def make_slots(archetype: str = "commerce", **values: object) -> dict[str, SlotRecord]:
    """Slots for a request. ``values`` keys use ``__`` for ``.`` (``scope__features=[...]``);
    a ``(value, state)`` tuple sets the state (default ``confirmed``)."""
    out: dict[str, SlotRecord] = {"idea.archetype": slot("idea.archetype", archetype)}
    for key, raw in values.items():
        slot_id = key.replace("__", ".")
        if isinstance(raw, tuple):
            out[slot_id] = slot(slot_id, raw[0], str(raw[1]))
        else:
            out[slot_id] = slot(slot_id, raw)
    return out


def make_brief(
    summary: str = "A small app for my shop where people can order",
    *,
    version: int = 1,
    mode: str = "guided",
    language: str = "en",
    assumed: tuple[tuple[str, str, str], ...] = (),
    features: tuple[str, ...] = (),
) -> Brief:
    rows = [
        BriefRow(
            key="building", title="What you are building", text=summary, slot_ids=["idea.summary"]
        ),
        BriefRow(
            key="users",
            title="Who uses it",
            text="Shoppers and the shop team",
            slot_ids=["users.roles"],
        ),
        BriefRow(
            key="features",
            title="Main features",
            text=", ".join(features),
            slot_ids=["scope.features"],
            features=[BriefFeature(id=f, label=f.replace("_", " ").capitalize()) for f in features],
        ),
    ]
    return Brief(
        version=version,
        mode=mode,  # type: ignore[arg-type]
        language=language,
        app_summary=summary,
        rows=rows,
        assumptions=[
            AssumedItem(slot_id=s, label=label, value_text=value, reason="You did not say")  # type: ignore[arg-type]
            for s, label, value in assumed
        ],
    ).with_hash()


def make_policy(policy_id: str = "guided-default", **limits: int) -> Policy:
    base = resolve_policy(policy_id)
    if limits:
        base = base.model_copy(update={"limits": Limits(**{**base.limits.model_dump(), **limits})})
    return base


def make_request(
    archetype: str = "commerce",
    *,
    features: list[str] | None = None,
    policy: Policy | None = None,
    brief: Brief | None = None,
    research: ResearchNote | None = None,
    excluded: list[str] | None = None,
    spec_version: int = 1,
    **slot_values: object,
) -> BuildRequest:
    if features is not None:
        slot_values["scope__features"] = features
    policy = policy or make_policy()
    return BuildRequest(
        session_id=TEST_SESSION,
        tenant_id="tnt_TEST00000000000000000001",
        app_id="app_TEST00000000000000000001",
        spec_version=spec_version,
        brief=brief or make_brief(mode=policy.mode),
        slots=make_slots(archetype, **slot_values),
        policy=policy,
        research=research,
        excluded_research_ids=excluded or [],
    )


def make_context(
    *,
    cancel: CancellationToken | None = None,
    progress: list[tuple[str, str, str, int, int]] | None = None,
) -> RunContext:
    events = progress if progress is not None else []

    async def emit(phase: str, step: str, label: str, done: int = 0, total: int = 0) -> None:
        events.append((phase, step, label, done, total))

    kwargs: dict[str, Any] = {"progress": emit}
    if cancel is not None:
        kwargs["cancel"] = cancel
    return RunContext(
        session_id="ses_TEST00000000000000000001",
        tenant_id="tnt_TEST00000000000000000001",
        request_id="req_TEST00000000000000000001",
        **kwargs,
    )


def make_note(
    brief_hash: str = "b" * 64,
    *,
    with_brand: bool = True,
    status: str = "ok",
    feature_applies_to: str | None = "scope.features:favorites",
) -> ResearchNote:
    """A small Research Note: one feature, one pattern, one integration and one brand suggestion.

    The feature suggestion (RA-001) names the ``favorites`` feature of the commerce pack the way
    the research engine does (``scope.features:<pack feature id>``); other packs have no such
    feature, so for them it stays a proposal for the section writers."""
    src = SourceRef(
        url="https://example.com/similar-apps", title="Similar apps", retrieved_at=date(2026, 11, 8)
    )
    src2 = SourceRef(
        url="https://example.org/guides", title="Guides", retrieved_at=date(2026, 11, 8)
    )
    return ResearchNote(
        brief_hash=brief_hash,
        status=status,  # type: ignore[arg-type]
        facts=[
            ResearchFact(
                id="F-001",
                job="R1",
                kind="feature",
                statement="Most similar apps let people save favorites",
                sources=[src, src2],
                verified=True,
            ),
            ResearchFact(
                id="F-002",
                job="R2",
                kind="pattern",
                statement="Order status as a timeline is common",
                sources=[src],
            ),
            ResearchFact(
                id="F-003",
                job="R5",
                kind="integration",
                statement="A maps provider is common",
                sources=[src2],
            ),
        ],
        suggestions=[
            ResearchSuggestion(
                id="RA-001",
                kind="feature",
                title="Favorites list",
                rationale="Seen in most similar apps",
                fact_ids=["F-001"],
                applies_to=feature_applies_to,
                verified=True,
            ),
            ResearchSuggestion(
                id="RA-002",
                kind="pattern",
                title="Status timeline on orders",
                rationale="A common pattern",
                fact_ids=["F-002"],
            ),
            ResearchSuggestion(
                id="RA-003",
                kind="integration",
                title="Store locator map",
                rationale="Commonly used",
                fact_ids=["F-003"],
            ),
            *(
                [
                    ResearchSuggestion(
                        id="RA-004",
                        kind="brand",
                        title="Match the brand colors",
                        rationale="From the brand site",
                        applies_to="design",
                    )
                ]
                if with_brand
                else []
            ),
        ],
        compliance=[
            CompliancePointer(
                flag="financial_data", title="Card data basics", url="https://example.com/pci"
            )
        ],
        brand=BrandFinding(
            source_url="https://example.com",
            colors=["#E4572E"],
            fonts=["Inter"],
            tone_words=["warm", "friendly"],
            nearest_direction="warm-friendly",
        )
        if with_brand
        else None,
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
    ).with_hash()


# --------------------------------------------------------------------------- gateways


def _resolve(path: str) -> Any:
    module_name, _, attr = path.partition(":")
    return getattr(import_module(module_name), attr)


def _result(spec: CallSpec[Any, Any], output: BaseModel, index: int) -> CallResult[Any]:
    return CallResult(
        call_id=f"call_TEST{index:022d}",
        spec_id=spec.id,
        spec_version=spec.version,
        model="mock",
        mode="mock",
        output=output,
        request_id="req_TEST00000000000000000001",
    )


@dataclass
class DirectMockGateway:
    """Runs each spec's mock handler in process. Records ``(spec id, input, ctx)``."""

    calls: list[tuple[str, BaseModel, CallContext]] = field(default_factory=list)

    async def run(
        self, spec: CallSpec[Any, Any], input: BaseModel, ctx: CallContext
    ) -> CallResult[Any]:
        if not isinstance(input, spec.input_model):
            raise TypeError(f"{spec.id}: wrong input model {type(input).__name__}")
        self.calls.append((spec.id, input, ctx))
        model = spec.output_model
        assert model is not None
        out = _resolve(spec.mock_handler)(input)
        output = out if isinstance(out, model) else model.model_validate(out)
        return _result(spec, output, len(self.calls))

    async def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            cap_micros=6_000_000, effective_cap_micros=5_700_000, spent_micros=0, reserved_micros=0
        )

    @property
    def ids(self) -> list[str]:
        return [c[0] for c in self.calls]


@dataclass
class ScriptedGateway:
    """Queued outputs or exceptions by call id; unscripted ids go to ``fallback`` (default: raise).

    ``script("C9a.screens_nav", output, ValueError(...))`` answers the first call with ``output``
    and raises for the second. ``repeat`` keeps answering with the last item of a queue.
    """

    fallback: DirectMockGateway | None = None
    queues: dict[str, deque[BaseModel | BaseException]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    last: dict[str, BaseModel | BaseException] = field(default_factory=dict)
    repeat: set[str] = field(default_factory=set)
    calls: list[tuple[str, BaseModel, CallContext]] = field(default_factory=list)

    def script(
        self, call_id: str, *items: BaseModel | BaseException, repeat: bool = False
    ) -> ScriptedGateway:
        self.queues[call_id].extend(items)
        if repeat:
            self.repeat.add(call_id)
        return self

    async def run(
        self, spec: CallSpec[Any, Any], input: BaseModel, ctx: CallContext
    ) -> CallResult[Any]:
        self.calls.append((spec.id, input, ctx))
        queue = self.queues.get(spec.id)
        item: BaseModel | BaseException
        if queue:
            item = queue.popleft()
            self.last[spec.id] = item
        elif spec.id in self.repeat and spec.id in self.last:
            item = self.last[spec.id]
        elif self.fallback is not None:
            return await self.fallback.run(spec, input, ctx)
        else:
            raise AssertionError(f"no scripted answer for {spec.id}")
        if isinstance(item, BaseException):
            raise item
        return _result(spec, item, len(self.calls))

    async def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            cap_micros=6_000_000, effective_cap_micros=5_700_000, spent_micros=0, reserved_micros=0
        )

    @property
    def ids(self) -> list[str]:
        return [c[0] for c in self.calls]

    def contexts(self, call_id: str) -> list[CallContext]:
        return [c[2] for c in self.calls if c[0] == call_id]


# --------------------------------------------------------------------------- a stand-in for C11


def fake_c11_handle(input: C11Input) -> C11Output:
    """The offline stand-in for C11 (the discovery engineer's spec is not merged yet)."""
    return C11Output(text=f"{input.app_name} is a mobile app built from {len(input.facts)} facts.")


def no_c11() -> CallSpec[C11Input, C11Output]:
    """Summary loader for tests that must not depend on which prompt specs are merged: C11 is
    'not available', so the engine uses its deterministic template summary."""
    from mde_prompts.registry import SpecNotFound

    raise SpecNotFound("C11.write_summary")


def fake_c11_spec() -> CallSpec[C11Input, C11Output]:
    return CallSpec(
        id="C11.write_summary",
        version=1,
        input_model=C11Input,
        output_model=C11Output,
        system_template="system.jinja",
        user_template="user.jinja",
        template_root=Path("."),
        route="standard",
        reasoning_effort="medium",
        max_output_tokens=400,
        mock_handler="helpers:fake_c11_handle",
        fallback="discovery.template_sentence",
    )
