"""Dev authentication (adapted from ``mde_context_api.auth``; never imported from it).

Dev headers (local use only): ``Authorization: Bearer <COMPASS_DEV_TOKEN>`` (constant-time compare),
``X-Tenant-Id`` (required), ``X-User-Id`` (required), ``X-Roles`` (optional comma list).
Token mode (``COMPASS_API_TOKENS``): the token selects a server-side identity and the X-* headers are
ignored. Anything missing or malformed is one identical 401.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Annotated, Final

from fastapi import Depends, Header, Request

from mde_compass.api.errors import Unauthorized
from mde_compass.api.settings import ApiSettings
from mde_compass.engine.ports import Caller

TENANT_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
USER_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$")
ROLE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_ROLES: Final = 16


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def authenticate(
    authorization: str | None,
    tenant: str | None,
    user: str | None,
    roles_header: str | None,
    settings: ApiSettings,
) -> Caller:
    """The ``Caller`` for these credentials, or ``Unauthorized`` (always the same body)."""
    token = bearer_token(authorization)
    if settings.token_table is not None:
        wanted = _digest(token or "")
        found = None
        for known, ident in settings.token_table.items():
            if hmac.compare_digest(wanted, _digest(known)) and found is None:
                found = ident
        if found is None or not token:
            raise Unauthorized
        return Caller(found.tenant_id, found.user_id, found.roles)
    if not _eq(token or "", settings.dev_token) or not token:
        raise Unauthorized
    tenant = (tenant or "").strip()
    user = (user or "").strip()
    if not TENANT_PATTERN.match(tenant) or not USER_PATTERN.match(user):
        raise Unauthorized
    roles: list[str] = []
    for part in (roles_header or "").split(","):
        role = part.strip()
        if not role:
            continue
        if not ROLE_PATTERN.match(role) or len(roles) >= MAX_ROLES:
            raise Unauthorized
        if role not in roles:
            roles.append(role)
    return Caller(tenant, user, tuple(roles))


async def caller_dependency(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_roles: Annotated[str | None, Header(alias="X-Roles")] = None,
) -> Caller:
    settings: ApiSettings = request.app.state.api_settings
    return authenticate(authorization, x_tenant_id, x_user_id, x_roles, settings)


CallerDep = Annotated[Caller, Depends(caller_dependency)]
