"""Deterministic merger for ``cmp.merge_claims``: normalised-text dedupe plus rule-based contradiction
detection (negation, different numbers, antonyms). Pure; also the mock handler's engine."""

from __future__ import annotations

from mde_compass.calls.merge_claims.conflicts import find_conflicts, has_tbd_marker
from mde_compass.calls.merge_claims.rules import MergeRules, load_rules
from mde_compass.calls.merge_claims.schema import (
    ConflictPair,
    MergeDecision,
    MergeInput,
    MergeOutput,
)
from mde_compass.calls.read_chunk.textutil import normalize

WORD_TO_FLOAT = {"high": 0.9, "medium": 0.6, "low": 0.3}


def confidence_word(value: float) -> str:
    """Domain confidence (0..1) as the wire word the model sees."""
    return "high" if value >= 0.75 else ("medium" if value >= 0.5 else "low")


def needs_confirming(type_: str, text: str, confidence: float, rules: MergeRules) -> bool:
    """Open issues, TBD text and low-confidence statements are never silently 'used'."""
    return (
        type_ == "open_issue"
        or has_tbd_marker(text, rules)
        or confidence < rules.low_confidence_below
    )


def fallback(inp: MergeInput) -> MergeOutput:
    """The deterministic ``cmp.merge_claims``: one decision per statement, every conflict reported."""
    rules = load_rules()
    sts = inp.statements
    first_seen: dict[str, int] = {}
    canonical: list[int] = []  # positions that are first occurrences
    dup_of: dict[int, int] = {}
    for pos, st in enumerate(sts):
        key = normalize(st.text)
        if key in first_seen:
            dup_of[pos] = first_seen[key]
        else:
            first_seen[key] = pos
            canonical.append(pos)
    pairs = find_conflicts([sts[p].text for p in canonical], rules)
    conflicts = [(canonical[i], canonical[j], why) for i, j, why in pairs]
    in_conflict = {p for a, b, _ in conflicts for p in (a, b)}
    decisions: list[MergeDecision] = []
    for pos, st in enumerate(sts):
        if pos in dup_of:
            status, dup = "duplicate", sts[dup_of[pos]].idx
        elif pos in in_conflict:
            status, dup = "conflicting", None
        elif needs_confirming(st.type, st.text, WORD_TO_FLOAT[st.confidence], rules):
            status, dup = "needs_confirming", None
        else:
            status, dup = "used", None
        decisions.append(
            MergeDecision(
                idx=st.idx,
                status=status,  # type: ignore[arg-type]
                duplicate_of=dup,
                slot_hint=rules.slot_hints.get(st.type),
            )
        )
    return MergeOutput(
        decisions=decisions,
        conflicts=[ConflictPair(a=sts[a].idx, b=sts[b].idx, reason=why) for a, b, why in conflicts],
        tbd_idx=[
            st.idx
            for pos, st in enumerate(sts)
            if pos not in dup_of and (st.type == "open_issue" or has_tbd_marker(st.text, rules))
        ],
    )
