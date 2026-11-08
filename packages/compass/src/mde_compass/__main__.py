"""``python -m mde_compass [--host H] [--port P] [--memory]``: run the Compass API with uvicorn.

Dev start: ``PYTHONPATH=packages/compass/src python -m mde_compass --port 8110 --memory`` and open
``http://127.0.0.1:8110/ui/``.

It will not put dev authentication on a network: without ``COMPASS_API_TOKENS`` the caller's identity
is a set of headers it writes itself, so a non-loopback bind is refused (exit code 2). Copied from the
Context API's guard.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from mde_compass import DEFAULT_PORT
from mde_compass.api.settings import ApiSettings, load_api_settings

REFUSAL = (
    "Refusing to listen on {host}: this server is using dev authentication (a shared token plus "
    "X-Tenant-Id / X-User-Id headers that the caller writes itself), which must never face a "
    "network. Bind 127.0.0.1 (the default), or set COMPASS_API_TOKENS to a token table "
    "(token=tenant:user[:role+role];...) so every token maps to an identity on the server."
)


def is_loopback(host: str) -> bool:
    """True for ``localhost`` and loopback IP literals; nothing else (names are never resolved)."""
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
    parser = argparse.ArgumentParser(prog="python -m mde_compass", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--memory", action="store_true", help="in-memory store (no database)")
    args = parser.parse_args(argv)
    try:
        settings = load_api_settings()
    except ValueError as exc:
        sys.stderr.write(f"Invalid configuration: {exc}\n")
        return 2
    refusal = bind_refusal(args.host, settings)
    if refusal is not None:
        sys.stderr.write(refusal + "\n")
        return 2
    if args.memory:
        os.environ["COMPASS_MEMORY"] = "1"  # inherited by the app factory

    import uvicorn

    uvicorn.run(
        "mde_compass.app:create_app", factory=True, host=args.host, port=args.port, log_level="info"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
