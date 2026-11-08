"""Shared base classes and tiny enums used across the contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

CONTRACTS_VERSION = "1.0.1"
SPEC_SCHEMA_ID = "mde.app-spec/1"


class Frozen(BaseModel):
    """Immutable, strict model. Change by ``model_copy(update=...)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Model(BaseModel):
    """Mutable, strict model (working drafts only)."""

    model_config = ConfigDict(extra="forbid")


class Principal(Frozen):
    """The authenticated caller (dev headers X-Tenant-Id / X-User-Id / X-Roles map to this)."""

    tenant_id: str
    user_id: str
    roles: list[str] = []  # e.g. "requester", "client_product_owner", "delivery_lead"


Mode = Literal["guided", "enterprise"]
GateKind = Literal["brief", "plan"]
Depth = Literal["light", "standard", "deep"]
Lang = Annotated[str, StringConstraints(min_length=2, max_length=12)]

# Stable id shapes (the spec file and every cross-reference use these).
ScreenId = Annotated[str, StringConstraints(pattern=r"^SCR-\d{3}$")]
FlowId = Annotated[str, StringConstraints(pattern=r"^FLW-\d{3}$")]
EntityId = Annotated[str, StringConstraints(pattern=r"^ENT-[A-Za-z][A-Za-z0-9]*$")]
ApiId = Annotated[str, StringConstraints(pattern=r"^API-[A-Za-z][A-Za-z0-9]*$")]
RoleId = Annotated[str, StringConstraints(pattern=r"^ROLE-[a-z][a-z0-9_]*$")]
IntegrationId = Annotated[str, StringConstraints(pattern=r"^INT-[a-z][a-z0-9_]*$")]
BuildStepId = Annotated[str, StringConstraints(pattern=r"^S(?:[0-9]|10)$")]
ResearchRefId = Annotated[str, StringConstraints(pattern=r"^RA-\d{3}$")]
