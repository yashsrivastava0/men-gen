"""``python -m mde_spec_agent serve``: the Spec Bench app (API and front end on one port, loopback only).

The cap comes from the launch environment (``SPEC_LIVE_BUDGET_USD``, ``SPEC_TRIPWIRE_USD``), never from ``.env``. Logs go to
``agents/spec/.runtime/logs/app.log`` (rotating). No database is used, so a plain uvicorn loop is enough.
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from mde_spec_agent.config import DEFAULT_PORT, data_dir, load_spec_settings

LOOPBACK = ("127.0.0.1", "localhost", "::1")


class _RotatingStream:
    """A file-like for structlog that writes through a rotating log file (``logs/app.log``)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))

    def write(self, text: str) -> int:
        line = text.rstrip("\n")
        if line:
            self._handler.handle(logging.LogRecord("spec", logging.INFO, "", 0, line, None, None))
        return len(text)

    def flush(self) -> None:
        self._handler.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mde_spec_agent serve", description="The Spec Bench app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.host not in LOOPBACK:
        sys.stderr.write("refusing a non-loopback host: the Spec Bench must never face a network\n")
        return 2
    settings = load_spec_settings()  # fail early on an invalid configuration
    from mde_core.logging import configure_logging

    configure_logging(
        "INFO", json=True, stream=_RotatingStream(data_dir(settings) / "logs" / "app.log")
    )
    sys.stdout.write(f"Spec Bench: http://{args.host}:{args.port}/\n")
    sys.stdout.flush()
    uvicorn.run(
        "mde_spec_agent.api.app:app_factory",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
