"""The interview memory of one turn: what was loaded, and what this turn adds or changes.

A turn is load, think, commit (ADR 0004: no transaction across a model call). So the handlers work on this
in-memory working set and ``Service._commit`` writes it in the turn's single unit of work. Facts are never
deleted: a correction replaces a fact's state (``superseded``, ``disputed``, ``not_used``), it does not remove it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from mde_compass.domain.interview import (
    BatchRecord,
    Fact,
    InterviewSummary,
    PlannerMode,
    QaRecord,
)

_NUMBER = re.compile(r"(\d+)$")


def _max_number(ids: list[str]) -> int:
    best = 0
    for value in ids:
        m = _NUMBER.search(value)
        if m:
            best = max(best, int(m.group(1)))
    return best


@dataclass
class InterviewMemory:
    facts: list[Fact] = field(default_factory=list)
    qa: list[QaRecord] = field(default_factory=list)  # the newest version of every question
    batches: list[BatchRecord] = field(default_factory=list)
    summary: InterviewSummary | None = None
    # what this turn writes
    new_fact_ids: list[str] = field(default_factory=list)
    changed_fact_ids: set[str] = field(default_factory=set)
    qa_writes: dict[tuple[str, int], QaRecord] = field(default_factory=dict)
    batch_writes: list[BatchRecord] = field(default_factory=list)
    summary_write: InterviewSummary | None = None
    # counters kept for the audit block
    fallbacks: int = 0

    # ------------------------------------------------------------------------------------------- facts
    def next_fact_id(self) -> str:
        return f"FCT-{_max_number([f.id for f in self.facts]) + 1:03d}"

    def next_batch_id(self) -> str:
        return f"BAT-{_max_number([b.id for b in self.batches]) + 1:03d}"

    def active_facts(self) -> list[Fact]:
        return [f for f in self.facts if f.status == "active"]

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def add_fact(self, fact: Fact) -> Fact:
        self.facts.append(fact)
        self.new_fact_ids.append(fact.id)
        return fact

    def update_fact(self, fact: Fact) -> Fact:
        """Replace a fact's state (never its id, statement, quote or creation time: the store refuses that)."""
        for i, old in enumerate(self.facts):
            if old.id == fact.id:
                self.facts[i] = fact
                if fact.id not in self.new_fact_ids:
                    self.changed_fact_ids.add(fact.id)
                return fact
        raise KeyError(fact.id)

    # --------------------------------------------------------------------------------------------- Q&A
    def put_qa(self, record: QaRecord) -> QaRecord:
        for i, old in enumerate(self.qa):
            if old.card_id == record.card_id:
                self.qa[i] = record
                break
        else:
            self.qa.append(record)
        self.qa_writes[(record.card_id, record.version)] = record
        return record

    def supersede_qa(self, old: QaRecord, new: QaRecord) -> QaRecord:
        """An edited answer: the old version is kept (status ``superseded``), the new one becomes current."""
        self.qa_writes[(old.card_id, old.version)] = old.model_copy(update={"status": "superseded"})
        return self.put_qa(new)

    def qa_for(self, card_id: str) -> QaRecord | None:
        return next((q for q in self.qa if q.card_id == card_id), None)

    def open_qa(self) -> list[QaRecord]:
        return [q for q in self.qa if q.status == "open"]

    def asked_count(self, *, origins: tuple[str, ...] | None = None) -> int:
        """Questions that were put to the person (shown), not those still waiting in a batch."""
        return sum(
            1
            for q in self.qa
            if q.status not in ("open", "dropped", "superseded")
            and (origins is None or q.card.origin in origins)
        )

    # ----------------------------------------------------------------------------------------- batches
    def add_batch(self, record: BatchRecord) -> BatchRecord:
        self.batches.append(record)
        self.batch_writes.append(record)
        return record

    def find_batch(self, mode: PlannerMode, inputs_hash: str) -> BatchRecord | None:
        return next(
            (b for b in self.batches if b.mode == mode and b.inputs_hash == inputs_hash), None
        )

    def set_summary(self, summary: InterviewSummary, now: datetime) -> None:
        self.summary = summary.model_copy(update={"updated_at": now})
        self.summary_write = self.summary

    @property
    def dirty(self) -> bool:
        return bool(
            self.new_fact_ids
            or self.changed_fact_ids
            or self.qa_writes
            or self.batch_writes
            or self.summary_write
        )
