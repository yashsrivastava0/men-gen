"""Fact projector: slots are a view over facts (build prompt 02, section 3.1; ADR 0015 point 4).

Every slot write that comes from a fact goes through ``SlotStore.write`` (the single writer of slots), so the
SlotStore rules keep applying: an assumption never replaces a user value, a correction keeps the old value in
``history``, an explicit removal clears. This module only decides *what* to write and *with what provenance*:

* ``Provenance.ref`` is the fact id (``FCT-###``) and ``quote`` the fact's verbatim quote (cut at 300 characters).
  The ADR 0013 evidence rule needs a non-empty ``ref``, so a fact-backed slot is Locked-capable without any change
  to ``Provenance``.
* The provenance type follows the origin: ``stated`` is ``user_text`` (``voice`` for a voice message), ``tap`` is
  ``user_tap``, ``document`` is ``document``, ``assumption`` is ``assumed``.
* A fact whose quote was not found in its source (``unverified_quote``), or that has no quote, is written as
  ``model_knowledge``: ``max_tier`` yields Default and the slot can never be Locked by it.
* Enterprise: an effect stamped with the speaker (``FactEffect.contributor_id``, see ``facts.with_contributor``)
  passes it to the slot store, so a second contributor's conflicting value marks the slot ``disputed`` (never an
  automatic merge) instead of replacing the first value; replays after a supersession use the same stamp.
* ``FactEffect.value_json`` is JSON text. It is parsed with ``json`` and checked against the slot's ``value_schema``
  (the same check card options use). A bad value never raises: it comes back in ``ProjectionResult.rejected``.
* ``reproject`` rebuilds slots after a fact was superseded or disputed: a slot whose provenance still points at a
  fact that is no longer active is cleared through the normal removal path (history kept) and the remaining
  ACTIVE facts are replayed in creation order, so a Locked slot is never supported only by a superseded or
  unverified fact.

Pure with respect to I/O and clocks: ``now`` is a parameter.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.domain.common import Provenance, ProvenanceType, Status
from mde_compass.domain.interview import DOCUMENT_LOCK_CONFIDENCE, Fact, FactEffect
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.cards import value_fits_slot as value_fits_schema
from mde_compass.engine.facts import fact_number
from mde_compass.engine.slots import USER_GIVEN, SlotStore, WriteResult, is_blank
from mde_compass.engine.slots import item_name as item_name

QUOTE_MAX_CHARS = 300
VALUE_JSON_MAX_CHARS = 20_000
LIST_KINDS: frozenset[str] = frozenset({"enum_list", "text_list", "object_list", "locales"})
FACT_REF_PREFIX = "FCT-"


@dataclass(frozen=True)
class RejectedEffect:
    """An effect that was not applied: a bad value, or a refusal by the slot store (for example an assumption
    that may not replace a user value)."""

    fact_id: str
    slot_id: str
    reason: str
    value_json: str = ""


@dataclass
class ProjectionResult:
    written: list[str] = field(
        default_factory=list
    )  # slot ids written (created, corrected, upgraded)
    cleared: list[str] = field(default_factory=list)  # slot ids emptied
    unchanged: list[str] = field(default_factory=list)  # slot ids where the write changed nothing
    rejected: list[RejectedEffect] = field(default_factory=list)

    def extend(self, other: ProjectionResult) -> None:
        for mine, theirs in (
            (self.written, other.written),
            (self.cleared, other.cleared),
            (self.unchanged, other.unchanged),
        ):
            mine.extend(s for s in theirs if s not in mine)
        self.rejected.extend(other.rejected)

    @property
    def touched(self) -> list[str]:
        """Slot ids whose value or state changed (written or cleared), once each, in order."""
        return list(dict.fromkeys([*self.written, *self.cleared]))


# ------------------------------------------------------------------------------------------ provenance
def _quote_of(fact: Fact) -> str:
    return fact.quote.strip()[:QUOTE_MAX_CHARS]


def provenance_type(fact: Fact, input_mode: str = "text") -> ProvenanceType:
    """The provenance type a fact earns. Without a verified quote a user-sourced fact is only ``model_knowledge``."""
    if fact.origin == "assumption":
        return "assumed"
    if fact.unverified_quote or not fact.quote.strip():
        return "model_knowledge"
    if fact.origin == "document" and fact.confidence < DOCUMENT_LOCK_CONFIDENCE:
        return "model_knowledge"  # a claim still waiting for the person to confirm it is not evidence (ADR 0016)
    if fact.origin == "tap":
        return "user_tap"
    if fact.origin == "document":
        return "document"
    return "voice" if input_mode == "voice" else "user_text"


def provenance_for(
    fact: Fact, *, now: datetime, input_mode: str = "text", contributor_id: str | None = None
) -> Provenance:
    quote = "" if fact.origin == "assumption" else _quote_of(fact)
    return Provenance(
        type=provenance_type(fact, input_mode),
        ref=fact.id,
        quote=quote,
        date=now,
        contributor_id=contributor_id,
    )


def _state_for(fact: Fact) -> Status:
    if fact.origin == "assumption":
        return "assumed"
    return "confirmed" if fact.origin == "tap" else "volunteered"


def verified_fact_ids(facts: Iterable[Fact]) -> frozenset[str]:
    """Facts that may support a Locked item: active, user-sourced, with a verified quote."""
    return frozenset(f.id for f in facts if f.supports_locked())


# ---------------------------------------------------------------------------------------------- values
def _reject_constant(name: str) -> object:
    raise ValueError(f"non-finite number {name}")


def _parse(value_json: str) -> tuple[JsonValue, str]:
    """(value, error): error is empty when the text is valid JSON."""
    if len(value_json) > VALUE_JSON_MAX_CHARS:
        return None, "value_too_long"
    try:
        parsed = json.loads(value_json, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None, "invalid_json"
    return cast(JsonValue, parsed), ""


def _normalize_shape(slot: SlotDef, value: JsonValue) -> JsonValue:
    """List slots always hold lists; object lists hold ``{"name": ...}`` items (never bare strings)."""
    kind = slot.value_schema.kind
    if kind not in LIST_KINDS:
        return value
    items: list[JsonValue] = list(value) if isinstance(value, list) else [value]
    if kind == "object_list":
        items = [{"name": it.strip()} if isinstance(it, str) else it for it in items]
    return items


def _merge(existing: JsonValue, new: JsonValue) -> JsonValue:
    base = existing if isinstance(existing, list) else []
    incoming = new if isinstance(new, list) else [new]
    seen = {item_name(v) for v in base}
    out = list(base)
    for item in incoming:
        key = item_name(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _stems(text: str) -> frozenset[str]:
    """The words of a name that carry meaning (four letters or more), cut to four letters so "pack" and "packs", "book"
    and "booking", "manage" and "management" meet."""
    import re

    return frozenset(w[:4] for w in re.findall(r"\w+", text.casefold()) if len(w) >= 4)


def _same_element(a: JsonValue, b: JsonValue) -> bool:
    """Two list elements are one when their names are equal, or when every meaningful word of the shorter name is in
    the longer one ("Class packs" and "Class packs and credits", "Reminders" and "Class reminders")."""
    na, nb = item_name(a), item_name(b)
    if na == nb:
        return True
    wa, wb = _stems(na), _stems(nb)
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def _covered(item: JsonValue, base: list[JsonValue]) -> list[int]:
    """The elements an incoming one is made of, when it names several of them ("Weekly timetable and booking" is the
    features "Weekly class timetable" and "Class booking"): each existing element whose own, distinctive words are all
    in the incoming name, provided together they account for every distinctive word of it. Words most elements share
    ("class") are not distinctive. Empty when the incoming element says something new."""
    words = _stems(item_name(item))
    names = [_stems(item_name(old)) for old in base]
    common = {w for w in set().union(*names) if sum(w in n for n in names) >= 3} if names else set()
    hits = [i for i, n in enumerate(names) if (n - common) and (n - common) <= words]
    if len(hits) < 2:
        return []
    seen = set().union(*(names[i] for i in hits))
    return hits if (words - common) <= seen else []


OVERRIDE_KEYS = frozenset({"priority", "status", "scope_status"})


def _update(old: JsonValue, item: JsonValue) -> JsonValue:
    """An element that is already there keeps its own name and its own words (intent, goal, purpose); the newer fact
    sets its priority and status and fills in what was missing."""
    if not (isinstance(old, dict) and isinstance(item, dict)):
        return old
    out = dict(old)
    for key, value in item.items():
        if key == "name":
            continue
        if key in OVERRIDE_KEYS or is_blank(out.get(key)):
            out[key] = value
    return out


def _merge_set(existing: JsonValue, new: JsonValue) -> JsonValue:
    """Joining a list of records that a FACT gives (``set``, or ``add`` to a list the person built). A fact about one
    or two elements ("reminders can wait for the second version") must never wipe a list the person built through other
    facts, and a fact that names an element again ("Class packs and credits are must-haves") must not make a second one:
    an element that is already there keeps its own name and takes the new properties, a name that is made of several
    existing elements updates each of them, anything else is added. A real correction supersedes the older facts, and
    ``reproject`` then rebuilds the slot from the facts that are still active, so it never reaches this function with
    the old list still in place."""
    base: list[JsonValue] = list(existing) if isinstance(existing, list) else []
    incoming = new if isinstance(new, list) else [new]
    for item in incoming:
        at = next((i for i, old in enumerate(base) if _same_element(old, item)), None)
        if at is not None:
            base[at] = _update(base[at], item)
            continue
        parts = _covered(item, base)
        if parts:
            for i in parts:
                base[i] = _update(base[i], item)
        else:
            base.append(item)
    return base


# Words that name a way of using the app, not a kind of person: an app called "Customer app" is for customers, so no role
# is made from such a name (the interview's own roles and the compile step name them).
SURFACE_WORDS = frozenset(
    {"app", "apps", "panel", "portal", "website", "site", "dashboard", "console", "web", "mobile"}
)


def roles_of_apps(apps: JsonValue) -> list[JsonValue]:
    """The kinds of person an app list implies: "Members use a mobile app" is an app called Members, and the people are
    members. One role element per app whose name is a kind of person; the app's purpose is the role's goal."""
    out: list[JsonValue] = []
    for el in apps if isinstance(apps, list) else []:
        if not isinstance(el, dict):
            continue
        name = str(el.get("name", "")).strip()
        if not name or SURFACE_WORDS & set(name.casefold().split()):
            continue
        role: dict[str, JsonValue] = {"name": name}
        if str(el.get("purpose", "")).strip():
            role["goal"] = str(el["purpose"]).strip()
        out.append(role)
    return out


def _store_write(
    slots: SlotStore,
    slot_id: str,
    value: JsonValue,
    fact: Fact,
    provenance: Provenance,
    turn_id: str | None,
    *,
    remove: bool = False,
    contributor_id: str | None = None,
) -> WriteResult:
    """The one place this module calls ``SlotStore.write``."""
    return slots.write(
        slot_id,
        value,
        state=_state_for(fact),
        provenance=provenance,
        confidence=fact.confidence,
        turn_id=turn_id,
        remove=remove,
        contributor_id=contributor_id,
    )


def _outcome(result: ProjectionResult, effect: FactEffect, fact_id: str, res: WriteResult) -> None:
    if res.applied:
        bucket = result.cleared if res.reason == "cleared" else result.written
        if effect.slot_id not in bucket:
            bucket.append(effect.slot_id)
    elif res.reason == "unchanged":
        if effect.slot_id not in result.unchanged:
            result.unchanged.append(effect.slot_id)
    else:
        result.rejected.append(
            RejectedEffect(fact_id, effect.slot_id, res.reason, effect.value_json)
        )


def _reject(result: ProjectionResult, fact: Fact, effect: FactEffect, reason: str) -> None:
    result.rejected.append(RejectedEffect(fact.id, effect.slot_id, reason, effect.value_json[:200]))


def _apply_effect(
    fact: Fact,
    effect: FactEffect,
    slots: SlotStore,
    catalog: Catalog,
    *,
    turn_id: str | None,
    now: datetime,
    input_mode: str,
    result: ProjectionResult,
    contributor_id: str | None = None,
) -> None:
    slot = catalog.slots.get(effect.slot_id)
    if slot is None:
        _reject(result, fact, effect, "unknown_slot")
        return
    value, error = _parse(effect.value_json)
    if error:
        _reject(result, fact, effect, error)
        return
    who = effect.contributor_id or contributor_id
    provenance = provenance_for(fact, now=now, input_mode=input_mode, contributor_id=who)
    kind = slot.value_schema.kind

    if effect.mode == "remove":
        remove_value: JsonValue = None
        if not is_blank(value):
            if kind in LIST_KINDS:
                remove_value = _normalize_shape(slot, value)
            else:
                record = slots.get(effect.slot_id)
                if record is None or item_name(record.value) != item_name(value):
                    _reject(result, fact, effect, "remove_value_mismatch")
                    return
        res = _store_write(
            slots,
            effect.slot_id,
            remove_value,
            fact,
            provenance,
            turn_id,
            remove=True,
            contributor_id=who,
        )
        _outcome(result, effect, fact.id, res)
        return

    if is_blank(value):
        _reject(result, fact, effect, "empty_value")
        return
    if effect.mode == "add" and kind not in LIST_KINDS:
        _reject(result, fact, effect, "add_needs_list_slot")
        return
    if not value_fits_schema(slot, value):
        _reject(result, fact, effect, "value_not_valid_for_slot")
        return
    value = _normalize_shape(slot, value)
    if effect.mode == "add":
        record = slots.get(effect.slot_id)
        # Items of an assumed default are not the user's list: the user's items start a fresh list.
        user_list = (
            record.value
            if record is not None and slots.filled(effect.slot_id) and record.status in USER_GIVEN
            else None
        )
        value = _merge_set(user_list, value) if kind == "object_list" else _merge(user_list, value)
    elif (
        kind == "object_list"
    ):  # a "set" of a list of records by a fact never wipes the person's list
        record = slots.get(effect.slot_id)
        if (
            record is not None
            and slots.filled(effect.slot_id)
            and record.status in USER_GIVEN
            and isinstance(record.value, list)
        ):
            value = _merge_set(record.value, value)
    res = _store_write(slots, effect.slot_id, value, fact, provenance, turn_id, contributor_id=who)
    _outcome(result, effect, fact.id, res)
    if effect.slot_id == "apps.list" and res.applied and "roles.list" in catalog.slots:
        implied = roles_of_apps(value)
        if implied:  # the people of an app the person named are roles of the Brief too
            roles = slots.get("roles.list")
            have = (
                roles.value
                if roles is not None and slots.filled("roles.list") and roles.status in USER_GIVEN
                else None
            )
            joined = _merge_set(have, implied)
            if joined != have:
                role_res = _store_write(
                    slots, "roles.list", joined, fact, provenance, turn_id, contributor_id=who
                )
                if role_res.applied and "roles.list" not in result.written:
                    result.written.append("roles.list")


# ------------------------------------------------------------------------------------------ projection
def project(
    fact: Fact,
    slots: SlotStore,
    catalog: Catalog,
    *,
    turn_id: str | None,
    now: datetime,
    input_mode: str = "text",
    contributor_id: str | None = None,
) -> ProjectionResult:
    """Apply each effect of ``fact`` through the slot store. Only an ACTIVE fact projects. ``contributor_id``
    is the speaker when the effects are not stamped already (enterprise)."""
    result = ProjectionResult()
    if fact.status != "active":
        for effect in fact.effects:
            _reject(result, fact, effect, "fact_not_active")
        return result
    for effect in fact.effects:
        _apply_effect(
            fact,
            effect,
            slots,
            catalog,
            turn_id=turn_id,
            now=now,
            input_mode=input_mode,
            result=result,
            contributor_id=contributor_id,
        )
    return result


def _chronological(facts: Iterable[Fact]) -> list[Fact]:
    """Creation order: ``created_at`` when every fact has it, otherwise the id number (ids are never reused)."""
    listed = list(facts)
    if all(f.created_at is not None for f in listed):
        return sorted(listed, key=lambda f: (cast(datetime, f.created_at), fact_number(f.id)))
    return sorted(listed, key=lambda f: fact_number(f.id))


def fact_refs(record: SlotRecord | None) -> frozenset[str]:
    """The fact ids a slot's provenance points at."""
    if record is None:
        return frozenset()
    return frozenset(p.ref for p in record.provenance if p.ref.startswith(FACT_REF_PREFIX))


class _FixedClock:
    """A clock that always says ``now`` (the scratch store of the dry run never reads a real clock)."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        return None

    def monotonic(self) -> float:
        return 0.0


def _replay(
    supporting: list[Fact],
    slot_id: str,
    target: SlotStore,
    catalog: Catalog,
    *,
    turn_id: str | None,
    now: datetime,
    result: ProjectionResult,
) -> None:
    for fact in supporting:
        for effect in fact.effects:
            if effect.slot_id == slot_id:
                _apply_effect(
                    fact,
                    effect,
                    target,
                    catalog,
                    turn_id=turn_id,
                    now=now,
                    input_mode="text",
                    result=result,
                )


def reproject(
    slot_ids: Iterable[str],
    facts: Iterable[Fact],
    slots: SlotStore,
    catalog: Catalog,
    *,
    turn_id: str | None,
    now: datetime,
) -> ProjectionResult:
    """Rebuild ``slot_ids`` from the ACTIVE facts after a supersession or dispute.

    For each slot the active facts that touch it are replayed in creation order on a scratch store first (a dry
    run). If the result equals what the slot holds now (same value and state) and the slot's provenance cites no
    fact that is no longer active, nothing is done: no history churn. Otherwise:

    * a slot whose value came from facts is cleared through the store's removal path (its history keeps the old
      value), then the active facts are replayed on the real store with the normal correction semantics, so a
      Locked slot is never supported by a superseded or disputed fact;
    * a slot with no active fact left ends up empty (cleared);
    * a slot whose value did not come from facts (no ``FCT-`` provenance) is never cleared; active facts, if any,
      correct it as a normal write would.

    Replays use text provenance; voice and text are both evidence, so evidence strength is unchanged.
    """
    active = [f for f in facts if f.status == "active"]
    result = ProjectionResult()
    for slot_id in dict.fromkeys(slot_ids):
        if slot_id not in catalog.slots:
            result.rejected.append(RejectedEffect("", slot_id, "unknown_slot"))
            continue
        supporting = _chronological(
            f for f in active if any(e.slot_id == slot_id for e in f.effects)
        )
        record = slots.get(slot_id)
        filled = slots.filled(slot_id)
        refs = fact_refs(record) if filled else frozenset[str]()
        stale = refs - frozenset(f.id for f in supporting)

        scratch = SlotStore(catalog, clock=_FixedClock(now))
        if filled and not refs and record is not None:
            scratch.load([record])  # a value that did not come from facts stays the base
        _replay(
            supporting,
            slot_id,
            scratch,
            catalog,
            turn_id=turn_id,
            now=now,
            result=ProjectionResult(),
        )
        expected = scratch.get(slot_id) if scratch.filled(slot_id) else None
        current = record if filled else None
        same = (expected is None and current is None) or (
            expected is not None
            and current is not None
            and (expected.value, expected.status) == (current.value, current.status)
        )
        if same and not stale:
            continue
        if filled and refs:
            marker = sorted(stale or refs, key=fact_number)[0]
            res = slots.write(
                slot_id,
                None,
                state="volunteered",
                provenance=Provenance(type="assumed", ref=marker, date=now),
                turn_id=turn_id,
                remove=True,
            )
            if res.applied:
                result.cleared.append(slot_id)
        _replay(supporting, slot_id, slots, catalog, turn_id=turn_id, now=now, result=result)
    return result


def facts_by_slot(facts: Iterable[Fact]) -> Mapping[str, list[Fact]]:
    """Active facts with an effect on each slot, in creation order (what ``reproject`` replays)."""
    out: dict[str, list[Fact]] = {}
    for fact in _chronological(f for f in facts if f.status == "active"):
        for slot_id in dict.fromkeys(e.slot_id for e in fact.effects):
            out.setdefault(slot_id, []).append(fact)
    return out
