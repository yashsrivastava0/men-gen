"""Document claims to facts (ADR 0016; the claim-to-fact logic ``Interview.after_ingest`` calls). Pure: no I/O, no model.

An uploaded document (a long PRD, an RFP, a requirements sheet) is read into claims by ``cmp.read_chunk`` and
``cmp.merge_claims``. This module turns them into the facts of the ledger so everything the document said reaches the
Brief, and nothing the document only CLAIMS is treated as something the person said:

* each claim becomes a fact typed and topic-tagged from its statement type (``document_map.yaml``: ``fact_types``) and
  from the heading or sheet it sits under, so the kinds file and the compile routing can place it;
* a claim that needs confirming becomes a fact with confidence BELOW 0.7, and such a document fact can never support a
  Locked item (``Fact.supports_locked``) until the person confirms it (``confirm_card`` and ``apply_confirmation``);
* duplicate claims merge into one fact, keeping the better quote;
* CONFLICTING claims are kept as disputed facts (never compiled, never lost) and every conflicting pair becomes a
  ``document`` assumption the person decides, instead of being counted and forgotten;
* a feature or role is named by the model's short name when the claim has one, else by the statement cut at a WORD
  boundary (at most 80 characters).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_compass.catalog.loader import Catalog, load_catalog
from mde_compass.domain.brief import AssumptionRecord
from mde_compass.domain.cards import Card, CardOption, CustomInput, YouDecide
from mde_compass.domain.claims import Claim, Statement
from mde_compass.domain.common import Provenance
from mde_compass.domain.interview import DOCUMENT_LOCK_CONFIDENCE, Fact
from mde_compass.engine.compile_apply import norm_name
from mde_compass.engine.derived_assumptions import (
    PLACEHOLDER_ID,
    lang_of,
    snippet,
    theme_of,
    touches_important_slot,
)

MAP_PATH = Path(__file__).with_name("document_map.yaml")
NAME_CHARS = 80
NEEDS_CONFIRMING_MAX = 0.65
"""Confidence of a fact read from a claim that needs confirming: always below ``DOCUMENT_LOCK_CONFIDENCE``."""
CONFIRMED_CONFIDENCE = 0.9
MAX_CONFIRM_OPTIONS = 15
LABEL_CHARS = 100
TOPIC_CHARS = 40
FACT_STATEMENT_CHARS = 1000
FACT_QUOTE_CHARS = 500
CONFIRM_FRAME = "document_confirm"
BASIS = "document"  # the ``AssumptionRecord.basis`` of a conflict inside a document
_WORDS = re.compile(r"[^\wऀ-ॿ]+|_+")


@lru_cache(maxsize=1)
def _fact_types() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    return dict(raw["fact_types"])  # type: ignore[no-any-return]


# ------------------------------------------------------------------------------------------- names
def cut_at_word(text: str, limit: int = NAME_CHARS) -> str:
    """``text`` on one line, cut at a WORD boundary to at most ``limit`` characters (never mid-word, no ellipsis:
    this is a name, not a sentence)."""
    line = " ".join(text.split())
    if len(line) <= limit:
        return line.rstrip(" ,;:.")
    cut = line[:limit]
    if line[limit] != " ":  # the cut falls inside a word: back up to the last space
        head = cut.rsplit(" ", 1)[0]
        cut = head if head else cut
    return cut.rstrip(" ,;:.")


def claim_name(statement: Statement) -> str:
    """The name of the feature or role a claim describes: the model's short name when the claim has one, else the
    statement cut at a word boundary to at most 80 characters."""
    short = " ".join(str(getattr(statement, "name", "") or "").split())
    return cut_at_word(short) if short else cut_at_word(statement.text)


# ------------------------------------------------------------------------------------- types, topics
def pointer_topics(pointer: str) -> list[str]:
    """Topic words from where a statement sits: ``heading: Payments`` gives ``payments``, ``sheet Roles!A1:D9`` gives
    ``roles``; a bare page or row range gives none."""
    out: list[str] = []
    for part in pointer.split(";"):
        part = part.strip()
        if part.lower().startswith("heading:"):
            name = part.split(":", 1)[1]
        elif part.lower().startswith("sheet "):
            name = part[6:].split("!", 1)[0]
        else:
            continue
        slug = "_".join(w for w in _WORDS.split(name.casefold()) if w)[:TOPIC_CHARS].strip("_")
        if slug and slug not in out:
            out.append(slug)
    return out


def fact_type_and_topics(statement: Statement) -> tuple[str, list[str]]:
    """The fact type and topic words of a statement (``rule_or_number`` is a number when it carries a digit)."""
    rule = _fact_types().get(statement.type) or {"type": "context", "topics": []}
    if "with_digit" in rule and any(ch.isdigit() for ch in statement.text):
        rule = rule["with_digit"]
    topics = [*rule.get("topics", []), *pointer_topics(statement.pointer)]
    return str(rule["type"]), list(dict.fromkeys(topics))


# ------------------------------------------------------------------------------------------- quotes
def _tokens(text: str) -> set[str]:
    return {norm for w in _WORDS.split(text.casefold()) if w and (norm := norm_name(w))}


def quote_score(statement: Statement) -> tuple[float, int]:
    """How well a claim's quote carries its statement: the share of the statement's words the quote holds, then the
    length (up to 300 characters). Higher is better; used to keep the better quote of two duplicates."""
    wanted = _tokens(statement.text)
    have = _tokens(statement.quote)
    share = len(wanted & have) / len(wanted) if wanted else 0.0
    return round(share, 4), min(len(statement.quote.strip()), 300)


# ------------------------------------------------------------------------------------- the facts
@dataclass
class DocumentFacts:
    facts: list[Fact] = field(
        default_factory=list
    )  # new facts, in claim order (ids from ``start_number``)
    merged: int = 0  # duplicate claims folded into another fact
    needs_confirming: int = 0  # facts below the confirmation confidence
    disputed: int = 0  # facts kept as disputed (conflicting claims)
    conflicts: list[tuple[str, str]] = field(default_factory=list)  # claim id pairs, each once


def _group_key(claim: Claim) -> str:
    return f"{claim.statement.type}|{norm_name(claim.statement.text)}"


def facts_from_claims(
    claims: Iterable[Claim],
    *,
    source_id: str,
    start_number: int,
    now: datetime,
    slot_ids: Collection[str] = (),
) -> DocumentFacts:
    """The facts a document's claims add to the ledger. ``start_number`` is the number of the first new id.

    ``used`` and ``needs_confirming`` claims become ACTIVE facts (the latter below 0.7: a claim, not a statement of the
    person); ``conflicting`` claims become DISPUTED facts; ``duplicate`` claims fold into the fact of the claim they
    repeat (the better quote wins); ``ignored`` claims add nothing.
    """
    listed = list(claims)
    result = DocumentFacts()
    groups: dict[str, list[Claim]] = {}
    for claim in listed:
        if claim.status in ("used", "needs_confirming", "duplicate"):
            groups.setdefault(_group_key(claim), []).append(claim)
    number = start_number
    done: set[str] = set()
    for claim in listed:
        if claim.status == "conflicting":
            result.facts.append(
                _fact(
                    claim,
                    claim,
                    number,
                    source_id,
                    now,
                    slot_ids,
                    status="disputed",
                    confidence=claim.statement.confidence,
                )
            )
            result.disputed += 1
            number += 1
            continue
        if claim.status not in ("used", "needs_confirming", "duplicate"):
            continue
        key = _group_key(claim)
        if key in done:
            continue
        done.add(key)
        members = groups[key]
        head = next((c for c in members if c.status != "duplicate"), members[0])
        best = max(members, key=lambda c: quote_score(c.statement))
        # one repeated statement is corroborated: it keeps the highest confidence of the group, and needs
        # confirming only while every copy does
        confirmed = any(c.status == "used" for c in members)
        confidence = max(c.statement.confidence for c in members)
        if not confirmed:
            confidence = min(confidence, NEEDS_CONFIRMING_MAX)
        result.facts.append(
            _fact(
                head,
                best,
                number,
                source_id,
                now,
                slot_ids,
                status="active",
                confidence=confidence,
                used=confirmed,
            )
        )
        result.merged += len(members) - 1
        result.needs_confirming += 1 if confidence < DOCUMENT_LOCK_CONFIDENCE else 0
        number += 1
    seen: set[tuple[str, str]] = set()
    for claim in listed:
        if claim.status != "conflicting":
            continue
        for other in claim.conflicts_with:
            pair = (min(claim.id, other), max(claim.id, other))
            if pair not in seen:
                seen.add(pair)
                result.conflicts.append(pair)
    return result


def _fact(
    head: Claim,
    best: Claim,
    number: int,
    source_id: str,
    now: datetime,
    slot_ids: Collection[str],
    *,
    status: str,
    confidence: float,
    used: bool = False,
) -> Fact:
    """A fact from ``head`` (its statement, type and pointer) with ``best``'s quote and claim id."""
    st = head.statement
    fact_type, topics = fact_type_and_topics(st)
    pointer = best.statement.pointer or st.pointer
    hint = head.slot_hint
    return Fact.model_validate(
        {
            "id": f"FCT-{number:03d}",
            "type": fact_type,
            "statement": st.text.strip()[:FACT_STATEMENT_CHARS] or "(empty)",
            "topics": topics,
            "consumers": ["all"],
            "confidence": round(min(max(confidence, 0.0), 1.0), 4),
            "status": status,
            "origin": "document",
            "source_ref": f"{source_id}:{pointer}" if pointer else source_id,
            "claim_id": best.id,
            "quote": best.statement.quote.strip()[:FACT_QUOTE_CHARS],
            "unverified_quote": False,
            # a used claim that maps to a need answers it: the interview must not ask it again (GAP-7)
            "aspects": [f"{hint}:*"] if used and status == "active" and hint in slot_ids else [],
            "created_at": now,
            "updated_at": now,
        }
    )


# ----------------------------------------------------------------------- conflicts as assumptions
def conflict_assumptions(
    claims: Sequence[Claim], catalog: Catalog, language: str
) -> list[AssumptionRecord]:
    """One assumption per conflicting pair of claims ("your document says both X and Y; we used neither until you
    decide"). ``high`` impact only when the touched slot has impact 4 or more or must be confirmed (it then blocks
    Gate 1). Merge them with ``assumptions.merge_derived(brief, fresh, ("document",))``."""
    lang = lang_of(language)
    by_id = {c.id: c for c in claims}
    out: list[AssumptionRecord] = []
    seen: set[tuple[str, str]] = set()
    for claim in sorted(claims, key=lambda c: c.id):
        if claim.status != "conflicting":
            continue
        for other_id in sorted(claim.conflicts_with):
            other = by_id.get(other_id)
            pair = (min(claim.id, other_id), max(claim.id, other_id))
            if other is None or pair in seen:
                continue
            seen.add(pair)
            first, second = sorted((claim, other), key=lambda c: c.id)
            slots = [s for s in (first.slot_hint, second.slot_hint) if s]
            out.append(
                AssumptionRecord(
                    id=PLACEHOLDER_ID,
                    claim=catalog.t(
                        "brief.v2.document.conflict",
                        lang,
                        first=snippet(first.statement.text),
                        second=snippet(second.statement.text),
                    ),
                    reason=catalog.t("brief.v2.document.conflict.reason", lang),
                    basis=BASIS,
                    impact="high" if touches_important_slot(slots, catalog) else "low",
                    theme=theme_of(slots, catalog),
                    slot_id=None,
                    decision="proposed",
                    what_changes=catalog.t("brief.v2.document.conflict.what_changes", lang),
                    tier="Default",
                    provenance=[Provenance(type="assumed", ref=first.id)],
                    confidence=0.5,
                    status="assumed",
                )
            )
    return out


# --------------------------------------------------------------------------- confirm what we read
def needing_confirmation(facts: Iterable[Fact]) -> list[Fact]:
    """Active document facts below the confirmation confidence, in id order."""
    return sorted(
        (
            f
            for f in facts
            if f.origin == "document"
            and f.status == "active"
            and f.confidence < DOCUMENT_LOCK_CONFIDENCE
        ),
        key=lambda f: f.id,
    )


def confirm_card(
    facts: Iterable[Fact],
    *,
    language: str = "en",
    card_id: str = "Q-000",
    catalog: Catalog | None = None,
) -> Card:
    """ONE multi-item ``pick_many`` card, "We read these in your document: tick what is right", with one option per fact
    that still needs confirming (at most 15, in id order). Ticked means confirmed, unticked means the person edits it.
    Nothing is pre-ticked (silence never confirms). The card is planner-origin with no need ids, so it is not charged
    to any need. Raises ``ValueError`` when no fact needs confirming (check ``needing_confirmation`` first)."""
    cat = catalog or load_catalog()
    lang = lang_of(language)
    pending = needing_confirmation(facts)[:MAX_CONFIRM_OPTIONS]
    if not pending:
        raise ValueError("no fact needs confirming")
    return Card(
        id=card_id,
        frame=CONFIRM_FRAME,
        slots=[],
        type="pick_many",
        question=cat.t("brief.v2.document.confirm.question", lang),
        why_we_ask=cat.t("brief.v2.document.confirm.why", lang),
        options=[
            CardOption(
                id=f.id,
                label=snippet(f.statement, LABEL_CHARS),
                basis="your_document",
                recommended=False,
                ticked=False,
                description=f.source_ref,
            )
            for f in pending
        ],
        custom_input=CustomInput(
            enabled=True, placeholder=cat.t("brief.v2.document.confirm.placeholder", lang)
        ),
        you_decide=YouDecide(enabled=False),
        language=language,
        intent="confirm what was read from the document",
        origin="planner",
        need_ids=[],
    )


def apply_confirmation(
    facts: Iterable[Fact], ticked_ids: Collection[str], *, now: datetime | None = None
) -> list[Fact]:
    """The facts the person ticked, raised to confidence 0.9 (confirmed). Only facts that were waiting for
    confirmation change; the result holds just those, as copies (the input is untouched)."""
    ticked = set(ticked_ids)
    changed: list[Fact] = []
    for fact in needing_confirmation(facts):
        if fact.id in ticked:
            update: dict[str, Any] = {"confidence": CONFIRMED_CONFIDENCE}
            if now is not None:
                update["updated_at"] = now
            changed.append(fact.model_copy(update=update))
    return changed
