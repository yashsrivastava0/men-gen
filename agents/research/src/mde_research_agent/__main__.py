"""``python -m mde_research_agent [--memory] [--port 8120]``: the dev server (CORS for the test console)."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from mde_research_agent import DEFAULT_PORT
from mde_research_agent.config import load_research_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mde_research_agent", description="Research agent dev server"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--memory", action="store_true", help="in-memory store: no database, nothing is kept"
    )
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write(
            "refusing a non-loopback host: dev authentication must never face a network\n"
        )
        return 2
    load_research_settings()  # fail early on an invalid configuration
    if args.memory:
        os.environ["RESEARCH_MEMORY"] = "1"
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

    return create_app(memory=os.environ.get("RESEARCH_MEMORY", "") == "1")


if __name__ == "__main__":
    sys.exit(main())
