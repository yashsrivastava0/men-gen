"""``GET .../provenance/{item_id}``: where a value came from (exact quotes, who, when, history).

``item_id`` is a path in the ChangeSet grammar (``idea.summary``, ``scope.FTR-004``,
``scope.FTR-004.priority``, ``assumptions.ASM-002``). The turn ids returned are the chat turns the UI
highlights in the history drawer; the history itself stays in the event log.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import JsonValue

from mde_compass.api.schemas import ProvenanceEntry, ProvenanceResponse
from mde_compass.domain.brief import Brief
from mde_compass.domain.common import Provenance
from mde_compass.views.paths import TURN_ID_RE, resolve_item


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def turn_id_of(ref: str) -> str | None:
    """The turn id inside a provenance ref (``evt_...``, optionally followed by a pointer)."""
    match = TURN_ID_RE.match(ref.strip())
    return match.group(1) if match else None


def _entry(p: Provenance) -> ProvenanceEntry:
    return ProvenanceEntry(
        type=p.type, ref=p.ref, quote=p.quote, date=_iso(p.date), who=p.contributor_id
    )


def _history_row(entry_value: JsonValue, entry: object, field: str | None) -> dict[str, JsonValue]:
    value = entry_value
    if field is not None and isinstance(value, dict) and field in value:
        value = value[field]
    row = entry.model_dump(mode="json")  # type: ignore[attr-defined]
    row["value"] = value
    return row


def provenance_for(brief: Brief, item_id: str) -> ProvenanceResponse | None:
    """Resolve ``item_id`` and describe its evidence, or None when the path does not exist."""
    resolved = resolve_item(brief, item_id.strip())
    if resolved is None:
        if item_id.strip() == "idea.summary" and brief.summary:
            return ProvenanceResponse(item_id="idea.summary", value=brief.summary)
        return None
    meta = resolved.meta
    history = [_history_row(h.value, h, resolved.field) for h in meta.history]
    turn_ids: list[str] = []
    refs = [p.ref for p in meta.provenance]
    for ref in [*refs, *(h.turn_id or "" for h in meta.history)]:
        turn = turn_id_of(ref)
        if turn and turn not in turn_ids:
            turn_ids.append(turn)
    return ProvenanceResponse(
        item_id=item_id.strip(),
        value=resolved.value,
        tier=meta.tier,
        status=meta.status,
        provenance=[_entry(p) for p in meta.provenance],
        value_history=history,
        turn_ids=turn_ids,
    )
