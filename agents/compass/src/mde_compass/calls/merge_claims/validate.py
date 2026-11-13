"""Code validators for ``cmp.merge_claims``: the model may only label statements, never drop, rewrite or
merge them away. Any structural violation rejects the whole answer (the deterministic merger takes over)."""

from __future__ import annotations

from mde_compass.calls.merge_claims.schema import MergeInput, MergeOutput
from mde_compass.calls.read_chunk.textutil import tokens

MIN_DUPLICATE_OVERLAP = 0.5


def _overlap(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    union = len(ta | tb)
    return len(ta & tb) / union if union else 0.0


def validate_output(inp: MergeInput, out: MergeOutput) -> list[str]:
    n = len(inp.statements)
    errors: list[str] = []
    seen = [d.idx for d in out.decisions]
    if sorted(seen) != list(range(n)):
        errors.append(f"merge: decisions must cover idx 0..{n - 1} exactly once")
        return errors
    text = {s.idx: s.text for s in inp.statements}
    for d in out.decisions:
        if d.status == "duplicate":
            if d.duplicate_of is None or not (0 <= d.duplicate_of < d.idx):
                errors.append(f"merge: idx {d.idx} duplicate_of must be an earlier statement")
            elif _overlap(text[d.idx], text[d.duplicate_of]) < MIN_DUPLICATE_OVERLAP:
                errors.append(f"merge: idx {d.idx} is not similar to idx {d.duplicate_of}")
    for c in out.conflicts:
        if not (0 <= c.a < n and 0 <= c.b < n) or c.a == c.b:
            errors.append(f"merge: bad conflict pair ({c.a}, {c.b})")
    for i in out.tbd_idx:
        if not 0 <= i < n:
            errors.append(f"merge: tbd idx {i} out of range")
    return errors
