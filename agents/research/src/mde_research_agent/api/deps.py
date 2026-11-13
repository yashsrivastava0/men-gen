"""Shared request dependencies: the caller (dev token) and the service."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request
from pydantic import BaseModel

from mde_research_agent.api.errors import Unauthorized
from mde_research_agent.config import ResearchSettings
from mde_research_agent.service import ResearchService


class Caller(BaseModel):
    tenant_id: str
    user_id: str


def get_service(request: Request) -> ResearchService:
    return request.app.state.service  # type: ignore[no-any-return]


async def get_caller(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> Caller:
    settings: ResearchSettings = request.app.state.settings
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(
        token.encode(), settings.research_dev_token.get_secret_value().encode()
    ):
        raise Unauthorized
    return Caller(tenant_id=x_tenant_id or "demo", user_id=x_user_id or "demo-user")


Auth = Annotated[Caller, Depends(get_caller)]
Svc = Annotated[ResearchService, Depends(get_service)]
