"""Code validators for ``cmp.read_chunk``: a statement's quote must be a verbatim slice of the chunk; text
that tries to instruct the reader is never a statement (document content is untrusted data)."""

from __future__ import annotations

from mde_compass.calls.read_chunk.fallback import looks_like_injection
from mde_compass.calls.read_chunk.rules import load_rules
from mde_compass.calls.read_chunk.schema import ReadChunkInput, ReadChunkOutput, WireStatement


def statement_problem(chunk_text: str, st: WireStatement) -> str | None:
    """Why this statement must be dropped, or ``None`` if it is acceptable."""
    rules = load_rules()
    quote = st.quote.strip()
    if len(quote) < rules.min_quote_chars:
        return "quote too short"
    if len(quote) > rules.max_quote_chars:
        return "quote too long"
    if quote not in chunk_text:
        return "quote is not in the chunk"
    if not st.text.strip():
        return "empty text"
    if looks_like_injection(quote, rules) or looks_like_injection(st.text, rules):
        return "instruction-like text"
    return None


def keep_valid(chunk_text: str, statements: list[WireStatement]) -> list[WireStatement]:
    """Drop statements whose quote is not in the chunk (never invents), instruction-like ones and repeats."""
    rules = load_rules()
    out: list[WireStatement] = []
    seen: set[tuple[str, str]] = set()
    for st in statements:
        if statement_problem(chunk_text, st) is not None:
            continue
        key = (st.type, st.quote.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(st.model_copy(update={"quote": st.quote.strip(), "text": st.text.strip()}))
        if len(out) >= rules.max_statements:
            break
    return out


def validate_output(inp: ReadChunkInput, out: ReadChunkOutput) -> list[str]:
    """Spec-level validator: reject the whole answer only when it has statements and none is usable."""
    if out.statements and not keep_valid(inp.chunk_text, out.statements):
        return ["read_chunk: no statement has a verbatim quote from the chunk"]
    return []
