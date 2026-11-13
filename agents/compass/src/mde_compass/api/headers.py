"""Security headers sent on EVERY response (API, SSE, static, errors).

The Content-Security-Policy is built by :func:`build_csp`. Every directive is fixed except ``connect-src``: it is
``'self'`` plus the origins of the Research service, because the test console (served by this app) calls the
Research agent on another port with ``fetch``. The origins come from the environment variable
``MDE_RESEARCH_ORIGIN`` (space or comma separated, for example ``http://127.0.0.1:8120 https://research.example.com``);
unset means the dev default (:data:`DEFAULT_RESEARCH_ORIGINS`), set but empty means none. Only plain ``http(s)://host[:port]``
origins are accepted: anything else (a path, a wildcard, a semicolon, a quote) is dropped, so the variable can never
widen the policy beyond naming a host.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Final

RESEARCH_ORIGIN_ENV: Final = "MDE_RESEARCH_ORIGIN"
DEFAULT_RESEARCH_ORIGINS: Final = "http://127.0.0.1:8120 http://localhost:8120"

_ORIGIN: Final = re.compile(
    r"^https?://(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?$"
)


def research_origins(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The Research origins allowed in ``connect-src`` (valid, de-duplicated, in the order given)."""
    source = os.environ if env is None else env
    raw = source.get(RESEARCH_ORIGIN_ENV)
    if raw is None:
        raw = DEFAULT_RESEARCH_ORIGINS
    origins: list[str] = []
    for part in raw.replace(",", " ").split():
        origin = part.rstrip("/")
        if _ORIGIN.match(origin) and origin not in origins:
            origins.append(origin)
    return tuple(origins)


@lru_cache(maxsize=16)
def _policy(origins: tuple[str, ...]) -> str:
    connect = " ".join(("'self'", *origins))
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        f"connect-src {connect}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )


def build_csp(env: Mapping[str, str] | None = None) -> str:
    """The CSP for the given environment (default: the process environment, read now)."""
    return _policy(research_origins(env))


def security_headers(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The headers for every response: CSP, nosniff and no-referrer."""
    return {
        "Content-Security-Policy": build_csp(env),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


# The policy with the default Research origins (an empty mapping means "variable unset"); kept for importers
# that want a constant. The middleware and the error responses call build_csp() / security_headers() instead,
# so a changed environment takes effect without a restart of the import.
CSP: Final = build_csp({})
SECURITY_HEADERS: Final = security_headers({})
