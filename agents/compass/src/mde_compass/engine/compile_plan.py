"""Planning the Brief compile (ADR 0016): which facts go to which call, what each call is told, and when a call can
be skipped. Pure functions over facts, slots and the Brief; no I/O, no model.

* ``route`` sends every active fact to the compile group(s) its type, kind or topics name (``fact_kinds.yaml``); a fact
  no group takes goes to ``general`` (it is still typed and placed).
* ``plan_calls`` cuts each group into batches that fit one call (a fact count and a character cap, so every wire
  piece stays well under the gateway's 20,000-character cap on untrusted input) and gives each batch a stable key and a
  digest of its facts.
* A batch whose digest equals the one stored in ``Brief.meta.compile_digests`` is not compiled again: the Brief was
  written from exactly these facts, so a re-run with the same ledger changes nothing and the Brief hash does not churn.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import JsonValue

from mde_compass.catalog.fact_kinds import (
    GENERAL_GROUP,
    group,
    kind_for,
    load_fact_kinds,
    route_groups,
)
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.interview import Fact
from mde_compass.engine.context_pack import ledger_line
from mde_compass.engine.facts import fact_number
from mde_compass.engine.interview_support import text_of
from mde_compass.engine.slots import SlotStore

FILL_PREFIX = "fill:"
TYPICAL_KEY = "typical"
DIGEST_CHARS = 24
MAX_EXISTING_LINES = 400
PROJECT_SLOTS_MAX = 14
_EXISTING_SECTIONS: tuple[tuple[str, str], ...] = (
    ("apps", "name"),
    ("roles", "name_in_user_words"),
    ("scope", "name"),
    ("integrations", "name"),
    ("glossary", "term"),
)


@dataclass(frozen=True)
class CallPlan:
    """One compile call: a batch of the facts of one group."""

    key: str  # ``fill:<group>:<n>``
    group: str
    sections: tuple[str, ...]
    facts: tuple[Fact, ...]
    digest: str


def fact_kind(fact: Fact) -> str:
    return kind_for(fact.type, fact.topics)


def route(facts: Iterable[Fact]) -> dict[str, list[Fact]]:
    """Active facts by compile group, groups in file order, facts in id order inside a group."""
    ordered = sorted((f for f in facts if f.status == "active"), key=lambda f: fact_number(f.id))
    buckets: dict[str, list[Fact]] = {g.id: [] for g in load_fact_kinds().groups}
    for fact in ordered:
        for gid in route_groups(fact.type, fact_kind(fact), fact.topics):
            buckets[gid].append(fact)
    return {gid: members for gid, members in buckets.items() if members}


def batches(facts: Sequence[Fact], max_facts: int, max_chars: int) -> list[list[Fact]]:
    """Consecutive batches of at most ``max_facts`` facts whose ledger lines total at most ``max_chars``."""
    out: list[list[Fact]] = []
    current: list[Fact] = []
    size = 0
    for fact in facts:
        cost = len(ledger_line(fact)) + 1
        if current and (len(current) >= max(1, max_facts) or size + cost > max_chars):
            out.append(current)
            current, size = [], 0
        current.append(fact)
        size += cost
    if current:
        out.append(current)
    return out


def digest_of(key: str, sections: Iterable[str], facts: Iterable[Fact]) -> str:
    """A stable hash of what a call is built from: its key, its sections and every fact line (statement, quote,
    origin, confidence). A superseded or new fact changes the batch, so the call runs again."""
    h = hashlib.sha256()
    h.update(key.encode())
    h.update("|".join(sections).encode())
    for fact in facts:
        h.update(b"\x00")
        h.update(ledger_line(fact).encode())
    return h.hexdigest()[:DIGEST_CHARS]


def plan_calls(facts: Iterable[Fact], *, max_facts: int, max_chars: int) -> list[CallPlan]:
    plans: list[CallPlan] = []
    for gid, members in route(facts).items():
        spec = group(gid)
        for n, batch in enumerate(batches(members, max_facts, max_chars)):
            key = f"{FILL_PREFIX}{gid}:{n}"
            plans.append(
                CallPlan(
                    key, gid, spec.sections, tuple(batch), digest_of(key, spec.sections, batch)
                )
            )
    return plans


def typical_digest(facts: Iterable[Fact]) -> str:
    """The typical-details call depends on the facts only: what the compile itself adds to the Brief must not make
    it run again."""
    h = hashlib.sha256()
    h.update(TYPICAL_KEY.encode())
    for fact in sorted(facts, key=lambda f: fact_number(f.id)):
        h.update(b"\x00")
        h.update(f"{fact.id}:{fact.statement}".encode())
    return h.hexdigest()[:DIGEST_CHARS]


def is_general(plan: CallPlan) -> bool:
    return plan.group == GENERAL_GROUP


def stale_keys(digests: dict[str, str], plans: Sequence[CallPlan]) -> list[str]:
    """Digest keys of fill calls that no longer exist (a group shrank): dropped so the map does not grow."""
    live = {p.key for p in plans}
    return [k for k in digests if k.startswith(FILL_PREFIX) and k not in live]


# ------------------------------------------------------------------------------- what a call is told
def existing_lines(brief: Brief) -> str:
    """The Brief items that already exist, one per line: ``FTR-001 | scope | Order tracking`` (``-`` when the
    record has no id). The model uses it not to repeat an item and to point a fact at a real id."""
    lines: list[str] = []
    for section, name_field in _EXISTING_SECTIONS:
        for record in getattr(brief, section):
            name = str(getattr(record, name_field, "")).strip()
            if not name:
                continue
            ident = str(getattr(record, "id", "") or "-")
            lines.append(f"{ident} | {section} | {' '.join(name.split())}")
            if len(lines) >= MAX_EXISTING_LINES:
                return "\n".join(lines)
    return "\n".join(lines)


def project_card(slots: SlotStore, catalog: Catalog) -> str:
    """What the project is, from the slots the person (or the interview) has already filled: the first stage of the
    catalog (idea, geography, audience, locales). Short by design: the facts carry the detail."""
    lines: list[str] = []
    for slot_id in catalog.slot_ids_by_order():
        slot = catalog.slots[slot_id]
        if slot.order > 2 or not slots.filled(slot_id):
            continue
        record = slots.get(slot_id)
        value: JsonValue = record.value if record is not None else None
        text = " ".join(text_of(value).split())
        if text:
            lines.append(f"{slot_id}: {text[:200]}")
        if len(lines) >= PROJECT_SLOTS_MAX:
            break
    return "\n".join(lines)


def slot_state(slots: SlotStore, paths: Iterable[str]) -> dict[str, str]:
    """Slot id -> its state, for the targets that already hold a value (an empty slot is simply absent)."""
    out: dict[str, str] = {}
    for path in paths:
        record = slots.get(path)
        if record is not None and slots.filled(path):
            out[path] = record.status
    return out


def group_paths(catalog: Catalog, sections: Iterable[str]) -> list[str]:
    """Slot ids a group's sections name (for ``slot_state``); ``_none`` names none."""
    wanted = {s.strip() for s in sections if s.strip()}
    return [
        sid
        for sid, slot in catalog.slots.items()
        if slot.level != "generated"
        and ({slot.brief_path.split(".", 1)[0], slot.group, sid} & wanted)
    ]
