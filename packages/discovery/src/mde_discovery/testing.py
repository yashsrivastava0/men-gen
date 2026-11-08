"""Test helpers for Discover (importable from tests, D-017): gateways and a conversation driver.

* ``DirectMockGateway`` implements ``GatewayClient.run`` by importing each spec's ``mock_handler``
  (``"pkg.module:function"``), calling ``handle(input)`` and wrapping the output in a
  ``CallResult``. It records every call. No network, no key, no cost.
* ``ScriptedGateway`` takes queued outputs or errors by call id (to drive fallbacks and exact
  scenarios) and falls back to the mock handler when a queue is empty.
* ``Conversation`` drives an engine like a client would (greeting, typing, taps, uploads) with a
  fake clock and sequential ids, and keeps every event.

Nothing here is used by production code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from pydantic import BaseModel, JsonValue

from mde_content.policies import resolve_policy
from mde_contracts.cards import CardInstance
from mde_contracts.common import GateKind, Mode
from mde_contracts.engines import DiscoveryRequest, DiscoveryResult, DiscoveryState, RunContext
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    Notice,
    OptionSelected,
    OutboundEvent,
    UiAction,
    UploadAdded,
    UserMessage,
)
from mde_contracts.gateway import BudgetStatus, CallContext, CallResult, CallSpec
from mde_contracts.policy import Policy
from mde_contracts.slots import SlotData, SlotRecord
from mde_core.clock import FakeClock
from mde_core.ids import SequentialIds
from mde_discovery.engine import DiscoverEngine


def resolve_handler(path: str) -> Callable[[Any], Any]:
    module, _, attr = path.partition(":")
    handler: Callable[[Any], Any] = getattr(import_module(module), attr)
    return handler


class DirectMockGateway:
    """``GatewayClient`` that runs the spec's offline handler directly."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, BaseModel]] = []
        self.contexts: list[CallContext] = []

    async def run(self, spec: CallSpec[Any, Any], input: Any, ctx: CallContext) -> CallResult[Any]:
        self.calls.append((spec.id, input))
        self.contexts.append(ctx)
        return self._result(spec, ctx, resolve_handler(spec.mock_handler)(input))

    async def budget_status(self) -> BudgetStatus:
        return BudgetStatus(
            cap_micros=6_000_000, effective_cap_micros=5_700_000, spent_micros=0, reserved_micros=0
        )

    def _result(self, spec: CallSpec[Any, Any], ctx: CallContext, output: Any) -> CallResult[Any]:
        return CallResult(
            call_id=f"call_TEST{len(self.calls):022d}",
            spec_id=spec.id,
            spec_version=spec.version,
            model="mock",
            mode="mock",
            output=output,
            request_id=ctx.request_id,
        )

    # ----------------------------------------------------------------- inspection
    def call_ids(self) -> list[str]:
        return [c[0] for c in self.calls]

    def count(self, call_id: str | None = None) -> int:
        return len(self.calls) if call_id is None else self.call_ids().count(call_id)

    def inputs(self, call_id: str) -> list[Any]:
        return [i for c, i in self.calls if c == call_id]


ScriptItem = BaseModel | BaseException | Callable[[Any], Any]


class ScriptedGateway(DirectMockGateway):
    """Queued outputs and errors by call id; an empty queue falls back to the mock handler."""

    def __init__(
        self,
        script: dict[str, list[ScriptItem]] | None = None,
        *,
        fallback_to_mock: bool = True,
    ) -> None:
        super().__init__()
        self.script: dict[str, list[ScriptItem]] = {k: list(v) for k, v in (script or {}).items()}
        self.fallback_to_mock = fallback_to_mock

    def queue(self, call_id: str, *items: ScriptItem) -> None:
        self.script.setdefault(call_id, []).extend(items)

    def fail_always(self, call_id: str, error: BaseException, times: int = 1000) -> None:
        self.script[call_id] = [error] * times

    async def run(self, spec: CallSpec[Any, Any], input: Any, ctx: CallContext) -> CallResult[Any]:
        self.calls.append((spec.id, input))
        self.contexts.append(ctx)
        queue = self.script.get(spec.id)
        if queue:
            item = queue.pop(0)
            if callable(item) and not isinstance(item, BaseModel | BaseException):
                item = item(input)
            if isinstance(item, BaseException):
                raise item
            return self._result(spec, ctx, item)
        if not self.fallback_to_mock:
            raise AssertionError(f"no scripted output left for {spec.id}")
        return self._result(spec, ctx, resolve_handler(spec.mock_handler)(input))


# --------------------------------------------------------------------------- contexts


def make_run_context(
    session_id: str = "ses_TESTSESSION", tenant_id: str = "tnt_TEST", request_id: str = "req_TEST"
) -> RunContext:
    return RunContext(session_id=session_id, tenant_id=tenant_id, request_id=request_id)


def make_call_context(
    session_id: str = "ses_TESTSESSION", purpose: str = "test", request_id: str = "req_TEST"
) -> CallContext:
    """A budget-agnostic ``CallContext`` for tests that call a gateway directly."""
    return CallContext(
        request_id=request_id,
        budget_scope=f"test:{session_id}",
        purpose=purpose,
        session_id=session_id,
        tenant_id="tnt_TEST",
    )


def make_request(
    *,
    policy: str | Policy = "guided-default",
    state: DiscoveryState | None = None,
    session_state: str = "DISCOVERING",
    gate_open: GateKind | None = None,
    language_hint: str | None = None,
    org_profile: dict[str, JsonValue] | None = None,
) -> DiscoveryRequest:
    resolved = resolve_policy(policy)
    return DiscoveryRequest.model_validate(
        {
            "session_id": "ses_TESTSESSION",
            "tenant_id": "tnt_TEST",
            "mode": resolved.mode,
            "policy": resolved,
            "state": state or DiscoveryState(),
            "session_state": session_state,
            "gate_open": gate_open,
            "language_hint": language_hint,
            "org_profile": org_profile,
        }
    )


# --------------------------------------------------------------------------- conversation driver


@dataclass
class Conversation:
    """Drive an engine the way the flow would, keeping every event."""

    gateway: DirectMockGateway = field(default_factory=DirectMockGateway)
    policy: str | Policy = "guided-default"
    language_hint: str | None = None
    org_profile: dict[str, JsonValue] | None = None  # sent with ``start`` only, like the flow
    ids: SequentialIds = field(default_factory=SequentialIds)
    clock: FakeClock = field(default_factory=FakeClock)
    state: DiscoveryState = field(default_factory=DiscoveryState)
    session_state: str = "DISCOVERING"
    gate_open: GateKind | None = None
    events: list[OutboundEvent] = field(default_factory=lambda: [])
    results: list[DiscoveryResult] = field(default_factory=lambda: [])
    engine: DiscoverEngine = field(init=False)

    def __post_init__(self) -> None:
        self.engine = DiscoverEngine(self.gateway, ids=self.ids, clock=self.clock)

    # ----------------------------------------------------------------- plumbing
    def swap_gateway(self, gateway: DirectMockGateway) -> None:
        """Continue the same conversation against another gateway (for example a scripted one)."""
        self.gateway = gateway
        self.engine = DiscoverEngine(gateway, ids=self.ids, clock=self.clock)

    def request(self, *, with_profile: bool = False) -> DiscoveryRequest:
        return make_request(
            policy=self.policy,
            state=self.state,
            session_state=self.session_state,
            gate_open=self.gate_open,
            language_hint=self.language_hint,
            org_profile=self.org_profile if with_profile else None,
        )

    def _take(self, result: DiscoveryResult) -> DiscoveryResult:
        self.state = result.state
        self.events.extend(result.events)
        self.results.append(result)
        return result

    @property
    def last(self) -> DiscoveryResult:
        return self.results[-1]

    @property
    def mode(self) -> Mode:
        return resolve_policy(self.policy).mode

    # ----------------------------------------------------------------- inbound events
    async def start(self) -> DiscoveryResult:
        request = self.request(with_profile=True)
        return self._take(await self.engine.start(request, make_run_context()))

    async def say(self, text: str) -> DiscoveryResult:
        event = UserMessage(event_id=self.ids("evt"), text=text)
        return self._take(await self.engine.handle(self.request(), event, make_run_context()))

    async def tap(
        self,
        option: str | list[str] | None = None,
        *,
        other_text: str | None = None,
        you_decide: bool = False,
        skipped: bool = False,
    ) -> DiscoveryResult:
        """Tap option id(s) or label(s) of the pending card (``None`` taps none)."""
        card = self.pending
        assert card is not None, "no pending card to tap"
        picks = [option] if isinstance(option, str) else list(option or [])
        ids: list[str] = []
        for pick in picks:
            match = next((o.id for o in card.card.options if pick in (o.id, o.label)), None)
            assert match is not None, f"{pick!r} is not an option of {card.card.id}"
            ids.append(match)
        event = OptionSelected(
            event_id=self.ids("evt"),
            card_instance_id=card.instance_id,
            option_ids=ids,
            other_text=other_text,
            you_decide=you_decide,
            skipped=skipped,
        )
        return self._take(await self.engine.handle(self.request(), event, make_run_context()))

    async def tap_recommended(self, text: str = "Blue Basket") -> DiscoveryResult:
        """What a user who agrees with every suggestion would tap."""
        card = self.pending
        assert card is not None, "no pending card to tap"
        c = card.card
        if c.type == "multi":
            ids = [o.id for o in c.options if o.recommended]
            return await self.tap(ids)
        if c.type in ("text", "upload"):
            return await self.tap(None, other_text=text)
        option = next((o for o in c.options if o.recommended), c.options[0])
        return await self.tap(option.id)

    async def ui(
        self, action: str, target: str | None = None, value: bool | None = None
    ) -> DiscoveryResult:
        event = UiAction.model_validate(
            {
                "event_id": self.ids("evt"),
                "action": action,
                "target": target,
                "value": value,
            }
        )
        return self._take(await self.engine.handle(self.request(), event, make_run_context()))

    async def reopen(self, slot_id: str) -> DiscoveryResult:
        """Gate 1 "change something": show the card of one slot again."""
        request = self.request()
        return self._take(await self.engine.reopen_slot(request, slot_id, make_run_context()))

    async def upload(
        self,
        filename: str = "notes.md",
        text: str | None = None,
        media_type: str = "text/markdown",
    ) -> DiscoveryResult:
        event = UploadAdded(
            event_id=self.ids("evt"),
            upload_id=f"upl_{len(self.state.uploads) + 1}",
            filename=filename,
            media_type=media_type,
            size_bytes=len(text or ""),
            text=text,
        )
        return self._take(await self.engine.handle(self.request(), event, make_run_context()))

    async def drive_recommended(self, limit: int = 30) -> list[str]:
        """Tap the recommended option until no card is pending. Returns the card ids asked."""
        asked: list[str] = []
        for _ in range(limit):
            card = self.pending
            if card is None:
                break
            asked.append(card.card.id)
            await self.tap_recommended()
        return asked

    # ----------------------------------------------------------------- inspection
    @property
    def pending(self) -> CardInstance | None:
        return self.state.pending_card

    def slot(self, slot_id: str) -> SlotRecord | None:
        return self.state.slots.get(slot_id)

    def value(self, slot_id: str) -> SlotData:
        rec = self.state.slots.get(slot_id)
        return rec.value if rec else None

    def items(self, slot_id: str) -> list[str]:
        """The items of a list slot ([] when empty or not a list)."""
        value = self.value(slot_id)
        return [str(v) for v in value] if isinstance(value, list) else []

    def state_of(self, slot_id: str) -> str:
        rec = self.state.slots.get(slot_id)
        return rec.state if rec else "empty"

    def cards(self) -> list[CardInstance]:
        return [e.card for e in self.events if isinstance(e, CardEvent)]

    def messages(self) -> list[str]:
        return [e.text for e in self.events if isinstance(e, AssistantMessage)]

    def notices(self) -> list[Notice]:
        return [e for e in self.events if isinstance(e, Notice)]

    def last_events(self) -> list[OutboundEvent]:
        return list(self.last.events)
