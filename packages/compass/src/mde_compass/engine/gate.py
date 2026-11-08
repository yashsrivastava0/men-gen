"""Gate 1 (prompt 6.11): when approval is possible, and what a typed "yes" is worth.

``gate_blockers`` lists everything that keeps the Brief from being approved (each blocker is a catalog string
key plus its placeholder values, so the UI can show it in the user's language):

* a ``must`` slot is still empty;
* a high-impact assumption has no explicit decision;
* a handoff probe has not passed (unless the Brief is flagged ``degraded`` after the repair rounds);
* a blocking open question exists;
* the supplied version or hash is not the current one;
* enterprise mode: a required approver role has not approved THIS hash.

``evaluate`` turns that into a verdict for a ``gate_decision{approve}``: it additionally refuses an approval
that carries no version and hash, so "no approval without a matching hash" holds by construction. Silence never
approves; ``classify_yes`` accepts a typed yes only when the gate is open, the classifier is confident and the
message is not compound ("ok but change the color" is a change request).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from mde_compass.domain.brief import Brief
from mde_compass.engine.assumptions import review
from mde_compass.engine.language import normalize
from mde_compass.engine.router import find_phrase, is_exact, load_phrases, strip_fillers

PROBE_KINDS: tuple[str, ...] = ("research", "blueprint", "design")
HEDGE_CONFIDENCE = 0.5
EXACT_CONFIDENCE = 0.95
MAX_YES_WORDS = 8  # a typed approval is short; anything longer is not a bare yes


@dataclass(frozen=True)
class Blocker:
    """One reason approval is not possible: ``key`` is a catalog string key (``blocker.*``)."""

    code: str
    key: str
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GateVerdict:
    accepted: bool
    blockers: list[Blocker]


def gate_blockers(
    brief: Brief,
    *,
    must_missing: Sequence[str],
    probes: Mapping[str, bool],
    blocking_questions: int,
    mode: str,
    approvals: Mapping[str, str] | None,
    required_roles: Sequence[str],
    supplied_version: int | None = None,
    supplied_hash: str | None = None,
    current_hash: str,
    current_version: int,
) -> list[Blocker]:
    """Everything that currently blocks approval (empty means the gate may open)."""
    blockers: list[Blocker] = []
    if must_missing:
        blockers.append(
            Blocker(
                "must_slots_empty", "blocker.must_slots_empty", {"items": ", ".join(must_missing)}
            )
        )
    unreviewed = review(brief).unreviewed_high_ids
    if unreviewed:
        blockers.append(
            Blocker(
                "high_impact_assumption",
                "blocker.high_impact_assumption",
                {"count": str(len(unreviewed))},
            )
        )
    if not brief.meta.degraded:
        failed = [k for k in PROBE_KINDS if probes.get(k) is not True]
        if failed:
            blockers.append(
                Blocker(
                    "probes_not_passed", "blocker.probes_not_passed", {"items": ", ".join(failed)}
                )
            )
    open_blocking = [q for q in brief.open_questions if q.blocking]
    if open_blocking:
        blockers.extend(
            Blocker("blocking_question", "blocker.blocking_question", {"question": q.text})
            for q in open_blocking
        )
    elif blocking_questions > 0:
        blockers.append(
            Blocker(
                "blocking_question",
                "blocker.blocking_question",
                {"question": str(blocking_questions)},
            )
        )
    if (supplied_version is not None and supplied_version != current_version) or (
        supplied_hash is not None and supplied_hash != current_hash
    ):
        blockers.append(Blocker("hash_mismatch", "blocker.hash_mismatch"))
    if mode == "enterprise":
        have = approvals or {}
        missing = [r for r in required_roles if have.get(r) != current_hash]
        if missing:
            blockers.append(
                Blocker(
                    "approvers_missing", "blocker.approvers_missing", {"roles": ", ".join(missing)}
                )
            )
    return blockers


def evaluate(
    brief: Brief,
    *,
    must_missing: Sequence[str],
    probes: Mapping[str, bool],
    blocking_questions: int,
    mode: str,
    approvals: Mapping[str, str] | None,
    required_roles: Sequence[str],
    supplied_version: int | None,
    supplied_hash: str | None,
    current_hash: str,
    current_version: int,
) -> GateVerdict:
    """The verdict on ``gate_decision{approve}``: accepted only with no blocker AND a matching version + hash."""
    blockers = gate_blockers(
        brief,
        must_missing=must_missing,
        probes=probes,
        blocking_questions=blocking_questions,
        mode=mode,
        approvals=approvals,
        required_roles=required_roles,
        supplied_version=supplied_version,
        supplied_hash=supplied_hash,
        current_hash=current_hash,
        current_version=current_version,
    )
    has_mismatch = any(b.code == "hash_mismatch" for b in blockers)
    if (supplied_version is None or supplied_hash is None) and not has_mismatch:
        blockers.append(Blocker("hash_mismatch", "blocker.hash_mismatch"))
    return GateVerdict(accepted=not blockers, blockers=blockers)


def _all_approval(core: str, phrases: tuple[str, ...]) -> str | None:
    """The message is nothing but approval phrases ("haan theek hai", "yes looks right"): return the first."""
    rest = f" {core} "
    first: str | None = None
    for phrase in sorted(phrases, key=len, reverse=True):
        token = f" {phrase} "
        if token in rest:
            first = first or phrase
            rest = rest.replace(token, " ")
    return first if first is not None and not rest.strip() else None


def classify_yes(
    text: str, *, gate_open: bool, language: str = "en", confidence_min: float = 0.8
) -> Literal["yes", "compound", "no", "unclear"]:
    """What a typed message means at the gate.

    ``yes`` only when the gate is open, the message is exactly an approval phrase (fillers allowed) and the
    confidence reaches ``confidence_min``; ``compound`` when it approves AND asks for something ("ok but change
    the color": a change request); ``no`` for a refusal; everything else (including any "yes" while the gate is
    closed, hedged yeses and longer texts) is ``unclear``. Silence (empty text) is ``unclear``, never a yes.
    """
    del language  # phrase tables cover English, Hindi and Hinglish together
    norm = normalize(text[:1_000])
    if not norm:
        return "unclear"
    ph = load_phrases()
    core = strip_fillers(norm)
    approve_hit = is_exact(norm, ph.approve) or _all_approval(core, ph.approve)
    starts_with_yes = approve_hit is not None or any(
        core.startswith(a + " ") for a in ph.approve if a
    )
    if find_phrase(norm, ph.change_cue) is not None and starts_with_yes:
        return "compound"
    if is_exact(norm, ph.negative) is not None:
        return "no"
    if approve_hit is None:
        return "unclear"
    confidence = EXACT_CONFIDENCE
    if find_phrase(norm, ph.hedge) is not None or len(norm.split()) > MAX_YES_WORDS:
        confidence = HEDGE_CONFIDENCE
    if not gate_open or confidence < confidence_min:
        return "unclear"
    return "yes"
