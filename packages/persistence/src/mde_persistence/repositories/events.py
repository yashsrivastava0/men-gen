"""``EventRepo``: inbound idempotency, per-session outbound sequence, reply lookup, and the
``card_instance`` projection (cards shown by ``card`` events, answered by ``option_selected``)."""

from __future__ import annotations

from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mde_contracts.events import CardEvent, InboundEvent, OptionSelected, OutboundEvent
from mde_core.errors import Conflict, NotFound, ValidationFailed
from mde_persistence.jsonutil import dump, strip_nul, utc
from mde_persistence.models import CardInstanceRow, SessionEventRow
from mde_persistence.repositories.base import SESSION, RepoBase, tbl

EVENT = tbl(SessionEventRow)
CARD = tbl(CardInstanceRow)

_OUTBOUND: TypeAdapter[OutboundEvent] = TypeAdapter(OutboundEvent)


def _answer_status(event: OptionSelected) -> str:
    if event.skipped:
        return "skipped"
    if event.you_decide:
        return "you_decide"
    return "answered"


class PgEventRepo(RepoBase):
    async def record_inbound(self, session_id: str, event: InboundEvent) -> bool:
        """True when stored; False (nothing stored, nothing projected) for a repeated event id."""
        tenant = self.tenant
        await self.require_session(session_id)
        now = self.now()
        stmt = (
            pg_insert(EVENT)
            .values(
                session_id=session_id,
                event_id=event.event_id,
                tenant_id=tenant,
                direction="in",
                type=event.type,
                seq=None,
                in_reply_to=None,
                payload=dump(event),
                at=utc(event.at) or now,
                recorded_at=now,
            )
            .on_conflict_do_nothing(index_elements=[EVENT.c.session_id, EVENT.c.event_id])
            .returning(EVENT.c.event_id)
        )
        if (await self._s.execute(stmt)).first() is None:
            return False
        if isinstance(event, OptionSelected):
            await self._project_answer(session_id, tenant, event, now)
        return True

    async def append_outbound(self, session_id: str, event: OutboundEvent) -> OutboundEvent:
        """Assign the next per-session ``seq`` (strictly increasing, unique) and store the event.

        The counter lives on the session row: the UPDATE takes its row lock, so concurrent
        appenders for one session are serialised until commit and ``seq`` order equals commit
        order (a replaying client never sees seq 5 before seq 4)."""
        tenant = self.tenant
        if event.session_id != session_id:
            raise ValidationFailed(
                f"event {event.event_id} belongs to session {event.session_id}, not {session_id}"
            )
        bump = (
            update(SESSION)
            .where(SESSION.c.id == session_id, SESSION.c.tenant_id == tenant)
            .values(outbound_seq=SESSION.c.outbound_seq + 1)
            .returning(SESSION.c.outbound_seq)
        )
        seq = (await self._s.execute(bump)).scalar_one_or_none()
        if seq is None:
            raise NotFound(f"session {session_id} not found")
        now = self.now()
        stored = event.model_copy(update={"seq": int(seq), "at": event.at or now})
        stmt = (
            pg_insert(EVENT)
            .values(
                session_id=session_id,
                event_id=stored.event_id,
                tenant_id=tenant,
                direction="out",
                type=stored.type,
                seq=stored.seq,
                in_reply_to=stored.in_reply_to,
                payload=dump(stored),
                at=utc(stored.at) or now,
                recorded_at=now,
            )
            .on_conflict_do_nothing(index_elements=[EVENT.c.session_id, EVENT.c.event_id])
            .returning(EVENT.c.event_id)
        )
        if (await self._s.execute(stmt)).first() is None:
            raise Conflict(f"event {stored.event_id} was already recorded for {session_id}")
        if isinstance(stored, CardEvent):
            await self._project_card(session_id, tenant, stored)
        return stored

    async def list_outbound(
        self, session_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[OutboundEvent]:
        stmt = (
            select(EVENT.c.payload)
            .where(
                EVENT.c.session_id == session_id,
                EVENT.c.tenant_id == self.tenant,
                EVENT.c.direction == "out",
                EVENT.c.seq > after_seq,
            )
            .order_by(EVENT.c.seq)
            .limit(limit)
        )
        return [_OUTBOUND.validate_python(p) for p in (await self._s.execute(stmt)).scalars()]

    async def replies_to(self, session_id: str, inbound_event_id: str) -> list[OutboundEvent]:
        stmt = (
            select(EVENT.c.payload)
            .where(
                EVENT.c.session_id == session_id,
                EVENT.c.tenant_id == self.tenant,
                EVENT.c.direction == "out",
                EVENT.c.in_reply_to == inbound_event_id,
            )
            .order_by(EVENT.c.seq)
        )
        return [_OUTBOUND.validate_python(p) for p in (await self._s.execute(stmt)).scalars()]

    # ------------------------------------------------------------------ projections

    async def _project_card(self, session_id: str, tenant: str, event: CardEvent) -> None:
        instance = event.card
        stmt = (
            pg_insert(CARD)
            .values(
                session_id=session_id,
                instance_id=instance.instance_id,
                tenant_id=tenant,
                card_id=instance.card.id,
                slot=instance.card.slot,
                card_type=instance.card.type,
                language=instance.language,
                generated=instance.generated,
                seq=event.seq,
                shown_at=utc(instance.shown_at) or utc(event.at) or self.now(),
                card=dump(instance),
                status="shown",
            )
            .on_conflict_do_nothing(index_elements=[CARD.c.session_id, CARD.c.instance_id])
        )
        await self._s.execute(stmt)

    async def _project_answer(
        self, session_id: str, tenant: str, event: OptionSelected, now: datetime
    ) -> None:
        stmt = (
            update(CARD)
            .where(
                CARD.c.session_id == session_id,
                CARD.c.tenant_id == tenant,
                CARD.c.instance_id == event.card_instance_id,
            )
            .values(
                status=_answer_status(event),
                answer=strip_nul(
                    {
                        "option_ids": list(event.option_ids),
                        "other_text": event.other_text,
                        "you_decide": event.you_decide,
                        "skipped": event.skipped,
                    }
                ),
                answered_event_id=event.event_id,
                answered_at=utc(event.at) or now,
            )
        )
        await self._s.execute(stmt)
