"""Safe expression language for ``ask_if`` (spec 3.4, brief 8.2).

A hand-written tokenizer, a recursive-descent parser and a tree-walking evaluator. There is no
``eval``, ``exec``, ``compile`` or ``ast`` anywhere in this module (hard rule 9): the input is only
ever data. Anything outside the grammar raises :class:`ExprError` and nothing else.

Grammar (``or`` binds loosest)::

    expr    := or_expr
    or_expr := and_expr ( "or" and_expr )*
    and_expr:= not_expr ( "and" not_expr )*
    not_expr:= "not" not_expr | compare
    compare := operand [ ( "==" | "!=" | "in" | "not" "in" ) operand ]
    operand := "(" expr ")" | list | string | number | "true" | "false" | "null" | slot
    list    := "[" [ item ( "," item )* ] "]"      # items: string, number, true/false/null, bareword
    slot    := name [ "." name ]                    # lower-case, e.g. idea.archetype, locale

Semantics are documented in ``docs/specs/question-cards.md``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal

from mde_contracts.slots import SLOT_IDS, SlotData

MAX_EXPR_LEN: Final = 500
MAX_DEPTH: Final = 16
MAX_LIST_ITEMS: Final = 64
MAX_STRING_LEN: Final = 200
MAX_NAME_LEN: Final = 64

# Names the discovery engine adds next to the slot values when it builds the evaluation context.
# They are read-only facts, not slots: ``sensitive.domains`` is the list of sensitive-domain ids
# the interpreter (C2) and the Domain Pack rules flagged in this session.
CONTEXT_VARS: Final[frozenset[str]] = frozenset({"sensitive.domains"})

Scalar = str | int | float | bool
Value = Scalar | list[Scalar] | None


class ExprError(ValueError):
    """The expression is not valid in the safe language (the only error this module raises)."""


# --------------------------------------------------------------------------- syntax tree


@dataclass(frozen=True, slots=True)
class Lit:
    value: Scalar | None


@dataclass(frozen=True, slots=True)
class ListLit:
    items: tuple[Scalar | None, ...]


@dataclass(frozen=True, slots=True)
class SlotRef:
    path: str


@dataclass(frozen=True, slots=True)
class Not:
    operand: Expr


@dataclass(frozen=True, slots=True)
class BoolOp:
    op: Literal["and", "or"]
    operands: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Compare:
    op: Literal["==", "!=", "in", "not in"]
    left: Expr
    right: Expr


Expr = Lit | ListLit | SlotRef | Not | BoolOp | Compare


# --------------------------------------------------------------------------- tokenizer

_NUM = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
_SLOT_PATH = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?")
_BAREWORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
_KEYWORDS = frozenset({"and", "or", "not", "in", "true", "false", "null"})


@dataclass(frozen=True, slots=True)
class _Tok:
    kind: Literal["lp", "rp", "lb", "rb", "comma", "eq", "ne", "str", "num", "word", "end"]
    text: str
    pos: int


def _read_string(src: str, start: int) -> tuple[str, int]:
    quote = src[start]
    out: list[str] = []
    i = start + 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            if i + 1 >= len(src) or src[i + 1] not in (quote, "\\"):
                raise ExprError(f"invalid escape at position {i}")
            out.append(src[i + 1])
            i += 2
            continue
        if c == quote:
            return "".join(out), i + 1
        out.append(c)
        if len(out) > MAX_STRING_LEN:
            raise ExprError("string literal too long")
        i += 1
    raise ExprError("unterminated string literal")


def _tokenize(src: str) -> list[_Tok]:
    if len(src) > MAX_EXPR_LEN:
        raise ExprError(f"expression longer than {MAX_EXPR_LEN} characters")
    toks: list[_Tok] = []
    i = 0
    n = len(src)
    single: dict[str, Literal["lp", "rp", "lb", "rb", "comma"]] = {
        "(": "lp",
        ")": "rp",
        "[": "lb",
        "]": "rb",
        ",": "comma",
    }
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
        elif c in single:
            toks.append(_Tok(single[c], c, i))
            i += 1
        elif c in "\"'":
            text, j = _read_string(src, i)
            toks.append(_Tok("str", text, i))
            i = j
        elif src.startswith("==", i):
            toks.append(_Tok("eq", "==", i))
            i += 2
        elif src.startswith("!=", i):
            toks.append(_Tok("ne", "!=", i))
            i += 2
        elif m := _NUM.match(src, i):
            nxt = src[m.end()] if m.end() < n else ""
            if nxt and (nxt.isalnum() or nxt in "_."):
                raise ExprError(f"invalid number at position {i}")
            toks.append(_Tok("num", m.group(0), i))
            i = m.end()
        elif m := _WORD.match(src, i):
            toks.append(_Tok("word", m.group(0), i))
            i = m.end()
        else:
            raise ExprError(f"unexpected character {c!r} at position {i}")
    toks.append(_Tok("end", "", n))
    return toks


# --------------------------------------------------------------------------- parser


def _check_slot_path(name: str) -> str:
    if (
        len(name) > MAX_NAME_LEN
        or "__" in name
        or not _SLOT_PATH.fullmatch(name)
        or name.endswith(("_", "."))
    ):
        raise ExprError(
            f"{name!r} is not a slot name (use a quoted string for text, "
            "and no attribute access, calls or indexing)"
        )
    return name


def _number(text: str) -> int | float:
    digits = text.lstrip("-").replace(".", "")
    if len(digits) > 15:
        raise ExprError("number too long")
    return float(text) if "." in text else int(text)


class _Parser:
    def __init__(self, toks: list[_Tok]) -> None:
        self._toks = toks
        self._i = 0
        self._depth = 0

    # -- helpers
    def _peek(self, ahead: int = 0) -> _Tok:
        j = min(self._i + ahead, len(self._toks) - 1)
        return self._toks[j]

    def _next(self) -> _Tok:
        tok = self._toks[self._i]
        if tok.kind != "end":
            self._i += 1
        return tok

    def _is_word(self, text: str, ahead: int = 0) -> bool:
        tok = self._peek(ahead)
        return tok.kind == "word" and tok.text == text

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise ExprError(f"expression nested deeper than {MAX_DEPTH} levels")

    def _leave(self) -> None:
        self._depth -= 1

    # -- grammar
    def parse(self) -> Expr:
        if self._peek().kind == "end":
            raise ExprError("empty expression")
        node = self._or()
        tok = self._peek()
        if tok.kind != "end":
            raise ExprError(f"unexpected {tok.text!r} at position {tok.pos}")
        return node

    def _or(self) -> Expr:
        self._enter()
        try:
            items = [self._and()]
            while self._is_word("or"):
                self._next()
                items.append(self._and())
            return items[0] if len(items) == 1 else BoolOp("or", tuple(items))
        finally:
            self._leave()

    def _and(self) -> Expr:
        items = [self._not()]
        while self._is_word("and"):
            self._next()
            items.append(self._not())
        return items[0] if len(items) == 1 else BoolOp("and", tuple(items))

    def _not(self) -> Expr:
        if self._is_word("not") and not self._is_word("in", 1):
            self._next()
            self._enter()
            try:
                return Not(self._not())
            finally:
                self._leave()
        return self._compare()

    def _compare(self) -> Expr:
        left = self._operand()
        tok = self._peek()
        op: Literal["==", "!=", "in", "not in"]
        if tok.kind == "eq":
            op = "=="
            self._next()
        elif tok.kind == "ne":
            op = "!="
            self._next()
        elif self._is_word("in"):
            op = "in"
            self._next()
        elif self._is_word("not") and self._is_word("in", 1):
            op = "not in"
            self._next()
            self._next()
        else:
            return left
        return Compare(op, left, self._operand())

    def _operand(self) -> Expr:
        tok = self._next()
        node: Expr
        if tok.kind == "lp":
            node = self._or()
            closing = self._next()
            if closing.kind != "rp":
                raise ExprError(f"expected ')' at position {closing.pos}")
        elif tok.kind == "lb":
            node = self._list()
        elif tok.kind == "str":
            node = Lit(tok.text)
        elif tok.kind == "num":
            node = Lit(_number(tok.text))
        elif tok.kind == "word":
            node = self._word(tok)
        else:
            raise ExprError(f"expected a value at position {tok.pos}")
        after = self._peek()
        if after.kind == "lp":
            raise ExprError("calls are not allowed")
        if after.kind == "lb":
            raise ExprError("indexing is not allowed")
        return node

    def _word(self, tok: _Tok) -> Expr:
        if tok.text == "true":
            return Lit(True)
        if tok.text == "false":
            return Lit(False)
        if tok.text == "null":
            return Lit(None)
        if tok.text in _KEYWORDS:
            raise ExprError(f"unexpected keyword {tok.text!r} at position {tok.pos}")
        return SlotRef(_check_slot_path(tok.text))

    def _list(self) -> ListLit:
        items: list[Scalar | None] = []
        if self._peek().kind == "rb":
            self._next()
            return ListLit(())
        while True:
            tok = self._next()
            if tok.kind == "str":
                items.append(tok.text)
            elif tok.kind == "num":
                items.append(_number(tok.text))
            elif tok.kind == "word":
                if tok.text == "true":
                    items.append(True)
                elif tok.text == "false":
                    items.append(False)
                elif tok.text == "null":
                    items.append(None)
                elif (
                    len(tok.text) > MAX_NAME_LEN
                    or "__" in tok.text
                    or not _BAREWORD.fullmatch(tok.text)
                ):
                    raise ExprError(f"{tok.text!r} is not allowed in a list (quote it)")
                else:
                    items.append(tok.text)
            else:
                raise ExprError(f"expected a list item at position {tok.pos}")
            if len(items) > MAX_LIST_ITEMS:
                raise ExprError(f"list longer than {MAX_LIST_ITEMS} items")
            sep = self._next()
            if sep.kind == "rb":
                return ListLit(tuple(items))
            if sep.kind != "comma":
                raise ExprError(f"expected ',' or ']' at position {sep.pos}")


@lru_cache(maxsize=2048)
def _parse_cached(expr: str) -> Expr:
    return _Parser(_tokenize(expr)).parse()


def parse(expr: str) -> Expr:
    """Parse ``expr``; raises :class:`ExprError` for anything outside the grammar."""
    if not isinstance(expr, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ExprError("expression must be a string")
    return _parse_cached(expr)


# --------------------------------------------------------------------------- evaluation


def _coerce(value: object) -> Value:
    """Reduce a context value to the closed set the language compares; empty means unfilled."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        items: list[Scalar] = [x for x in value if isinstance(x, (str, bool, int, float))]
        return items or None
    return None


def _scalar_eq(a: Scalar, b: Scalar) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    return a == b


def _member(x: Scalar, items: list[Scalar]) -> bool:
    return any(_scalar_eq(x, y) for y in items)


def _equal(a: Value, b: Value) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list) or isinstance(b, list):
        if isinstance(a, list) and isinstance(b, list):
            return all(_member(x, b) for x in a) and all(_member(y, a) for y in b)
        return False
    return _scalar_eq(a, b)


def _contains(left: Value, right: Value) -> bool:
    if left is None or right is None or not isinstance(right, list):
        return False
    if isinstance(left, list):
        return any(_member(x, right) for x in left)
    return _member(left, right)


def _truthy(value: Value) -> bool:
    return value is not None and bool(value)


def _value(node: Expr, ctx: Mapping[str, SlotData]) -> Value:
    if isinstance(node, Lit):
        return _coerce(node.value)
    if isinstance(node, ListLit):
        return _coerce([x for x in node.items if x is not None])
    if isinstance(node, SlotRef):
        return _coerce(ctx.get(node.path))
    if isinstance(node, Not):
        return not _truthy(_value(node.operand, ctx))
    if isinstance(node, BoolOp):
        if node.op == "and":
            return all(_truthy(_value(x, ctx)) for x in node.operands)
        return any(_truthy(_value(x, ctx)) for x in node.operands)
    left = _value(node.left, ctx)
    right = _value(node.right, ctx)
    if node.op == "==":
        return _equal(left, right)
    if node.op == "!=":
        return not _equal(left, right)
    if node.op == "in":
        return _contains(left, right)
    return not _contains(left, right)


def evaluate(expr: Expr | str, ctx: Mapping[str, SlotData]) -> bool:
    """Evaluate to a bool. Unfilled slots (missing, ``None``, ``""``, ``[]``) read as ``null``."""
    node = parse(expr) if isinstance(expr, str) else expr
    return _truthy(_value(node, ctx))


def holds(ask_if: str | None, ctx: Mapping[str, SlotData]) -> bool:
    """Card/slot precondition: no expression means "always"."""
    return True if ask_if is None else evaluate(ask_if, ctx)


# --------------------------------------------------------------------------- analysis


def _walk(node: Expr) -> Iterable[Expr]:
    yield node
    if isinstance(node, Not):
        yield from _walk(node.operand)
    elif isinstance(node, BoolOp):
        for child in node.operands:
            yield from _walk(child)
    elif isinstance(node, Compare):
        yield from _walk(node.left)
        yield from _walk(node.right)


def referenced_slots(expr: Expr | str) -> set[str]:
    """Every slot or context name the expression reads."""
    node = parse(expr) if isinstance(expr, str) else expr
    return {n.path for n in _walk(node) if isinstance(n, SlotRef)}


def unknown_slots(expr: Expr | str, known: Iterable[str] = (*SLOT_IDS, *CONTEXT_VARS)) -> set[str]:
    """Names in the expression that are neither a real slot id nor a documented context name."""
    return referenced_slots(expr) - set(known)
