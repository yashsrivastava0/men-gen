"""Who a caller is: identity shapes, and the optional server-side token table (``API_TOKENS``).

Two ways to become a ``Principal``:

* **Dev headers** (default; local use only): one shared bearer token (``API_DEV_TOKEN``) plus
  ``X-Tenant-Id`` / ``X-User-Id`` / ``X-Roles``. The headers are *claims the caller makes about
  itself*: whoever holds the token can be anyone with any role. That is fine on a developer's own
  machine and wrong everywhere else.
* **Token table** (``API_TOKENS``): a JSON object ``token -> {tenant_id, user_id, roles, ...}``. A
  presented token *is* the identity: the headers are ignored, so a forged ``X-User-Id`` or
  ``X-Roles`` changes nothing. This is the production-shaped mode.

Nothing in this module ever puts a token, or any part of the configured JSON, into an error
message: a bad configuration names the entry number and the field, never the value.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from mde_context_api.bindings import PolicyBinding
from mde_contracts.common import Principal

# The tenant id becomes a directory name in the hand-off repo (PROJECTS_DIR/<tenant>/<app>), so it
# is held to what ``mde_flow.handoff.validate_name`` accepts.
TENANT_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
USER_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
ROLE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
MAX_ROLES: Final = 20

TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z0-9._~+/=-]{16,256}$")
MAX_TOKENS: Final = 500


def valid_tenant(tenant: str) -> bool:
    return bool(TENANT_PATTERN.match(tenant)) and ".." not in tenant


def valid_user(user: str) -> bool:
    return bool(USER_PATTERN.match(user))


class TokenEntry(PolicyBinding):
    """One configured token's identity (and, optionally, the policies it may start)."""

    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()

    def principal(self) -> Principal:
        return Principal(tenant_id=self.tenant_id, user_id=self.user_id, roles=list(self.roles))

    def binding(self) -> PolicyBinding | None:
        own = PolicyBinding(
            allowed_policies=self.allowed_policies,
            default_policy=self.default_policy,
            default_mode=self.default_mode,
        )
        return own if own.restricts else None


@dataclass(frozen=True)
class TokenTable:
    """The parsed ``API_TOKENS``. Tokens are held as SHA-256 digests, never as text.

    Matching hashes the presented token and compares it against every digest in constant time
    (fixed-length inputs, no early exit), so neither the length of a token nor the position of a
    match shows in the timing.
    """

    entries: tuple[tuple[bytes, TokenEntry], ...]
    tokens: tuple[str, ...]  # kept only to scrub them from logs (``ApiSettings.secret_values``)

    def __len__(self) -> int:
        return len(self.entries)


def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def parse_api_tokens(raw: str, *, forbidden: tuple[str, ...] = ()) -> TokenTable:
    """``API_TOKENS`` JSON -> ``TokenTable``; ``ValueError`` (value-free message) when it is wrong.

    ``forbidden`` lists tokens that may not be used (the default dev token).
    """
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError(
            "API_TOKENS is not valid JSON (expected an object: token -> identity)"
        ) from None
    if not isinstance(data, dict) or not data:
        raise ValueError("API_TOKENS must be a non-empty JSON object: token -> identity")
    if len(data) > MAX_TOKENS:
        raise ValueError(f"API_TOKENS holds too many tokens (at most {MAX_TOKENS})")
    entries: list[tuple[bytes, TokenEntry]] = []
    tokens: list[str] = []
    for number, (token, body) in enumerate(data.items(), start=1):
        if not isinstance(token, str) or not TOKEN_PATTERN.match(token):
            raise ValueError(
                f"API_TOKENS entry {number}: a token must be 16 to 256 characters "
                "(letters, digits and . _ ~ + / = -)"
            )
        if token in forbidden:
            raise ValueError(f"API_TOKENS entry {number}: that token value is not allowed")
        entries.append((digest(token), _entry(number, body)))
        tokens.append(token)
    return TokenTable(entries=tuple(entries), tokens=tuple(tokens))


def _entry(number: int, body: object) -> TokenEntry:
    try:
        entry = TokenEntry.model_validate(body)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) or "entry" for err in exc.errors()})
        raise ValueError(f"API_TOKENS entry {number} is not valid: {', '.join(fields)}") from None
    problems: list[str] = []
    if not valid_tenant(entry.tenant_id):
        problems.append("tenant_id")
    if not valid_user(entry.user_id):
        problems.append("user_id")
    if len(entry.roles) > MAX_ROLES or any(not ROLE_PATTERN.match(r) for r in entry.roles):
        problems.append("roles")
    if problems:
        raise ValueError(f"API_TOKENS entry {number} is not valid: {', '.join(problems)}")
    return entry
