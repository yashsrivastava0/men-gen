"""Safe expression language: truth tables, rejection of everything outside the grammar, fuzzing."""

from __future__ import annotations

import contextlib
import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_content.expr import (
    CONTEXT_VARS,
    MAX_EXPR_LEN,
    ExprError,
    evaluate,
    holds,
    parse,
    referenced_slots,
    unknown_slots,
)
from mde_contracts.slots import SlotData

CTX: dict[str, SlotData] = {
    "idea.archetype": "commerce",
    "scope.features": ["search", "payments", "cart_checkout"],
    "auth.method": "none",
    "behavior.analytics": False,
    "behavior.notifications": True,
    "locale": "en",
    "scope.apps": ["customer"],
    "users.roles": [],
    "brand.name": "",
    "sensitive.domains": ["health"],
}


# --------------------------------------------------------------------------- truth tables


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        # equality on scalars
        ("idea.archetype == 'commerce'", True),
        ("idea.archetype == 'food'", False),
        ('idea.archetype == "commerce"', True),
        ("idea.archetype != 'commerce'", False),
        ("idea.archetype != 'food'", True),
        ("locale == 'en'", True),
        # booleans are not numbers and not strings
        ("behavior.analytics == false", True),
        ("behavior.analytics == true", False),
        ("behavior.notifications == true", True),
        ("behavior.notifications == 1", False),
        ("behavior.notifications == 'true'", False),
        # unfilled slots read as null: equality false, inequality true, null checks work
        ("brand.name == 'x'", False),
        ("brand.name != 'x'", True),
        ("brand.name == null", True),
        ("brand.name != null", False),
        ("users.roles == null", True),
        ("design.direction == null", True),
        ("design.direction == 'clean-bright'", False),
        ("idea.archetype == null", False),
        ("idea.archetype != null", True),
        # membership with a literal list (barewords are strings inside lists)
        ("idea.archetype in [commerce, food_delivery]", True),
        ("idea.archetype in [food_delivery, custom]", False),
        ("idea.archetype in ['commerce']", True),
        ("idea.archetype not in [food_delivery, custom]", True),
        ("idea.archetype not in [commerce]", False),
        ("idea.archetype in []", False),
        ("idea.archetype not in []", True),
        # membership in a list-valued slot
        ("'payments' in scope.features", True),
        ("'reviews' in scope.features", False),
        ("'reviews' not in scope.features", True),
        ("'payments' in users.roles", False),
        ("'payments' not in users.roles", True),
        ("'payments' in design.direction", False),
        # a list on the left means "any of these"
        ("scope.features in [payments, reviews]", True),
        ("scope.features in [reviews, loyalty]", False),
        ("scope.apps in [customer, courier]", True),
        ("scope.apps in [courier]", False),
        # list equality ignores order
        ("scope.apps == [customer]", True),
        ("scope.features == [cart_checkout, payments, search]", True),
        ("scope.features == [payments]", False),
        # unfilled slot on the left of ``in`` is false, on the right is false
        ("design.direction in [clean-bright]", False),
        ("design.direction not in [clean-bright]", True),
        ("'x' in design.direction", False),
        # numbers
        ("1 == 1", True),
        ("1 == 1.0", True),
        ("2 != 3", True),
        ("-1 == -1", True),
        ("1 in [1, 2]", True),
        ("3 in [1, 2]", False),
        # context variables behave like slots
        ("'health' in sensitive.domains", True),
        ("'finance' in sensitive.domains", False),
        ("sensitive.domains != null", True),
        # literals and bare truthiness
        ("true", True),
        ("false", False),
        ("null", False),
        ("behavior.notifications", True),
        ("behavior.analytics", False),
        ("brand.name", False),
        ("idea.archetype", True),
        ("scope.features", True),
        ("users.roles", False),
        ("not behavior.analytics", True),
        ("not auth.method", False),
    ],
)
def test_truth_table(expr: str, expected: bool) -> None:
    assert evaluate(expr, CTX) is expected


def test_boolean_operators_and_precedence() -> None:
    t, f = "true", "false"
    table = [
        (f"{t} and {t}", True),
        (f"{t} and {f}", False),
        (f"{f} or {t}", True),
        (f"{f} or {f}", False),
        (f"not {t}", False),
        (f"not {f}", True),
        (f"not not {t}", True),
        # and binds tighter than or
        (f"{t} or {f} and {f}", True),
        (f"({t} or {f}) and {f}", False),
        (f"{f} and {f} or {t}", True),
        # not binds tighter than and
        (f"not {f} and {f}", False),
        (f"not ({f} and {f})", True),
        # not in is a comparison, not a negation
        ("'a' not in [b] and true", True),
        ("not 'a' in [a]", False),
    ]
    for expr, expected in table:
        assert evaluate(expr, {}) is expected, expr


def test_compound_expressions_from_the_spec() -> None:
    ctx: dict[str, SlotData] = {"idea.archetype": "booking_services"}
    assert evaluate("idea.archetype in [commerce, marketplace, booking_services, custom]", ctx)
    assert evaluate("idea.archetype == 'commerce' or idea.archetype == 'booking_services'", ctx)
    assert not evaluate("idea.archetype == 'commerce' and 'payments' in scope.features", ctx)
    assert evaluate("auth.method == null and not ('x' in scope.features)", ctx)


def test_parentheses_and_whitespace() -> None:
    assert evaluate("  ( ( true ) )  ", {})
    assert evaluate("\t'a'\n==\n'a'", {})
    assert evaluate("(idea.archetype=='commerce')", CTX)


def test_empty_values_are_unfilled() -> None:
    ctx: dict[str, SlotData] = {"a.b": "", "c.d": [], "e.f": None}
    assert evaluate("a.b == null and c.d == null and e.f == null and g.h == null", ctx)
    assert not evaluate("a.b", ctx)


def test_string_escapes() -> None:
    assert evaluate("'it\\'s' == \"it's\"", {})
    assert evaluate(r"'a\\b' == 'a\\b'", {})
    with pytest.raises(ExprError):
        parse(r"'bad \n escape'")
    with pytest.raises(ExprError):
        parse("'unterminated")


def test_holds_treats_none_as_always() -> None:
    assert holds(None, {}) is True
    assert holds("false", {}) is False
    assert holds("idea.archetype == 'commerce'", CTX) is True


def test_parse_returns_reusable_tree() -> None:
    tree = parse("idea.archetype in [commerce, custom]")
    assert evaluate(tree, CTX) is True
    assert evaluate(tree, {"idea.archetype": "food_delivery"}) is False


# --------------------------------------------------------------------------- analysis


def test_referenced_slots() -> None:
    expr = "idea.archetype in [a, b] and ('x' in scope.features or not locale == 'en')"
    assert referenced_slots(expr) == {"idea.archetype", "scope.features", "locale"}
    assert referenced_slots("true") == set()
    assert referenced_slots(parse("a.b == c.d")) == {"a.b", "c.d"}


def test_unknown_slots_uses_slot_ids_and_context_vars() -> None:
    assert unknown_slots("idea.archetype == 'x' and 'health' in sensitive.domains") == set()
    assert unknown_slots("idea.archtype == 'x'") == {"idea.archtype"}
    assert "sensitive.domains" in CONTEXT_VARS


# --------------------------------------------------------------------------- rejection

INVALID = [
    "",
    "   ",
    "(",
    ")",
    "()",
    "[",
    "[a",
    "[a,",
    "[a,]",
    "[,]",
    "a ==",
    "== a",
    "a.b ==",
    "a.b in",
    "a.b not",
    "a.b not a",
    "a.b == c.d == e.f",
    "a.b = c.d",
    "a.b ! c.d",
    "a.b and",
    "and a.b",
    "a.b or or c.d",
    "not",
    "a.b c.d",
    "'x' 'y'",
    "1 2",
    "true false",
    "in [a]",
    "1abc",
    "1.2.3",
    "1.",
    "99999999999999999999 == 1",
    "a.b == 'x' )",
    "[a b]",
    "[[a]]",
    "[(a)]",
    "[a.b]",
    "[__x]",
    "[a__b]",
    "[1abc]",
]


@pytest.mark.parametrize("expr", INVALID)
def test_invalid_syntax_raises_expr_error(expr: str) -> None:
    with pytest.raises(ExprError):
        parse(expr)
    with pytest.raises(ExprError):
        evaluate(expr, {})


INJECTION = [
    "__import__('os').system('echo pwned')",
    "__import__('os')",
    "().__class__",
    "().__class__.__bases__[0].__subclasses__()",
    "a.b()",
    "a.b.c",
    "a.b.c.d",
    "idea.archetype.upper",
    "idea.archetype.__class__",
    "idea.__dict__",
    "__builtins__",
    "x.__len__()",
    "scope.features[0]",
    "scope.features [0]",
    "'a'.upper()",
    "'a'[0]",
    "(a.b)(c.d)",
    "[a][0]",
    "lambda: 1",
    "exec('1')",
    "eval('1')",
    "open('/etc/passwd')",
    "a.b if c.d else e.f",
    "a.b; c.d",
    "a.b\n__import__('os')",
    "{'a': 1}",
    "`id`",
    "$(id)",
    "a.b == 1 # comment",
    "a.b + c.d",
    "a.b - 1",
    "a.b * c.d",
    "a.b < c.d",
    "a.b >= 1",
    "a.b is None",
    "a.b is not None",
    "not_a_slot.__class__",
    "Idea.Archetype == 'x'",
    "idea.Archetype == 'x'",
    "idea.archetype == 'x' and \x00",
    "é == 'x'",
]


@pytest.mark.parametrize("expr", INJECTION)
def test_injection_attempts_are_rejected(expr: str) -> None:
    with pytest.raises(ExprError):
        parse(expr)
    with pytest.raises(ExprError):
        evaluate(expr, CTX)


def test_injection_payload_has_no_side_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = [
        "__import__('os').environ.update(MDE_PWNED='1')",
        "__import__('os').system('echo pwned')",
        "exec(\"import os; os.environ['MDE_PWNED']='1'\")",
        "[x for x in ().__class__.__base__.__subclasses__()]",
    ]
    monkeypatch.delenv("MDE_PWNED", raising=False)
    for payload in payloads:
        with pytest.raises(ExprError):
            evaluate(payload, CTX)
    assert "MDE_PWNED" not in os.environ


def test_attribute_access_names_never_read_the_context_object() -> None:
    class Spy:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"attribute {name} touched")

    assert evaluate("a.b == null", {"a.b": None}) is True
    # values that are not plain data are treated as unfilled, never inspected
    ctx: dict[str, SlotData] = {"a.b": Spy()}  # type: ignore[dict-item]  # pyright: ignore[reportAssignmentType]
    assert evaluate("a.b == null", ctx) is True


def test_length_cap() -> None:
    ok = "true" + " or true" * 40
    assert len(ok) < MAX_EXPR_LEN
    assert evaluate(ok, {}) is True
    with pytest.raises(ExprError):
        parse("true" + " or true" * 200)


def test_depth_cap_is_an_expr_error_not_a_recursion_error() -> None:
    with pytest.raises(ExprError):
        parse("(" * 100 + "true" + ")" * 100)
    with pytest.raises(ExprError):
        parse("not " * 100 + "true")
    assert evaluate("(" * 5 + "true" + ")" * 5, {}) is True
    assert evaluate("not not not true", {}) is False


def test_list_item_cap() -> None:
    items = ", ".join(f"a{i}" for i in range(65))
    with pytest.raises(ExprError):
        parse(f"x.y in [{items}]")
    items_ok = ", ".join(f"a{i}" for i in range(64))
    assert not evaluate(f"x.y in [{items_ok}]", {"x.y": "zz"})


def test_non_string_input_is_rejected() -> None:
    with pytest.raises(ExprError):
        parse(None)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------- fuzzing

_ALPHABET = [
    *"abcdefghijklmnopqrstuvwxyz_.0123456789 \t\n()[],'\"=!\\-",
    "and",
    "or",
    "not",
    "in",
    "true",
    "false",
    "null",
    "==",
    "!=",
    "idea.archetype",
    "scope.features",
    "__import__",
    "()",
]


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=300))
def test_fuzz_arbitrary_text_only_ever_raises_expr_error(text: str) -> None:
    with contextlib.suppress(ExprError):
        evaluate(text, CTX)


@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(_ALPHABET), max_size=40).map(" ".join))
def test_fuzz_token_soup_only_ever_raises_expr_error(text: str) -> None:
    with contextlib.suppress(ExprError):
        evaluate(text, CTX)
        referenced_slots(text)


@settings(max_examples=200, deadline=None)
@given(st.text(alphabet=st.characters(), min_size=0, max_size=50), st.text(max_size=20))
def test_fuzz_string_literals_never_escape_their_quotes(body: str, other: str) -> None:
    quoted = "'" + body.replace("\\", "\\\\").replace("'", "\\'") + "'"
    ctx: dict[str, SlotData] = {"a.b": other}
    expected = other == body  # "" on both sides: both read as null, so equal
    assert evaluate(f"a.b == {quoted}", ctx) is expected


# A tiny reference model: random boolean formulas over three atoms evaluate like Python's own
# operators, computed without the language under test.
_ATOMS = {
    "a.x == 'p'": lambda c: c.get("a.x") == "p",
    "'q' in a.y": lambda c: "q" in (c.get("a.y") or []),
    "a.z != null": lambda c: bool(c.get("a.z")),
}


def _formula(depth: int) -> st.SearchStrategy[tuple[str, object]]:
    atom = st.sampled_from(sorted(_ATOMS)).map(lambda s: (s, ("atom", s)))
    if depth == 0:
        return atom
    sub = st.deferred(lambda: _formula(depth - 1))
    return st.one_of(
        atom,
        sub.map(lambda t: (f"not ({t[0]})", ("not", t[1]))),
        st.tuples(sub, sub).map(
            lambda p: (f"({p[0][0]}) and ({p[1][0]})", ("and", p[0][1], p[1][1]))
        ),
        st.tuples(sub, sub).map(
            lambda p: (f"({p[0][0]}) or ({p[1][0]})", ("or", p[0][1], p[1][1]))
        ),
    )


def _ref(tree: object, ctx: dict[str, SlotData]) -> bool:
    assert isinstance(tree, tuple)
    kind = tree[0]
    if kind == "atom":
        return _ATOMS[tree[1]](ctx)
    if kind == "not":
        return not _ref(tree[1], ctx)
    if kind == "and":
        return _ref(tree[1], ctx) and _ref(tree[2], ctx)
    return _ref(tree[1], ctx) or _ref(tree[2], ctx)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    _formula(3),
    st.sampled_from(["p", "r", None]),
    st.sampled_from([["q"], ["r"], [], None]),
    st.sampled_from(["v", "", None]),
)
def test_random_formulas_match_a_reference_model(
    formula: tuple[str, object], x: str | None, y: list[str] | None, z: str | None
) -> None:
    ctx: dict[str, SlotData] = {"a.x": x, "a.y": y, "a.z": z}
    assert evaluate(formula[0], ctx) is _ref(formula[1], ctx)


def test_list_items_may_be_booleans_null_and_numbers() -> None:
    ctx: dict[str, SlotData] = {"a.b": True, "c.d": 2}
    assert evaluate("a.b in [true, x]", ctx)
    assert not evaluate("a.b in [false]", ctx)
    assert evaluate("c.d in [1, 2, 3]", ctx)
    assert evaluate("c.d in [null, 2]", ctx)
    assert not evaluate("a.b in [1]", ctx)  # true is not the number 1
    assert evaluate("x.y == [a, b, c]", {"x.y": ["c", "a", "b"]})
    assert not evaluate("x.y == [a, b]", {"x.y": ["a", "b", "c"]})
    assert not evaluate("x.y == [a]", {"x.y": "a"})  # a scalar is not a list


def test_string_literal_cap() -> None:
    with pytest.raises(ExprError):
        parse("'" + "a" * 201 + "'")
    assert evaluate("'" + "a" * 200 + "' == 'x'", {}) is False
