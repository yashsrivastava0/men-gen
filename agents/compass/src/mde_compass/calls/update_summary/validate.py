"""Code validators of ``cmp.update_summary``: the summary may shrink words but never lose a fact.

* ``structural`` is the spec-level validator (a failure means the deterministic merge runs): every fact id of the
  new facts digest AND every fact id already in the summary appears in some topic's ``fact_ids``, no earlier
  topic is gone, and the summary is not absurdly large.
* ``sanitize`` tidies what is left (squashes spaces, caps and de-duplicates lines, merges topics with the same
  name, drops fact ids that exist nowhere, names a nameless topic ``general``). It never removes a known fact id
  or a topic, so a summary that passed ``structural`` still passes after it.
* ``validate`` is ``structural`` plus the check that no unknown fact id is left.
"""

from __future__ import annotations

from mde_compass.calls.update_summary.digest import (
    digest_ids,
    is_fact_id,
    normalize_topic,
    topic_key,
)
from mde_compass.calls.update_summary.schema import SummaryInput, SummaryOutput, TopicWire

MAX_TOPICS = 40
MAX_LINE_CHARS = 240
MAX_DECISIONS = 20
MAX_ISSUES = 12
MAX_VOCABULARY = 20
MAX_TONE = 8


def previous_fact_ids(inp: SummaryInput) -> list[str]:
    return list(dict.fromkeys(fid for t in inp.previous_topics for fid in t.fact_ids))


def known_fact_ids(inp: SummaryInput) -> set[str]:
    return {*previous_fact_ids(inp), *digest_ids(inp.new_facts_digest)}


def structural(inp: SummaryInput, out: SummaryOutput) -> list[str]:
    """Why this summary must not replace the old one (empty list: acceptable)."""
    errors: list[str] = []
    covered = {fid for t in out.topics for fid in t.fact_ids}
    new_missing = [fid for fid in digest_ids(inp.new_facts_digest) if fid not in covered]
    if new_missing:
        errors.append(f"drops new facts: {', '.join(new_missing[:8])}")
    old_missing = [fid for fid in previous_fact_ids(inp) if fid not in covered]
    if old_missing:
        errors.append(f"drops facts it already covered: {', '.join(old_missing[:8])}")
    kept = {topic_key(normalize_topic(t.topic)) for t in out.topics}
    lost = [t.topic for t in inp.previous_topics if topic_key(normalize_topic(t.topic)) not in kept]
    if lost:
        errors.append(f"drops earlier topics: {', '.join(lost[:5])}")
    if len(out.topics) > MAX_TOPICS:
        errors.append(f"too many topics ({len(out.topics)})")
    return errors


def validate(inp: SummaryInput, out: SummaryOutput) -> list[str]:
    errors = structural(inp, out)
    known = known_fact_ids(inp)
    unknown = [fid for t in out.topics for fid in t.fact_ids if fid not in known]
    if unknown:
        errors.append(f"fact ids that exist nowhere: {', '.join(dict.fromkeys(unknown))}")
    return errors


def _lines(values: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        text = " ".join(v.split())[:MAX_LINE_CHARS].strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
        if len(out) >= limit:
            break
    return out


def sanitize(inp: SummaryInput, out: SummaryOutput) -> tuple[SummaryOutput, list[str]]:
    """Tidy the summary; returns (output, notes about what was dropped or merged)."""
    known = known_fact_ids(inp)
    notes: list[str] = []
    merged: dict[str, TopicWire] = {}
    for t in out.topics:
        name = normalize_topic(t.topic)
        key = topic_key(name)
        ids = [fid for fid in t.fact_ids if is_fact_id(fid) and fid in known]
        notes += [f"unknown fact id {fid}" for fid in t.fact_ids if fid not in known]
        if key in merged:
            first = merged[key]
            notes.append(f"merged duplicate topic {name}")
            merged[key] = first.model_copy(
                update={
                    "decisions": [*first.decisions, *t.decisions],
                    "open_issues": [*first.open_issues, *t.open_issues],
                    "vocabulary": [*first.vocabulary, *t.vocabulary],
                    "tone": [*first.tone, *t.tone],
                    "fact_ids": [*first.fact_ids, *ids],
                }
            )
        else:
            merged[key] = t.model_copy(update={"topic": name, "fact_ids": ids})
    topics = [
        t.model_copy(
            update={
                "decisions": _lines(t.decisions, MAX_DECISIONS),
                "open_issues": _lines(t.open_issues, MAX_ISSUES),
                "vocabulary": _lines(t.vocabulary, MAX_VOCABULARY),
                "tone": _lines(t.tone, MAX_TONE),
                "fact_ids": list(dict.fromkeys(t.fact_ids)),
            }
        )
        for t in merged.values()
    ]
    return out.model_copy(update={"topics": topics}), notes
