"""Assumptions that stand for no slot (ADR 0016): typical details nobody asked about, corrections, review findings.

Gate 1 shows them next to the slot assumptions so the person approves what the Brief says that they did not say:

* ``typical``: "apps like this usually have X and you did not mention it" (drafted by ``cmp.compile_brief``, at most
  12, never Locked, never overriding a value the person gave);
* ``correction``: "We used Y; you first said X" (a fact that replaced an earlier one, or two facts that conflict);
* ``review``: a finding of ``cmp.scan_ambiguity`` that does not add up (a contradiction, an unrealistic claim, a missing
  dependency, an implausible number or timeline).

``impact`` is ``high`` only when the touched slot matters (catalog impact 4 or more, or it must be confirmed); a high
impact assumption blocks Gate 1 until the person decides (``engine.gate``). A typical detail is high only when the
model called it risky or its kind is one of the file's ``high_impact_kinds``. All wording comes from the strings files
(``brief.v2.*``, en and hi); no English is written here. Everything is pure: ids are placeholders that
``assumptions.merge_derived`` replaces.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from mde_compass.catalog.fact_kinds import is_high_impact_kind, normalize_kind, theme_for
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import AssumptionRecord, Brief, Theme
from mde_compass.domain.common import Provenance
from mde_compass.domain.interview import Fact
from mde_compass.engine.assumptions import HIGH_IMPACT_AT, THEME_BY_GROUP, claim_key
from mde_compass.engine.compile_apply import norm_name
from mde_compass.engine.compile_types import MAX_TYPICAL, ReviewFinding, TypicalDetail
from mde_compass.engine.facts import fact_number

PLACEHOLDER_ID = "ASM-000"
MAX_REVIEW = 5
SNIPPET_CHARS = 120
CLAIM_CHARS = 240


def lang_of(language: str) -> str:
    """The strings exist in ``en`` and ``hi``; Hinglish and anything else reads the English ones."""
    return "hi" if language == "hi" else "en"


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    """One line cut at a word boundary (never mid-word, an ellipsis when cut)."""
    line = " ".join(text.split())
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0] or line[:limit]
    return cut.rstrip(" ,;:.") + "…"


def touches_important_slot(slot_ids: Iterable[str], catalog: Catalog) -> bool:
    """The touched slot is one the Brief cannot be wrong about: impact 4 or more, or it must be confirmed."""
    return any(
        (slot := catalog.slots.get(sid)) is not None
        and (slot.impact >= HIGH_IMPACT_AT or slot.must_confirm)
        for sid in slot_ids
    )


def theme_of(slot_ids: Iterable[str], catalog: Catalog) -> Theme:
    """The Gate 1 theme of the first known slot (``Business`` when there is none)."""
    for sid in slot_ids:
        slot = catalog.slots.get(sid)
        if slot is not None:
            return THEME_BY_GROUP.get(slot.group, "Business")
    return "Business"


def _record(
    *,
    claim: str,
    reason: str,
    basis: str,
    impact: str,
    theme: Theme,
    what_changes: str,
    provenance: Provenance,
) -> AssumptionRecord:
    return AssumptionRecord(
        id=PLACEHOLDER_ID,
        claim=claim[:CLAIM_CHARS],
        reason=reason,
        basis=basis,
        impact="high" if impact == "high" else "low",
        theme=theme,
        slot_id=None,
        decision="proposed",
        what_changes=what_changes,
        tier="Default",
        provenance=[provenance],
        confidence=0.5,
        status="assumed",
    )


# ----------------------------------------------------------------------------------------- typical
def typical_assumptions(
    typical: Sequence[TypicalDetail], brief: Brief, catalog: Catalog, language: str
) -> list[AssumptionRecord]:
    """Typical details as assumptions: at most 12, none that repeats what the Brief already says or assumes."""
    lang = lang_of(language)
    known = {claim_key(a.claim) for a in brief.assumptions if a.basis != "typical"}
    names = {
        norm_name(n)
        for n in (
            *(f.name for f in brief.scope),
            *(r.name_in_user_words for r in brief.roles),
            *(a.name for a in brief.apps),
        )
        if len(n.strip()) >= 4
    }
    out: list[AssumptionRecord] = []
    for item in typical:
        claim = " ".join(item.claim.split())
        key = claim_key(claim)
        padded = f" {norm_name(claim)} "
        if not claim or key in known or any(f" {n} " in padded for n in names):
            continue  # already said, or it is a feature or role the Brief has
        known.add(key)
        kind = normalize_kind(item.kind, "context")
        high = item.risky or is_high_impact_kind(kind)
        out.append(
            _record(
                claim=claim,
                reason=" ".join(item.reason.split())[:CLAIM_CHARS]
                or catalog.t("brief.v2.typical.reason", lang),
                basis="typical",
                impact="high" if high else "low",
                theme=theme_for(kind),  # type: ignore[arg-type]
                what_changes=" ".join(item.what_changes.split())[:CLAIM_CHARS]
                or catalog.t("brief.v2.typical.what_changes", lang),
                provenance=Provenance(type="model_knowledge", ref="compile:typical"),
            )
        )
        if len(out) >= MAX_TYPICAL:
            break
    return out


# ------------------------------------------------------------------------------------- corrections
def _ancestors(fact: Fact, by_id: dict[str, Fact]) -> list[Fact]:
    """Every fact this one replaced, directly or through a chain (oldest first), only those now superseded."""
    found: dict[str, Fact] = {}
    todo = list(fact.supersedes)
    while todo:
        old = by_id.get(todo.pop())
        if old is None or old.id in found or old.id == fact.id:
            continue
        if old.status == "superseded":
            found[old.id] = old
            todo.extend(old.supersedes)
    return sorted(found.values(), key=lambda f: fact_number(f.id))


def _effect_slots(facts: Iterable[Fact]) -> list[str]:
    return list(dict.fromkeys(e.slot_id for f in facts for e in f.effects))


def _same_text(a: str, b: str) -> bool:
    return norm_name(a) == norm_name(b)


def corrections_from_facts(
    facts: Sequence[Fact], catalog: Catalog, language: str
) -> list[AssumptionRecord]:
    """ "We used Y; you first said X" for every active fact that replaced an earlier one, and one entry for facts
    that conflict (status ``disputed``): the person sees what changed and decides."""
    lang = lang_of(language)
    by_id = {f.id: f for f in facts}
    out: list[AssumptionRecord] = []
    for fact in sorted(facts, key=lambda f: fact_number(f.id)):
        if fact.status != "active" or not fact.supersedes:
            continue
        chain = _ancestors(fact, by_id)
        if not chain or all(_same_text(fact.statement, f.statement) for f in chain):
            continue  # nothing really changed
        first = chain[0]
        slots = _effect_slots([fact, *chain])
        out.append(
            _record(
                claim=catalog.t(
                    "brief.v2.correction.replaced",
                    lang,
                    used=snippet(fact.statement),
                    first=snippet(first.statement),
                ),
                reason=catalog.t("brief.v2.correction.reason", lang),
                basis="correction",
                impact="high" if touches_important_slot(slots, catalog) else "low",
                theme=theme_of(slots, catalog),
                what_changes=catalog.t("brief.v2.correction.what_changes", lang),
                provenance=Provenance(type="assumed", ref=fact.id),
            )
        )
    out.extend(_disputed(facts, catalog, lang))
    return out


def _disputed(facts: Sequence[Fact], catalog: Catalog, lang: str) -> list[AssumptionRecord]:
    """Disputed facts, grouped by the slot they touch (else their first topic); a group of two or more says what
    was said both ways, a lone one asks for confirmation."""
    groups: dict[str, list[Fact]] = {}
    for fact in sorted(facts, key=lambda f: fact_number(f.id)):
        if fact.status != "disputed" or fact.claim_id:
            continue  # a conflict inside a document is paired by its claims (``document_facts.conflict_assumptions``)
        slots = _effect_slots([fact])
        key = f"slot:{slots[0]}" if slots else f"topic:{fact.topics[0] if fact.topics else fact.id}"
        groups.setdefault(key, []).append(fact)
    out: list[AssumptionRecord] = []
    for members in groups.values():
        first, newest = members[0], members[-1]
        slots = _effect_slots(members)
        claim = (
            catalog.t(
                "brief.v2.correction.disputed",
                lang,
                first=snippet(first.statement),
                used=snippet(newest.statement),
            )
            if len(members) > 1
            else catalog.t("brief.v2.correction.disputed_one", lang, first=snippet(first.statement))
        )
        out.append(
            _record(
                claim=claim,
                reason=catalog.t("brief.v2.correction.reason", lang),
                basis="correction",
                impact="high" if touches_important_slot(slots, catalog) else "low",
                theme=theme_of(slots, catalog),
                what_changes=catalog.t("brief.v2.correction.what_changes", lang),
                provenance=Provenance(type="assumed", ref=newest.id),
            )
        )
    return out


# ------------------------------------------------------------------------------------------ review
ASSUMPTION_KINDS = frozenset({"conflicting", "unrealistic", "dependency", "plausibility"})
"""The finding kinds that become assumptions; ``vague`` and ``undefined`` stay non-blocking open questions."""


def review_from_findings(
    findings: Sequence[ReviewFinding], catalog: Catalog, language: str
) -> list[AssumptionRecord]:
    """Review findings that do not add up, as assumptions the person decides (at most five)."""
    lang = lang_of(language)
    out: list[AssumptionRecord] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.kind not in ASSUMPTION_KINDS:
            continue
        text = snippet(finding.text, 200)
        claim = catalog.t(f"brief.v2.review.{finding.kind}", lang, text=text)
        key = claim_key(claim)
        if not text or key in seen:
            continue
        seen.add(key)
        slots = [finding.slot_id] if finding.slot_id else []
        out.append(
            _record(
                claim=claim,
                reason=catalog.t("brief.v2.review.reason", lang),
                basis="review",
                impact="high" if touches_important_slot(slots, catalog) else "low",
                theme=theme_of(slots, catalog),
                what_changes=catalog.t("brief.v2.review.what_changes", lang),
                provenance=Provenance(type="assumed", ref=finding.slot_id or ""),
            )
        )
        if len(out) >= MAX_REVIEW:
            break
    return out
