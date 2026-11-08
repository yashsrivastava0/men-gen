"""Template summariser: the offline brain of C11 (mock handler and engine fallback).

Joins the plain facts it is given into one sentence (``brief_sentence``) or a few short sentences
(``plan_summary``), capped at ``max_words``. It adds nothing: no features, numbers or promises.
"""

from __future__ import annotations

from mde_contracts.calls import C11Input, C11Output


def _cap(text: str, max_words: int) -> str:
    parts = text.split()
    if len(parts) <= max_words:
        return text
    return " ".join(parts[:max_words]).rstrip(",;:") + "."


def summarise(inp: C11Input) -> C11Output:
    """Deterministic C11 text from ``inp.facts``."""
    facts = [" ".join(f.split()).strip(" .,;") for f in inp.facts if f.strip()]
    if not facts:
        return C11Output(text=(inp.app_name or "").strip())
    if inp.kind == "plan_summary":
        text = " ".join(f"{f[:1].upper()}{f[1:]}." for f in facts)
    else:
        head = f"{facts[0][:1].upper()}{facts[0][1:]}"
        text = ", ".join([head, *facts[1:]]) + "."
    return C11Output(text=_cap(text, max(1, inp.max_words)))
