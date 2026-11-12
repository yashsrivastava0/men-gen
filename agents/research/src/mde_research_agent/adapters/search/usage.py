"""Tavily's own credit count: ``GET https://api.tavily.com/usage`` (bearer auth, optional ``X-Project-ID``).

Read on 2026-11-13 from docs.tavily.com (see ``docs/research-agent/provider-facts.md``): the answer is
``{"key": {"usage", "limit" | null, "search_usage", ...}, "account": {"current_plan", "plan_usage", "plan_limit",
"paygo_usage", "paygo_limit", ...}}``. The numbers are credits used in the CURRENT billing cycle; there is no
``remaining`` field (remaining = limit - usage). The endpoint is rate limited (429 with ``Retry-After``).

``fetch_tavily_usage`` is one plain ``httpx`` call with a 10 second timeout. It NEVER raises: it returns a
``UsageResult`` with either a ``reading`` or an ``error`` word (``auth``, ``rate_limited``, ``quota``, ``timeout``,
``network``, ``bad_response``, ``http_error``) and, for a 429, the ``retry_after`` seconds. The key is redacted from every
message and never logged. The account figures (the plan's allowance, what the dashboard shows) are preferred over the
key's own; the key's figures are the fallback and say so in ``scope``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from mde_research_agent.adapters.search.common import (
    Secret,
    check_key,
    error_from_response,
    transport_error,
)
from mde_research_agent.adapters.search.tavily import TAVILY_BASE_URL
from mde_research_agent.ports.search import SearchError

USAGE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class UsageResult:
    """One answer: ``reading`` (``{used, limit, plan, read_at, source, scope}``) or an ``error`` word."""

    reading: dict[str, Any] | None = None
    error: str | None = None
    retry_after: float | None = None
    status: int | None = None


def _count(value: object) -> int | None:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_usage(body: object, *, now: datetime) -> dict[str, Any] | None:
    """The reading in a ``/usage`` body, or None when it holds no usable count."""
    if not isinstance(body, dict):
        return None
    raw_key, raw_account = body.get("key"), body.get("account")
    key: dict[str, Any] = raw_key if isinstance(raw_key, dict) else {}
    account: dict[str, Any] = raw_account if isinstance(raw_account, dict) else {}
    used, limit, scope = (
        _count(account.get("plan_usage")),
        _count(account.get("plan_limit")),
        "account",
    )
    if used is None:
        used, limit, scope = _count(key.get("usage")), _count(key.get("limit")), "key"
    if used is None:
        return None
    plan = account.get("current_plan")
    return {
        "used": used,
        "limit": limit,
        "plan": plan.strip()[:60] if isinstance(plan, str) and plan.strip() else None,
        "read_at": now.isoformat(),
        "source": "provider",
        "scope": scope,
    }


async def fetch_tavily_usage(
    api_key: str,
    client: httpx.AsyncClient,
    *,
    base_url: str = TAVILY_BASE_URL,
    timeout_s: float = USAGE_TIMEOUT_S,
    project_id: str | None = None,
    now: datetime | None = None,
) -> UsageResult:
    """Ask Tavily how many credits the key's account has used this cycle. Never raises."""
    secret = Secret(api_key)
    try:
        key = check_key(secret)
    except SearchError:
        return UsageResult(error="auth")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {key}"}
    if project_id:
        headers["X-Project-ID"] = project_id
    try:
        response = await client.get(
            f"{base_url.rstrip('/')}/usage",
            headers=headers,
            timeout=timeout_s,
            follow_redirects=False,  # a redirect must never carry the key to another host
        )
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError) as exc:
        return UsageResult(error=transport_error("Tavily", exc).kind)
    except Exception:  # the promise is: never raises
        return UsageResult(error="network")
    if not 200 <= response.status_code < 300:
        err = error_from_response("Tavily", response, secret)
        return UsageResult(error=err.kind, retry_after=err.retry_after, status=response.status_code)
    try:
        body = response.json()
    except ValueError:
        return UsageResult(error="bad_response", status=response.status_code)
    reading = parse_usage(body, now=now or datetime.now(UTC))
    if reading is None:
        return UsageResult(error="bad_response", status=response.status_code)
    return UsageResult(reading=reading, status=response.status_code)
