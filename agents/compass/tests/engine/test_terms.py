from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.domain.interview import Fact
from mde_compass.engine.terms import (
    contains_project_term,
    extract_terms,
    jaccard,
    project_terms,
    stem,
    tokenize,
)

IDEAS = {
    "tiffin": "I want to build an app for a tiffin delivery service in Pune",
    "clinic": "An appointment booking app for my family clinic in Indore",
    "pharmacy": "Inventory management tool for B2B pharmacies",
    "kids": "A language learning app for children's classes",
    "seniors": "Ride-sharing app for senior citizens",
}
EXPECTED = {
    "tiffin": ["tiffin", "delivery", "pune"],
    "clinic": ["appointment", "booking", "family", "clinic", "indore"],
    "pharmacy": ["inventory", "b2b", "pharmacies"],
    "kids": ["language", "learning", "children", "classes"],
    "seniors": ["ride", "sharing", "senior", "citizens"],
}


@pytest.mark.parametrize("key", list(IDEAS))
def test_extract_terms_for_the_five_ideas(key: str) -> None:
    assert extract_terms(IDEAS[key]) == EXPECTED[key]


def test_stop_words_and_generic_words_are_dropped() -> None:
    assert extract_terms("I would like to make an app for my users and customers") == []
    assert extract_terms("the app is for the people who are in the city") == ["city"]


def test_dedupe_keeps_first_seen_order() -> None:
    assert extract_terms("Pune tiffin, tiffin in PUNE, delivery") == ["pune", "tiffin", "delivery"]


def test_hyphens_apostrophes_digits() -> None:
    assert tokenize("Ride-sharing") == ["ride", "sharing"]
    assert tokenize("children's and seniors' rides") == ["children", "and", "seniors", "rides"]
    assert tokenize("it's don't") == ["it", "dont"]
    assert extract_terms("5 kids, 12 pharmacies, 24x7 support, b2b") == [
        "kids",
        "pharmacies",
        "24x7",
        "support",
        "b2b",
    ]
    assert tokenize("") == [] and tokenize("  ... !!  ") == []


def test_typographic_apostrophes() -> None:
    assert tokenize("children’s") == ["children"]


def test_hindi_keeps_matras_and_drops_hindi_stop_words() -> None:
    terms = extract_terms("मुझे पुणे में टिफिन सर्विस का ऐप बनाना है", language="hi")
    assert terms == ["पुणे", "टिफिन"]
    clinic = extract_terms("इंदौर में फैमिली क्लिनिक के लिए अपॉइंटमेंट ऐप", language="hi")
    assert clinic == ["इंदौर", "फैमिली", "क्लिनिक", "अपॉइंटमेंट"]


def test_hinglish() -> None:
    assert extract_terms("Mujhe Pune mein tiffin delivery ka app banana hai") == [
        "pune",
        "tiffin",
        "delivery",
    ]
    assert extract_terms("meri clinic ke liye appointment app chahiye") == ["clinic", "appointment"]


def test_language_argument_does_not_change_the_result() -> None:
    text = "Mujhe tiffin delivery in Pune चाहिए"
    assert extract_terms(text, language="hi") == extract_terms(text, language="en")


def test_long_input_is_bounded() -> None:
    text = "tiffin " * 100_000
    assert extract_terms(text) == ["tiffin"]


# ---------------------------------------------------------------------------------------- project_terms
def _fact(
    n: int, statement: str, *, entities: list[str] | None = None, status: str = "active"
) -> Fact:
    return Fact.model_validate(
        {
            "id": f"FCT-{n:03d}",
            "type": "context",
            "statement": statement,
            "entities": entities or [],
            "status": status,
        }
    )


def test_project_terms_merges_idea_facts_glossary_and_extra() -> None:
    terms = project_terms(
        IDEAS["tiffin"],
        [
            _fact(1, "Customers pay weekly by UPI", entities=["Dabbawala Union"]),
            _fact(2, "Old idea was a pizza shop", status="superseded"),
        ],
        glossary=["Thali plan", "ride sharing"],
        extra=["Annapurna"],
    )
    assert {"tiffin", "delivery", "pune", "weekly", "upi", "dabbawala", "union"} <= terms
    assert {"thali", "plan", "ride", "sharing", "annapurna"} <= terms
    assert "pizza" not in terms  # superseded facts no longer count (a pivot drops the old words)
    assert "app" not in terms and "customers" not in terms


def test_project_terms_empty() -> None:
    assert project_terms("") == frozenset()
    assert project_terms("an app for my users") == frozenset()


# ----------------------------------------------------------------------------------- contains_project_term
def test_contains_is_word_aware_and_case_folded() -> None:
    terms = frozenset({"pune", "tiffin", "art"})
    assert contains_project_term("Should customers in PUNE pick a meal?", terms)
    assert contains_project_term("Which tiffins do you deliver?", terms)
    assert not contains_project_term("Where do you start?", terms)  # 'art' is not 'start'
    assert not contains_project_term("Punekar customers?", terms)  # a different word
    assert not contains_project_term("How will people pay?", terms)
    assert not contains_project_term("anything", frozenset())
    assert not contains_project_term("", terms)


def test_contains_folds_plural_and_possessive() -> None:
    assert contains_project_term("Which pharmacies will use it?", {"pharmacy"})
    assert contains_project_term("the pharmacy's stock", {"pharmacies"})
    assert contains_project_term("Pune's weather", {"pune"})
    assert contains_project_term("Will the children log in?", {"child"})
    assert contains_project_term("Which child profiles?", {"children"})
    assert contains_project_term("seniors' rides", {"senior"})
    assert contains_project_term("classes", {"class"})


def test_contains_multi_word_terms_need_the_words_in_a_row() -> None:
    assert contains_project_term("Is ride sharing allowed?", {"ride sharing"})
    assert not contains_project_term("Is sharing a ride allowed?", {"ride sharing"})
    assert contains_project_term("ride-sharing", {"ride sharing"})


def test_contains_hindi_and_hinglish() -> None:
    assert contains_project_term("पुणे में टिफिन कैसे पहुँचेगा?", {"टिफिन"})
    assert not contains_project_term("टिफिन कैसे पहुँचेगा?", {"क्लिनिक"})
    assert contains_project_term("Pune mein tiffin kab aayega?", {"tiffin"})


def test_each_question_matches_only_its_own_idea() -> None:
    questions = {
        "tiffin": "Should customers pick a tiffin each day or subscribe weekly?",
        "clinic": "Which doctors take appointments at your family clinic?",
        "pharmacy": "How does a pharmacy reorder stock when inventory runs low?",
        "kids": "What age groups are your language classes for?",
        "seniors": "Who drives the ride, a volunteer or a paid driver?",
    }
    terms = {k: project_terms(v) for k, v in IDEAS.items()}
    for asked, text in questions.items():
        for idea, idea_terms in terms.items():
            assert contains_project_term(text, idea_terms) == (asked == idea), (asked, idea)


def test_generic_questions_contain_no_project_term_for_any_idea() -> None:
    generic = ["What features do you want?", "Who will use your app?", "How should users sign in?"]
    for idea in IDEAS.values():
        terms = project_terms(idea)
        assert not any(contains_project_term(q, terms) for q in generic)


# ------------------------------------------------------------------------------------------------ jaccard
def test_jaccard() -> None:
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(["a", "a"], ["a"]) == 1.0
    assert jaccard([], ["a"]) == 0.0 and jaccard([], []) == 0.0
    assert jaccard({"a"}, {"b"}) == 0.0


def test_the_five_ideas_have_no_shared_terms() -> None:
    sets = {k: frozenset(extract_terms(v)) for k, v in IDEAS.items()}
    keys = list(sets)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            assert jaccard(sets[a], sets[b]) == 0.0


def test_stem() -> None:
    assert [
        stem(w) for w in ("pharmacies", "appointments", "classes", "status", "bus", "kids", "b2b")
    ] == [
        "pharmacy",
        "appointment",
        "class",
        "status",
        "bus",
        "kid",
        "b2b",
    ]
    assert stem("children") == "child" and stem("पुणे") == "पुणे"


# --------------------------------------------------------------------------------------------- properties
@settings(max_examples=100)
@given(st.text(max_size=300))
def test_extract_terms_is_deterministic_unique_and_self_matching(text: str) -> None:
    terms = extract_terms(text)
    assert terms == extract_terms(text)
    assert len(terms) == len(set(terms))
    if terms:
        assert contains_project_term(text, terms)
    assert contains_project_term(text, []) is False


@settings(max_examples=100)
@given(
    st.sets(st.text(alphabet="abcdef", min_size=1, max_size=5)),
    st.sets(st.text(alphabet="abcdef", min_size=1, max_size=5)),
)
def test_jaccard_properties(a: set[str], b: set[str]) -> None:
    assert jaccard(a, b) == jaccard(b, a)
    assert 0.0 <= jaccard(a, b) <= 1.0
    if a:
        assert jaccard(a, a) == 1.0
