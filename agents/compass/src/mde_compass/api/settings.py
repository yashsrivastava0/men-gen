"""API settings: dev auth, body and stream limits. Read once from the environment (a dict in tests)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from mde_compass.config import DEFAULT_CONFIG

DEFAULT_DEV_TOKEN = "dev-token"  # noqa: S105  (a documented local-development default)
MAX_BODY_BYTES = 256 * 1024
MAX_STREAMS_PER_CALLER = 8


@dataclass(frozen=True)
class TokenIdentity:
    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApiSettings:
    dev_token: str = DEFAULT_DEV_TOKEN
    token_table: dict[str, TokenIdentity] | None = None  # token mode: token -> identity
    max_body_bytes: int = MAX_BODY_BYTES
    max_streams_per_caller: int = MAX_STREAMS_PER_CALLER
    sse_poll_seconds: float = DEFAULT_CONFIG.sse_poll_seconds
    sse_max_poll_seconds: float = 2.0
    sse_keepalive_seconds: float = 15.0
    sse_max_seconds: float = DEFAULT_CONFIG.sse_max_seconds
    host: str = "127.0.0.1"
    port: int = 8110
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def token_mode(self) -> bool:
        return self.token_table is not None


def parse_token_table(raw: str) -> dict[str, TokenIdentity]:
    """``COMPASS_API_TOKENS="tokA=tenant:user[:role+role];tokB=..."`` -> table. Raises ValueError."""
    table: dict[str, TokenIdentity] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        token, sep, ident = entry.partition("=")
        parts = ident.split(":")
        if not sep or not token.strip() or len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("COMPASS_API_TOKENS entries look like token=tenant:user[:role+role]")
        roles = tuple(r for r in (parts[2].split("+") if len(parts) > 2 else []) if r)
        table[token.strip()] = TokenIdentity(parts[0], parts[1], roles)
    if not table:
        raise ValueError("COMPASS_API_TOKENS is set but empty")
    return table


def load_api_settings(env: Mapping[str, str] | None = None) -> ApiSettings:
    """Settings from ``COMPASS_DEV_TOKEN``, ``COMPASS_API_TOKENS``, ``COMPASS_MAX_BODY_BYTES``."""
    source = os.environ if env is None else env
    raw_tokens = source.get("COMPASS_API_TOKENS", "").strip()
    table = parse_token_table(raw_tokens) if raw_tokens else None
    max_body = MAX_BODY_BYTES
    raw_body = source.get("COMPASS_MAX_BODY_BYTES", "").strip()
    if raw_body.isdigit() and int(raw_body) > 0:
        max_body = int(raw_body)
    return ApiSettings(
        dev_token=source.get("COMPASS_DEV_TOKEN", "").strip() or DEFAULT_DEV_TOKEN,
        token_table=table,
        max_body_bytes=max_body,
    )
