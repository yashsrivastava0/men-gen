"""Scripted, deterministic fakes of the three engines and the project repo (no LLM, no network).

They honour the engine protocols in ``mde_contracts.engines`` closely enough to drive the whole
flow: a Discover loop with cards and typed commands, a research engine that can succeed, report
``unavailable`` or blow up, and a spec builder that can build, ask one question, repair or fail.

Typed commands understood by :class:`FakeDiscoveryEngine` (everything else is an answer or chat):

* ``yes`` / ``ok`` / ``go ahead`` / ``build it`` / ``looks good``: a confident approval. The fake
  reports ``approval_confident=True`` *even when no gate is open*: the flow must not trust it alone.
* ``ok maybe`` / ``looks fine but ...``: an unclear approval (never confident).
* ``set <slot>=<value>`` / ``clear <slot>``: a change. ``feature+ <id>`` / ``feature- <id>``: toggle.
* ``add screen <name>``: an ``add_screen`` change op. ``stop`` / ``pause`` / ``start over``.
* a question mark: a question (no slot change).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import JsonValue

from mde_contracts.brief import Brief
from mde_contracts.calls import ChangeOp, Intent
from mde_contracts.cards import Card, CardInstance, CardOption, CardProgress
from mde_contracts.engines import (
    BuildRequest,
    BuildResult,
    DiscoveryRequest,
    DiscoveryResult,
    DiscoveryState,
    ResearchRequest,
    RunContext,
)
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    GateDecision,
    InboundEvent,
    OptionSelected,
    OutboundEvent,
    UiAction,
    UploadAdded,
    UserMessage,
)
from mde_contracts.ports import BudgetVerdict, CommitInfo
from mde_contracts.research import ResearchNote, ResearchSuggestion
from mde_contracts.slots import SlotRecord, SlotSource
from mde_contracts.spec import ResearchAddition
from mde_core.errors import Conflict
from mde_core.hashing import sha256_hex
from mde_flow.diffing import diff_ids
from mde_flow.testing.builders import (
    brief_from_slots,
    make_plan,
    make_spec,
    render_spec_text,
    slot,
)

REQUIRED_SLOTS: tuple[str, ...] = (
    "idea.summary",
    "idea.archetype",
    "auth.method",
    "design.direction",
)
CONFIDENT_APPROVALS = frozenset(
    {"yes", "ok", "okay", "go ahead", "build it", "looks good", "looks right", "yes, build it"}
)
_OPTIONS: dict[str, list[tuple[str, str]]] = {
    "idea.archetype": [("A", "commerce"), ("B", "booking"), ("C", "custom")],
    "auth.method": [("A", "email"), ("B", "phone"), ("C", "sso")],
    "design.direction": [("A", "clean-bright"), ("B", "bold-dark"), ("C", "warm-friendly")],
    "pay.model": [("A", "mock-checkout"), ("B", "card"), ("C", "none")],
}
#: What the scripted engine says on the first silence (the real Discover has its own copy).
FAKE_IDLE_NUDGE = "Still there? Pick an option above, or tell me to decide."
_SET_RE = re.compile(r"^set\s+([a-z_.]+)\s*=\s*(.+)$", re.IGNORECASE)


def _card(
    slot_id: str, ids: Callable[[str], str], *, asked: int = 1, cap: int = 10
) -> CardInstance:
    options = _OPTIONS.get(slot_id)
    if options is None:
        card = Card.model_validate(
            {"id": f"Q-{slot_id}", "slot": slot_id, "type": "text", "text": f"Tell me: {slot_id}"}
        )
    else:
        card = Card.model_validate(
            {
                "id": f"Q-{slot_id}",
                "slot": slot_id,
                "type": "choice3",
                "text": f"Which {slot_id} fits best?",
                "options": [
                    CardOption(
                        id=oid, label=value, recommended=(oid == "A"), sets={slot_id: value}
                    ).model_dump()
                    for oid, value in options
                ],
            }
        )
    return CardInstance(
        instance_id=ids("crd"), card=card, progress=CardProgress(asked=asked, cap=cap)
    )


def _recommended(card: Card) -> CardOption | None:
    return next(
        (o for o in card.options if o.recommended), card.options[0] if card.options else None
    )


@dataclass
class FakeDiscoveryEngine:
    """A scripted Discover engine. ``override`` can pre-empt any ``handle`` call."""

    ids: Callable[[str], str]
    required: tuple[str, ...] = REQUIRED_SLOTS
    override: Callable[[DiscoveryRequest, InboundEvent], DiscoveryResult | None] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    compiles: int = 0
    # The Org Profile each call carried (spec 10): the flow sends it with `start` only
    org_profiles: list[tuple[str, dict[str, JsonValue] | None]] = field(
        default_factory=list[tuple[str, dict[str, JsonValue] | None]]
    )

    def profile_seen_by(self, method: str) -> list[dict[str, JsonValue] | None]:
        """The ``org_profile`` of every ``method`` call, in order."""
        return [p for name, p in self.org_profiles if name == method]

    # ----------------------------------------------------------------- protocol
    async def start(self, req: DiscoveryRequest, ctx: RunContext) -> DiscoveryResult:
        self.calls.append(("start", ""))
        self.org_profiles.append(("start", req.org_profile))
        state = req.state.model_copy(update={"greeted": True})
        msg = self._say(req, "Hi! What would you like to build?", chips=["A shop", "Bookings"])
        return DiscoveryResult(state=state, events=[msg], intent="greeting")

    async def handle(
        self, req: DiscoveryRequest, event: InboundEvent, ctx: RunContext
    ) -> DiscoveryResult:
        self.calls.append(("handle", self._describe(event)))
        self.org_profiles.append(("handle", req.org_profile))
        if self.override is not None:
            forced = self.override(req, event)
            if forced is not None:
                return forced
        if isinstance(event, OptionSelected):
            return self._on_option(req, event)
        if isinstance(event, UiAction):
            return self._on_ui(req, event)
        if isinstance(event, GateDecision):
            return self._finish(req, req.state, [], "other")
        text = event.text if isinstance(event, UserMessage) else (event.text or "")
        assert isinstance(event, (UserMessage, UploadAdded))
        return self._on_text(req, text)

    async def reopen_slot(
        self, req: DiscoveryRequest, slot_id: str, ctx: RunContext
    ) -> DiscoveryResult:
        self.calls.append(("reopen_slot", slot_id))
        self.org_profiles.append(("reopen_slot", req.org_profile))
        card = _card(slot_id, self.ids)
        state = req.state.model_copy(update={"pending_card": card})
        return DiscoveryResult(state=state, events=[self._card_event(req, card)], intent="change")

    async def apply_changes(
        self, req: DiscoveryRequest, ops: list[ChangeOp], source: SlotSource, ctx: RunContext
    ) -> DiscoveryResult:
        self.calls.append(
            ("apply_changes", ",".join(f"{o.kind}:{o.slot_id or o.target_id}" for o in ops))
        )
        self.org_profiles.append(("apply_changes", req.org_profile))
        state, changed = self._apply_ops(req.state, ops, source)
        res = self._finish(req, state, [], "change", changed=changed)
        return res.model_copy(update={"change_ops": []})

    async def compile_brief(self, req: DiscoveryRequest, ctx: RunContext) -> Brief:
        self.compiles += 1
        self.calls.append(("compile_brief", ""))
        self.org_profiles.append(("compile_brief", req.org_profile))
        return brief_from_slots(req.state.slots, mode=req.mode, language=req.state.language)

    # ----------------------------------------------------------------- internals
    @staticmethod
    def _describe(event: InboundEvent) -> str:
        if isinstance(event, UserMessage):
            return f"user_message:{event.text}"
        if isinstance(event, UiAction):
            return f"ui_action:{event.action}"
        return event.type

    def _card_event(self, req: DiscoveryRequest, card: CardInstance) -> CardEvent:
        return CardEvent(event_id=self.ids("evt"), session_id=req.session_id, card=card)

    def _say(
        self, req: DiscoveryRequest, text: str, *, chips: Sequence[str] = ()
    ) -> AssistantMessage:
        return AssistantMessage(
            event_id=self.ids("evt"),
            session_id=req.session_id,
            text=text,
            chips=list(chips),
            canned=True,
        )

    def _missing(self, state: DiscoveryState) -> list[str]:
        return [s for s in self.required if s not in state.slots or state.slots[s].state == "empty"]

    def _put(
        self, state: DiscoveryState, slot_id: str, value: object, *, kind: str, source: str
    ) -> DiscoveryState:
        slots = dict(state.slots)
        slots[slot_id] = slot(slot_id, value, state=kind, source=source)  # type: ignore[arg-type]
        return state.model_copy(update={"slots": slots})

    def _finish(
        self,
        req: DiscoveryRequest,
        state: DiscoveryState,
        events: list[OutboundEvent],
        intent: Intent,
        *,
        changed: Sequence[str] = (),
        ops: Sequence[ChangeOp] = (),
        approval_confident: bool = False,
        stop: bool = False,
        pause: bool = False,
        start_over: bool = False,
        ask_next: bool = True,
    ) -> DiscoveryResult:
        missing = self._missing(state)
        out = list(events)
        if missing and state.pending_card is None and ask_next:
            card = _card(missing[0], self.ids, asked=state.card_count + 1)
            state = state.model_copy(
                update={"pending_card": card, "card_count": state.card_count + 1}
            )
            out.append(self._card_event(req, card))
        return DiscoveryResult(
            state=state.model_copy(update={"turn_count": state.turn_count + 1}),
            events=out,
            intent=intent,
            brief_complete=not missing,
            approval_confident=approval_confident,
            change_ops=list(ops),
            changed_slots=list(changed),
            stop_requested=stop,
            pause_requested=pause,
            start_over_requested=start_over,
        )

    def _apply_ops(
        self, state: DiscoveryState, ops: Sequence[ChangeOp], source: SlotSource
    ) -> tuple[DiscoveryState, list[str]]:
        changed: list[str] = []
        for op in ops:
            if op.kind == "set_slot" and op.slot_id:
                value: object = (
                    op.value_list
                    if op.value_list is not None
                    else (op.value_bool if op.value_bool is not None else op.value_text)
                )
                state = self._put(state, op.slot_id, value, kind="confirmed", source=source)
                changed.append(op.slot_id)
            elif op.kind == "clear_slot" and op.slot_id:
                slots = dict(state.slots)
                slots.pop(op.slot_id, None)
                state = state.model_copy(update={"slots": slots})
                changed.append(op.slot_id)
            elif op.kind == "toggle_feature" and op.target_id:
                cur = state.slots.get("scope.features")
                feats = list(cur.value) if cur and isinstance(cur.value, list) else []
                if op.value_bool is False:
                    feats = [f for f in feats if f != op.target_id]
                elif op.target_id not in feats:
                    feats.append(op.target_id)
                state = self._put(state, "scope.features", feats, kind="confirmed", source=source)
                changed.append("scope.features")
            elif op.kind == "other" and op.instruction == "toggle_assumption" and op.slot_id:
                rec = state.slots.get(op.slot_id)
                if op.value_bool is False:
                    slots = dict(state.slots)
                    slots.pop(op.slot_id, None)
                    state = state.model_copy(update={"slots": slots})
                elif rec is not None:
                    state = self._put(
                        state, op.slot_id, rec.value, kind="assumed", source="default"
                    )
                changed.append(op.slot_id)
        return state, changed

    def _on_option(self, req: DiscoveryRequest, event: OptionSelected) -> DiscoveryResult:
        state = req.state
        pending = state.pending_card
        if pending is None or pending.instance_id != event.card_instance_id:
            return self._finish(
                req, state, [self._say(req, "That question is no longer current.")], "other",
                ask_next=False,
            )  # fmt: skip
        card = pending.card
        state = state.model_copy(update={"pending_card": None})
        changed: list[str] = []
        if event.you_decide or event.skipped:
            opt = _recommended(card)
            if opt is not None:
                for sid, val in opt.sets.items():
                    state = self._put(state, sid, val, kind="assumed", source="default")
                    changed.append(sid)
        elif card.type == "text":
            state = self._put(
                state, card.slot, event.other_text or "", kind="confirmed", source="tap"
            )
            changed.append(card.slot)
        else:
            chosen = next((o for o in card.options if o.id in event.option_ids), None)
            if chosen is not None:
                for sid, val in chosen.sets.items():
                    state = self._put(state, sid, val, kind="confirmed", source="tap")
                    changed.append(sid)
        state = self._defaults_after(state, changed)
        ack = self._say(req, "Got it.")
        return self._finish(req, state, [ack], "answer", changed=changed)

    def _defaults_after(self, state: DiscoveryState, changed: Sequence[str]) -> DiscoveryState:
        """Choosing an app type assumes a starter feature list (shows up as 'We assumed')."""
        if "idea.archetype" in changed and "scope.features" not in state.slots:
            return self._put(
                state, "scope.features", ["browse", "search"], kind="assumed", source="default"
            )
        return state

    def _on_idle(self, req: DiscoveryRequest, event: UiAction) -> DiscoveryResult:
        """What the real Discover does with ``idle`` (spec 3.2, 9): nothing pending, nothing to say;
        the first silence on a card nudges once beside the same card; the next one assumes the
        recommended option, except on a card that is not an ordinary or compliance question."""
        state = req.state
        pending = state.pending_card
        if pending is None:
            return self._finish(req, state, [], "other", ask_next=False)
        card = pending.card
        if card.id not in state.nudged_card_ids:
            state = state.model_copy(update={"nudged_card_ids": [*state.nudged_card_ids, card.id]})
            events: list[OutboundEvent] = [
                self._say(req, FAKE_IDLE_NUDGE),
                self._card_event(req, pending),
            ]
            return self._finish(req, state, events, "other", ask_next=False)
        if card.kind not in ("standard", "compliance") or not card.skip.enabled:
            return self._finish(req, state, [], "other", ask_next=False)
        decide = OptionSelected(
            event_id=event.event_id, card_instance_id=pending.instance_id, you_decide=True
        )
        return self._on_option(req, decide)

    def _on_ui(self, req: DiscoveryRequest, event: UiAction) -> DiscoveryResult:
        state = req.state
        if event.action == "idle":
            return self._on_idle(req, event)
        if event.action == "finish_with_defaults":
            changed: list[str] = []
            for sid in self._missing(state):
                opts = _OPTIONS.get(sid)
                value = opts[0][1] if opts else "defaulted"
                state = self._put(state, sid, value, kind="assumed", source="default")
                changed.append(sid)
            state = self._defaults_after(state, changed).model_copy(
                update={"finished_with_defaults": True, "pending_card": None}
            )
            return self._finish(
                req, state, [self._say(req, "Using smart defaults.")], "other", changed=changed
            )
        return self._finish(req, state, [], "other", ask_next=False)

    def _on_text(self, req: DiscoveryRequest, raw: str) -> DiscoveryResult:
        state = req.state
        text = raw.strip()
        low = text.lower()
        if low in {"stop", "cancel"}:
            return self._finish(req, state, [], "stop", stop=True, ask_next=False)
        if low in {"pause", "pause please"}:
            return self._finish(req, state, [], "pause", pause=True, ask_next=False)
        if low in {"start over", "restart"}:
            return self._finish(req, state, [], "start_over", start_over=True, ask_next=False)
        unclear = low.startswith(("ok maybe", "looks fine but", "i guess", "hmm"))
        if low in CONFIDENT_APPROVALS or unclear:
            return self._on_approval_text(req, low, unclear=unclear)
        ops = self._parse_ops(text)
        if ops:
            state, changed = self._apply_ops(state, ops, "text")
            return self._finish(
                req, state, [self._say(req, "Updated.")], "change", changed=changed, ops=ops
            )
        if text.endswith("?"):
            return self._finish(
                req, state, [self._say(req, "Good question. You will see the plan first.")],
                "question", ask_next=False,
            )  # fmt: skip
        if req.session_state != "DISCOVERING" or req.gate_open is not None:
            return self._finish(req, state, [self._say(req, "Noted.")], "chitchat", ask_next=False)
        pending = state.pending_card
        slot_id = pending.card.slot if pending is not None else (self._missing(state) or [""])[0]
        if not slot_id:
            return self._finish(req, state, [self._say(req, "Noted.")], "chitchat", ask_next=False)
        state = self._put(state, slot_id, text, kind="confirmed", source="text")
        state = state.model_copy(update={"pending_card": None})
        state = self._defaults_after(state, [slot_id])
        return self._finish(req, state, [self._say(req, "Got it.")], "answer", changed=[slot_id])

    def _on_approval_text(
        self, req: DiscoveryRequest, low: str, *, unclear: bool
    ) -> DiscoveryResult:
        state = req.state
        if unclear:
            ops = self._parse_ops(low.split(" but ", 1)[1]) if " but " in low else []
            changed: list[str] = []
            if ops:
                state, changed = self._apply_ops(state, ops, "text")
            msg = self._say(req, "I was not sure that was a yes.")
            return self._finish(
                req, state, [msg], "approve", changed=changed, ops=ops, approval_confident=False,
                ask_next=False,
            )  # fmt: skip
        if req.gate_open is None and state.pending_card is not None:
            # No gate: 'yes' answers the pending card with its recommended option.
            card = state.pending_card.card
            opt = _recommended(card)
            changed = []
            state = state.model_copy(update={"pending_card": None})
            if opt is not None:
                for sid, val in opt.sets.items():
                    state = self._put(state, sid, val, kind="confirmed", source="text")
                    changed.append(sid)
            state = self._defaults_after(state, changed)
            return self._finish(
                req, state, [self._say(req, "Got it.")], "approve", changed=changed,
                approval_confident=True,
            )  # fmt: skip
        events: list[OutboundEvent] = []
        if req.gate_open is None:
            events.append(self._say(req, "What do you mean?"))
        return self._finish(req, state, events, "approve", approval_confident=True, ask_next=False)

    @staticmethod
    def _parse_ops(text: str) -> list[ChangeOp]:
        m = _SET_RE.match(text.strip())
        if m:
            sid, val = m.group(1), m.group(2).strip()
            return [
                ChangeOp.model_validate({"kind": "set_slot", "slot_id": sid, "value_text": val})
            ]
        low = text.strip().lower()
        if low.startswith("clear "):
            return [ChangeOp.model_validate({"kind": "clear_slot", "slot_id": low[6:].strip()})]
        if low.startswith("feature+ "):
            return [ChangeOp(kind="toggle_feature", target_id=low[9:].strip(), value_bool=True)]
        if low.startswith("feature- "):
            return [ChangeOp(kind="toggle_feature", target_id=low[9:].strip(), value_bool=False)]
        if low.startswith("add screen "):
            return [ChangeOp(kind="add_screen", instruction=text.strip()[11:].strip())]
        return []


# --------------------------------------------------------------------------- research

ResearchMode = Literal["ok", "unavailable", "raise"]


class _Gate:
    """A deterministic rendezvous: the engine announces it is inside, the test releases it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self._released = asyncio.Event()

    def release(self) -> None:
        self._released.set()

    async def wait(self, ctx: RunContext) -> None:
        self.entered.set()
        while not self._released.is_set():
            ctx.cancel.check()
            await asyncio.sleep(0)  # yield only; never a wall-clock sleep


@dataclass
class FakeResearchEngine:
    """Succeeds with two suggestions by default; ``modes`` scripts the next calls."""

    modes: list[ResearchMode] = field(default_factory=list[ResearchMode])
    steps: int = 3
    hold: _Gate | None = None
    calls: list[ResearchRequest] = field(default_factory=list[ResearchRequest])

    def pause_inside(self) -> _Gate:
        self.hold = _Gate()
        return self.hold

    async def run(self, req: ResearchRequest, ctx: RunContext) -> ResearchNote:
        self.calls.append(req)
        mode: ResearchMode = self.modes.pop(0) if self.modes else "ok"
        for i in range(self.steps):
            ctx.cancel.check()
            await ctx.progress("research", "search", "Looking at similar apps", i, self.steps)
            if i == 1 and self.hold is not None:
                await self.hold.wait(ctx)
        if mode == "raise":
            raise RuntimeError("research exploded")
        if mode == "unavailable":
            return ResearchNote.unavailable(req.brief.hash, "failed", req.depth)
        suggestions = [
            ResearchSuggestion(
                id="RA-001", kind="feature", title="offline-mode", rationale="Seen in most apps",
                applies_to="scope.features", verified=True,
            ),
            ResearchSuggestion(
                id="RA-002", kind="pattern", title="onboarding", rationale="Common pattern"
            ),
        ]  # fmt: skip
        return ResearchNote(
            brief_hash=req.brief.hash, depth=req.depth, status="ok", suggestions=suggestions,
            queries_run=2,
        ).with_hash()  # fmt: skip


@dataclass(frozen=True)
class FakeResearchReport:
    """The part of ``DefaultResearchEngine``'s report the flow reads."""

    note: ResearchNote
    injection_events: list[object]


@dataclass
class FakeReportingResearch(FakeResearchEngine):
    """A research engine that also reports ``injections`` ignored pieces of content (the flow tells
    the user, in plain words, that it ignored instructions it found)."""

    injections: int = 1

    async def run_with_report(self, req: ResearchRequest, ctx: RunContext) -> FakeResearchReport:
        note = await self.run(req, ctx)
        return FakeResearchReport(note, [object()] * self.injections)


# --------------------------------------------------------------------------- budget gate


@dataclass
class FakeBudgetGate:
    """A scripted ``BudgetGate``: ``script`` holds the next answers (True: planning may go on);
    when it runs out the last answer repeats. ``fail`` makes the next check raise."""

    script: list[bool] = field(default_factory=list[bool])
    message: str | None = "The budget for this app has been reached, so I paused before building."
    calls: list[str] = field(default_factory=list[str])
    fail: bool = False
    _last: bool = True

    def allow(self, ok: bool) -> None:
        """From now on (after the script) every check answers ``ok``."""
        self.script.append(ok)

    async def check(self, *, session_id: str, tenant_id: str) -> BudgetVerdict:
        self.calls.append(session_id)
        if self.fail:
            self.fail = False
            raise RuntimeError("the gateway did not answer")
        if self.script:
            self._last = self.script.pop(0)
        if self._last:
            return BudgetVerdict(ok=True)
        return BudgetVerdict(ok=False, reason="cap_reached", message=self.message)


# --------------------------------------------------------------------------- spec builder

BuildMode = Literal["ok", "needs_answer", "repair", "raise", "invalid_hash"]


@dataclass
class FakeSpecBuilder:
    """Builds a small valid spec from the slots (one screen per feature).

    ``ask_slot``: when set and that slot is empty, the first build asks one question
    (``needs_answer``) and builds normally once it is answered.
    """

    ids: Callable[[str], str]
    modes: list[BuildMode] = field(default_factory=list[BuildMode])
    ask_slot: str | None = None
    steps: int = 3
    hold: _Gate | None = None
    calls: list[BuildRequest] = field(default_factory=list[BuildRequest])

    def pause_inside(self) -> _Gate:
        self.hold = _Gate()
        return self.hold

    async def build(self, req: BuildRequest, ctx: RunContext) -> BuildResult:
        self.calls.append(req)
        mode: BuildMode = self.modes.pop(0) if self.modes else "ok"
        for i in range(self.steps):
            ctx.cancel.check()
            await ctx.progress("build", "baseline", "Putting your plan together", i, self.steps)
            if i == 1 and self.hold is not None:
                await self.hold.wait(ctx)
        ctx.cancel.check()
        if mode == "raise":
            raise RuntimeError("builder exploded")
        if self.ask_slot and not _filled(req.slots, self.ask_slot):
            card = _card(self.ask_slot, self.ids)
            return BuildResult(status="needs_answer", question=card, question_slot=self.ask_slot)
        return self._ok(req, mode)

    def _ok(self, req: BuildRequest, mode: BuildMode) -> BuildResult:
        slots = req.slots
        feats_rec = slots.get("scope.features")
        features = (
            list(feats_rec.value)
            if feats_rec and isinstance(feats_rec.value, list)
            else ["browse", "search"]
        )
        additions: list[ResearchAddition] = []
        if req.research is not None:
            for s in req.research.suggestions:
                included = s.id not in req.excluded_research_ids
                additions.append(
                    ResearchAddition(
                        id=s.id, title=s.title, kind=s.kind, included=included, verified=s.verified
                    )
                )
                if included and s.kind == "feature" and s.title not in features:
                    features.append(s.title)
        direction = _text(slots, "design.direction", "clean-bright")
        auth = _text(slots, "auth.method", "email")
        prev = req.previous_spec
        spec = make_spec(
            version=req.spec_version, app_id=req.app_id, brief_hash=req.brief.hash,
            features=features, direction=direction, auth=auth, research_additions=additions,
            previous_version=prev.header.spec_version if prev else None,
        )  # fmt: skip
        if prev is not None:
            changed = diff_ids(prev, spec)
            spec = spec.model_copy(
                update={
                    "revision": spec.revision.model_copy(
                        update={"changed_ids": [*changed.added, *changed.changed]}
                    )
                }
            )
        text = render_spec_text(spec)
        digest = sha256_hex(text) if mode != "invalid_hash" else "0" * 64
        plan = make_plan(spec, spec_hash=digest, note=req.research)
        return BuildResult(
            status="ok", spec=spec, spec_text=text, spec_hash=digest, plan=plan,
            proposals_applied=1, repair_rounds=1 if mode == "repair" else 0,
        )  # fmt: skip


def _filled(slots: dict[str, SlotRecord], slot_id: str) -> bool:
    rec = slots.get(slot_id)
    return rec is not None and rec.state != "empty"


def _text(slots: dict[str, SlotRecord], slot_id: str, default: str) -> str:
    rec = slots.get(slot_id)
    if rec is None or rec.state == "empty" or rec.value is None:
        return default
    return str(rec.value)


# --------------------------------------------------------------------------- project repo


@dataclass
class FakeCommit:
    tenant_id: str
    app_id: str
    version: int
    spec_hash: str
    sha: str
    message: str


@dataclass
class FakeProjectRepo:
    """In-memory ``ProjectRepoPort``: idempotent per (tenant, app, version, hash)."""

    commits: list[FakeCommit] = field(default_factory=list[FakeCommit])
    fail_next: int = 0
    on_commit: Callable[[FakeCommit], Awaitable[None] | None] | None = None

    async def commit_spec(
        self,
        *,
        tenant_id: str,
        app_id: str,
        spec_text: str,
        version: int,
        spec_hash: str,
        message: str,
    ) -> CommitInfo:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("git is unavailable")
        for c in self.commits:
            if (c.tenant_id, c.app_id, c.version) == (tenant_id, app_id, version):
                if c.spec_hash != spec_hash:
                    raise Conflict("that version was already committed with a different hash")
                return CommitInfo(sha=c.sha, path="APP_SPEC.md", repo_dir=f"{tenant_id}/{app_id}")
        sha = sha256_hex(f"{tenant_id}/{app_id}/{version}/{spec_hash}")[:40]
        commit = FakeCommit(tenant_id, app_id, version, spec_hash, sha, message)
        self.commits.append(commit)
        if self.on_commit is not None:
            res = self.on_commit(commit)
            if res is not None:
                await res
        return CommitInfo(sha=sha, path="APP_SPEC.md", repo_dir=f"{tenant_id}/{app_id}")
