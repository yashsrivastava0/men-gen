"""``python -m mde_context_api``: run the API with uvicorn (Windows-safe).

psycopg's async mode needs a selector event loop on Windows, and the policy must be set before the
loop exists. Importing ``mde_context_api.loop`` does that, and uvicorn is told to build its loop
with ``mde_context_api.loop:selector_loop_factory`` (also under ``--reload``, where the child
process imports it by name).

    python -m mde_context_api [--host H] [--port P] [--reload] [--fake-builder]

``--fake-builder`` uses the scripted Spec Builder (same as ``SPEC_BUILDER=fake``) until the real one
is wired in. Mock mode needs no key and no network.

**It will not put dev authentication on a network.** Without ``API_TOKENS`` the caller's identity
and roles are headers it writes itself, so the process refuses (exit code 2) to bind anything but a
loopback address in that mode. Set ``API_TOKENS`` (a token table, see ``mde_context_api.identity``)
to listen on a network address. There is deliberately no override flag: the only way to listen on a
network is to configure real identities.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from pydantic import ValidationError

from mde_context_api.loop import LOOP_FACTORY_PATH  # sets the Windows selector policy first
from mde_context_api.settings import ApiSettings, load_api_settings
from mde_core.logging import configure_logging, get_logger

REFUSAL = (
    "Refusing to listen on {host}: this server is using dev authentication (a shared token plus "
    "X-Tenant-Id / X-User-Id / X-Roles headers that the caller writes itself), which must never "
    "face a network. Bind 127.0.0.1 (the default), or set API_TOKENS to a token table so every "
    "token maps to an identity on the server (see docs/packages/context-api.md)."
)


def is_loopback(host: str) -> bool:
    """True for ``localhost`` and loopback IP literals (``127.0.0.0/8``, ``::1``); nothing else.

    A host name other than ``localhost`` is not resolved: it could point anywhere, so it counts
    as a network address. ``0.0.0.0`` and ``::`` (every interface) are network addresses too.
    """
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def bind_refusal(host: str, settings: ApiSettings) -> str | None:
    """Why the server may not listen on ``host`` with these settings, or ``None`` if it may."""
    if settings.token_mode or is_loopback(host):
        return None
    return REFUSAL.format(host=host)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mde_context_api", description=__doc__)
    parser.add_argument("--host", default=None, help="default: API_HOST (127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="default: API_PORT (8100)")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    parser.add_argument(
        "--docs",
        action="store_true",
        help="serve /docs, /redoc and /openapi.json (unauthenticated: dev only; API_DOCS=1)",
    )
    parser.add_argument(
        "--fake-builder",
        action="store_true",
        help="use the scripted Spec Builder (dev only; same as SPEC_BUILDER=fake)",
    )
    args = parser.parse_args(argv)

    if args.fake_builder:
        os.environ["SPEC_BUILDER"] = "fake"  # inherited by the reload child process
    if args.docs:
        os.environ["API_DOCS"] = "1"
    try:
        settings = load_api_settings()
    except ValidationError as exc:  # a bad API_TOKENS / TENANT_POLICIES: say where, never the value
        problems = "; ".join(str(err.get("msg", "invalid")) for err in exc.errors())
        sys.stderr.write(f"Invalid configuration: {problems}\n")
        return 2
    if settings.api_docs:
        os.environ["API_DOCS"] = "1"  # also for the --reload child, which builds its own app
    configure_logging(settings.log_level, json=False)

    host = args.host or settings.api_host
    refusal = bind_refusal(host, settings)
    if refusal is not None:
        sys.stderr.write(refusal + "\n")
        return 2
    log = get_logger("mde_context_api")
    if settings.token_mode:
        log.info("auth_mode", mode="token_table", tokens=len(settings.token_table or ()))
    else:
        log.warning("auth_mode", mode="dev_headers", note="local use only; never on a network")

    import uvicorn

    uvicorn.run(
        "mde_context_api.main:app",
        host=host,
        port=args.port or settings.api_port,
        reload=args.reload,
        loop=LOOP_FACTORY_PATH,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
