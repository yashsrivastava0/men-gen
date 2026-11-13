"""Working context for each interview call (build prompt 02, section 3.3; ADR 0015).

Five sections in a fixed order, each held to its own token budget (``config.ContextBudgets``; tokens are estimated as
characters / ``chars_per_token``, no tokenizer dependency):

1. ``project_card``: the idea one-liner, category, geography, audience, apps, mode, language, situation profile;
2. ``fact_digest``: every active fact, one line each, grouped by topic. Over budget: all facts of the relevant topics
   first, then the highest confidence and most recent, and the rolling summary for the rest;
3. ``needs_table``: open needs with their sufficient answers and what is missing;
4. ``asked_list``: previous questions as id, status, intent (the newest kept when it does not all fit);
5. ``last_qa``: the last N question and answer pairs verbatim.

The output is deterministic (no clock, no randomness, no dict-order dependence): cassette keys are hashes of the
rendered prompt, so identical inputs must give identical text. A section never exceeds its budget and says whether it
was cut. ``full_ledger`` is the opposite: the entire active ledger with quotes, never truncated, for ``compile_brief``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import ceil

from pydantic import JsonValue

from mde_compass.config import ContextBudgets
from mde_compass.domain.interview import Fact, InterviewSummary, QaRecord
from mde_compass.engine.facts import (
    DEFAULT_CHARS_PER_TOKEN,
    DigestResult,
    digest_line,
    digest_report,
    group_by_topic,
)
from mde_compass.engine.quotes import option_label_sources

ELLIPSIS = "…"
SUMMARY_HEADER = "Earlier details (summary):"
_VALUE_CAPS: tuple[int, ...] = (
    300,
    150,
    80,
    40,
)  # shrink long values step by step to fit the budget


def estimate_tokens(text: str, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """``ceil(len(text) / chars_per_token)``; 0 for the empty text."""
    return ceil(len(text) / max(1, chars_per_token)) if text else 0


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _fit(text: str, max_chars: int) -> tuple[str, bool]:
    """``text`` cut to at most ``max_chars`` (at a line end when possible, with an ellipsis) and whether it was cut."""
    if len(text) <= max_chars:
        return text, False
    if max_chars <= 0:
        return "", True
    head = text[: max_chars - 1]
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    return head.rstrip() + ELLIPSIS, True


# ------------------------------------------------------------------------------------------- data types
@dataclass(frozen=True)
class AskedItem:
    """One previously asked question as the asked list shows it."""

    card_id: str
    intent: str
    status: str

    @classmethod
    def from_qa(cls, qa: QaRecord) -> AskedItem:
        return cls(qa.card_id, qa.card.intent or qa.card.question, qa.status)


@dataclass(frozen=True)
class ContextSection:
    name: str
    text: str
    tokens: int
    budget: int  # tokens
    truncated: bool
    dropped: int = 0  # items (facts, needs, questions, answers) left out


@dataclass(frozen=True)
class ContextPack:
    project_card: ContextSection
    fact_digest: ContextSection
    needs_table: ContextSection
    asked_list: ContextSection
    last_qa: ContextSection

    @property
    def sections(self) -> tuple[ContextSection, ...]:
        return (
            self.project_card,
            self.fact_digest,
            self.needs_table,
            self.asked_list,
            self.last_qa,
        )

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.sections)

    @property
    def truncated(self) -> dict[str, bool]:
        return {s.name: s.truncated for s in self.sections}

    def as_vars(self) -> dict[str, str]:
        """Section texts by name, for the prompt template."""
        return {s.name: s.text for s in self.sections}


def _section(
    name: str, text: str, budget_tokens: int, cpt: int, truncated: bool, dropped: int = 0
) -> ContextSection:
    limit = max(0, budget_tokens) * cpt
    text, cut = _fit(text, limit)
    return ContextSection(
        name, text, estimate_tokens(text, cpt), max(0, budget_tokens), truncated or cut, dropped
    )


# ------------------------------------------------------------------------------------------ project card
def _render_value(value: JsonValue) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _one_line(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(r for r in (_render_value(v) for v in value) if r)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _project_card_text(card: Mapping[str, JsonValue], limit: int) -> tuple[str, bool]:
    rows = [(k, _render_value(v)) for k, v in sorted(card.items())]
    rows = [(k, v) for k, v in rows if v]
    full = "\n".join(f"{k}: {v}" for k, v in rows)
    if len(full) <= limit:
        return full, False
    text = full
    for cap in _VALUE_CAPS:
        shortened = [(k, v if len(v) <= cap else v[: cap - 1] + ELLIPSIS) for k, v in rows]
        text = "\n".join(f"{k}: {v}" for k, v in shortened)
        if len(text) <= limit:
            return text, True
    return text, True


# ---------------------------------------------------------------------------------------------- digest
def _summary_text(summary: InterviewSummary | None, dropped_ids: frozenset[str]) -> str:
    if summary is None or not summary.topics:
        return ""
    order = sorted(
        range(len(summary.topics)),
        key=lambda i: (not (dropped_ids & set(summary.topics[i].fact_ids)), i),
    )
    lines: list[str] = []
    for i in order:
        t = summary.topics[i]
        parts = [t.topic]
        for label, items in (
            ("decisions", t.decisions),
            ("open", t.open_issues),
            ("words", t.vocabulary),
            ("tone", t.tone),
        ):
            if items:
                parts.append(f"{label}: " + "; ".join(_one_line(x) for x in items))
        if len(parts) > 1:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def _digest_section(
    facts: Sequence[Fact],
    summary: InterviewSummary | None,
    budgets: ContextBudgets,
    relevant_topics: Iterable[str],
) -> ContextSection:
    cpt = max(1, budgets.chars_per_token)
    limit = max(0, budgets.fact_digest) * cpt
    topics = tuple(relevant_topics)
    first = digest_report(
        facts, topics=topics, budget_tokens=budgets.fact_digest, chars_per_token=cpt
    )
    if not first.dropped:
        return _section("fact_digest", first.text, budgets.fact_digest, cpt, False)
    dropped_ids = frozenset(first.dropped)
    summary_body = _summary_text(summary, dropped_ids)
    if not summary_body:
        return _section(
            "fact_digest", first.text, budgets.fact_digest, cpt, True, len(first.dropped)
        )
    summary_full = f"{SUMMARY_HEADER}\n{summary_body}"
    reserve = min(len(summary_full) + 1, limit // 4)
    shrunk: DigestResult = digest_report(
        facts, topics=topics, budget_tokens=(limit - reserve) // cpt, chars_per_token=cpt
    )
    room = limit - len(shrunk.text) - (1 if shrunk.text else 0)
    summary_part, _ = _fit(summary_full, room)
    text = (
        f"{shrunk.text}\n{summary_part}"
        if shrunk.text and summary_part
        else shrunk.text or summary_part
    )
    return _section("fact_digest", text, budgets.fact_digest, cpt, True, len(shrunk.dropped))


# ------------------------------------------------------------------------------------------ asked list
def _asked_text(items: Sequence[AskedItem], limit: int) -> tuple[str, bool, int]:
    lines = [f"{a.card_id} | {a.status} | {_one_line(a.intent)}" for a in items]
    full = "\n".join(lines)
    if len(full) <= limit:
        return full, False, 0
    kept: list[str] = []
    used = 0
    note_reserve = len(_earlier_note(len(lines))) + 1
    for line in reversed(lines):  # the newest questions are kept
        cost = len(line) + (1 if kept else 0)
        if used + cost + note_reserve > limit:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    dropped = len(lines) - len(kept)
    text = "\n".join([_earlier_note(dropped), *kept]) if kept else ""
    return text, True, dropped


def _earlier_note(count: int) -> str:
    return f"({count} earlier questions not shown)"


# -------------------------------------------------------------------------------------------- last Q&A
def answer_text(qa: QaRecord) -> str:
    """The answer as the user gave it, in words: selected option labels, then typed text."""
    if qa.status in ("skipped", "you_decide", "assumed", "dropped", "open", "superseded"):
        return f"[{qa.status}]"
    parts: list[str] = []
    answer = qa.answer
    ids: list[str] = []
    if isinstance(answer, str):
        ids = [answer]
    elif isinstance(answer, list):
        ids = [str(a) for a in answer if isinstance(a, str | int) and not isinstance(a, bool)]
    labels = option_label_sources(qa.card, ids)
    parts.extend(labels)
    if isinstance(answer, str) and not labels and answer.strip():
        parts.append(_one_line(answer))
    elif isinstance(answer, list) and not labels:
        parts.extend(_one_line(str(a)) for a in answer if str(a).strip())
    elif isinstance(answer, dict):
        parts.append(
            "; ".join(
                f"{k}: {_render_value(v)}" for k, v in sorted(answer.items()) if _render_value(v)
            )
        )
    elif isinstance(answer, int | float) and not isinstance(answer, bool):
        parts.append(str(answer))
    if qa.custom_text and qa.custom_text.strip():
        parts.append(_one_line(qa.custom_text))
    return " / ".join(dict.fromkeys(p for p in parts if p))


def _qa_block(qa: QaRecord, answer_cap: int | None = None) -> str:
    answer = answer_text(qa)
    if answer_cap is not None and len(answer) > answer_cap:
        answer = answer[: max(0, answer_cap - 1)] + ELLIPSIS
    return f"{qa.card_id}: {_one_line(qa.card.question)}\nA: {answer or '(no answer)'}"


def _last_qa_text(qa_list: Sequence[QaRecord], count: int, limit: int) -> tuple[str, bool, int]:
    """The last ``count`` answered questions verbatim; the oldest are dropped first when they do not fit, and a
    single oversized answer is cut. Returns (text, truncated, dropped for budget)."""
    answered = [q for q in qa_list if q.status != "open"]
    latest = answered[-count:] if count > 0 else []
    blocks = [_qa_block(q) for q in latest]
    dropped = 0
    while len(blocks) > 1 and len("\n".join(blocks)) > limit:
        blocks.pop(0)
        latest = latest[1:]
        dropped += 1
    text = "\n".join(blocks)
    cut = False
    if len(text) > limit and latest:
        newest = latest[-1]
        lead = len(f"{newest.card_id}: {_one_line(newest.card.question)}\nA: ")
        text = _qa_block(newest, max(0, limit - lead))
        cut = True
    return text, dropped > 0 or cut, dropped


# ---------------------------------------------------------------------------------------------- build
def build_context(
    *,
    project_card: Mapping[str, JsonValue],
    facts: Iterable[Fact],
    needs_table: str,
    asked: Sequence[AskedItem | QaRecord],
    last_qa: Sequence[QaRecord],
    summary: InterviewSummary | None,
    budgets: ContextBudgets,
    relevant_topics: Iterable[str] = (),
) -> ContextPack:
    """The five sections, each within its budget, deterministic for identical inputs."""
    cpt = max(1, budgets.chars_per_token)

    card_text, card_cut = _project_card_text(project_card, max(0, budgets.project_card) * cpt)
    card_section = _section("project_card", card_text, budgets.project_card, cpt, card_cut)

    digest_section = _digest_section(list(facts), summary, budgets, relevant_topics)

    lines = needs_table.splitlines()
    needs_limit = max(0, budgets.needs_table) * cpt
    if len(needs_table) <= needs_limit:
        needs_section = _section("needs_table", needs_table, budgets.needs_table, cpt, False)
    else:
        note = "(+{n} more needs not shown)"
        kept: list[str] = []
        used = 0
        for line in lines:
            cost = len(line) + (1 if kept else 0)
            if used + cost + len(note.format(n=len(lines))) + 1 > needs_limit:
                break
            kept.append(line)
            used += cost
        dropped = len(lines) - len(kept)
        text = "\n".join([*kept, note.format(n=dropped)]) if kept else ""
        needs_section = _section("needs_table", text, budgets.needs_table, cpt, True, dropped)

    items = [a if isinstance(a, AskedItem) else AskedItem.from_qa(a) for a in asked]
    asked_text, asked_cut, asked_dropped = _asked_text(items, max(0, budgets.asked_list) * cpt)
    asked_section = _section(
        "asked_list", asked_text, budgets.asked_list, cpt, asked_cut, asked_dropped
    )

    qa_text, qa_cut, qa_dropped = _last_qa_text(
        last_qa, budgets.last_qa_count, max(0, budgets.last_qa) * cpt
    )
    qa_section = _section("last_qa", qa_text, budgets.last_qa, cpt, qa_cut, qa_dropped)

    return ContextPack(card_section, digest_section, needs_section, asked_section, qa_section)


# ------------------------------------------------------------------------------------------ full ledger
def ledger_line(fact: Fact) -> str:
    """One fact with everything the Brief compiler needs: the digest line plus confidence, origin, consumers,
    entities and the full verbatim quote."""
    parts = [
        digest_line(fact),
        f"conf {fact.confidence:.2f}",
        fact.origin + (" (quote unverified)" if fact.unverified_quote else ""),
    ]
    if fact.consumers:
        parts.append("consumers: " + ",".join(fact.consumers))
    if fact.entities:
        parts.append("entities: " + ",".join(fact.entities))
    if fact.quote.strip():
        parts.append('quote: "' + _one_line(fact.quote) + '"')
    return " | ".join(parts)


def full_ledger(facts: Iterable[Fact]) -> str:
    """Every ACTIVE fact with its quote, grouped by topic, never truncated (for ``cmp.compile_brief``)."""
    active = [f for f in facts if f.status == "active"]
    parts: list[str] = []
    for topic, members in group_by_topic(active):
        parts.append(f"# {topic}")
        parts.extend(ledger_line(f) for f in members)
    return "\n".join(parts)
