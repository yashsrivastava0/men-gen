from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from mde_compass.config import ContextBudgets
from mde_compass.domain.cards import Card, CardOption
from mde_compass.domain.interview import Fact, InterviewSummary, QaRecord, SummaryTopic
from mde_compass.engine.context_pack import (
    AskedItem,
    ContextPack,
    answer_text,
    build_context,
    estimate_tokens,
    full_ledger,
    ledger_line,
)

CARD: dict[str, JsonValue] = {
    "idea": "tiffin delivery service",
    "geography": "Pune",
    "language": "en",
    "apps": ["customer", "rider"],
}


def fact(
    n: int, topic: str = "menu", conf: float = 0.5, text: str | None = None, **kw: object
) -> Fact:
    base: dict[str, object] = {
        "id": f"FCT-{n:03d}",
        "type": "rule",
        "statement": text or f"statement number {n} about {topic}",
        "topics": [topic],
        "confidence": conf,
        "quote": f"quote {n}",
        "qa_card_id": f"Q-{n:02d}",
    }
    base.update(kw)
    return Fact.model_validate(base)


def qa(
    n: int,
    question: str | None = None,
    *,
    answer: JsonValue = None,
    custom: str | None = None,
    status: str = "answered",
    options: list[CardOption] | None = None,
    intent: str = "",
) -> QaRecord:
    card = Card(
        id=f"Q-{n:02d}",
        frame="planner",
        slots=[],
        type="choose_one" if options else "open_prompt",
        question=question or f"Question number {n}?",
        options=options or [],
        intent=intent or f"intent {n}",
    )
    return QaRecord.model_validate(
        {
            "card_id": card.id,
            "card": card.model_dump(),
            "status": status,
            "answer": answer,
            "custom_text": custom,
        }
    )


def ctx(**kw: object) -> ContextPack:
    base: dict[str, object] = {
        "project_card": CARD,
        "facts": [],
        "needs_table": "",
        "asked": [],
        "last_qa": [],
        "summary": None,
        "budgets": ContextBudgets(),
    }
    base.update(kw)
    return build_context(**base)  # type: ignore[arg-type]


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2
    assert estimate_tokens("abcdef", 3) == 2 and estimate_tokens("abc", 0) == 3


# ---------------------------------------------------------------------------------------- small inputs
def test_small_inputs_pass_through_untruncated_in_fixed_order() -> None:
    pack = ctx(
        facts=[fact(1, "pricing"), fact(2, "menu")],
        needs_table="idea.summary | must | impact 5 | missing",
        asked=[AskedItem("Q-01", "who orders", "answered")],
        last_qa=[qa(1, answer="Offices", status="answered")],
    )
    assert [s.name for s in pack.sections] == [
        "project_card",
        "fact_digest",
        "needs_table",
        "asked_list",
        "last_qa",
    ]
    assert (
        pack.project_card.text
        == "apps: customer, rider\ngeography: Pune\nidea: tiffin delivery service\nlanguage: en"
    )
    assert pack.fact_digest.text.splitlines() == [
        "# menu",
        "FCT-002 [rule|menu] statement number 2 about menu (from Q-02)",
        "# pricing",
        "FCT-001 [rule|pricing] statement number 1 about pricing (from Q-01)",
    ]
    assert pack.needs_table.text == "idea.summary | must | impact 5 | missing"
    assert pack.asked_list.text == "Q-01 | answered | who orders"
    assert pack.last_qa.text == "Q-01: Question number 1?\nA: Offices"
    assert not any(pack.truncated.values())
    assert pack.total_tokens == sum(s.tokens for s in pack.sections)
    assert set(pack.as_vars()) == {s.name for s in pack.sections}


def test_empty_everything() -> None:
    pack = ctx()
    assert pack.fact_digest.text == "" and pack.needs_table.text == "" and pack.last_qa.text == ""
    assert not any(pack.truncated.values())


def test_project_card_is_independent_of_key_order_and_skips_blanks() -> None:
    a = ctx(project_card={"b": "2", "a": "1", "c": "", "d": None, "e": []})
    b = ctx(project_card={"a": "1", "e": [], "d": None, "c": "", "b": "2"})
    assert a.project_card.text == b.project_card.text == "a: 1\nb: 2"


def test_project_card_long_values_shrink_to_the_budget() -> None:
    card: dict[str, JsonValue] = {"idea": "x" * 5000, "geo": "Pune"}
    pack = ctx(project_card=card, budgets=ContextBudgets(project_card=50))
    assert pack.project_card.truncated
    assert pack.project_card.tokens <= 50 and "geo: Pune" in pack.project_card.text


# --------------------------------------------------------------------------------------- fact digest
def test_fact_digest_over_budget_keeps_relevant_topics_and_adds_summary() -> None:
    facts = [
        fact(i, ["menu", "pricing", "staff", "route"][i % 4], conf=0.3 + (i % 5) / 10)
        for i in range(1, 61)
    ]
    summary = InterviewSummary(
        version=1,
        topics=[
            SummaryTopic(topic="staff", decisions=["two riders"], fact_ids=["FCT-002"]),
            SummaryTopic(
                topic="route",
                decisions=["Kothrud first"],
                vocabulary=["dabba"],
                fact_ids=["FCT-003"],
            ),
        ],
    )
    budgets = ContextBudgets(fact_digest=300)
    pack = ctx(facts=facts, summary=summary, budgets=budgets, relevant_topics=["pricing"])
    section = pack.fact_digest
    assert section.truncated and section.dropped > 0
    assert section.tokens <= 300
    assert "[rule|pricing]" in section.text
    assert "Earlier details (summary):" in section.text
    assert "route | decisions: Kothrud first | words: dabba" in section.text
    # every pricing fact that is shown ranks above every one that was dropped
    shown = {f.id for f in facts if f.id in section.text}
    pricing = [f for f in facts if f.topics == ["pricing"]]
    if any(f.id not in shown for f in pricing):
        assert all(f.topics == ["pricing"] for f in facts if f.id in shown)


def test_fact_digest_over_budget_without_a_summary_still_fits() -> None:
    facts = [fact(i) for i in range(1, 80)]
    pack = ctx(facts=facts, budgets=ContextBudgets(fact_digest=100))
    assert pack.fact_digest.truncated and pack.fact_digest.tokens <= 100
    assert "more facts omitted" in pack.fact_digest.text


def test_summary_topics_of_dropped_facts_come_first() -> None:
    facts = [fact(i, text="y" * 80) for i in range(1, 30)]
    summary = InterviewSummary(
        topics=[
            SummaryTopic(topic="kept", decisions=["a"], fact_ids=["FCT-029"]),
            SummaryTopic(topic="gone", decisions=["b"], fact_ids=["FCT-001"]),
        ]
    )
    pack = ctx(facts=facts, summary=summary, budgets=ContextBudgets(fact_digest=200))
    text = pack.fact_digest.text
    assert text.index("gone |") < text.index("kept |")


# ------------------------------------------------------------------------------------- needs and asked
def test_needs_table_is_cut_at_line_ends_with_a_count() -> None:
    table = "\n".join(
        f"need.{i} | must | impact 5 | missing | sufficient: " + "z" * 40 for i in range(40)
    )
    pack = ctx(needs_table=table, budgets=ContextBudgets(needs_table=100))
    section = pack.needs_table
    assert section.truncated and section.tokens <= 100 and section.dropped > 0
    lines = section.text.splitlines()
    assert (
        lines[0].startswith("need.0 |")
        and lines[-1] == f"(+{section.dropped} more needs not shown)"
    )
    assert all(line.startswith("need.") for line in lines[:-1])


def test_asked_list_keeps_the_newest_and_accepts_records() -> None:
    items = [AskedItem(f"Q-{i:02d}", f"intent number {i}", "answered") for i in range(1, 60)]
    pack = ctx(asked=items, budgets=ContextBudgets(asked_list=80))
    section = pack.asked_list
    assert section.truncated and section.tokens <= 80 and section.dropped > 0
    assert section.text.splitlines()[0] == f"({section.dropped} earlier questions not shown)"
    assert section.text.splitlines()[-1].startswith("Q-59 |")
    from_records = ctx(asked=[qa(1, intent="learn who orders", status="skipped")])
    assert from_records.asked_list.text == "Q-01 | skipped | learn who orders"


# --------------------------------------------------------------------------------------------- last Q&A
def test_last_qa_is_verbatim_and_limited_to_n() -> None:
    options = [CardOption(id="a", label="Daily"), CardOption(id="b", label="Weekly")]
    records = [
        qa(1, answer="old answer"),
        qa(2, answer="b", options=options),
        qa(3, answer=["a", "b"], options=options),
        qa(4, answer="free text", custom="with a typed note"),
        qa(5, status="skipped"),
        qa(6, status="open"),  # not answered yet: never shown
    ]
    pack = ctx(last_qa=records, budgets=ContextBudgets(last_qa_count=4))
    assert pack.last_qa.text.splitlines() == [
        "Q-02: Question number 2?",
        "A: Weekly",
        "Q-03: Question number 3?",
        "A: Daily / Weekly",
        "Q-04: Question number 4?",
        "A: free text / with a typed note",
        "Q-05: Question number 5?",
        "A: [skipped]",
    ]
    assert not pack.last_qa.truncated  # the window of N is by design, not a cut


def test_last_qa_drops_the_oldest_to_fit_and_cuts_one_oversized_answer() -> None:
    records = [qa(i, answer="a" * 200) for i in range(1, 5)]
    pack = ctx(last_qa=records, budgets=ContextBudgets(last_qa=120))
    assert pack.last_qa.truncated and pack.last_qa.dropped > 0 and pack.last_qa.tokens <= 120
    assert pack.last_qa.text.startswith("Q-0") and "Q-04" in pack.last_qa.text
    huge = ctx(last_qa=[qa(1, answer="b" * 5000)], budgets=ContextBudgets(last_qa=60))
    assert huge.last_qa.truncated and huge.last_qa.tokens <= 60
    assert huge.last_qa.text.startswith("Q-01: Question number 1?")


def test_answer_text_forms() -> None:
    options = [CardOption(id="a", label="Daily")]
    assert answer_text(qa(1, answer="a", options=options)) == "Daily"
    assert answer_text(qa(1, answer={"city": "Pune", "n": 2, "x": ""})) == "city: Pune; n: 2"
    assert answer_text(qa(1, answer=42)) == "42"
    assert answer_text(qa(1, answer="3 cities", custom="3 cities")) == "3 cities"
    assert answer_text(qa(1, status="you_decide")) == "[you_decide]"
    assert answer_text(qa(1, answer=None)) == ""


# ---------------------------------------------------------------------------------------- determinism
def test_output_ignores_timestamps_and_input_order() -> None:
    a = [fact(i, ["menu", "pricing"][i % 2]) for i in range(1, 8)]
    b = [
        f.model_copy(
            update={
                "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
            }
        )
        for f in reversed(a)
    ]
    assert ctx(facts=a).as_vars() == ctx(facts=b).as_vars()
    assert ctx(facts=a).as_vars() == ctx(facts=a).as_vars()


@settings(max_examples=80, deadline=None)
@given(
    n_facts=st.integers(0, 60),
    fact_budget=st.integers(0, 300),
    card_budget=st.integers(0, 100),
    needs_budget=st.integers(0, 150),
    asked_budget=st.integers(0, 150),
    qa_budget=st.integers(0, 150),
    cpt=st.integers(1, 6),
    n_asked=st.integers(0, 40),
    n_qa=st.integers(0, 8),
    long_answer=st.integers(0, 600),
    relevant=st.lists(st.sampled_from(["menu", "pricing", "staff", "none"]), max_size=3),
    with_summary=st.booleans(),
)
def test_sections_never_exceed_their_budgets_and_are_deterministic(
    n_facts: int,
    fact_budget: int,
    card_budget: int,
    needs_budget: int,
    asked_budget: int,
    qa_budget: int,
    cpt: int,
    n_asked: int,
    n_qa: int,
    long_answer: int,
    relevant: list[str],
    with_summary: bool,
) -> None:
    budgets = ContextBudgets(
        project_card=card_budget,
        fact_digest=fact_budget,
        needs_table=needs_budget,
        asked_list=asked_budget,
        last_qa=qa_budget,
        chars_per_token=cpt,
    )
    topics = ["menu", "pricing", "staff"]
    facts = [fact(i, topics[i % 3], conf=(i % 10) / 10) for i in range(1, n_facts + 1)]
    summary = (
        InterviewSummary(
            topics=[SummaryTopic(topic="menu", decisions=["d" * 80], fact_ids=["FCT-003"])]
        )
        if with_summary
        else None
    )
    kwargs: dict[str, object] = {
        "project_card": {"idea": "x" * 300, "geo": "Pune", "apps": ["a", "b"]},
        "facts": facts,
        "needs_table": "\n".join(f"need.{i} | must | impact 5 | missing" for i in range(30)),
        "asked": [AskedItem(f"Q-{i:02d}", "intent " * 5, "answered") for i in range(n_asked)],
        "last_qa": [qa(i, answer="w" * long_answer) for i in range(1, n_qa + 1)],
        "summary": summary,
        "budgets": budgets,
        "relevant_topics": relevant,
    }
    pack = build_context(**kwargs)  # type: ignore[arg-type]
    for section, budget in zip(
        pack.sections,
        (card_budget, fact_budget, needs_budget, asked_budget, qa_budget),
        strict=True,
    ):
        assert section.budget == budget
        assert section.tokens == estimate_tokens(section.text, cpt)
        assert section.tokens <= budget, (section.name, section.tokens, budget)
        assert len(section.text) <= budget * cpt
    assert pack.as_vars() == build_context(**kwargs).as_vars()  # type: ignore[arg-type]
    kwargs["facts"] = list(reversed(facts))
    assert pack.fact_digest.text == build_context(**kwargs).fact_digest.text  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------- full ledger
def test_full_ledger_has_every_active_fact_with_its_quote() -> None:
    long_quote = "I run a tiffin kitchen. " * 40
    facts = [
        fact(1, "pricing", quote="we charge 80 rupees a day", consumers=["research", "design"]),
        fact(2, "menu", quote=long_quote, entities=["Annapurna"], unverified_quote=True),
        fact(3, status="superseded"),
        fact(4, "pricing", origin="assumption", quote=""),
    ]
    ledger = full_ledger(facts)
    lines = ledger.splitlines()
    assert lines[0] == "# menu" and "# pricing" in lines
    assert "FCT-003" not in ledger
    assert ledger_line(facts[0]) == (
        "FCT-001 [rule|pricing] statement number 1 about pricing (from Q-01)"
        " | conf 0.50 | stated | consumers: research,design"
        ' | quote: "we charge 80 rupees a day"'
    )
    assert " ".join(long_quote.split()) in ledger  # the whole quote, not cut
    assert "(quote unverified)" in ledger and "entities: Annapurna" in ledger
    assert "FCT-004" in ledger and ledger_line(facts[3]).endswith("| assumption")


def test_full_ledger_never_truncates() -> None:
    facts = [fact(i, ["a", "b", "c"][i % 3]) for i in range(1, 2001)]
    ledger = full_ledger(facts)
    assert all(f"FCT-{i:03d} " in ledger for i in (1, 500, 2000))
    assert sum(1 for line in ledger.splitlines() if line.startswith("FCT-")) == 2000
    assert full_ledger([]) == ""


@pytest.mark.parametrize("n", [0, 1, 5])
def test_ledger_is_deterministic(n: int) -> None:
    facts = [fact(i) for i in range(1, n + 1)]
    assert full_ledger(facts) == full_ledger(list(reversed(facts)))
