"""``python -m mde_research_agent``: the Research app (API and front end on one port, live models and the live web only).

Options: ``--host`` (loopback only), ``--port`` (default 8120), ``--inputs DIR``, ``--data DIR``. With no flags it
uses ``docs/compass/research-inputs`` for inputs and ``agents/research/.runtime`` for its data. The app always runs live; if a
search key is missing it still starts and its front end says exactly what to set; it never runs on made-up content.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from mde_research_agent import DEFAULT_PORT
from mde_research_agent.config import data_dir, load_research_settings


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
            self._handler.handle(
                logging.LogRecord("research", logging.INFO, "", 0, line, None, None)
            )
        return len(text)

    def flush(self) -> None:
        self._handler.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mde_research_agent", description="The Research app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--inputs", type=Path, default=None, help="folder of approved Compass exports (read-only)"
    )
    parser.add_argument("--data", type=Path, default=None, help="the app's data folder")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write(
            "refusing a non-loopback host: dev authentication must never face a network\n"
        )
        return 2
    if args.inputs is not None:
        os.environ["RESEARCH_INPUTS_DIR"] = str(args.inputs)
    if args.data is not None:
        os.environ["RESEARCH_DATA_DIR"] = str(args.data)
    cfg = load_research_settings()  # fail early on an invalid configuration
    from mde_core.logging import configure_logging

    configure_logging("INFO", json=True, stream=_RotatingStream(data_dir(cfg) / "logs" / "app.log"))
    sys.stdout.write(f"Research app: http://{args.host}:{args.port}/\n")
    sys.stdout.flush()
    uvicorn.run(
        "mde_research_agent.__main__:app_factory",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


def app_factory():  # type: ignore[no-untyped-def]
    from mde_research_agent.api import create_app

    return create_app()


if __name__ == "__main__":
    sys.exit(main())
