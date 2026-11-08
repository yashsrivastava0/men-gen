"""``mde spec ...``: read and check an ``APP_SPEC.md`` with the Spec Loader.

* ``simulate-consumer``: walk the build sequence the way the frontend engine will and report slice
  sizes and contract violations (the same output and exit codes as
  ``scripts/simulate_frontend_consumer.py``: 0 ok, 1 contract violation, 2 unreadable file);
* ``verify``: does the embedded hash match the file (and, with ``--hash``, the one an approval is
  bound to)?
* ``show``: the index of the file, or the tiered slice of one screen, step or flow, or the exact
  text of ids.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mde_cli.console import Out
from mde_cli.errors import EXIT_BAD_INPUT, EXIT_OK, EXIT_PROBLEM, CliError
from mde_spec_loader import (
    SpecError,
    SpecIndex,
    compute_spec_hash,
    embedded_hash,
    simulate_consumer,
)


def load_index(path: Path) -> SpecIndex:
    """Index the file; an unreadable or malformed one is a bad-input error (exit 2)."""
    try:
        return SpecIndex.from_path(path)
    except (OSError, UnicodeDecodeError, SpecError) as exc:
        raise CliError(f"cannot read {path}: {exc}", exit_code=EXIT_BAD_INPUT) from None


def simulate_consumer_command(path: Path, budget: int, out: Out) -> int:
    try:
        index = load_index(path)
        report = simulate_consumer(index, budget_tokens=budget)
        view = index.index()
    except SpecError as exc:  # a file that indexes but does not parse as a whole spec
        raise CliError(f"cannot read {path}: {exc}", exit_code=EXIT_BAD_INPUT) from None
    screens = sum(1 for b in view.blocks if b.kind == "screen")
    out.line(
        f"{path.name}: version {view.spec_version}, {screens} screens, ~{view.total_tokens} "
        f"tokens in total, hash {'ok' if report.hash_ok else 'MISMATCH'}"
    )
    out.line(f"budget per slice: {report.budget_tokens} tokens")
    out.line(f"{'target':<10} {'kind':<7} {'tokens':>7}")
    for item in report.slices:
        flag = "  OVER BUDGET" if item.over_budget else ""
        out.line(f"{item.id:<10} {item.kind:<7} {item.tokens:>7}{flag}")
    out.line(f"largest slice: {report.max_tokens} tokens")
    if report.problems:
        sys.stderr.write(f"\n{len(report.problems)} problem(s):\n")
        for problem in report.problems:
            sys.stderr.write(f"  - {problem}\n")
        return EXIT_PROBLEM
    out.line("ok: every slice is within budget and every reference resolves")
    return EXIT_OK


def verify_command(path: Path, expected: str | None, out: Out) -> int:
    text = load_index(path).text
    embedded = embedded_hash(text)
    recomputed = compute_spec_hash(text)
    out.line(f"{path.name}: embedded hash {embedded or '(none)'}")
    out.line(f"{' ' * len(path.name)}  recomputed    {recomputed}")
    if not embedded:
        out.line("MISMATCH: the file has no embedded hash.")
        return EXIT_PROBLEM
    if embedded != recomputed:
        out.line("MISMATCH: the file was changed after its hash was written.")
        return EXIT_PROBLEM
    if expected is not None and expected != embedded:
        out.line(f"MISMATCH: the file is intact but it is not the version bound to {expected}.")
        return EXIT_PROBLEM
    out.line(
        "ok: the embedded hash matches the file" + (" and the expected hash." if expected else ".")
    )
    return EXIT_OK


def show_command(path: Path, screen: str | None, ids: list[str], out: Out) -> int:
    index = load_index(path)
    try:
        if ids:
            for spec_id, text in index.get(ids).items():
                out.line(f"--- {spec_id}", "bold")
                out.line(text)
            return EXIT_OK
        if screen:
            sliced = index.slice(screen)
            out.line(
                f"--- slice {sliced.id} ({sliced.spec_tag}), about {sliced.token_estimate} tokens, "
                f"blocks: {', '.join(sliced.block_ids)}",
                "bold",
            )
            out.line(sliced.text)
            return EXIT_OK
        view = index.index()
    except SpecError as exc:
        raise CliError(str(exc), exit_code=EXIT_BAD_INPUT) from None
    out.line(
        f"{path.name}: version {view.spec_version}, app {view.app_id}, hash {view.hash}, "
        f"~{view.total_tokens} tokens"
    )
    out.line("")
    out.line("sections:", "bold")
    for section in view.sections:
        out.line(f"  {section.number:>2}  {section.title:<28} {section.tokens:>6} tokens")
    out.line("")
    out.line(f"{'block':<12} {'kind':<12} {'lines':<11} {'tokens':>6}", "bold")
    for block in view.blocks:
        span = f"{block.line_start}-{block.line_end}"
        out.line(f"{block.id:<12} {block.kind:<12} {span:<11} {block.tokens:>6}")
    return EXIT_OK
