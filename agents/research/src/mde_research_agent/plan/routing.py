"""Which job and which topic carries a Brief research request: one request, ONE topic.

Compass writes research requests as a topic line, a question and the decision they unlock. The planner builds its own
topics from the Brief; this module decides which of those topics a request belongs to, so the preview and the report
can say "this request is answered by that topic" and so no request is spread over several topics.

The decision is plain code over data (``plan.request_jobs`` in ``data/plan.yaml``, words and two-word phrases per job):

* the words of the app's own CATEGORY are ignored ("delivery" in a delivery app says nothing about the flows job);
* a word in the request's topic line counts three times as much as one in its question, decision or entity;
* a two-word phrase counts for both words;
* the job with the highest score wins (ties go to the earlier job in ``JOB_NAMES``); no score means no job.

Nothing here names a word, a company or a place: add a word to the data file to change a decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mde_research_agent.catalog.loader import JOB_NAMES
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.text import same_thing, stem_set, stems
from mde_research_agent.domain.input import Request, ResearchInput

TOPIC_WEIGHT = 3
QUESTION_WEIGHT = 1


@dataclass(frozen=True)
class Reading:
    """A request as stems: its topic line (with its entity) and its question (with the decision it unlocks)."""

    topic: tuple[str, ...]
    question: tuple[str, ...]


def category_stems(inp: ResearchInput) -> frozenset[str]:
    """The stems of the app's own category words: they describe the product, not what the request asks about."""
    return stem_set(inp.idea.category)


def read_request(req: Request, ignore: frozenset[str] = frozenset()) -> Reading:
    def kept(text: str) -> tuple[str, ...]:
        return tuple(s for s in stems(text) if s not in ignore)

    # the entity field is usually a long description of the app, not a name: it counts like the question, not the topic
    return Reading(
        topic=kept(req.topic),
        question=kept(f"{req.question} {req.decision_unlocked} {req.entity}"),
    )


def _contains(seq: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return n > 0 and any(seq[i : i + n] == phrase for i in range(len(seq) - n + 1))


def _keys(words: Sequence[str]) -> list[tuple[str, ...]]:
    """The distinct stem tuples of a word list ("permit" and "permits" are one word)."""
    return list(dict.fromkeys(k for k in (stems(w) for w in words) if k))


def score_words(reading: Reading, words: Sequence[str]) -> tuple[int, int]:
    """``(score, first)``: how strongly ``reading`` matches a list of words and two-word phrases (see the module doc),
    and the position in the topic line of the earliest match (a tie goes to the job named first in the line)."""
    topic, question = set(reading.topic), set(reading.question)
    total, first = 0, len(reading.topic)
    for key in _keys(words):
        if len(key) == 1:
            total += TOPIC_WEIGHT * (key[0] in topic) + QUESTION_WEIGHT * (key[0] in question)
            if key[0] in topic:
                first = min(first, reading.topic.index(key[0]))
        else:
            hit = _contains(reading.topic, key)
            total += len(key) * (
                TOPIC_WEIGHT * hit + QUESTION_WEIGHT * _contains(reading.question, key)
            )
            if hit:
                first = min(first, min(i for i, s in enumerate(reading.topic) if s == key[0]))
    return total, first


def job_scores(reading: Reading, cat: Catalog) -> dict[str, int]:
    return {job: score_words(reading, cat.plan.request_jobs.get(job, ()))[0] for job in JOB_NAMES}


def job_of_request(reading: Reading, cat: Catalog) -> str | None:
    """The job a request belongs to, or None when no word of any job occurs in it."""
    ranked = []
    for index, job in enumerate(JOB_NAMES):
        score, first = score_words(reading, cat.plan.request_jobs.get(job, ()))
        ranked.append((score, -first, -index, job))
    best = max(ranked)
    return best[3] if best[0] > 0 else None


def best_by_words(reading: Reading, candidates: Mapping[str, Sequence[str]]) -> str | None:
    """The key whose word list matches ``reading`` best (the first one on a tie), or None when none matches."""
    best: tuple[int, str] | None = None
    for key, words in candidates.items():
        score = score_words(reading, words)[0]
        if score > 0 and (best is None or score > best[0]):
            best = (score, key)
    return best[1] if best else None


def specific_entity(req: Request, ignore: frozenset[str]) -> str:
    """The request's entity when it names something of its own; an entity that only repeats the app's category
    ("hospital patient and staff apps" in a hospital app) is no entity at all."""
    named = stem_set(req.entity) - ignore
    return req.entity if named else ""


def overlap(reading: Reading, text: str) -> int:
    """How many distinct stems of the request's topic line occur in ``text`` (a target's label and anchors)."""
    return len(set(reading.topic) & stem_set(text))


def names_same_thing(a: str, b: str) -> bool:
    return bool(a.strip() and b.strip() and same_thing(a, b))
