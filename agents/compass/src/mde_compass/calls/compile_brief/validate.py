"""Structural checks on the compile output (run by ``CallRunner``; a failure means the flagged fallback).

They refuse an output that cannot be trusted as evidence: a path that is not one of the offered targets, a fact id
that is not in the ledger, an item or addition with no fact behind it, a fact that is both placed and called not used,
a value that is not JSON. For the typical task only the proposals matter (nothing is filled or placed). They do NOT require full coverage and do not look inside a value: the adapter checks each value
against its slot's ``value_schema`` and drops what does not fit, and it completes coverage (a fact the model
mentioned nowhere is placed by code), so one gap never costs the whole compile.
"""

from __future__ import annotations

import json

from mde_compass.calls.compile_brief.ledger import join_text, ledger_ids
from mde_compass.calls.compile_brief.schema import CompileInput, CompileOutput

MAX_ERRORS = 12
MAX_TYPICAL = 12


def _parses(value_json: str) -> bool:
    try:
        json.loads(value_json)
    except (ValueError, RecursionError):
        return False
    return True


def structural(inp: CompileInput, out: CompileOutput) -> list[str]:
    errors: list[str] = []
    paths = {t.path for t in inp.targets}
    known = set(ledger_ids(join_text(inp.ledger)))
    if inp.task == "typical":
        return [] if len(out.typical) <= MAX_TYPICAL * 3 else ["typical: far too many entries"]
    for add in out.additions:
        where = f"addition {add.path!r}"
        if add.path not in paths:
            errors.append(f"{where}: not one of the targets")
        if not add.fact_ids:
            errors.append(f"{where}: no supporting fact id")
        if not _parses(add.value_json):
            errors.append(f"{where}: value_json is not JSON")
        unknown = [f for f in add.fact_ids if f not in known]
        if unknown:
            errors.append(f"{where}: fact ids not in the ledger {unknown[:5]}")
    for item in out.items:
        where = f"item {item.path!r}"
        if item.path not in paths:
            errors.append(f"{where}: not one of the targets")
        if not item.fact_ids:
            errors.append(f"{where}: no supporting fact id")
        if not _parses(item.value_json):
            errors.append(f"{where}: value_json is not JSON")
        unknown = [f for f in item.fact_ids if f not in known]
        if unknown:
            errors.append(f"{where}: fact ids not in the ledger {unknown[:5]}")
    for p in out.placements:
        if p.fact_id not in known:
            errors.append(f"placement {p.fact_id!r}: not a fact id of the ledger")
    for n in out.not_used:
        if n.fact_id not in known:
            errors.append(f"not_used {n.fact_id!r}: not a fact id of the ledger")
    both = {p.fact_id for p in out.placements} & {n.fact_id for n in out.not_used}
    if both:
        errors.append(f"facts both placed and not used: {sorted(both)[:5]}")
    return errors[:MAX_ERRORS]
