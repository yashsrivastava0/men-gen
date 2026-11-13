import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.load import classify, compute_load
from mde_research_agent.core.text import dedupe_names, same_thing, stem, stems, tokens

CFG = load_catalog().load


def test_tokens_keep_indic_words_whole() -> None:
    word = "इनवॉइस"  # a Devanagari word with vowel signs
    assert tokens(f"GST {word}, 12%") == ["gst", word, "12"]
    kn = "ಸೆಮೆಸ್ಟರ್"
    assert tokens(kn) == [kn]


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("booking", "book"),
        ("payments", "pay"),
        ("classes", "class"),
        ("tracking", "track"),
        ("scheduling", "schedule"),
        ("drivers", "driver"),
    ],
)
def test_stem_pairs_meet(a: str, b: str) -> None:
    assert stem(a) == stem(b)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Online payment", "Pay for orders online", True),
        ("Load booking", "Load booking and tracking", True),  # one contains the other
        ("driver", "drivers", True),
        ("Class booking", "Class packs", False),
        ("Weekly class timetable", "Class booking", False),
        ("Trip tracking", "Load booking", False),
    ],
)
def test_same_thing(a: str, b: str, same: bool) -> None:
    assert same_thing(a, b) is same


def test_dedupe_keeps_the_first_id_as_representative() -> None:
    got = dedupe_names(
        [
            ("FTR-001", "Load booking"),
            ("FTR-002", "Trip tracking"),
            ("FTR-003", "Load booking and tracking"),
        ]
    )
    assert got == {"FTR-001": "FTR-001", "FTR-002": "FTR-002", "FTR-003": "FTR-001"}


@given(st.text(max_size=40))
def test_stems_never_raise_and_are_lowercase(text: str) -> None:
    for s in stems(text):
        assert s == s.casefold()


def test_classes_have_the_documented_boundaries_with_a_margin_of_three() -> None:
    assert CFG.class_margin == 3
    # a score within 3 points BELOW a boundary already uses the larger class: the cuts are 22 and 58
    assert [classify(n, CFG) for n in (0, 17, 21, 22, 23, 27, 57, 58, 60, 83, 200)] == [
        "focused",
        "focused",
        "focused",
        "standard",
        "standard",
        "standard",
        "standard",
        "deep",
        "deep",
        "deep",
        "deep",
    ]
    no_margin = CFG.model_copy(update={"class_margin": 0})
    assert [classify(n, no_margin) for n in (24, 25, 60, 61)] == [
        "focused",
        "standard",
        "standard",
        "deep",
    ]


def test_load_score_components_and_weights() -> None:
    info = compute_load(
        features=6, facts_of_kind=8, apps=1, sensitive_flags=0, named_references=0, config=CFG
    )
    assert (info.score, info.load_class) == (17, "focused")
    info = compute_load(
        features=24, facts_of_kind=32, apps=6, sensitive_flags=2, named_references=1, config=CFG
    )
    assert info.score == 24 + 32 + 18 + 10 + 2 == 86 and info.load_class == "deep"
    assert info.components.sensitive == 10 and info.components.apps == 18


@given(
    st.integers(0, 80), st.integers(0, 80), st.integers(0, 10), st.integers(0, 4), st.integers(0, 5)
)
def test_load_is_monotone_in_every_term(f: int, k: int, a: int, s: int, r: int) -> None:
    base = compute_load(
        features=f, facts_of_kind=k, apps=a, sensitive_flags=s, named_references=r, config=CFG
    ).score
    for kw in ("features", "facts_of_kind", "apps", "sensitive_flags", "named_references"):
        args = dict(features=f, facts_of_kind=k, apps=a, sensitive_flags=s, named_references=r)
        args[kw] += 1
        assert compute_load(**args, config=CFG).score > base
