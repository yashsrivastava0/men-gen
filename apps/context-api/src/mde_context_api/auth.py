"""Authentication: a bearer token, mapped to a ``Principal``. Two modes (see ``identity.py``).

**Dev headers** (the default; local use only):

* ``Authorization: Bearer <API_DEV_TOKEN>`` (compared in constant time),
* ``X-Tenant-Id``  (required) the tenant every unit of work of the request is bound to,
* ``X-User-Id``    (required) the caller,
* ``X-Roles``      (optional) comma separated gate roles, for example ``client_product_owner``.

These headers are *trusted identity*: the one shared token lets its holder claim any tenant, any
user and any role, including the second approver of a two-approver gate. Never put this mode on a
network (``python -m mde_context_api`` refuses to bind a non-loopback address in it).

**Token table** (``API_TOKENS`` set; production-shaped): the presented token selects a configured
identity ``{tenant_id, user_id, roles}`` on the server and the ``X-*`` headers are ignored, so a
forged header changes nothing. The dev token is not accepted in this mode.

Anything missing, unknown or malformed is a 401 with the same body, so the response says nothing
about which part was wrong. Real SSO is out of scope (BRIEF section 4); this is the seam it would
replace.

Tenant isolation does not live here: this only decides *who is asking*. Every flow call binds its
units of work to ``principal.tenant_id``, and the store filters by it, so another tenant's session
id is simply not found (404), exactly like an id that never existed.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Header, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mde_context_api.bindings import PolicyBinding
from mde_context_api.errors import Unauthorized
from mde_context_api.identity import (
    MAX_ROLES,
    ROLE_PATTERN,
    TokenTable,
    digest,
    valid_tenant,
    valid_user,
)
from mde_context_api.settings import ApiSettings
from mde_contracts.common import Principal

TENANT_HEADER: Final = "X-Tenant-Id"
USER_HEADER: Final = "X-User-Id"
ROLES_HEADER: Final = "X-Roles"

__all__ = [
    "MAX_ROLES",
    "ROLES_HEADER",
    "TENANT_HEADER",
    "USER_HEADER",
    "Caller",
    "CallerDep",
    "PrincipalDep",
    "authenticate",
    "authenticate_caller",
    "caller_dependency",
    "principal_dependency",
]

_bearer = HTTPBearer(
    auto_error=False,
    description=(
        "Dev mode: the dev token (API_DEV_TOKEN) plus X-Tenant-Id and X-User-Id. "
        "Token mode (API_TOKENS set): one of the configured tokens; the X-* headers are ignored."
    ),
)


@dataclass(frozen=True)
class Caller:
    """The authenticated caller: who they are, and (token mode) the policies their token may start."""

    principal: Principal
    policy: PolicyBinding | None = None


def _token_ok(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _match_token(presented: str, table: TokenTable) -> Caller | None:
    """The configured identity for ``presented``: every digest is compared, none stops the loop."""
    wanted = digest(presented)
    found: Caller | None = None
    for known, entry in table.entries:
        if hmac.compare_digest(wanted, known) and found is None:
            found = Caller(entry.principal(), entry.binding())
    return found


def authenticate_caller(
    token: str | None,
    tenant: str | None,
    user: str | None,
    roles_header: str | None,
    settings: ApiSettings,
) -> Caller:
    """The ``Caller`` for these credentials, or ``Unauthorized`` (always the same body)."""
    table = settings.token_table
    if table is not None:  # token mode: the token is the identity, the headers are not read
        caller = _match_token(token or "", table)
        if caller is None or not token:
            raise Unauthorized
        return caller
    # one comparison either way, so a missing token costs about the same as a wrong one
    if not _token_ok(token or "", settings.dev_token) or not token:
        raise Unauthorized
    tenant = (tenant or "").strip()
    user = (user or "").strip()
    if not valid_tenant(tenant) or not valid_user(user):
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
    return Caller(Principal(tenant_id=tenant, user_id=user, roles=roles))


def authenticate(
    token: str | None,
    tenant: str | None,
    user: str | None,
    roles_header: str | None,
    settings: ApiSettings,
) -> Principal:
    """The ``Principal`` for these credentials, or ``Unauthorized`` (always the same body)."""
    return authenticate_caller(token, tenant, user, roles_header, settings).principal


async def caller_dependency(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
    x_tenant_id: Annotated[
        str | None,
        Header(
            alias=TENANT_HEADER,
            description="Tenant the request is bound to (dev mode only; ignored with API_TOKENS).",
        ),
    ] = None,
    x_user_id: Annotated[
        str | None,
        Header(
            alias=USER_HEADER,
            description="The calling user (dev mode only; ignored with API_TOKENS).",
        ),
    ] = None,
    x_roles: Annotated[
        str | None,
        Header(
            alias=ROLES_HEADER,
            description=(
                "Comma separated gate roles the caller acts as (dev mode only; ignored with "
                "API_TOKENS)."
            ),
        ),
    ] = None,
) -> Caller:
    """FastAPI dependency: the authenticated caller (reads settings lazily; builds no state)."""
    settings: ApiSettings = request.app.state.holder.settings
    scheme = credentials.scheme.lower() if credentials else ""
    token = credentials.credentials if credentials and scheme == "bearer" else None
    return authenticate_caller(token, x_tenant_id, x_user_id, x_roles, settings)


CallerDep = Annotated[Caller, Depends(caller_dependency)]


async def principal_dependency(caller: CallerDep) -> Principal:
    """FastAPI dependency: just the ``Principal`` (the dependency above is resolved once)."""
    return caller.principal


PrincipalDep = Annotated[Principal, Depends(principal_dependency)]
