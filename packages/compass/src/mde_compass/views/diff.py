"""Brief diff in the ChangeSet path grammar (``GET .../brief/diff?from=&to=``).

Dict-section items are addressed ``<section>.<key>``; list records by id (or natural key) and, when only
some fields moved, ``<section>.<id>.<field>``. A record that appears or disappears is one ``added`` or
``removed`` entry carrying the whole record. Provenance, history and confidence are not content, so they
never produce entries (the section hashes still tell which sections moved).
"""

from __future__ import annotations

from pydantic import BaseModel, JsonValue

from mde_compass.api.schemas import DiffEntry, DiffResponse
from mde_compass.domain.brief import DICT_SECTIONS, LIST_SECTIONS, SECTION_KEYS, Brief
from mde_compass.domain.common import BriefItem
from mde_compass.domain.hashing import changed_sections, section_hashes
from mde_compass.views.paths import iter_records, json_dump

_META_FIELDS = frozenset({"history", "provenance", "confidence"})


def _entry(path: str, before: JsonValue, after: JsonValue) -> DiffEntry | None:
    if before == after:
        return None
    if before is None:
        return DiffEntry(path=path, before=None, after=after, kind="added")
    if after is None:
        return DiffEntry(path=path, before=before, after=None, kind="removed")
    return DiffEntry(path=path, before=before, after=after, kind="changed")


def _item_value(item: BriefItem | None) -> JsonValue:
    return item.value if item is not None and item.has_value() else None


def _dict_entries(
    section: str, a: dict[str, BriefItem], b: dict[str, BriefItem]
) -> list[DiffEntry]:
    out: list[DiffEntry] = []
    for key in sorted(set(a) | set(b)):
        before, after = a.get(key), b.get(key)
        path = f"{section}.{key}"
        candidates = [_entry(path, _item_value(before), _item_value(after))]
        if before is not None and after is not None:
            candidates.append(_entry(f"{path}.tier", before.tier, after.tier))
            candidates.append(_entry(f"{path}.status", before.status, after.status))
        out.extend(e for e in candidates if e is not None)
    return out


def _record_content(record: BaseModel) -> dict[str, JsonValue]:
    return {k: v for k, v in json_dump(record).items() if k not in _META_FIELDS}


def _list_entries(brief_a: Brief, brief_b: Brief, section: str) -> list[DiffEntry]:
    old = dict(iter_records(brief_a, section))
    new = dict(iter_records(brief_b, section))
    out: list[DiffEntry] = []
    for key in [*old, *(k for k in new if k not in old)]:
        path = f"{section}.{key}"
        before, after = old.get(key), new.get(key)
        if before is None and after is not None:
            out.append(DiffEntry(path=path, after=_record_content(after), kind="added"))
        elif after is None and before is not None:
            out.append(DiffEntry(path=path, before=_record_content(before), kind="removed"))
        elif before is not None and after is not None:
            old_fields, new_fields = _record_content(before), _record_content(after)
            for name in new_fields:
                entry = _entry(f"{path}.{name}", old_fields.get(name), new_fields.get(name))
                if entry is not None:
                    out.append(entry)
    return out


def diff_briefs(a: Brief, b: Brief, *, from_version: int, to_version: int) -> DiffResponse:
    """What changed from ``a`` to ``b``: entries per path plus the sections whose hash moved."""
    entries: list[DiffEntry] = []
    for section in SECTION_KEYS:
        if section in DICT_SECTIONS:
            entries.extend(_dict_entries(section, getattr(a, section), getattr(b, section)))
        elif section in LIST_SECTIONS:
            entries.extend(_list_entries(a, b, section))
    for field in ("summary", "pitch"):
        entry = _entry(field, getattr(a, field) or None, getattr(b, field) or None)
        if entry is not None:
            entries.append(entry)
    return DiffResponse(
        from_version=from_version,
        to_version=to_version,
        sections_changed=changed_sections(section_hashes(a), section_hashes(b)),
        entries=entries,
    )


def changed_paths(a: Brief, b: Brief) -> list[str]:
    """Just the paths that differ (used to mark rows *Updated*)."""
    return [e.path for e in diff_briefs(a, b, from_version=0, to_version=0).entries]
