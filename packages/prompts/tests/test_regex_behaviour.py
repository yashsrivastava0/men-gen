"""Rule-based handlers keep their behaviour after the linear-time rewrite (security review 2).

The patterns and loops that were rewritten (phrase search, negation scan, ``add a ... screen``,
name and reference extraction, the change-request hedge) are compared with the previous
implementations, kept here as the reference, on ordinary and on randomised small inputs. The guard
that every pattern stays linear on hostile input is ``tests/security/test_regex_linearity.py``.
"""

from __future__ import annotations

import random
import re

import pytest

from mde_contracts.calls import C1Input, C14Input
from mde_prompts.calls.c1_understand import keyword as k1
from mde_prompts.calls.c1_understand import lexicon as lx
from mde_prompts.calls.c14_interpret_change import rules as r14

# ----------------------------------------------------------------------------- reference code


def _old_keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    hay = lx.normalize(text)
    return [k for k in keywords if lx.normalize(k) and lx._word_pattern(k).search(hay)]  # pyright: ignore[reportPrivateUsage]


def _old_negated(text: str, keywords: tuple[str, ...]) -> bool:
    toks = lx.words(text)
    for kw in keywords:
        kt = lx.normalize(kw).split()
        if not kt:
            continue
        for i in range(len(toks) - len(kt) + 1):
            if toks[i : i + len(kt)] == kt:
                before = toks[max(0, i - 3) : i]
                after = toks[i + len(kt) : i + len(kt) + 4]
                if k1.NEGATION_BEFORE & set(before) or k1.NEGATION_AFTER & set(after):
                    return True
    return False


_OLD_NAME = re.compile(
    r"(?:\bcalled\b|\bnamed\b|\bname is\b|\bname it\b|\bcall it\b|\bcall the app\b|\bapp name is\b"
    r"|\bnaam\b|\bनाम\b)\s*[:\-]?\s*[\"“']?([^\"”',.;!?\n]{2,38})",
    re.IGNORECASE,
)
_OLD_JAISA = re.compile(r"\b([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)?)\s+(?:jaisa|jaise|ki tarah)\b")
_OLD_HEDGE = re.compile(
    r"\b(?:but|lekin|magar|however)\b|[,;]\s*(?:and\s+)?(?=make|change|add|remove)", re.I
)

# ----------------------------------------------------------------------------- helpers


def test_squash_spaces_and_clip() -> None:
    assert lx.squash_spaces("a \t\n b 　c") == "a b c"
    assert lx.clip("abcdef", 3) == "abc"
    assert lx.clip("abc", 3) == "abc"
    assert len(lx.clip("x" * (lx.MAX_SCAN_CHARS + 5))) == lx.MAX_SCAN_CHARS


def test_normalize_memoisation_does_not_change_the_result() -> None:
    long_text = ("Hello, World! It's  FINE " * 200) + "नमस्ते"
    assert lx.normalize(long_text) == lx.normalize(long_text)
    assert lx._normalize(long_text) == lx.normalize(long_text)  # pyright: ignore[reportPrivateUsage]
    assert lx.normalize("  A  b ") == "a b"


# ----------------------------------------------------------------------------- phrase search

_VOCAB = [
    "shop", "cart", "no", "payments", "reviews", "nahi", "without", "app", "login", "pay online",
    "cash on delivery", "wishlist", "dark", "mode", "the", "a", "and", "hata", "ratings", "pay",
    "online", "delivery", "cash",
]  # fmt: skip
_KEYWORD_SETS = [
    ("cart", "pay online", "cash on delivery", "payments", "pay"),
    ("reviews", "ratings", "no payments", "wishlist"),
    ("dark mode", "login", "cart", ""),
]


def test_keyword_hits_and_negation_equal_the_reference_on_random_texts() -> None:
    rng = random.Random(99)
    for _ in range(3000):
        text = " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(0, 12)))
        for kws in _KEYWORD_SETS:
            assert lx.keyword_hits(text, kws) == _old_keyword_hits(text, kws), (text, kws)
            assert k1._negated(text, kws) == _old_negated(text, kws), (text, kws)  # pyright: ignore[reportPrivateUsage]
            phrases = tuple(k for k in kws if k)
            assert lx.has_phrase(text, phrases) == bool(_old_keyword_hits(text, phrases)), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("no payments please", True),
        ("payments nahi chahiye", True),
        ("I want payments", False),
        ("without cart or reviews", True),
        ("the shop has a cart and ratings", False),
    ],
)
def test_negation_examples(text: str, expected: bool) -> None:
    assert k1._negated(text, ("payments", "cart", "reviews")) is expected  # pyright: ignore[reportPrivateUsage]


# ----------------------------------------------------------------------------- C14 rules


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("add a wishlist screen", "wishlist"),
        ("Add the order history page", "order history"),
        ("add one loyalty tab please", "loyalty"),
        ("also add favourites screen and then change the look", "favourites"),
        ("add an FAQ tab", "FAQ"),  # the old pattern read the article "an" as "a" + "n FAQ"
        ("add awesome gallery screen", "awesome gallery"),  # and "awesome" as "a" + "wesome"
        ("add a wishlist screen and a profile page", "wishlist"),  # the first, not the last
    ],
)
def test_add_screen_names(text: str, name: str) -> None:
    match = r14.ADD_SCREEN.search(lx.squash_spaces(text))
    assert match is not None and match.group(1) == name


def test_add_screen_does_not_match_without_a_screen_word_or_with_a_tiny_name() -> None:
    assert r14.ADD_SCREEN.search("add a wishlist") is None
    assert r14.ADD_SCREEN.search("add ui screen") is None  # a name is 3 to 60 characters


def test_hedge_equals_the_old_pattern() -> None:
    rng = random.Random(5)
    words = ["looks", "good", "but", "make", "it", "blue", ",", ";", "and", "add", "however", "x"]
    for _ in range(3000):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 10)))
        a, b = _OLD_HEDGE.search(text), r14._HEDGE.search(text)  # pyright: ignore[reportPrivateUsage]
        assert (a.end() if a else None) == (b.end() if b else None), text


def _change(message: str) -> C14Input:
    return C14Input(
        message=message, state="PLAN_REVIEW", gate_open="plan", selected_screen_id="SCR-004"
    )


def test_a_screen_request_pads_and_clips_to_the_same_answer() -> None:
    plain = r14.interpret(_change("add a wishlist screen"))
    padded = r14.interpret(_change("add   a \n\t  wishlist    screen"))
    assert plain.ops == padded.ops and plain.ops[0].kind == "add_screen"
    assert plain.ops[0].instruction == "wishlist"


def test_the_rules_read_only_the_first_part_of_a_huge_message() -> None:
    message = "add a wishlist screen. " + "blah " * 20_000
    assert len(message) > r14.MAX_MESSAGE_CHARS
    assert r14.interpret(_change(message)).ops[0].instruction == "wishlist"


# ----------------------------------------------------------------------------- names and references


def test_name_pattern_equals_the_old_pattern_on_ordinary_sentences() -> None:
    rng = random.Random(21)
    words = ["called", "named", "name is", "name it", "call it", "naam", "Marketly", "Fresh", "shop",
             ":", "-", '"', ",", ".", "my", "app", "is", "X", "AB", "a"]  # fmt: skip
    for _ in range(3000):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 8)))
        a, b = _OLD_NAME.search(text), k1.NAME_PATTERN.search(text)
        assert (a.group(1) if a else None) == (b.group(1) if b else None), text


def test_reference_patterns_equal_the_old_ones_on_ordinary_sentences() -> None:
    rng = random.Random(8)
    words = ["like", "similar to", "inspired by", "Swiggy", "Uber", "Eats", "the", "app", "but",
             "jaisa", "ki tarah", "Zomato", "pets", "R&D", "O'Neil"]  # fmt: skip
    for _ in range(3000):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 8)))
        a = [m.group(1) for m in _OLD_JAISA.finditer(text)]
        b = [m.group(1) for m in k1._JAISA_PATTERN.finditer(text)]  # pyright: ignore[reportPrivateUsage]
        assert a == b, text


def test_references_and_name_still_work_in_a_sentence() -> None:
    refs = {r.text for r in k1.extract_references("an app like Swiggy for pets, Zomato jaisa")}
    assert {"Swiggy", "Zomato"} <= refs
    assert k1.extract_name("my shop is called Marketly Fresh, for groceries") == "Marketly Fresh"
    assert k1.extract_name("called\n\n\n\nMarketly") == "Marketly"


def test_handlers_read_a_bounded_prefix() -> None:
    long_msg = "I want a shop app called Marketly with a cart. " + "filler " * 20_000
    out = k1.understand(C1Input(message=long_msg))
    assert out.intent in {"idea", "volunteered"}
    assert any(u.slot_id == "brand.name" for u in out.slot_updates)
