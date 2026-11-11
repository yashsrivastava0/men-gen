"""Run state and evidence: everything a run knows, in plain objects.

Pages are stored with their text (the quote check reads it), queries with what they returned, and atoms
(extracted items) with their verification result. Nothing here does I/O; the worker and the director fill it in,
and ``assemble`` reads it. Quotes live only here and in ``research_evidence.json``; the public ``research.json``
never carries them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from mde_core.cancel import CancellationToken
from mde_research_agent.core.quotes import NormalizedText, normalize_with_offsets
from mde_research_agent.domain.events import TargetStatus
from mde_research_agent.domain.plan import Target

AtomStatus = Literal["verified", "rejected"]


@dataclass
class PageRecord:
    id: str
    url: str  # canonical
    final_url: str
    domain: str  # registrable domain
    source_class: str
    title: str
    text: str
    content_hash: str
    text_source: str  # provider | fetch
    published: date | None
    retrieved_on: date
    language: str | None = None
    _norm: NormalizedText | None = None

    @property
    def norm(self) -> NormalizedText:
        if self._norm is None:
            self._norm = normalize_with_offsets(self.text)
        return self._norm


@dataclass
class QueryRecord:
    id: str
    target_id: str
    round: int
    text: str
    variant: str
    language: str
    country: str | None
    provider: str
    exact: bool = False
    domain: str | None = None
    results: int = 0
    urls: list[str] = field(default_factory=list)
    cached: bool = False
    failed: str | None = None
    cost_micros: int = 0
    seed: bool = False
    applied: dict[str, str] = field(default_factory=dict)
    locale_weak: bool = False


@dataclass
class AtomRecord:
    id: str
    kind: str
    job: str
    target_id: str
    norm_key: str
    label: str
    claim: str
    quote: str
    impact: str
    published_on: date | None
    data: dict[str, Any]
    page_id: str
    round: int
    status: AtomStatus = "verified"
    reject_reason: str | None = None
    verdict: str | None = None  # supported | partial | not_supported | None (unchecked)
    flags: set[str] = field(default_factory=set)
    seed: bool = False
    query_ids: list[str] = field(default_factory=list)
    checked: bool = False
    about_region: str = "unknown"
    about_entity: str | None = None
    unsupported_fields: list[str] = field(default_factory=list)  # paths in ``data`` the checker could not back


@dataclass
class EntityRecord:
    name: str
    kind: str  # competitor | provider | reference
    domain: str | None = None  # the entity's own domain, once a page on it was read
    target_ids: set[str] = field(default_factory=set)


@dataclass
class TargetState:
    target: Target
    status: TargetStatus = "queued"
    queries: list[str] = field(default_factory=list)  # every query text sent for this topic
    followups: int = 0
    atoms: list[AtomRecord] = field(default_factory=list)
    page_ids: list[str] = field(default_factory=list)
    slots_required: int = 0
    slots_filled: int = 0
    cancel: CancellationToken = field(default_factory=CancellationToken)
    reason: str | None = (
        None  # why not covered: not_found | budget_stop | provider_down | user_cancelled
    )

    @property
    def verified(self) -> list[AtomRecord]:
        return [a for a in self.atoms if a.status == "verified"]


@dataclass
class DropCounts:
    """Why items did not reach the output. Every drop has a counted reason (R26): nothing is lost silently."""

    quote_not_found: int = 0
    number_not_in_quote: int = 0
    not_supported: int = 0
    unsupported_field: int = 0
    forbidden_pattern: int = 0
    claim_too_long: int = 0
    invalid: int = 0
