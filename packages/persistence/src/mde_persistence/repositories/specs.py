"""``SpecRepo`` (spec versions; ``spec_draft`` is a version with status ``draft``) and
``ApprovalRepo`` (gate approvals, append-only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from mde_contracts.common import GateKind
from mde_contracts.gates import ApprovalRecord
from mde_contracts.plan import PlanView
from mde_contracts.records import SpecStatus, SpecVersionRecord
from mde_contracts.spec import AppSpec
from mde_core.errors import Conflict, NotFound, ValidationFailed
from mde_persistence.jsonutil import clean_text, dump, utc, utc_required
from mde_persistence.models import ApprovalRow, SpecVersionRow
from mde_persistence.repositories.base import RepoBase, tbl

SPEC = tbl(SpecVersionRow)
APPROVAL = tbl(ApprovalRow)

SPEC_STATUSES: tuple[str, ...] = ("draft", "approved", "handed_off", "superseded")
# The only legal status moves (also enforced by the spec_version_guard trigger).
SPEC_MOVES: frozenset[tuple[str, str]] = frozenset(
    {
        ("draft", "approved"),
        ("draft", "superseded"),
        ("approved", "handed_off"),
        ("approved", "superseded"),
    }
)


def _spec_record(row: Any) -> SpecVersionRecord:
    return SpecVersionRecord(
        session_id=row.session_id,
        tenant_id=row.tenant_id,
        version=row.version,
        hash=row.hash,
        status=row.status,
        spec_text=row.spec_text,
        spec=AppSpec.model_validate(row.spec),
        plan=None if row.plan is None else PlanView.model_validate(row.plan),
        brief_version=row.brief_version,
        research_version=row.research_version,
        git_sha=row.git_sha,
        created_at=utc_required(row.created_at),
        approved_at=utc(row.approved_at),
    )


class PgSpecRepo(RepoBase):
    async def add(self, rec: SpecVersionRecord) -> SpecVersionRecord:
        tenant = self.own(rec.tenant_id)
        await self.require_session(rec.session_id)
        now = self.now()
        stmt = (
            pg_insert(SPEC)
            .values(
                session_id=rec.session_id,
                version=rec.version,
                tenant_id=tenant,
                hash=rec.hash,
                status=rec.status,
                spec_text=clean_text(rec.spec_text),
                spec=dump(rec.spec),
                plan=None if rec.plan is None else dump(rec.plan),
                brief_version=rec.brief_version,
                research_version=rec.research_version,
                git_sha=rec.git_sha,
                created_at=utc(rec.created_at) or now,
                approved_at=utc(rec.approved_at),
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[SPEC.c.session_id, SPEC.c.version])
            .returning(*SPEC.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"spec {rec.session_id} v{rec.version} already exists")
        return _spec_record(row)

    async def get(self, session_id: str, version: int) -> SpecVersionRecord | None:
        stmt = select(SPEC).where(
            SPEC.c.session_id == session_id,
            SPEC.c.tenant_id == self.tenant,
            SPEC.c.version == version,
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _spec_record(row)

    async def latest(self, session_id: str) -> SpecVersionRecord | None:
        stmt = (
            select(SPEC)
            .where(SPEC.c.session_id == session_id, SPEC.c.tenant_id == self.tenant)
            .order_by(SPEC.c.version.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _spec_record(row)

    async def list_versions(self, session_id: str) -> list[SpecVersionRecord]:
        stmt = (
            select(SPEC)
            .where(SPEC.c.session_id == session_id, SPEC.c.tenant_id == self.tenant)
            .order_by(SPEC.c.version)
        )
        return [_spec_record(r) for r in (await self._s.execute(stmt)).all()]

    async def set_status(
        self,
        session_id: str,
        version: int,
        status: SpecStatus,
        *,
        git_sha: str | None = None,
        approved_at: datetime | None = None,
    ) -> SpecVersionRecord:
        """The only mutation of a stored version.

        Legal moves: draft -> approved | superseded, approved -> handed_off | superseded. Repeating
        the current status is a no-op. ``git_sha`` and ``approved_at`` may be set once (moving to
        ``approved`` stamps ``approved_at`` with the clock when none is given); a different value
        for an already-set field, or any other move, raises ``Conflict``. Content never changes.
        """
        if status not in SPEC_STATUSES:
            raise ValidationFailed(f"unknown spec status {status!r}")
        where = (
            SPEC.c.session_id == session_id,
            SPEC.c.tenant_id == self.tenant,
            SPEC.c.version == version,
        )
        row = (await self._s.execute(select(SPEC).where(*where).with_for_update())).one_or_none()
        if row is None:
            raise NotFound(f"spec {session_id} v{version} not found")
        current: str = row.status
        label = f"spec {session_id} v{version}"
        if status != current and (current, status) not in SPEC_MOVES:
            raise Conflict(f"{label}: illegal status move {current} -> {status}")

        changes: dict[str, object] = {}
        if status != current:
            changes["status"] = status
        if git_sha is not None:
            if row.git_sha is not None and row.git_sha != git_sha:
                raise Conflict(f"{label}: git_sha is already set")
            if row.git_sha is None:
                changes["git_sha"] = git_sha
        stamp = utc(approved_at)
        if stamp is None and status == "approved" and current != "approved":
            stamp = self.now()
        if stamp is not None:
            stored = utc(row.approved_at)
            if stored is not None and stored != stamp:
                raise Conflict(f"{label}: approved_at is already set")
            if stored is None:
                changes["approved_at"] = stamp
        if not changes:
            return _spec_record(row)
        try:
            updated = (
                await self._s.execute(
                    update(SPEC)
                    .where(*where)
                    .values(updated_at=self.now(), **changes)
                    .returning(*SPEC.c)
                )
            ).one()
        except DBAPIError as exc:  # the guard trigger is the backstop of the checks above
            raise Conflict(f"{label}: {exc.orig}") from exc
        return _spec_record(updated)


def _approval_record(row: Any) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        session_id=row.session_id,
        gate=row.gate,
        version=row.version,
        hash=row.hash,
        approver_id=row.approver_id,
        role=row.role,
        decision=row.decision,
        via=row.via,
        comment=row.comment,
        at=utc_required(row.at),
    )


class PgApprovalRepo(RepoBase):
    async def add(self, rec: ApprovalRecord) -> ApprovalRecord:
        tenant = self.tenant
        await self.require_session(rec.session_id)
        stmt = (
            pg_insert(APPROVAL)
            .values(
                id=rec.id,
                session_id=rec.session_id,
                tenant_id=tenant,
                gate=rec.gate,
                version=rec.version,
                hash=rec.hash,
                approver_id=rec.approver_id,
                role=rec.role,
                decision=rec.decision,
                via=rec.via,
                comment=clean_text(rec.comment),
                at=utc(rec.at) or self.now(),
            )
            .on_conflict_do_nothing(index_elements=[APPROVAL.c.id])
            .returning(*APPROVAL.c)
        )
        row = (await self._s.execute(stmt)).one_or_none()
        if row is None:
            raise Conflict(f"approval {rec.id} already exists")
        return _approval_record(row)

    async def list(
        self,
        session_id: str,
        gate: GateKind,
        *,
        version: int | None = None,
        hash: str | None = None,
    ) -> list[ApprovalRecord]:
        stmt = select(APPROVAL).where(
            APPROVAL.c.session_id == session_id,
            APPROVAL.c.tenant_id == self.tenant,
            APPROVAL.c.gate == gate,
        )
        if version is not None:
            stmt = stmt.where(APPROVAL.c.version == version)
        if hash is not None:
            stmt = stmt.where(APPROVAL.c.hash == hash)
        stmt = stmt.order_by(APPROVAL.c.at, APPROVAL.c.id)
        return [_approval_record(r) for r in (await self._s.execute(stmt)).all()]
