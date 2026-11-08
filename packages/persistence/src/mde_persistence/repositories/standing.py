"""Small per-tenant stores: opt-in memory items, the Org Profile document, policy documents.

None of these has a port in ``mde_contracts`` yet; they hang off ``PgUnitOfWork`` as ``memory``,
``org_profiles`` and ``policies``. All of them are tenant-bound like every other repository.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import JsonValue
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mde_contracts.common import Frozen
from mde_contracts.policy import Policy
from mde_core.errors import ValidationFailed
from mde_persistence.jsonutil import dump, strip_nul, utc_required
from mde_persistence.models import MemoryItemRow, OrgProfileRow, PolicyRow
from mde_persistence.repositories.base import RepoBase, ensure_tenant, tbl

MEMORY = tbl(MemoryItemRow)
ORG = tbl(OrgProfileRow)
POLICY = tbl(PolicyRow)

JsonObject = dict[str, JsonValue]
MemoryScope = Literal["user", "org"]


class MemoryItem(Frozen):
    scope: MemoryScope
    owner_id: str  # user id for ``user`` scope; empty for ``org``
    key: str
    value: JsonObject
    created_at: datetime
    updated_at: datetime


def _memory_item(row: Any) -> MemoryItem:
    return MemoryItem(
        scope=row.scope,
        owner_id=row.owner_id,
        key=row.key,
        value=row.value,
        created_at=utc_required(row.created_at),
        updated_at=utc_required(row.updated_at),
    )


class PgMemoryRepo(RepoBase):
    """Opt-in memory: ``user`` scope belongs to one user of the tenant, ``org`` to the tenant."""

    @staticmethod
    def _owner(scope: MemoryScope, owner_id: str) -> str:
        if scope == "org" and owner_id:
            raise ValidationFailed("org-scope memory has no owner_id")
        if scope == "user" and not owner_id:
            raise ValidationFailed("user-scope memory needs an owner_id")
        return owner_id

    async def put(
        self, scope: MemoryScope, key: str, value: JsonObject, *, owner_id: str = ""
    ) -> MemoryItem:
        tenant = self.tenant
        owner = self._owner(scope, owner_id)
        now = self.now()
        await ensure_tenant(self._s, tenant, now)
        ins = pg_insert(MEMORY).values(
            tenant_id=tenant,
            scope=scope,
            owner_id=owner,
            key=key,
            value=strip_nul(value),
            created_at=now,
            updated_at=now,
        )
        stmt = ins.on_conflict_do_update(
            index_elements=[MEMORY.c.tenant_id, MEMORY.c.scope, MEMORY.c.owner_id, MEMORY.c.key],
            set_={"value": ins.excluded.value, "updated_at": ins.excluded.updated_at},
        ).returning(*MEMORY.c)
        return _memory_item((await self._s.execute(stmt)).one())

    async def get(self, scope: MemoryScope, key: str, *, owner_id: str = "") -> MemoryItem | None:
        stmt = select(MEMORY).where(
            MEMORY.c.tenant_id == self.tenant,
            MEMORY.c.scope == scope,
            MEMORY.c.owner_id == self._owner(scope, owner_id),
            MEMORY.c.key == key,
        )
        row = (await self._s.execute(stmt)).one_or_none()
        return None if row is None else _memory_item(row)

    async def list(self, scope: MemoryScope, *, owner_id: str = "") -> list[MemoryItem]:
        stmt = (
            select(MEMORY)
            .where(
                MEMORY.c.tenant_id == self.tenant,
                MEMORY.c.scope == scope,
                MEMORY.c.owner_id == self._owner(scope, owner_id),
            )
            .order_by(MEMORY.c.key)
        )
        return [_memory_item(r) for r in (await self._s.execute(stmt)).all()]

    async def delete(self, scope: MemoryScope, key: str, *, owner_id: str = "") -> bool:
        stmt = (
            delete(MEMORY)
            .where(
                MEMORY.c.tenant_id == self.tenant,
                MEMORY.c.scope == scope,
                MEMORY.c.owner_id == self._owner(scope, owner_id),
                MEMORY.c.key == key,
            )
            .returning(MEMORY.c.key)
        )
        return (await self._s.execute(stmt)).first() is not None


class PgOrgProfileRepo(RepoBase):
    """The tenant's Org Profile: one JSON document (brand, identity, compliance, approvers...)."""

    async def get(self) -> JsonObject | None:
        stmt = select(ORG.c.doc).where(ORG.c.tenant_id == self.tenant)
        doc = (await self._s.execute(stmt)).scalar_one_or_none()
        return None if doc is None else dict(doc)

    async def put(self, doc: JsonObject) -> JsonObject:
        tenant = self.tenant
        now = self.now()
        await ensure_tenant(self._s, tenant, now)
        ins = pg_insert(ORG).values(
            tenant_id=tenant, doc=strip_nul(doc), created_at=now, updated_at=now
        )
        stmt = ins.on_conflict_do_update(
            index_elements=[ORG.c.tenant_id],
            set_={"doc": ins.excluded.doc, "updated_at": ins.excluded.updated_at},
        ).returning(ORG.c.doc)
        return dict((await self._s.execute(stmt)).scalar_one())


class PgPolicyRepo(RepoBase):
    """Per-tenant policy documents (``Policy`` as JSONB), keyed by policy id."""

    async def get(self, policy_id: str) -> Policy | None:
        stmt = select(POLICY.c.doc).where(
            POLICY.c.tenant_id == self.tenant, POLICY.c.id == policy_id
        )
        doc = (await self._s.execute(stmt)).scalar_one_or_none()
        return None if doc is None else Policy.model_validate(doc)

    async def put(self, policy: Policy) -> Policy:
        tenant = self.tenant
        now = self.now()
        await ensure_tenant(self._s, tenant, now)
        ins = pg_insert(POLICY).values(
            tenant_id=tenant, id=policy.id, doc=dump(policy), created_at=now, updated_at=now
        )
        stmt = ins.on_conflict_do_update(
            index_elements=[POLICY.c.tenant_id, POLICY.c.id],
            set_={"doc": ins.excluded.doc, "updated_at": ins.excluded.updated_at},
        ).returning(POLICY.c.doc)
        return Policy.model_validate((await self._s.execute(stmt)).scalar_one())

    async def list(self) -> list[Policy]:
        stmt = select(POLICY.c.doc).where(POLICY.c.tenant_id == self.tenant).order_by(POLICY.c.id)
        return [Policy.model_validate(d) for d in (await self._s.execute(stmt)).scalars()]
