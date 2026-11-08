"""Shared plumbing for the repositories: tenant binding, table handles, existence checks."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import Table, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mde_core.clock import Clock
from mde_core.errors import NotFound, TenantViolation
from mde_persistence.models import Base, SessionRow, TenantRow


def tbl(model: type[Base]) -> Table:
    """The ``Table`` behind a declarative model (Core statements are used for all DML)."""
    return cast(Table, model.__table__)


SESSION = tbl(SessionRow)
TENANT = tbl(TenantRow)


class RepoBase:
    """A repository bound to one tenant (``None`` = system scope: job claim and recovery only)."""

    def __init__(self, session: AsyncSession, tenant_id: str | None, clock: Clock) -> None:
        self._s = session
        self._tenant_id = tenant_id
        self._clock = clock

    @property
    def tenant(self) -> str:
        """The bound tenant; system scope has none and must not touch tenant data."""
        if self._tenant_id is None:
            raise TenantViolation(
                "system scope (tenant_id=None) may only claim and recover jobs",
                details={"scope": "system"},
            )
        return self._tenant_id

    def own(self, tenant_id: str) -> str:
        """Check that a record's tenant is the repository's tenant; return it."""
        bound = self.tenant
        if tenant_id != bound:
            raise TenantViolation(
                "record belongs to another tenant than the repository is bound to",
                details={"record_tenant": tenant_id, "bound_tenant": bound},
            )
        return bound

    def now(self) -> datetime:
        return self._clock.now()

    async def require_session(self, session_id: str) -> None:
        """Raise ``NotFound`` unless the session exists in this tenant (ids of other tenants are
        indistinguishable from ids that do not exist)."""
        stmt = select(literal(1)).where(
            SESSION.c.id == session_id, SESSION.c.tenant_id == self.tenant
        )
        if (await self._s.execute(stmt)).first() is None:
            raise NotFound(f"session {session_id} not found")


async def ensure_tenant(session: AsyncSession, tenant_id: str, now: datetime) -> None:
    """Tenants are created implicitly the first time a tenant stores something (dev headers)."""
    stmt = pg_insert(TENANT).values(id=tenant_id, created_at=now).on_conflict_do_nothing()
    await session.execute(stmt)
