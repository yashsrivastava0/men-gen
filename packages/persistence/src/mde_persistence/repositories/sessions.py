"""``SessionRepo``: sessions with compare-and-set saves, plus the ``slot`` projection."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mde_contracts.engines import DiscoveryState
from mde_contracts.records import SessionRecord
from mde_core.errors import Conflict
from mde_persistence.jsonutil import clean_text, dump, strip_nul, utc, utc_required
from mde_persistence.models import SlotRow
from mde_persistence.repositories.base import SESSION, RepoBase, ensure_tenant, tbl

SLOT = tbl(SlotRow)


def _mutable_values(rec: SessionRecord) -> dict[str, object]:
    """Columns written by create and save (never id, tenant, version, timestamps, counters)."""
    return {
        "owner_id": rec.owner_id,
        "app_id": rec.app_id,
        "mode": rec.mode,
        "policy_id": rec.policy_id,
        "state": rec.state,
        "plan_phase": rec.plan_phase,
        "origin": rec.origin,
        "paused_from": rec.paused_from,
        "language": rec.language,
        "discovery": dump(rec.discovery),
        "brief_version": rec.brief_version,
        "approved_brief_version": rec.approved_brief_version,
        "research_version": rec.research_version,
        "spec_version": rec.spec_version,
        "handed_off_version": rec.handed_off_version,
        "queued_messages": strip_nul(list(rec.queued_messages)),
        "title": clean_text(rec.title),
    }


def to_record(row: Any) -> SessionRecord:
    return SessionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        owner_id=row.owner_id,
        app_id=row.app_id,
        mode=row.mode,
        policy_id=row.policy_id,
        state=row.state,
        plan_phase=row.plan_phase,
        origin=row.origin,
        paused_from=row.paused_from,
        language=row.language,
        discovery=DiscoveryState.model_validate(row.discovery),
        brief_version=row.brief_version,
        approved_brief_version=row.approved_brief_version,
        research_version=row.research_version,
        spec_version=row.spec_version,
        handed_off_version=row.handed_off_version,
        queued_messages=list(row.queued_messages),
        title=row.title,
        version=row.version,
        created_at=utc_required(row.created_at),
        updated_at=utc_required(row.updated_at),
    )


class PgSessionRepo(RepoBase):
    async def create(self, rec: SessionRecord) -> SessionRecord:
        tenant = self.own(rec.tenant_id)
        now = self.now()
        await ensure_tenant(self._s, tenant, now)
        stmt = (
            pg_insert(SESSION)
            .values(
                id=rec.id,
                tenant_id=tenant,
                version=rec.version,
                created_at=utc(rec.created_at) or now,
                updated_at=now,
                **_mutable_values(rec),
            )
            .on_conflict_do_nothing(index_elements=[SESSION.c.id])
            .returning(*SESSION.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"session {rec.id} already exists")
        await self._sync_slots(rec.id, tenant, rec.discovery)
        return to_record(row)

    async def get(self, session_id: str, *, for_update: bool = False) -> SessionRecord | None:
        stmt = select(SESSION).where(SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else to_record(row)

    async def save(self, rec: SessionRecord, *, expected_version: int) -> SessionRecord:
        tenant = self.own(rec.tenant_id)
        stmt = (
            update(SESSION)
            .where(
                SESSION.c.id == rec.id,
                SESSION.c.tenant_id == tenant,
                SESSION.c.version == expected_version,
            )
            .values(version=SESSION.c.version + 1, updated_at=self.now(), **_mutable_values(rec))
            .returning(*SESSION.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            await self.require_session(rec.id)  # NotFound for unknown or foreign ids
            raise Conflict(
                f"session {rec.id}: stale write (expected version {expected_version})",
                details={"session_id": rec.id, "expected_version": expected_version},
            )
        await self._sync_slots(rec.id, tenant, rec.discovery)
        return to_record(row)

    async def list_for_owner(self, owner_id: str, *, limit: int = 50) -> list[SessionRecord]:
        stmt = (
            select(SESSION)
            .where(SESSION.c.tenant_id == self.tenant, SESSION.c.owner_id == owner_id)
            .order_by(SESSION.c.updated_at.desc(), SESSION.c.id.desc())
            .limit(limit)
        )
        return [to_record(r) for r in (await self._s.execute(stmt)).all()]

    async def _sync_slots(self, session_id: str, tenant: str, discovery: DiscoveryState) -> None:
        """Rewrite the ``slot`` projection from ``discovery.slots`` (JSONB stays the truth)."""
        rows: list[dict[str, object]] = []
        for slot in discovery.slots.values():
            data = dump(slot)
            rows.append(
                {
                    "session_id": session_id,
                    "slot_id": slot.slot_id,
                    "tenant_id": tenant,
                    "value": data["value"],
                    "state": slot.state,
                    "source": slot.source,
                    "evidence": clean_text(slot.evidence),
                    "updated_at": utc(slot.updated_at),
                }
            )
        if rows:
            ins = pg_insert(SLOT).values(rows)
            ins = ins.on_conflict_do_update(
                index_elements=[SLOT.c.session_id, SLOT.c.slot_id],
                set_={
                    name: ins.excluded[name]
                    for name in ("value", "state", "source", "evidence", "updated_at")
                },
            )
            await self._s.execute(ins)
            keep = [str(r["slot_id"]) for r in rows]
            await self._s.execute(
                delete(SLOT).where(SLOT.c.session_id == session_id, SLOT.c.slot_id.not_in(keep))
            )
        else:
            await self._s.execute(delete(SLOT).where(SLOT.c.session_id == session_id))
