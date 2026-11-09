"""Conversation driver for scenario and persona tests: plays a session against a real ``Service``.

Mirrors what the UI does: sends inbound events (taps, text, edits, gate decisions) and keeps every outbound event
it sees. Event ids are sequential so runs are reproducible.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import JsonValue

from mde_compass.domain import Card, StoredEvent
from mde_compass.domain.events import (
    INBOUND_ADAPTER,
    AssumptionDecision,
    BriefEdit,
    BriefFeedback,
    CardAnswer,
    CardBack,
    CardSkip,
    CardYouDecide,
    Command,
    GateDecision,
    LookupEdit,
    UserMessage,
)
from mde_compass.engine.ports import Caller, TurnResult


class Conversation:
    def __init__(self, service: Any, caller: Caller, session_id: str) -> None:
        self.service = service
        self.caller = caller
        self.session_id = session_id
        self.events: list[StoredEvent] = []
        self._n = 0

    @classmethod
    async def start(
        cls,
        service: Any,
        *,
        mode: str = "guided",
        locale: str = "en",
        tenant: str = "t1",
        user: str = "u1",
    ) -> Conversation:
        caller = Caller(tenant_id=tenant, user_id=user)
        session = await service.create_session(caller, mode=mode, locale=locale, user_label="Test")
        conv = cls(service, caller, session.id)
        conv.events.extend(await service.list_outbound(caller, session.id))
        return conv

    def _id(self) -> str:
        self._n += 1
        return f"evt_c{self._n:04d}"

    async def send(self, event: Any) -> TurnResult:
        result = await self.service.handle_event(self.caller, self.session_id, event)
        self.events.extend(result.events)
        return result

    async def say(
        self, text: str, *, mode: str = "text", contributor: str | None = None
    ) -> TurnResult:
        return await self.send(
            UserMessage(
                event_id=self._id(),
                text=text,
                input_mode=cast(Any, mode),
                contributor_id=contributor,
            )
        )

    async def answer(
        self, value: JsonValue, *, custom: str | None = None, card: Card | None = None
    ) -> TurnResult:
        c = card or self.last_card()
        assert c is not None, "no card to answer"
        return await self.send(
            CardAnswer(event_id=self._id(), card_id=c.id, value=value, custom_text=custom)
        )

    async def tap_recommended(self) -> TurnResult:
        card = self.last_card()
        assert card is not None
        opt = card.recommended_option() or (card.options[0] if card.options else None)
        if card.type == "pick_many":
            return await self.answer(
                [o.id for o in card.options if o.ticked] or ([opt.id] if opt else []), card=card
            )
        if opt is None:
            return await self.answer("ok", card=card)
        return await self.answer(opt.id, card=card)

    async def you_decide(self) -> TurnResult:
        card = self.last_card()
        assert card is not None
        return await self.send(CardYouDecide(event_id=self._id(), card_id=card.id))

    async def skip(self) -> TurnResult:
        card = self.last_card()
        assert card is not None
        return await self.send(CardSkip(event_id=self._id(), card_id=card.id))

    async def back(self) -> TurnResult:
        return await self.send(CardBack(event_id=self._id()))

    async def command(self, name: str) -> TurnResult:
        return await self.send(Command(event_id=self._id(), name=name))  # type: ignore[arg-type]

    async def edit(self, path: str, value: JsonValue) -> TurnResult:
        return await self.send(BriefEdit(event_id=self._id(), item_id=path, value=value))

    async def feedback(self, target: str, reason: str, text: str = "") -> TurnResult:
        return await self.send(
            BriefFeedback(event_id=self._id(), target=target, reason=cast(Any, reason), text=text)
        )

    async def decide(self, asm_id: str, decision: str, value: JsonValue = None) -> TurnResult:
        return await self.send(
            AssumptionDecision(
                event_id=self._id(), id=asm_id, decision=cast(Any, decision), value=value
            )
        )

    async def lookup(
        self, action: str, *, text: str = "", request_id: str | None = None
    ) -> TurnResult:
        return await self.send(
            LookupEdit(
                event_id=self._id(), action=cast(Any, action), text=text, request_id=request_id
            )
        )

    async def approve(self, *, contributor: str | None = None, stale: bool = False) -> TurnResult:
        brief = await self.service.get_brief(self.caller, self.session_id)
        version = int(brief["version"]) - (1 if stale else 0)
        return await self.send(
            GateDecision(
                event_id=self._id(),
                contributor_id=contributor,
                decision="approve",
                version=version,
                brief_hash=str(brief["_hash"]),
                section_hashes=dict(brief["_section_hashes"]),
            )  # fmt: skip
        )

    # ---- reading
    def of_type(self, type_name: str) -> list[StoredEvent]:
        return [e for e in self.events if e.type == type_name]

    def last_card(self) -> Card | None:
        for ev in reversed(self.events):
            if ev.type == "card":
                return Card.model_validate(ev.payload["card"])
        return None

    def texts(self) -> list[str]:
        return [str(e.payload["text"]) for e in self.events if e.type == "assistant_message"]

    async def state(self) -> str:
        return (await self.service.get_session(self.caller, self.session_id)).state

    async def brief(self) -> dict[str, Any]:
        return await self.service.get_brief(self.caller, self.session_id)

    async def view(self) -> dict[str, Any]:
        return await self.service.brief_view(self.caller, self.session_id)

    async def drive_to_review(self, *, max_steps: int = 40) -> int:
        """Tap the recommended option on every card until the Brief is in review; returns cards answered."""
        steps = 0
        while await self.state() != "BRIEF_REVIEW" and steps < max_steps:
            card = self.last_card()
            if card is None:
                break
            await self.tap_recommended()
            steps += 1
        return steps


__all__ = ["INBOUND_ADAPTER", "Conversation"]
