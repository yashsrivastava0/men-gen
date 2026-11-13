"""Wire models of ``cmp.update_summary`` (strict-schema-safe: every field required, no open objects).

The rolling summary is a flat list of topics. Each topic keeps its decisions, open issues, the user's own
vocabulary, tone notes and the ids of the facts it covers. The summary never replaces the fact ledger; it is the
compact view used only when the facts exceed the working-context budget (ADR 0015).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicWire(Wire):
    topic: str
    decisions: list[str]  # short plain statements of what was decided or stated
    open_issues: list[str]  # what is still unclear or unanswered for the topic
    vocabulary: list[str]  # the user's own words for things in this topic
    tone: list[str]  # notes on how the user wants it to feel or be said
    fact_ids: list[str]  # FCT-### ids of the facts this topic covers


class SummaryInput(Wire):
    language: str  # "en" | "hi" | "hi-Latn": the language the summary lines are written in
    previous_topics: list[TopicWire]  # the summary so far (empty the first time)
    new_facts_digest: str  # one line per new fact: FCT-014 [rule|pricing] statement (from Q-03)
    through_fact_id: str  # the newest fact the refreshed summary must cover


class SummaryOutput(Wire):
    topics: list[TopicWire]
    through_fact_id: str
