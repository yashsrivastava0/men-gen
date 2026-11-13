"""Fact ledger helpers (build prompt 02, section 3; ADR 0015). Pure functions over ``Fact``; nothing is stored here.

* ``supersede`` / ``dispute`` / ``mark_not_used`` return updated copies and never delete a fact: older facts stay in
  the ledger with a new status so history, undo and the audit trail keep working.
* ``digest`` is the one-line-per-fact view sent with every interview call, grouped by topic and held to a token
  budget (``digest_report`` also says what was dropped). ``digest_line`` is the line format shared with the full
  ledger of ``context_pack``.
* ``fact_coverage`` is the "nothing wasted, nothing lost" check: every active fact must be referenced by a Brief item
  or be marked ``not_used`` with a reason. It also reports references that point at facts that are not active or do
  not exist, because a Brief item's evidence has to resolve to a live fact.

Timestamps come in as parameters (``now``); these functions never read a clock.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from mde_compass.domain.interview import FACT_ID_RE, Fact

GENERAL_TOPIC = "general"
DEFAULT_CHARS_PER_TOKEN = 4


def fact_number(fact_id: str) -> int:
    """The numeric part of ``FCT-014`` (ids are never reused, so this is also the creation order)."""
    try:
        return int(fact_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _seq(fact: Fact) -> int:
    return fact_number(fact.id)


def _one_line(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------------------------------- status changes
def with_contributor(fact: Fact, contributor_id: str | None) -> Fact:
    """``fact`` with every effect stamped with who said it (enterprise sessions). No contributor: unchanged."""
    if not contributor_id or not fact.effects:
        return fact
    effects = [e.model_copy(update={"contributor_id": contributor_id}) for e in fact.effects]
    return fact.model_copy(update={"effects": effects})


def contributor_of(fact: Fact) -> str | None:
    """Who said it: the contributor id stamped on the fact's effects, if any."""
    return next((e.contributor_id for e in fact.effects if e.contributor_id), None)


def supersede(old: Fact, new: Fact, *, now: datetime) -> tuple[Fact, Fact]:
    """``old`` becomes ``superseded``; ``new.supersedes`` gains ``old.id``. Both are copies; nothing is deleted."""
    if old.id == new.id:
        raise ValueError("a fact cannot supersede itself")
    old_out = old.model_copy(update={"status": "superseded", "updated_at": now})
    new_out = new.model_copy(
        update={
            "supersedes": list(dict.fromkeys([*new.supersedes, old.id])),
            "updated_at": now,
            "created_at": new.created_at or now,
        }
    )
    return old_out, new_out


def dispute(a: Fact, b: Fact, *, now: datetime) -> tuple[Fact, Fact]:
    """Two facts that conflict without a clear correction: both become ``disputed`` (a clarification follows).

    A fact that is already ``superseded`` or ``not_used`` is left as it is: it cannot be revived by a dispute.
    """

    def mark(fact: Fact) -> Fact:
        if fact.status in ("active", "disputed"):
            return fact.model_copy(update={"status": "disputed", "updated_at": now})
        return fact

    return mark(a), mark(b)


def mark_not_used(fact: Fact, reason: str, now: datetime) -> Fact:
    """Small talk and other statements no Brief item needs: kept, with a reason, never deleted."""
    clean = reason.strip()
    if not clean:
        raise ValueError("a fact marked not_used needs a reason")
    if fact.status == "superseded":
        raise ValueError(f"{fact.id} is superseded and cannot be marked not_used")
    return fact.model_copy(
        update={"status": "not_used", "not_used_reason": clean, "updated_at": now}
    )


# ---------------------------------------------------------------------------------------------- lines
def digest_line(fact: Fact) -> str:
    """``FCT-014 [rule|pricing] statement (from Q-03)``: id, type, topics, statement, where it came from."""
    topics = ",".join(t.strip() for t in fact.topics if t.strip())
    tag = f"{fact.type}|{topics}" if topics else fact.type
    source = (fact.qa_card_id or fact.source_ref).strip()
    suffix = f" (from {source})" if source else ""
    return f"{fact.id} [{tag}] {_one_line(fact.statement)}{suffix}"


def topic_of(fact: Fact) -> str:
    """The group a fact is listed under: its first topic, else ``general``."""
    for topic in fact.topics:
        if topic.strip():
            return topic.strip()
    return GENERAL_TOPIC


def group_by_topic(facts: Iterable[Fact]) -> list[tuple[str, list[Fact]]]:
    """Facts grouped by topic: groups alphabetical (``general`` last), facts by id within a group."""
    groups: dict[str, list[Fact]] = {}
    for fact in facts:
        groups.setdefault(topic_of(fact), []).append(fact)
    order = sorted(groups, key=lambda t: (t == GENERAL_TOPIC, t.casefold(), t))
    return [(topic, sorted(groups[topic], key=_seq)) for topic in order]


def _render(groups: Sequence[tuple[str, Sequence[Fact]]]) -> str:
    parts: list[str] = []
    for topic, members in groups:
        parts.append(f"# {topic}")
        parts.extend(digest_line(f) for f in members)
    return "\n".join(parts)


# --------------------------------------------------------------------------------------------- digest
@dataclass(frozen=True)
class DigestResult:
    text: str
    kept: tuple[str, ...]  # fact ids shown
    dropped: tuple[
        str, ...
    ]  # fact ids left out because of the budget (the rolling summary covers them)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)


def _is_relevant(fact: Fact, relevant: frozenset[str]) -> bool:
    return any(t.strip().casefold() in relevant for t in fact.topics)


def _omitted_note(count: int) -> str:
    return f"(+{count} more facts omitted, see the summary)"


def digest_report(
    facts: Iterable[Fact],
    *,
    topics: Iterable[str] = (),
    budget_tokens: int,
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
) -> DigestResult:
    """The digest of the ACTIVE facts within ``budget_tokens`` and which facts did not fit.

    All active facts are listed when they fit. Otherwise facts are ranked: first those of the relevant ``topics``,
    then by confidence (high first), then newest first; they are taken in that order while they fit and the rest
    are dropped. If even the relevant facts do not fit, the highest-ranked of them are kept: the output never
    exceeds the budget. A final note says how many facts were left out (only if it fits).
    """
    active = sorted((f for f in facts if f.status == "active"), key=_seq)
    limit = max(0, budget_tokens) * max(1, chars_per_token)
    full = _render(group_by_topic(active))
    if len(full) <= limit:
        return DigestResult(full, tuple(f.id for f in active), ())

    relevant = frozenset(t.strip().casefold() for t in topics if t.strip())
    ranked = sorted(active, key=lambda f: (not _is_relevant(f, relevant), -f.confidence, -_seq(f)))
    note_reserve = len(_omitted_note(len(active))) + 1
    room = limit - note_reserve if limit > note_reserve else limit
    chosen: list[Fact] = []
    opened: set[str] = set()
    parts = 0
    used = 0
    for fact in ranked:
        topic = topic_of(fact)
        new_parts = [digest_line(fact)]
        if topic not in opened:
            new_parts.insert(0, f"# {topic}")
        # joining k parts costs k - 1 newlines: adding n parts costs n of them, or n - 1 into an empty text
        cost = sum(len(p) for p in new_parts) + len(new_parts) - (1 if parts == 0 else 0)
        if used + cost <= room:
            chosen.append(fact)
            opened.add(topic)
            parts += len(new_parts)
            used += cost
    text = _render(group_by_topic(chosen))
    kept = {f.id for f in chosen}
    dropped = tuple(f.id for f in active if f.id not in kept)
    note = _omitted_note(len(dropped))
    if dropped and text and len(text) + 1 + len(note) <= limit:
        text = f"{text}\n{note}"
    return DigestResult(text, tuple(f.id for f in active if f.id in kept), dropped)


def digest(
    facts: Iterable[Fact],
    *,
    topics: Iterable[str] = (),
    budget_tokens: int,
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
) -> str:
    """Text of :func:`digest_report` (use that when you need to know how many facts were dropped)."""
    return digest_report(
        facts, topics=topics, budget_tokens=budget_tokens, chars_per_token=chars_per_token
    ).text


# ------------------------------------------------------------------------------------------ coverage
@dataclass(frozen=True)
class CoverageReport:
    """Fact coverage at compile time and before Gate 1.

    ``used``: referenced by a Brief item. ``not_used``: marked ``not_used`` with a reason. ``unreferenced``: active
    facts no Brief item uses (and ``not_used`` facts without a reason): each is a defect. ``stale``: references to
    facts that are superseded or disputed. ``unknown``: references to fact ids that do not exist.
    """

    used: tuple[str, ...]
    not_used: tuple[str, ...]
    unreferenced: tuple[str, ...]
    stale: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.unreferenced or self.stale or self.unknown)

    @property
    def ratio(self) -> float:
        """Share of the facts that must be covered which are (1.0 when there is nothing to cover)."""
        covered = len(self.used) + len(self.not_used)
        total = covered + len(self.unreferenced)
        return covered / total if total else 1.0


def fact_coverage(facts: Iterable[Fact], referenced_ids: Collection[str]) -> CoverageReport:
    """Every active fact must be referenced by a Brief item or be ``not_used`` with a non-empty reason."""
    refs = frozenset(referenced_ids)
    by_id: dict[str, Fact] = {}
    for fact in facts:
        by_id[fact.id] = fact
    ordered = sorted(by_id.values(), key=_seq)
    used: list[str] = []
    not_used: list[str] = []
    unreferenced: list[str] = []
    for fact in ordered:
        if fact.status in ("active", "not_used"):
            if fact.id in refs:
                used.append(fact.id)
            elif fact.status == "not_used" and fact.not_used_reason.strip():
                not_used.append(fact.id)
            else:
                unreferenced.append(fact.id)
    stale = [f.id for f in ordered if f.status in ("superseded", "disputed") and f.id in refs]
    unknown = sorted(
        (r for r in refs if FACT_ID_RE.match(r) and r not in by_id),
        key=fact_number,
    )
    return CoverageReport(
        used=tuple(used),
        not_used=tuple(not_used),
        unreferenced=tuple(unreferenced),
        stale=tuple(stale),
        unknown=tuple(unknown),
    )
