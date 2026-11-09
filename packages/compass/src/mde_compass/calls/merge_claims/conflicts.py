"""Contradiction detection by rules: same subject, opposite value (negation, different number, antonym).

Pure and linear in the text (token passes only); pairing uses an inverted index with a document-frequency
cutoff, so it stays near-linear in the number of statements. A conflict is never an excuse to drop anything:
callers keep both statements and flag them (prompt 7: "drop conflicting items silently" is the thing to avoid).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from mde_compass.calls.merge_claims.rules import MergeRules, load_rules
from mde_compass.calls.read_chunk.textutil import is_number, normalize, tokens


@dataclass(frozen=True)
class _Feat:
    norm: str
    core: frozenset[str]
    ants: frozenset[str]
    neg: bool
    nums: frozenset[str]


def _stem(token: str) -> str:
    """Crude plural folding so "refund"/"refunds" match (English only; other scripts are left as written)."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss") and token.isascii():
        return token[:-1]
    return token


def _features(text: str, rules: MergeRules) -> _Feat:
    toks = tokens(text)
    neg = any(t in rules.negations for t in toks)
    nums = frozenset(t for t in toks if is_number(t))
    content = [
        t
        for t in toks
        if t[0].isalnum()
        and not is_number(t)
        and t not in rules.stopwords
        and t not in rules.modals
        and t not in rules.negations
    ]
    return _Feat(
        norm=" ".join(toks),
        core=frozenset(_stem(t) for t in content if t not in rules.antonyms),
        ants=frozenset(t for t in content if t in rules.antonyms),
        neg=neg,
        nums=nums,
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _reason(a: _Feat, b: _Feat, rules: MergeRules) -> str | None:
    if a.norm == b.norm:
        return None  # same text: a duplicate, not a conflict
    if (
        len(a.core & b.core) < rules.min_shared_tokens
        or _jaccard(a.core, b.core) < rules.min_similarity
    ):
        return None
    if a.neg != b.neg:
        return "negation"
    if a.nums and b.nums and a.nums != b.nums and len(a.nums) == len(b.nums):
        return "number"
    if any(y in rules.antonyms.get(x, frozenset()) for x in a.ants for y in b.ants):
        return "antonym"
    return None


def find_conflicts(
    texts: Sequence[str], rules: MergeRules | None = None
) -> list[tuple[int, int, str]]:
    """Contradicting pairs ``(i, j, reason)`` with ``i < j`` over the given texts, in index order."""
    r = rules or load_rules()
    feats = [_features(t, r) for t in texts]
    postings: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(feats):
        for tok in f.core:
            postings[tok].append(i)
    shared: dict[tuple[int, int], int] = defaultdict(int)
    for ids in postings.values():
        if len(ids) > r.candidate_df_cutoff:
            continue
        for x, i in enumerate(ids):
            for j in ids[x + 1 :]:
                shared[(i, j)] += 1
    found: list[tuple[int, int, str]] = []
    for (i, j), count in sorted(shared.items()):
        if count < r.min_shared_tokens:
            continue
        reason = _reason(feats[i], feats[j], r)
        if reason is not None:
            found.append((i, j, reason))
    return found


def has_tbd_marker(text: str, rules: MergeRules | None = None) -> bool:
    r = rules or load_rules()
    norm = f" {normalize(text)} "
    return any(f" {m} " in norm for m in r.tbd_markers)
