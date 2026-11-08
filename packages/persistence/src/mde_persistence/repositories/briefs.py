"""``BriefRepo`` (Brief versions) and ``ResearchRepo`` (Research Notes and their projections)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mde_contracts.brief import Brief
from mde_contracts.records import BriefVersionRecord, ResearchNoteRecord
from mde_contracts.research import ResearchNote
from mde_contracts.slots import SlotRecord
from mde_core.errors import Conflict, NotFound, ValidationFailed
from mde_persistence.jsonutil import clean_text, dump, strip_nul, utc, utc_required
from mde_persistence.models import (
    BriefVersionRow,
    ResearchFactRow,
    ResearchJobRow,
    ResearchNoteRow,
)
from mde_persistence.repositories.base import RepoBase, tbl

BRIEF = tbl(BriefVersionRow)
NOTE = tbl(ResearchNoteRow)
RJOB = tbl(ResearchJobRow)
RFACT = tbl(ResearchFactRow)

# draft -> approved | superseded, approved -> superseded; same status is a no-op.
_BRIEF_MOVES = {("draft", "approved"), ("draft", "superseded"), ("approved", "superseded")}
_BRIEF_STATUSES = ("draft", "approved", "superseded")


def _brief_record(row: Any) -> BriefVersionRecord:
    return BriefVersionRecord(
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        brief=Brief.model_validate(row.brief),
        slots={k: SlotRecord.model_validate(v) for k, v in row.slots.items()},
        created_at=utc_required(row.created_at),
    )


class PgBriefRepo(RepoBase):
    async def add(self, rec: BriefVersionRecord) -> BriefVersionRecord:
        tenant = self.own(rec.tenant_id)
        await self.require_session(rec.session_id)
        brief = rec.brief
        stmt = (
            pg_insert(BRIEF)
            .values(
                session_id=rec.session_id,
                version=brief.version,
                tenant_id=tenant,
                status=brief.status,
                hash=brief.hash,
                brief=dump(brief),
                slots={k: dump(v) for k, v in rec.slots.items()},
                created_at=utc(rec.created_at) or self.now(),
            )
            .on_conflict_do_nothing(index_elements=[BRIEF.c.session_id, BRIEF.c.version])
            .returning(*BRIEF.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"brief {rec.session_id} v{brief.version} already exists")
        return _brief_record(row)

    async def get(self, session_id: str, version: int) -> BriefVersionRecord | None:
        stmt = select(BRIEF).where(
            BRIEF.c.session_id == session_id,
            BRIEF.c.tenant_id == self.tenant,
            BRIEF.c.version == version,
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _brief_record(row)

    async def latest(self, session_id: str) -> BriefVersionRecord | None:
        stmt = (
            select(BRIEF)
            .where(BRIEF.c.session_id == session_id, BRIEF.c.tenant_id == self.tenant)
            .order_by(BRIEF.c.version.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _brief_record(row)

    async def set_status(self, session_id: str, version: int, status: str) -> None:
        """The only mutation of a stored Brief: draft -> approved | superseded, approved ->
        superseded. Repeating the current status is a no-op."""
        if status not in _BRIEF_STATUSES:
            raise ValidationFailed(f"unknown brief status {status!r}")
        stmt = (
            select(BRIEF.c.status, BRIEF.c.brief)
            .where(
                BRIEF.c.session_id == session_id,
                BRIEF.c.tenant_id == self.tenant,
                BRIEF.c.version == version,
            )
            .with_for_update()
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound(f"brief {session_id} v{version} not found")
        if row.status == status:
            return
        if (row.status, status) not in _BRIEF_MOVES:
            raise Conflict(
                f"brief {session_id} v{version}: illegal status move {row.status} -> {status}"
            )
        brief_doc: dict[str, object] = {**row.brief, "status": status}
        await self._s.execute(
            update(BRIEF)
            .where(
                BRIEF.c.session_id == session_id,
                BRIEF.c.tenant_id == self.tenant,
                BRIEF.c.version == version,
            )
            .values(status=status, brief=brief_doc)
        )


def _note_record(row: Any) -> ResearchNoteRecord:
    return ResearchNoteRecord(
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        note=ResearchNote.model_validate(row.note),
        created_at=utc_required(row.created_at),
    )


class PgResearchRepo(RepoBase):
    async def add(self, rec: ResearchNoteRecord) -> ResearchNoteRecord:
        tenant = self.own(rec.tenant_id)
        await self.require_session(rec.session_id)
        note = rec.note
        created = utc(rec.created_at) or self.now()
        stmt = (
            pg_insert(NOTE)
            .values(
                session_id=rec.session_id,
                version=note.version,
                tenant_id=tenant,
                hash=note.hash,
                brief_hash=note.brief_hash,
                status=note.status,
                depth=note.depth,
                note=dump(note),
                created_at=created,
            )
            .on_conflict_do_nothing(index_elements=[NOTE.c.session_id, NOTE.c.version])
            .returning(*NOTE.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"research note {rec.session_id} v{note.version} already exists")
        await self._project(rec.session_id, tenant, note, created)
        return _note_record(row)

    async def latest(self, session_id: str) -> ResearchNoteRecord | None:
        stmt = (
            select(NOTE)
            .where(NOTE.c.session_id == session_id, NOTE.c.tenant_id == self.tenant)
            .order_by(NOTE.c.version.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _note_record(row)

    async def _project(
        self, session_id: str, tenant: str, note: ResearchNote, created: datetime
    ) -> None:
        """``research_job`` (one row per job that produced facts) and ``research_fact`` rows."""
        if not note.facts:
            return
        per_job = Counter(f.job for f in note.facts)
        await self._s.execute(
            pg_insert(RJOB).values(
                [
                    {
                        "session_id": session_id,
                        "note_version": note.version,
                        "job": job,
                        "tenant_id": tenant,
                        "status": note.status,
                        "fact_count": count,
                        "created_at": created,
                    }
                    for job, count in sorted(per_job.items())
                ]
            )
        )
        facts: list[dict[str, object]] = []
        for fact in note.facts:
            first = fact.sources[0] if fact.sources else None
            facts.append(
                {
                    "session_id": session_id,
                    "note_version": note.version,
                    "fact_id": fact.id,
                    "tenant_id": tenant,
                    "job": fact.job,
                    "kind": fact.kind,
                    "statement": clean_text(fact.statement),
                    "impact": fact.impact,
                    "confidence": fact.confidence,
                    "verified": fact.verified,
                    "primary_url": clean_text(first.url) if first else None,
                    "retrieved_on": first.retrieved_at if first else None,
                    "sources": strip_nul([s.model_dump(mode="json") for s in fact.sources]),
                }
            )
        await self._s.execute(
            pg_insert(RFACT)
            .values(facts)
            .on_conflict_do_nothing(
                index_elements=[RFACT.c.session_id, RFACT.c.note_version, RFACT.c.fact_id]
            )
        )
