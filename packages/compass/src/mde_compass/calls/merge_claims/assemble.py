"""Turn a (validated) ``MergeOutput`` plus the statements into the domain ``MergeResult``.

This is where the "must not" rules are enforced by code, whatever the model said:
* nothing is dropped: there is exactly one ``Claim`` per statement (duplicates stay, marked ``duplicate``);
* every contradiction the rules can see is added to the model's, and both sides are marked ``conflicting``
  (a conflicting statement is never reported as a duplicate or as clean);
* open issues, TBD text and low-confidence statements are never ``used``;
* slot hints are only kept when they name a real catalog slot.
"""

from __future__ import annotations

from collections.abc import Collection

from mde_compass.calls.merge_claims.conflicts import find_conflicts, has_tbd_marker
from mde_compass.calls.merge_claims.fallback import needs_confirming
from mde_compass.calls.merge_claims.rules import load_rules
from mde_compass.calls.merge_claims.schema import MergeDecision, MergeOutput
from mde_compass.calls.read_chunk.textutil import normalize
from mde_compass.domain.claims import Claim, ClaimStatus, Statement
from mde_compass.engine.ports import MergeResult


def claim_id(position: int) -> str:
    return f"CLM-{position + 1:03d}"


def assemble(
    statements: list[Statement],
    out: MergeOutput,
    *,
    source_id: str,
    slot_ids: Collection[str],
) -> MergeResult:
    rules = load_rules()
    n = len(statements)
    decision = {d.idx: d for d in out.decisions if 0 <= d.idx < n}
    first_seen: dict[str, int] = {}
    dup_of: dict[int, int] = {}
    for pos, st in enumerate(statements):
        key = normalize(st.text)
        if key in first_seen:
            dup_of[pos] = first_seen[key]
        else:
            first_seen[key] = pos
    # contradictions: the model's, plus every one the rules can see (code cannot be talked out of a conflict)
    pairs: set[tuple[int, int]] = set()
    for c in out.conflicts:
        if 0 <= c.a < n and 0 <= c.b < n and c.a != c.b:
            pairs.add((min(c.a, c.b), max(c.a, c.b)))
    canonical = [p for p in range(n) if p not in dup_of]
    for i, j, _ in find_conflicts([statements[p].text for p in canonical], rules):
        pairs.add((canonical[i], canonical[j]))
    partners: dict[int, set[int]] = {}
    for a, b in pairs:
        partners.setdefault(a, set()).add(b)
        partners.setdefault(b, set()).add(a)

    tbd_positions = {i for i in out.tbd_idx if 0 <= i < n}
    claims: list[Claim] = []
    for pos, st in enumerate(statements):
        d = decision.get(pos)
        if pos in partners:
            status: ClaimStatus = "conflicting"
        elif pos in dup_of or _model_duplicate(d, pos):
            status = "duplicate"
        elif needs_confirming(st.type, st.text, st.confidence, rules) or (
            d is not None and d.status == "needs_confirming"
        ):
            status = "needs_confirming"
        else:
            status = "used"
        hint = d.slot_hint if d is not None and d.slot_hint in slot_ids else None
        if hint is None:
            default = rules.slot_hints.get(st.type)
            hint = default if default in slot_ids else None
        claims.append(
            Claim(
                id=claim_id(pos),
                source_id=source_id,
                statement=st,
                status=status,
                slot_hint=hint,
                conflicts_with=[claim_id(p) for p in sorted(partners.get(pos, ()))],
            )
        )

    tbd: list[str] = []
    seen_tbd: set[str] = set()
    for pos, st in enumerate(statements):
        is_tbd = st.type == "open_issue" or has_tbd_marker(st.text, rules) or pos in tbd_positions
        key = normalize(st.text)
        if is_tbd and key not in seen_tbd:
            seen_tbd.add(key)
            tbd.append(st.text)

    settled = sum(1 for c in claims if c.status in ("used", "duplicate"))
    return MergeResult(
        claims=claims,
        conflicts=[(claim_id(a), claim_id(b)) for a, b in sorted(pairs)],
        tbd=tbd,
        coverage=round(settled / n, 4) if n else 1.0,
        statement_count=n,
        needs_confirming=sum(1 for c in claims if c.status in ("needs_confirming", "conflicting")),
    )


def _model_duplicate(d: MergeDecision | None, pos: int) -> bool:
    """A duplicate claim by the model alone (text differs after normalising): accepted when it names an
    earlier statement (the validator already checked the similarity)."""
    return (
        d is not None
        and d.status == "duplicate"
        and d.duplicate_of is not None
        and 0 <= d.duplicate_of < pos
    )
