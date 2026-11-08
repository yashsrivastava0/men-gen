"""Generate (or check) the golden briefs: ``expected.yaml`` and ``APP_SPEC.md.golden`` per brief.

    uv run python -m evals.runners.make_goldens            # rewrite the goldens
    uv run python -m evals.runners.make_goldens --check    # exit 1 if any golden is out of date

The sessions run offline in mock mode through the real engines (``evals.harness``). A golden
changes only when the product changes what it writes for the same conversation: review the diff.
Never run with a live gateway: goldens are mock-mode snapshots.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

import structlog

from evals.harness.briefs import GoldenBrief, dump_expected, load_briefs, result_expected
from evals.harness.driver import FlowDriver
from evals.harness.journeys import play_journey
from evals.harness.stack import build_stack
from evals.runners._io import say


async def render_brief(brief: GoldenBrief) -> tuple[str, str]:
    """(expected.yaml text, spec text) for one brief, played in mock mode."""
    with tempfile.TemporaryDirectory() as tmp:
        stack = build_stack(Path(tmp) / "projects")
        driver = FlowDriver.for_user(stack)
        result = await play_journey(driver, brief.journey)
        session = await driver.session_record()
        return dump_expected(result_expected(result, session)), result.spec.spec_text


def write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # LF, no newline translation: the hash covers the bytes


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")  # tolerate an autocrlf checkout


async def run(check: bool) -> int:
    stale: list[str] = []
    for brief in load_briefs():
        expected, spec_text = await render_brief(brief)
        for path, text in ((brief.expected_path, expected), (brief.golden_path, spec_text)):
            current = read_text(path) if path.exists() else None
            if current == text:
                continue
            if check:
                stale.append(str(path))
            else:
                write_text(path, text)
                say(f"wrote {path}")
    if stale:
        say("out of date (run: uv run python -m evals.runners.make_goldens):")
        for path in stale:
            say(f"  {path}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="only verify; do not write")
    args = parser.parse_args(argv)
    os.environ.setdefault("LLM_MODE", "mock")
    os.environ["MDE_FORBID_LIVE"] = "1"
    os.environ["MDE_ENV_FILE"] = ""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    return asyncio.run(run(args.check))


if __name__ == "__main__":
    sys.exit(main())
