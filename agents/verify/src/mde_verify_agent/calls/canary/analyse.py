"""The code reading of a task card's reads: which ids are used but never defined in them.

This is the mock handler's and the fallback's whole judgement (a model reads the same reads and may find more). It is
deterministic: the same reads give the same questions.
"""

from __future__ import annotations

import re

from mde_verify_agent.calls.canary.schema import CanaryIn, CanaryOut, CoverOut
from mde_verify_agent.digest import split_read_blocks

_ENT_USE = re.compile(r"\b(ENT-S?\d+)\.([a-z][a-z0-9_]*)")
_OP_USE = re.compile(r"\b(OP-S?\d+)\b")
_ENT_DEF = re.compile(r"^## (ENT-S?\d+)\b", re.M)
_OP_DEF = re.compile(r"^(OP-S?\d+)\s", re.M)
_NAME = re.compile(r"\bname: (.+?)(?:\s{2,}|$)", re.M)


def missing_facts(reads: dict[str, str]) -> list[str]:
    """Blocking questions from ids a screen uses that no read defines. Navigation targets (SCR-) are not questions:
    a screen may point at a screen that another card builds."""
    screens = {p: t for p, t in reads.items() if p.startswith("screens/")}
    everything = "\n".join(reads.values())
    defined_ent = set(_ENT_DEF.findall(everything))
    defined_op = set(_OP_DEF.findall(everything))
    questions: list[str] = []
    for path in sorted(screens):
        text = screens[path]
        screen = path.removeprefix("screens/").removesuffix(".md")
        for ent, fld in sorted(set(_ENT_USE.findall(text))):
            if ent not in defined_ent:
                questions.append(
                    f"{screen} binds {ent}.{fld} but no read defines {ent}: what are its fields?"
                )
            elif not re.search(rf"\b{re.escape(fld)}\b", _entity_block(everything, ent)):
                questions.append(
                    f"{screen} binds {ent}.{fld} but {ent} in my reads has no field {fld}: which is right?"
                )
        for op in sorted(set(_OP_USE.findall(text))):
            if op not in defined_op:
                questions.append(
                    f"{screen} calls {op} but no read defines {op}: what does it take and return?"
                )
    return list(dict.fromkeys(questions))


def _entity_block(text: str, ent: str) -> str:
    m = re.search(rf"^## {re.escape(ent)}\b.*?(?=^## |\Z)", text, re.M | re.S)
    return m.group(0) if m else ""


def outline_for(reads: dict[str, str]) -> list[str]:
    steps: list[str] = []
    for path in sorted(reads):
        if not path.startswith("screens/"):
            continue
        text = reads[path]
        m = _NAME.search(text)
        name = m.group(1).strip() if m else path
        regions = len(re.findall(r"\{key: ", text.split("\nactions:")[0]))
        actions = (
            len(re.findall(r"\bintent: ", text.split("\nactions:")[1].split("\nstates:")[0]))
            if "\nactions:" in text
            else 0
        )
        steps.append(
            f"Build {name} ({path}): {regions} regions, {actions} actions, every listed state"
        )
    if not steps:
        steps.append("Set up the shared foundation named in the prefix bundle")
    return steps


def analyse(inp: CanaryIn, *, with_outline: bool) -> CanaryOut:
    reads = split_read_blocks(inp.reads)
    questions = missing_facts(reads)
    blocked_screens = " ".join(questions)
    first = inp.read_paths[0] if inp.read_paths else "the card"
    cover: list[CoverOut] = []
    for c in inp.criteria:
        cover.append(
            CoverOut(
                criterion=c,
                satisfiable=not questions,
                how=(
                    f"the screen files in {first} and the reads that follow state what is needed"
                    if not questions
                    else f"blocked: {blocked_screens[:160]}"
                ),
            )
        )
    return CanaryOut(
        outline=outline_for(reads) if with_outline else [],
        blocking_questions=questions,
        assumptions=[],
        acceptance_coverage=cover,
    )
