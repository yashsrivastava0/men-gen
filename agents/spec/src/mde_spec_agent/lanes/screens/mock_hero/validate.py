"""Local checks of a MOCK-HERO output: no new ids, only writable fields, every record complete, nothing unsafe (FN-11,
FN-12, FN-13). The runner re-asks once per group, then falls back and flags."""

from __future__ import annotations

from mde_spec_agent.lanes.screens.mock_hero.schema import HeroIn, HeroOut
from mde_spec_core.mock import find_unsafe

MAX_MESSAGES = 12


def validate(inp: HeroIn, out: HeroOut) -> list[str]:
    errors: list[str] = []
    entity_of = {r.id: e for e in inp.entities for r in e.records}
    writable = {e.id: {f.key for f in e.fields} for e in inp.entities}
    seen: set[tuple[str, str]] = set()
    got: dict[str, set[str]] = {}
    for v in out.values:
        e = entity_of.get(v.record)
        if e is None:
            errors.append(f"{v.record}: no such record in this call; you may not mint ids")
            continue
        if v.field not in writable[e.id]:
            errors.append(
                f"{v.record}.{v.field}: not a field you write (code owns ids, status, dates, relations, derived values)"
            )
            continue
        if (v.record, v.field) in seen:
            errors.append(f"{v.record}.{v.field}: written twice")
        seen.add((v.record, v.field))
        if not v.value.strip():
            errors.append(f"{v.record}.{v.field}: empty value")
        got.setdefault(v.record, set()).add(v.field)
        for bad in find_unsafe([v.value], allowed_names=inp.allowed_names):
            errors.append(f"{v.record}.{v.field}: {bad.kind} ({bad.detail})")
    for rid, e in sorted(entity_of.items()):
        missing = sorted(writable[e.id] - got.get(rid, set()))
        if missing:
            errors.append(f"{rid}: no value for {', '.join(missing)}")
    briefs: dict[str, int] = {}
    for b in out.briefs:
        e = entity_of.get(b.record)
        if e is None or not e.media:
            errors.append(f"{b.record}: an image brief for a record without a picture field")
            continue
        briefs[b.record] = briefs.get(b.record, 0) + 1
        if not b.subject.strip() or b.count < 1:
            errors.append(f"{b.record}: an image brief needs a subject and a count of at least 1")
        for bad in find_unsafe([b.subject, *b.avoid], allowed_names=inp.allowed_names):
            errors.append(f"{b.record} image brief: {bad.kind} ({bad.detail})")
    for rid, e in sorted(entity_of.items()):
        if e.media and briefs.get(rid, 0) != 1:
            errors.append(f"{rid}: exactly one image brief is needed")
    return errors[:MAX_MESSAGES]
