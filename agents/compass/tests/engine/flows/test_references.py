"""References: a named business is checked first, "exactly like" warns once, borrow/avoid come from one form."""

from __future__ import annotations

import time

import pytest

from mde_compass.catalog import load_catalog
from mde_compass.engine.flows import references as refs


@pytest.mark.parametrize(
    ("text", "brand"),
    [
        ("I own Swiggy and want a new delivery app for Pune restaurants", "Swiggy"),
        ("we run Fresh Bake Cafe in Jaipur", "Fresh Bake Cafe"),
        ("My company is Acme and I need an app", "Acme"),
        ("meri company Zoop hai", "Zoop"),
    ],
)
def test_a_business_named_as_the_users_own_is_found(text: str, brand: str) -> None:
    found = refs.own_business(text)
    assert found is not None and found[0] == brand


@pytest.mark.parametrize(
    "text",
    [
        "I own a bakery in Pune",  # lower-case noun, no brand
        "we are in Pune",  # a place, not a business
        "I want an app like Swiggy",
        "we run on Hindi and English",
        "",
    ],
)
def test_ordinary_text_names_no_own_business(text: str) -> None:
    assert refs.own_business(text) is None


def test_exactly_like_is_detected() -> None:
    assert refs.exact_copy_cue("exactly like Zomato, same logo and same screens")
    assert refs.exact_copy_cue("bilkul jaisa Zomato")
    assert not refs.exact_copy_cue("something like Zomato but simpler")


def test_matchers_are_linear_on_hostile_text() -> None:
    started = time.perf_counter()
    refs.own_business("I own " + "Aaaa " * 8000)
    refs.own_business("i own " * 8000)
    refs.exact_copy_cue("exactly " * 8000)
    assert time.perf_counter() - started < 2.0


def _ref(label: str, **kw: object) -> refs.RefInfo:
    return refs.RefInfo(label=label, **kw)  # type: ignore[arg-type]


def test_the_own_business_confirm_comes_first_and_only_once() -> None:
    a, b = _ref("Swiggy", claimed_own=True), _ref("Notion")
    assert refs.pending_confirm([a, b], set()) is a
    assert refs.pending_confirm([a, b], {"swiggy"}) is None
    assert refs.pending_confirm([_ref("Swiggy", claimed_own=True, confirmed=True)], set()) is None


def test_the_form_waits_for_the_confirm_and_is_asked_once() -> None:
    a, b = _ref("Swiggy", claimed_own=True), _ref("Notion")
    assert [r.label for r in refs.needs_form([a, b], form_asked=False)] == ["Notion"]
    assert refs.needs_form([a, b], form_asked=True) == []
    assert refs.needs_form([_ref("Notion", borrow=("clean lists",))], form_asked=False) == []
    assert refs.needs_form([], form_asked=False) == []


def test_the_confirm_card_names_the_brand_and_cannot_be_assumed() -> None:
    card = refs.confirm_card(
        _ref("Swiggy"), catalog=load_catalog(), card_id="Q-008", n=7, of_about=8, language="en"
    )
    assert card.frame == "apps_you_like" and "Swiggy" in card.question
    assert [o.id for o in card.options] == ["own", "like"]
    assert card.must_confirm and refs.flow_card(card)
    assert refs.confirm_label(card, "own") == "Swiggy"


def test_the_form_card_asks_borrow_and_avoid_for_each_reference() -> None:
    card = refs.form_card(
        [_ref("Swiggy"), _ref("Notion")],
        catalog=load_catalog(),
        card_id="Q-008",
        n=7,
        of_about=8,
        language="en",
    )
    assert card.type == "quick_form" and card.slots == ["scope.references"]
    assert [o.id for o in card.options] == ["borrow_0", "avoid_0", "borrow_1", "avoid_1"]
    assert "Swiggy" in card.options[0].label and "Notion" in card.options[3].label
    hi = refs.form_card(
        [_ref("Swiggy")], catalog=load_catalog(), card_id="Q-008", n=7, of_about=8, language="hi"
    )
    assert "Swiggy" in hi.question and hi.options[0].label != card.options[0].label


def test_a_form_answer_fills_borrow_and_avoid_and_confirms_the_records() -> None:
    card = refs.form_card(
        [_ref("Swiggy"), _ref("Notion")],
        catalog=load_catalog(),
        card_id="Q-008",
        n=7,
        of_about=8,
        language="en",
    )
    answer = {
        "borrow_0": "live tracking, fast re-order",
        "avoid_0": "",
        "borrow_1": ["simple pages"],
        "avoid_1": "clutter; ads",
    }
    form = refs.parse_form(card, answer)
    assert form["Swiggy"] == {"borrow": ["live tracking", "fast re-order"], "avoid": []}
    assert form["Notion"] == {"borrow": ["simple pages"], "avoid": ["clutter", "ads"]}
    held = [
        {"label": "Swiggy", "borrow": [], "avoid": [], "_meta": {"status": "assumed"}},
        {"label": "Notion", "_meta": {"status": "assumed"}},
    ]
    out = refs.apply_form(held, form)
    assert all("_meta" not in i for i in out)  # type: ignore[operator]
    assert out[0]["borrow"] == ["live tracking", "fast re-order"]  # type: ignore[index]


def test_a_slot_keyed_answer_changes_no_text_but_still_confirms() -> None:
    card = refs.form_card(
        [_ref("Swiggy")], catalog=load_catalog(), card_id="Q-008", n=7, of_about=8, language="en"
    )
    assert refs.parse_form(card, {"scope.references": [{"label": "Swiggy"}]}) == {
        "Swiggy": {"borrow": [], "avoid": []}
    }
    assert refs.parse_form(card, "text") == {}
    held = [{"label": "Swiggy", "_meta": {"status": "assumed"}}]
    assert "_meta" not in refs.apply_form(held, {})[0]  # type: ignore[operator]


def test_apply_form_confirms_only_the_asked_labels() -> None:
    held = [{"label": "Swiggy", "_meta": {"s": 1}}, {"label": "Notion", "_meta": {"s": 2}}]
    out = refs.apply_form(held, {}, labels={"notion"})
    assert "_meta" in out[0] and "_meta" not in out[1]  # type: ignore[operator]


def test_drop_removes_one_reference() -> None:
    held = [{"label": "Swiggy"}, {"label": "Notion"}]
    assert refs.drop(held, "swiggy") == [{"label": "Notion"}]


def test_words_typed_on_the_form_are_added_to_the_first_reference() -> None:
    card = refs.form_card(
        [_ref("Swiggy"), _ref("Notion")],
        catalog=load_catalog(),
        card_id="Q-008",
        n=7,
        of_about=8,
        language="en",
    )
    form: dict[str, dict[str, list[str]]] = {}
    refs.add_note(form, card, "live tracking; quick re-order")
    assert form == {"Swiggy": {"borrow": ["live tracking", "quick re-order"], "avoid": []}}
    refs.add_note(form, card, "   ")
    assert form["Swiggy"]["borrow"] == ["live tracking", "quick re-order"]
