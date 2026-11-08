"""One conversation with one session, over the HTTP API.

``Conversation`` is the part that both ``mde chat`` (which renders every event) and ``mde sim``
(which only collects them) share: it creates or resumes a session, sends the five inbound events
with fresh ids, keeps a de-duplicated log of every outbound event it has seen, and waits for the
plan job without ever hanging.

Events can reach the client twice (a reply to a POST and the stream) and out of order (the job
appends progress while a request is being answered), so the log is keyed by ``seq``: each event is
handed to ``on_event`` exactly once, and the cursor only advances over a gap-free prefix.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import aclosing
from typing import Final

from mde_cli.client import ApiClient, Identity
from mde_contracts.cards import Card
from mde_contracts.common import GateKind
from mde_contracts.events import (
    GateEvent,
    OutboundEvent,
    SpecReady,
    StateChanged,
)
from mde_core.ids import new_id
from mde_flow.models import SessionSnapshot

WAIT_CHUNK_S: Final = 5.0
DEFAULT_WAIT_S: Final = 600.0
DEFAULT_RECOMMENDED_TEXT: Final = "Blue Basket"

EventHook = Callable[[OutboundEvent], None]


class ConversationError(Exception):
    """Something the user can act on (nothing to answer, a wait that ran out)."""


class PlanTimeout(ConversationError):
    """The plan job did not finish within the time allowed (it keeps running)."""


def resolve_options(card: Card, picks: Sequence[str]) -> list[str]:
    """Option ids for ids or labels (case-insensitive); an unknown pick is an error."""
    ids: list[str] = []
    for pick in picks:
        wanted = pick.strip().lower()
        match = next(
            (o.id for o in card.options if wanted in (o.id.lower(), o.label.strip().lower())),
            None,
        )
        if match is None:
            have = ", ".join(o.id for o in card.options) or "none"
            raise ConversationError(f"{pick!r} is not an option of this question (options: {have})")
        ids.append(match)
    return ids


def recommended_ids(card: Card) -> list[str]:
    """The suggested option(s): every suggested one on a multi card, else the first (or option 1)."""
    if card.type == "multi":
        return [o.id for o in card.options if o.recommended]
    if card.type in ("text", "upload") or not card.options:
        return []
    return [next((o for o in card.options if o.recommended), card.options[0]).id]


def _ends_planning(event: OutboundEvent) -> bool:
    if isinstance(event, GateEvent | SpecReady):
        return True
    return isinstance(event, StateChanged) and event.to_state != "PLANNING"


class Conversation:
    def __init__(self, client: ApiClient, *, on_event: EventHook | None = None) -> None:
        self.client = client
        self.on_event = on_event
        self.session_id = ""
        self.snap: SessionSnapshot | None = None
        self.log: list[OutboundEvent] = []
        self.owner: Identity = client.identity  # who created the session (the requester)
        self.contiguous = 0  # every seq up to here has been seen
        self._extra: set[int] = set()  # seen seqs above a gap

    # ----------------------------------------------------------------------------- the log

    def _accept(self, events: Sequence[OutboundEvent]) -> list[OutboundEvent]:
        new: list[OutboundEvent] = []
        for event in events:
            if event.seq <= self.contiguous or event.seq in self._extra:
                continue
            self._extra.add(event.seq)
            self.log.append(event)
            new.append(event)
            if self.on_event is not None:
                self.on_event(event)
        while self.contiguous + 1 in self._extra:
            self.contiguous += 1
            self._extra.discard(self.contiguous)
        return new

    async def drain(self) -> list[OutboundEvent]:
        """Fetch and hand out whatever is stored beyond the cursor (job events arrive outside any
        request)."""
        stored = await self.client.stored_events(self.session_id, after=self.contiguous)
        return self._accept(stored)

    @property
    def last_gate(self) -> GateEvent | None:
        for event in reversed(self.log):
            if isinstance(event, GateEvent):
                return event
        return None

    def gate_events(self, gate: GateKind) -> list[GateEvent]:
        return [e for e in self.log if isinstance(e, GateEvent) and e.gate == gate]

    # ----------------------------------------------------------------------------- the session

    async def start(
        self, mode: str = "guided", policy_id: str | None = None
    ) -> list[OutboundEvent]:
        created = await self.client.create_session(mode, policy_id)
        self.session_id = created.session.session_id
        self.snap = created.session
        self.owner = self.client.identity
        return self._accept(created.events)

    async def resume(self, session_id: str) -> SessionSnapshot:
        """Pick up an existing session where it stands (the past is not replayed)."""
        self.session_id = session_id
        snap = await self.refresh()
        self.contiguous = snap.last_seq
        return snap

    async def refresh(self) -> SessionSnapshot:
        self.snap = await self.client.snapshot(self.session_id)
        return self.snap

    @property
    def state(self) -> str:
        return self.snap.state if self.snap is not None else "UNKNOWN"

    # ----------------------------------------------------------------------------- sending

    async def post(
        self, event: dict[str, object], identity: Identity | None = None
    ) -> list[OutboundEvent]:
        """Send one inbound event; returns the events that were new to us (the replies and anything
        the job appended meanwhile)."""
        reply = await self.client.post_event(self.session_id, event, identity)
        new = self._accept(reply.events)
        top = max([self.contiguous, *self._extra])
        if reply.last_seq > top or self._extra:
            new.extend(await self.drain())
        await self.refresh()
        return new

    @staticmethod
    def event_id() -> str:
        return new_id("evt")

    async def say(self, text: str, identity: Identity | None = None) -> list[OutboundEvent]:
        return await self.post(
            {"type": "user_message", "event_id": self.event_id(), "text": text}, identity
        )

    async def tap(
        self,
        option_ids: Sequence[str] = (),
        *,
        other_text: str | None = None,
        you_decide: bool = False,
        skipped: bool = False,
    ) -> list[OutboundEvent]:
        snap = await self.refresh()
        if snap.pending_card is None:
            raise ConversationError("There is no question waiting for an answer right now.")
        return await self.post(
            {
                "type": "option_selected",
                "event_id": self.event_id(),
                "card_instance_id": snap.pending_card.instance_id,
                "option_ids": list(option_ids),
                "other_text": other_text,
                "you_decide": you_decide,
                "skipped": skipped,
            }
        )

    async def tap_recommended(self, text: str = DEFAULT_RECOMMENDED_TEXT) -> list[OutboundEvent]:
        """What a user who agrees with every suggestion would do on the pending card."""
        snap = await self.refresh()
        if snap.pending_card is None:
            raise ConversationError("There is no question waiting for an answer right now.")
        card = snap.pending_card.card
        ids = recommended_ids(card)
        if card.type in ("text", "upload"):
            return await self.tap((), other_text=text)
        return await self.tap(ids)

    async def answer_with_recommended(
        self, text: str = DEFAULT_RECOMMENDED_TEXT, limit: int = 60
    ) -> list[OutboundEvent]:
        """Take the suggested answer on every pending card until the interview is over."""
        out: list[OutboundEvent] = []
        for _ in range(limit):
            snap = await self.refresh()
            if snap.state != "DISCOVERING" or snap.pending_card is None:
                return out
            out.extend(await self.tap_recommended(text))
        raise ConversationError(f"The interview did not finish within {limit} questions.")

    async def upload(
        self, filename: str, text: str | None, media_type: str = "text/markdown"
    ) -> list[OutboundEvent]:
        return await self.post(
            {
                "type": "upload_added",
                "event_id": self.event_id(),
                "upload_id": new_id("upl"),
                "filename": filename,
                "media_type": media_type,
                "size_bytes": len((text or "").encode("utf-8")),
                "text": text,
            }
        )

    async def decide(
        self,
        decision: str = "approve",
        *,
        gate: GateKind | None = None,
        comment: str | None = None,
        role: str | None = None,
        identity: Identity | None = None,
    ) -> list[OutboundEvent]:
        """Decide the gate that is open (its version and hash are read from the session)."""
        snap = await self.refresh()
        if snap.gate is None or (gate is not None and snap.gate.gate != gate):
            raise ConversationError("There is no page waiting for a decision right now.")
        return await self.post(
            {
                "type": "gate_decision",
                "event_id": self.event_id(),
                "gate": snap.gate.gate,
                "decision": decision,
                "version": snap.gate.version,
                "hash": snap.gate.hash,
                "comment": comment,
                "role": role,
            },
            identity,
        )

    async def ui(
        self, action: str, target: str | None = None, value: bool | None = None
    ) -> list[OutboundEvent]:
        return await self.post(
            {
                "type": "ui_action",
                "event_id": self.event_id(),
                "action": action,
                "target": target,
                "value": value,
            }
        )

    # ----------------------------------------------------------------------------- waiting

    async def wait_planning(self, timeout_s: float = DEFAULT_WAIT_S) -> SessionSnapshot:
        """Follow the stream until the session leaves PLANNING (research and build run in the
        background). Bounded: after ``timeout_s`` it raises :class:`PlanTimeout`; the job goes on."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            snap = await self.refresh()
            if snap.state != "PLANNING":
                await self.drain()
                return snap
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PlanTimeout(
                    f"Still planning after {int(timeout_s)} seconds. The work goes on: come back "
                    f"with --session {self.session_id}."
                )
            async with aclosing(
                self.client.watch(
                    self.session_id, after=self.contiguous, timeout_s=min(remaining, WAIT_CHUNK_S)
                )
            ) as stream:
                async for event in stream:
                    self._accept([event])
                    if _ends_planning(event):
                        break

    async def watch_for(self, seconds: float) -> list[OutboundEvent]:
        """Show whatever happens in the next ``seconds`` (others approving, a job finishing)."""
        out: list[OutboundEvent] = []
        async with aclosing(
            self.client.watch(self.session_id, after=self.contiguous, timeout_s=seconds)
        ) as stream:
            async for event in stream:
                out.extend(self._accept([event]))
        await self.refresh()
        return out
