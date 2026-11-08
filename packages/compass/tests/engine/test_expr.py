from __future__ import annotations

import time

import pytest

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.slots import SlotRecord
from mde_compass.engine.expr import MAX_EXPR_LEN, ExprError, holds, referenced_slots


def rec(slot_id: str, value: object, status: str = "volunteered") -> SlotRecord:
    return SlotRecord(slot_id=slot_id, value=value, status=status)  # type: ignore[arg-type]


SLOTS = {
    "idea.category": rec("idea.category", "commerce"),
    "idea.summary": rec("idea.summary", "a shop"),
    "audience.segments": rec("audience.segments", ["students", "parents"]),
    "empty.one": rec("empty.one", None, "empty"),
}


def test_none_and_blank_hold() -> None:
    assert holds(None, {}) and holds("", {}) and holds("   ", {})


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("filled('idea.summary')", True),
        ("filled('idea.missing')", False),
        ("empty('idea.missing')", True),
        ("empty('empty.one')", True),
        ("value('idea.category') == 'commerce'", True),
        ("value('idea.category') != 'commerce'", False),
        ("value('idea.category') != 'internal_tool'", True),
        ("value('idea.category') in ['commerce', 'booking']", True),
        ("value('idea.category') not in ['commerce']", False),
        ("value('audience.segments') == 'students'", True),
        ("value('audience.segments') in ['x', 'parents']", True),
        ("value('idea.missing') != 'x'", True),
        ("value('idea.missing') == 'x'", False),
        ("filled('idea.summary') and value('idea.category') != 'internal_tool'", True),
        ("filled('idea.missing') or filled('idea.summary')", True),
        ("not filled('idea.missing')", True),
        ("not (filled('idea.summary') and empty('idea.summary'))", True),
        ('filled("idea.summary")', True),
    ],
)
def test_holds(expr: str, expected: bool) -> None:
    assert holds(expr, SLOTS) is expected


@pytest.mark.parametrize(
    "bad",
    [
        "filled(idea.summary)",
        "filled('a'",
        "foo('a')",
        "value('a') = 'x'",
        "filled('a') and",
        "(filled('a')",
        "filled('a') filled('b')",
        "value('a') in 'x'",
        "__import__('os')",
        "1 + 1",
        "filled('a') ; x",
    ],
)
def test_bad_expressions_raise_expr_error(bad: str) -> None:
    with pytest.raises(ExprError):
        holds(bad, SLOTS)


def test_never_evaluates_python() -> None:
    with pytest.raises(ExprError):
        holds("__import__('os').system('echo hi')", SLOTS)
    with pytest.raises(ExprError):
        holds("filled('a').__class__", SLOTS)


def test_referenced_slots() -> None:
    expr = "filled('a.b') and (value('c') in ['x'] or not empty('d.e'))"
    assert referenced_slots(expr) == {"a.b", "c", "d.e"}
    assert referenced_slots("") == set()


def test_every_catalog_ask_if_parses_and_evaluates() -> None:
    cat = load_catalog()
    for slot in cat.slots.values():
        holds(slot.ask_if, {})
        assert referenced_slots(slot.ask_if or "") <= set(cat.slots)


def test_hostile_input_is_rejected_quickly() -> None:
    cases = [
        "(" * 50_000,
        "not " * 12_000,
        "filled('a') and " * 3_000 + "filled('a')",
        "'" * 50_000,
        ("filled('a') or " * 4_000)[:50_000],
    ]
    start = time.monotonic()
    for text in cases:
        with pytest.raises(ExprError):
            holds(text, SLOTS)
    assert time.monotonic() - start < 2.0


def test_deep_nesting_within_the_length_cap_is_bounded() -> None:
    text = "(" * 500 + "filled('a')" + ")" * 500
    assert len(text) < MAX_EXPR_LEN
    with pytest.raises(ExprError):
        holds(text, SLOTS)


def test_long_valid_expression_is_linear() -> None:
    text = " or ".join(["filled('idea.summary')"] * 150)
    assert len(text) < MAX_EXPR_LEN
    start = time.monotonic()
    assert holds(text, SLOTS)
    assert time.monotonic() - start < 0.5
