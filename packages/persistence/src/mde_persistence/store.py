"""``PostgresStore``: the ``Store`` / ``UnitOfWork`` implementation (brief 8.8).

``Store.begin(tenant_id)`` opens one transaction on one pooled connection and hands out repositories
bound to that tenant. It commits on clean exit and rolls back on any exception. ``begin(None)`` is
system scope: only job claim and recovery work there, everything else raises ``TenantViolation``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mde_contracts.ports import (
    ApprovalRepo,
    BriefRepo,
    EventRepo,
    JobRepo,
    ResearchRepo,
    SessionRepo,
    SpecRepo,
)
from mde_core.clock import Clock, SystemClock
from mde_persistence.repositories.briefs import PgBriefRepo, PgResearchRepo
from mde_persistence.repositories.events import PgEventRepo
from mde_persistence.repositories.jobs import PgJobRepo
from mde_persistence.repositories.sessions import PgSessionRepo
from mde_persistence.repositories.specs import PgApprovalRepo, PgSpecRepo
from mde_persistence.repositories.standing import PgMemoryRepo, PgOrgProfileRepo, PgPolicyRepo


class PgUnitOfWork:
    """Repositories sharing one transaction, all bound to ``tenant_id`` (``None`` = system)."""

    sessions: SessionRepo
    events: EventRepo
    briefs: BriefRepo
    research: ResearchRepo
    specs: SpecRepo
    approvals: ApprovalRepo
    jobs: JobRepo
    # Beyond the ports: standing per-tenant data.
    memory: PgMemoryRepo
    org_profiles: PgOrgProfileRepo
    policies: PgPolicyRepo

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: str | None,
        clock: Clock,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._session = session
        self.tenant_id = tenant_id
        self.sessions = PgSessionRepo(session, tenant_id, clock)
        self.events = PgEventRepo(session, tenant_id, clock)
        self.briefs = PgBriefRepo(session, tenant_id, clock)
        self.research = PgResearchRepo(session, tenant_id, clock)
        self.specs = PgSpecRepo(session, tenant_id, clock)
        self.approvals = PgApprovalRepo(session, tenant_id, clock)
        self.jobs = PgJobRepo(session, tenant_id, clock, default_max_attempts=max_attempts)
        self.memory = PgMemoryRepo(session, tenant_id, clock)
        self.org_profiles = PgOrgProfileRepo(session, tenant_id, clock)
        self.policies = PgPolicyRepo(session, tenant_id, clock)

    async def commit(self) -> None:
        """Commit now; the unit of work stays usable (a new transaction starts on next use)."""
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class PostgresStore:
    """``Store`` over an ``AsyncEngine`` (see ``mde_persistence.db.create_engine``).

    ``clock`` is the time source for every timestamp and for stuck-job recovery (inject a
    ``FakeClock`` in tests); ``max_attempts`` is the attempt limit given to new jobs.
    """

    def __init__(
        self, engine: AsyncEngine, *, clock: Clock | None = None, max_attempts: int = 3
    ) -> None:
        self._engine = engine
        self._clock = clock or SystemClock()
        self._max_attempts = max_attempts

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def clock(self) -> Clock:
        return self._clock

    @asynccontextmanager
    async def begin(self, tenant_id: str | None) -> AsyncIterator[PgUnitOfWork]:
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            uow = PgUnitOfWork(session, tenant_id, self._clock, max_attempts=self._max_attempts)
            try:
                yield uow
            except Exception:
                await session.rollback()
                raise
            await session.commit()
