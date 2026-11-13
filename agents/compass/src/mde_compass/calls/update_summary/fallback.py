"""The deterministic merge: the mock handler and the fallback of ``cmp.update_summary``.

Pure function ``fallback(wire_input) -> wire_output``. It keeps every previous topic untouched and files each new
fact under the first topic tag of its digest line (``FCT-014 [rule|pricing] ...`` goes to ``pricing``; a line with
no tag goes to ``general``): the statement text is added to the topic's ``decisions`` and the id to its
``fact_ids``. Nothing is invented, no fact id is ever dropped, and running it twice with the same digest changes
nothing the second time. ``through_fact_id`` is the one the request names, else the highest id in the digest, else
the previous one.
"""

from __future__ import annotations

from mde_compass.calls.update_summary.digest import DigestLine, parse_digest, topic_key
from mde_compass.calls.update_summary.schema import SummaryInput, SummaryOutput, TopicWire

MAX_STATEMENT_CHARS = 240


def _number(fact_id: str) -> int:
    return int(fact_id.removeprefix("FCT-"))


def merge(previous: list[TopicWire], lines: list[DigestLine]) -> list[TopicWire]:
    """``previous`` with the new fact lines filed under their topics (new topics are appended in order)."""
    topics = [t.model_copy(deep=True) for t in previous]
    index = {topic_key(t.topic): i for i, t in enumerate(topics)}
    known = {fid for t in topics for fid in t.fact_ids}
    for line in lines:
        if line.fact_id in known:
            continue  # already summarised: the merge is idempotent
        known.add(line.fact_id)
        key = topic_key(line.topic)
        if key not in index:
            index[key] = len(topics)
            topics.append(
                TopicWire(
                    topic=line.topic,
                    decisions=[],
                    open_issues=[],
                    vocabulary=[],
                    tone=[],
                    fact_ids=[],
                )
            )
        topic = topics[index[key]]
        text = line.statement[:MAX_STATEMENT_CHARS]
        if text and text.casefold() not in {d.casefold() for d in topic.decisions}:
            topic.decisions.append(text)
        topic.fact_ids.append(line.fact_id)
    return topics


def through_id(inp: SummaryInput, lines: list[DigestLine]) -> str:
    if inp.through_fact_id:
        return inp.through_fact_id
    if lines:
        return max((line.fact_id for line in lines), key=_number)
    return ""


def fallback(inp: SummaryInput) -> SummaryOutput:
    lines = parse_digest(inp.new_facts_digest)
    return SummaryOutput(
        topics=merge(inp.previous_topics, lines),
        through_fact_id=through_id(inp, lines),
    )
