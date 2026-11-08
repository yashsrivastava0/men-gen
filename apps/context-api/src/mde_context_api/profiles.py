"""The tenant's Org Profile document (spec 3.5 and 10: standing answers for Enterprise sessions).

``mde_contracts.ports.UnitOfWork`` has no org-profile repository yet (D-044), but
``mde_persistence.PgUnitOfWork`` does (``org_profiles``). This small adapter uses it when the store
offers one and keeps the document in memory otherwise (the in-memory store used by offline tests).
The session route stores a posted profile before the session starts and otherwise reads the stored
one; the flow hands it to Discover on the first turn of an Enterprise session, and Discover turns it
into ``assumed`` slots with source ``org_profile`` (``docs/packages/discovery.md``, "Org Profile").
"""

from __future__ import annotations

from typing import Protocol, cast

from pydantic import JsonValue

from mde_contracts.ports import Store

JsonObject = dict[str, JsonValue]


class _OrgProfileRepo(Protocol):
    async def get(self) -> JsonObject | None: ...

    async def put(self, doc: JsonObject) -> JsonObject: ...


class OrgProfiles:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._memory: dict[str, JsonObject] = {}

    async def put(self, tenant_id: str, doc: JsonObject) -> None:
        async with self._store.begin(tenant_id) as uow:
            repo = cast("_OrgProfileRepo | None", getattr(uow, "org_profiles", None))
            if repo is not None:
                await repo.put(doc)
                return
        self._memory[tenant_id] = dict(doc)

    async def get(self, tenant_id: str) -> JsonObject | None:
        async with self._store.begin(tenant_id) as uow:
            repo = cast("_OrgProfileRepo | None", getattr(uow, "org_profiles", None))
            if repo is not None:
                return await repo.get()
        doc = self._memory.get(tenant_id)
        return dict(doc) if doc is not None else None
