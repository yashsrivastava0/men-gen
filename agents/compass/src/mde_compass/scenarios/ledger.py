"""What a finished planner session leaves behind, and the section-6 measures of build prompt 02 over it.

``load_session`` reads the interview memory (Q&A ledger, facts, batches) and the Brief out of the store of a
finished ``Conversation``. The measure functions are pure over that snapshot, so the same numbers back the
scenario checks, the property tests and the persona report:

* project-term share of the non-universal questions (target at least 90%),
* repeated questions (target zero),
* fact coverage (every active fact is in ``domain_facts``, cited by a Brief item, or ``not_used``; target 100%),
* quote verification (no Locked item rests on a fact whose quote was not found in its source),
* fallbacks (target zero) and the question budget of the session.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from mde_compass.catalog import Catalog, load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import BatchRecord, Fact, QaRecord
from mde_compass.engine import facts as facts_mod
from mde_compass.engine import terms as terms_mod
from mde_compass.engine.language import normalize
from mde_compass.engine.question_validators import is_universal
from mde_compass.testing import Conversation

FCT_REF = re.compile(r"^FCT-\d{3,}$")
SHOWN_STATUSES = ("answered", "skipped", "you_decide", "assumed", "superseded")


@dataclass
class SessionData:
    """A snapshot of one finished session's memory."""

    brief: Brief
    qa: list[QaRecord]  # the newest version of every question
    qa_versions: list[QaRecord]  # every version
    facts: list[Fact]
    batches: list[BatchRecord]
    user_texts: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------------------------------- views
    @property
    def active_facts(self) -> list[Fact]:
        return [f for f in self.facts if f.status == "active"]

    def shown_cards(self) -> list[Card]:
        """Questions the person saw (a question dropped after it was shown still counts as shown)."""
        return [q.card for q in self.qa if q.asked_at is not None or q.status in SHOWN_STATUSES]

    def planned_cards(self) -> list[Card]:
        return [c for c in self.shown_cards() if c.frame == "planned"]


async def load_session(conv: Conversation, tenant: str = "t1") -> SessionData:
    """Read the ledgers and the Brief of a conversation from its store."""
    svc = conv.service
    async with svc.store.begin(tenant) as uow:
        qa = await uow.qa.list(conv.session_id)
        versions = await uow.qa.list(conv.session_id, latest_only=False)
        facts = await uow.facts.list(conv.session_id)
        batches = await uow.batches.list(conv.session_id)
        brief = (await uow.briefs.get(conv.session_id)).brief
        history = await uow.events.list_history(conv.session_id, after_idx=0, limit=1000)
    texts: list[str] = []
    for ev in history.items:
        if ev.direction != "in":
            continue
        for key in ("text", "custom_text"):
            value = ev.payload.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
    return SessionData(brief, qa, versions, facts, batches, texts)


# ------------------------------------------------------------------------------------------ the walk
def fact_refs(node: Any) -> set[str]:
    """Every ``FCT-###`` string in a JSON-like value (a Brief item, a section, a whole Brief), except inside the
    ``history`` of an item: an earlier value keeps the evidence it had and is no longer in force."""
    found: set[str] = set()
    if isinstance(node, str):
        if FCT_REF.match(node):
            found.add(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if (
                key != "history"
            ):  # earlier values of an item keep the evidence they had: they are not in force
                found |= fact_refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= fact_refs(value)
    return found


def brief_json(brief: Brief) -> dict[str, Any]:
    return brief.model_dump(mode="json")


def _content(brief: Brief) -> dict[str, Any]:
    """The Brief without its internal audit block (``meta``): what consumers and the coverage check look at."""
    data = brief_json(brief)
    data.pop("meta", None)
    return data


def locked_items(node: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every Brief item whose tier is Locked, with its path."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if node.get("tier") == "Locked":
            found.append((path or "<root>", node))
        for key, value in node.items():
            if key in ("meta", "history"):  # earlier values keep the tier and evidence they had
                continue
            found.extend(locked_items(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found.extend(locked_items(item, f"{path}[{i}]"))
    return found


# ---------------------------------------------------------------------------------------- the measures
def terms_of(data: SessionData) -> frozenset[str]:
    """The project's own words, as the engine collects them: the idea, the user's messages, the active facts."""
    idea = ""
    item = data.brief.idea.get("summary")
    if item is not None and isinstance(item.value, str):
        idea = item.value
    said = " ".join(f.quote for f in data.active_facts if f.quote)
    entities = [e for f in data.active_facts for e in f.entities]
    glossary = [g.term for g in data.brief.glossary]
    return terms_mod.project_terms(
        f"{idea} {' '.join(data.user_texts)} {said}", data.active_facts, glossary, extra=entities
    )


def project_term_report(
    cards: list[Card], terms: frozenset[str], catalog: Catalog | None = None
) -> tuple[int, int, list[str]]:
    """(questions with a project term, non-universal questions, the ids of those without one)."""
    cat = catalog or load_catalog()
    scored = [c for c in cards if not is_universal(c, cat)]
    missing = [
        c.id for c in scored if not terms_mod.contains_project_term(_plain(c.question), terms)
    ]
    return len(scored) - len(missing), len(scored), missing


def _plain(text: str) -> str:
    return text.replace("[mock]", "").strip()


def repeated_questions(cards: list[Card]) -> list[tuple[str, str]]:
    """Pairs of shown questions with the same normalized wording or intent. A follow-up refining its parent is
    not a repeat of it."""
    out: list[tuple[str, str]] = []
    seen_text: dict[str, Card] = {}
    seen_intent: dict[str, Card] = {}
    for card in cards:
        text = normalize(_plain(card.question))
        intent = normalize(_plain(card.intent))
        for index, key in ((seen_text, text), (seen_intent, intent)):
            other = index.get(key) if key else None
            is_repeat = (
                other is not None
                and other.id != card.id
                and other.id
                != card.parent_card_id  # a follow-up refines its parent: not a repeat of it,
                and card.id != other.parent_card_id  # whichever of the two the ledger lists first
            )
            if other is not None and is_repeat and (other.id, card.id) not in out:
                out.append((other.id, card.id))
            if key:
                index.setdefault(key, card)
    return out


@dataclass(frozen=True)
class Coverage:
    active: int
    covered: int
    unreferenced: tuple[str, ...]
    stale: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return self.covered / self.active if self.active else 1.0


def coverage(data: SessionData) -> Coverage:
    """Fact coverage: every active fact is placed in ``domain_facts``, cited by some Brief item, or ``not_used``."""
    refs = fact_refs(_content(data.brief))
    report = facts_mod.fact_coverage(data.facts, refs)
    live = [f for f in data.facts if f.status == "active"]
    covered = [f for f in live if f.id in refs]
    return Coverage(
        active=len(live),
        covered=len(covered),
        unreferenced=report.unreferenced,
        stale=report.stale,
        unknown=report.unknown,
    )


def quote_violations(data: SessionData) -> list[str]:
    """Locked Brief items whose supporting fact has no verified quote, or whose FCT evidence does not resolve to an
    existing active fact. Empty is the target (quote verification 100%)."""
    by_id = {f.id: f for f in data.facts}
    problems: list[str] = []
    for path, item in locked_items(_content(data.brief)):
        refs = {
            str(p.get("ref", ""))
            for p in item.get("provenance", [])
            if isinstance(p, dict) and FCT_REF.match(str(p.get("ref", "")))
        }
        for ref in sorted(refs):
            fact = by_id.get(ref)
            if fact is None:
                problems.append(f"{path}: {ref} does not exist")
            elif not fact.supports_locked():
                why = "unverified quote" if fact.unverified_quote else f"status {fact.status}"
                problems.append(f"{path}: Locked on {ref} ({why})")
    return problems


def dangling_refs(data: SessionData) -> list[str]:
    """Brief evidence references (``FCT-###``) that do not resolve to an existing fact."""
    known = {f.id for f in data.facts}
    return sorted(fact_refs(_content(data.brief)) - known)


def fallback_count(data: SessionData) -> int:
    """Fallbacks used: the audit block's counter, or the flagged batches and cards if that is larger."""
    block = data.brief.meta.interview
    counted = block.fallback_count if block is not None else 0
    flagged = sum(1 for b in data.batches if b.used_fallback)
    cards = sum(1 for c in data.shown_cards() if c.fallback)
    return max(counted, flagged, cards)


def question_count(data: SessionData) -> int:
    """Questions the person was shown (the budget's unit; follow-ups included)."""
    return len(data.shown_cards())


def needle_in_brief(brief: Brief, needle: str) -> bool:
    """The planted text (case and spacing insensitive) occurs somewhere in the Brief's content."""
    return normalize(needle) in normalize(json.dumps(_content(brief), ensure_ascii=False))


__all__ = [
    "Coverage",
    "SessionData",
    "brief_json",
    "coverage",
    "dangling_refs",
    "fact_refs",
    "fallback_count",
    "load_session",
    "locked_items",
    "needle_in_brief",
    "project_term_report",
    "question_count",
    "quote_violations",
    "repeated_questions",
    "terms_of",
]
