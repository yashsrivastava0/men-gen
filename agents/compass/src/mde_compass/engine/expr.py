"""The ``ask_if`` mini-language, evaluated by a real tokenizer and a recursive-descent parser.

There is no ``eval``, ``exec``, ``compile`` or ``ast`` here (hard rule 9): the text is only data.
Adapted from ``mde_content.expr`` (credit: the Discover engine) for the grammar documented in
``mde_compass.catalog.loader``::

    expr    := and_expr ("or" and_expr)*
    and_expr:= not_expr ("and" not_expr)*
    not_expr:= "not" not_expr | atom
    atom    := "(" expr ")" | filled('s') | empty('s')
             | value('s') (== | != | in | not in) operand

Hostile input is bounded: the length is capped, nesting depth is capped, the tokenizer is a single
left-to-right scan and the parser consumes each token once, so work is linear in the input.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Final

from mde_compass.domain.slots import SlotRecord

MAX_EXPR_LEN: Final = 4000
MAX_DEPTH: Final = 24
MAX_LIST_ITEMS: Final = 64

_FUNCS: Final = ("filled", "empty", "value")
_PUNCT: Final = {"(", ")", "[", "]", ","}


class ExprError(ValueError):
    """The expression is not valid in the safe language (the only error this module raises)."""


def _tokenize(text: str) -> list[tuple[str, str]]:
    if len(text) > MAX_EXPR_LEN:
        raise ExprError(f"expression longer than {MAX_EXPR_LEN} characters")
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in _PUNCT:
            tokens.append(("op", ch))
            i += 1
        elif ch in "=!":
            if text[i : i + 2] not in ("==", "!="):
                raise ExprError(f"unexpected {ch!r} at {i}")
            tokens.append(("op", text[i : i + 2]))
            i += 2
        elif ch in "'\"":
            end = text.find(ch, i + 1)
            if end < 0:
                raise ExprError(f"unterminated string at {i}")
            tokens.append(("str", text[i + 1 : end]))
            i = end + 1
        elif ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_."):
                j += 1
            tokens.append(("word", text[i:j]))
            i = j
        else:
            raise ExprError(f"unexpected {ch!r} at {i}")
    return tokens


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokenize(text)
        self.i = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _next(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise ExprError("unexpected end of expression")
        self.i += 1
        return tok

    def _expect(self, text: str) -> None:
        tok = self._next()
        if tok[1] != text or tok[0] == "str":
            raise ExprError(f"expected {text!r} but found {tok[1]!r}")

    def parse(self) -> tuple[Any, ...]:
        node = self._or(0)
        if self._peek() is not None:
            raise ExprError(f"trailing text {self._peek()!r}")
        return node

    def _or(self, depth: int) -> tuple[Any, ...]:
        node = self._and(depth)
        while self._peek() == ("word", "or"):
            self.i += 1
            node = ("or", node, self._and(depth))
        return node

    def _and(self, depth: int) -> tuple[Any, ...]:
        node = self._not(depth)
        while self._peek() == ("word", "and"):
            self.i += 1
            node = ("and", node, self._not(depth))
        return node

    def _not(self, depth: int) -> tuple[Any, ...]:
        if depth > MAX_DEPTH:
            raise ExprError("expression nested too deeply")
        if self._peek() == ("word", "not"):
            self.i += 1
            return ("not", self._not(depth + 1))
        return self._atom(depth)

    def _string(self) -> str:
        kind, text = self._next()
        if kind != "str":
            raise ExprError(f"expected a quoted string, found {text!r}")
        return text

    def _atom(self, depth: int) -> tuple[Any, ...]:
        kind, text = self._next()
        if kind == "op" and text == "(":
            node = self._or(depth + 1)
            self._expect(")")
            return node
        if kind != "word" or text not in _FUNCS:
            raise ExprError(f"unexpected {text!r}")
        self._expect("(")
        slot = self._string()
        self._expect(")")
        if text in ("filled", "empty"):
            return (text, slot)
        op = self._next()
        if op[0] == "op" and op[1] in ("==", "!="):
            return ("value", slot, op[1], (self._string(),))
        negate = False
        if op == ("word", "not"):
            negate = True
            op = self._next()
        if op == ("word", "in"):
            self._expect("[")
            items = [self._string()]
            while self._peek() == ("op", ","):
                self.i += 1
                items.append(self._string())
                if len(items) > MAX_LIST_ITEMS:
                    raise ExprError("list too long")
            self._expect("]")
            return ("value", slot, "not in" if negate else "in", tuple(items))
        raise ExprError(f"value('{slot}') needs ==, !=, in or not in")


@lru_cache(maxsize=1024)
def _parse(expr: str) -> tuple[Any, ...]:
    return _Parser(expr).parse()


def referenced_slots(expr: str) -> set[str]:
    """Slot ids an expression mentions (also validates the syntax). ``None``/empty -> empty set."""
    if not expr or not expr.strip():
        return set()
    refs: set[str] = set()
    stack = [_parse(expr)]
    while stack:
        node = stack.pop()
        if node[0] in ("and", "or"):
            stack.extend((node[1], node[2]))
        elif node[0] == "not":
            stack.append(node[1])
        else:
            refs.add(node[1])
    return refs


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _filled(rec: SlotRecord | None) -> bool:
    return rec is not None and rec.status != "empty" and not _blank(rec.value)


def _members(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if not isinstance(v, dict | list)]
    if isinstance(value, dict | list):
        return []
    return [str(value)]


def _eval(node: tuple[Any, ...], slots: Mapping[str, SlotRecord | None]) -> bool:
    tag = node[0]
    if tag == "and":
        return _eval(node[1], slots) and _eval(node[2], slots)
    if tag == "or":
        return _eval(node[1], slots) or _eval(node[2], slots)
    if tag == "not":
        return not _eval(node[1], slots)
    rec = slots.get(node[1])
    if tag == "filled":
        return _filled(rec)
    if tag == "empty":
        return not _filled(rec)
    members = _members(rec.value) if _filled(rec) and rec is not None else []
    hit = bool(set(members) & set(node[3]))
    return {"==": hit, "in": hit, "!=": not hit, "not in": not hit}[node[2]]


def holds(expr: str | None, slots: Mapping[str, SlotRecord | None]) -> bool:
    """Evaluate ``expr`` over slot records. ``None`` or empty text holds (no condition).

    ``value('s') == 'x'`` is true when the slot is filled and its value (or, for a list slot, one of
    its items) equals ``x``; ``!=`` is its negation, so an empty slot satisfies ``!=``.
    Raises :class:`ExprError` for text outside the grammar.
    """
    if expr is None or not expr.strip():
        return True
    return _eval(_parse(expr), slots)
