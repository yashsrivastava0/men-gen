"""Tolerant JSON parsing of model text (doc 18 section 4.2; FN-01, FN-02).

The text a model returns is untrusted and often not clean: prose around the object, a reasoning
block, code fences, or an answer cut off at the output limit. ``tolerant_parse`` strips the noise,
takes the FIRST JSON object that parses and fits the call's top-level shape, and, when the text was
cut off inside the main array, keeps the items that finished (the rest is redone in smaller batches,
see ``split.py``). It never raises: junk gives ``ok=False`` with a plain reason.

Pure function: no network, no model, no vendor name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# How many "{" start positions are tried before giving up (keeps junk with thousands of braces
# linear-ish; real model output has one to a handful of candidates).
MAX_CANDIDATES = 200
# Salvage tries the last N cut points, newest first, in case garbage sits between the last two.
_SALVAGE_CUT_TRIES = 5

_THINK_PAIR = re.compile(r"<(think|thinking|reasoning|thought)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<(?:think|thinking|reasoning|thought)>", re.IGNORECASE)
_REASON_LINE = re.compile(r"^\s*(?:reasoning|thinking|thoughts?|analysis)\s*:", re.IGNORECASE)
_FENCE_LINE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")
_FENCE_INLINE_OPEN = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*(?=[{\[])")


@dataclass(frozen=True)
class TopLevelShape:
    """What the call's top-level object must look like: required keys and a main array key."""

    required_keys: tuple[str, ...] = ()
    array_key: str | None = None

    def all_required(self) -> tuple[str, ...]:
        if self.array_key and self.array_key not in self.required_keys:
            return (*self.required_keys, self.array_key)
        return self.required_keys

    def missing(self, value: object) -> list[str]:
        """Plain list of what is wrong with ``value`` (empty: it fits)."""
        if not isinstance(value, dict):
            return ["top level is not an object"]
        obj: dict[str, Any] = value
        problems = [f"missing key {k!r}" for k in self.all_required() if k not in obj]
        if self.array_key and self.array_key in obj and not isinstance(obj[self.array_key], list):
            problems.append(f"{self.array_key!r} is not an array")
        return problems

    def fits(self, value: object) -> bool:
        return not self.missing(value)


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    value: dict[str, Any] | None = None
    reason: str = ""
    truncated: bool = False
    salvaged_count: int = 0  # array items kept from a truncated answer
    unfinished: bool = False  # the answer ended inside an item (that item is NOT in value)
    unfinished_fragment: str = ""
    stripped: tuple[str, ...] = field(default_factory=tuple)  # "think", "reasoning_line", "fence"


def tolerant_parse(
    text: str, top_level_shape: TopLevelShape | None = None, *, max_candidates: int = MAX_CANDIDATES
) -> ParseResult:
    """Strip noise, return the first JSON object that fits, or salvage a truncated main array.

    Never raises. ``ok=False`` carries a reason; a truncated answer with nothing finished gives
    ``ok=False, truncated=True, salvaged_count=0`` (the caller redoes every item).
    """
    shape = top_level_shape or TopLevelShape()
    try:
        return _parse(text, shape, max_candidates)
    except Exception as exc:  # defensive: junk must never raise (RecursionError and friends)
        return ParseResult(ok=False, reason=f"unparseable text ({type(exc).__name__})")


def _parse(text: object, shape: TopLevelShape, max_candidates: int) -> ParseResult:
    if not isinstance(text, str) or not text.strip():
        return ParseResult(ok=False, reason="empty text")
    cleaned, stripped = strip_noise(text)
    if not cleaned.strip():
        return ParseResult(
            ok=False, reason="nothing left after stripping reasoning", stripped=stripped
        )
    starts = _candidate_starts(cleaned, max_candidates)
    if not starts:
        return ParseResult(ok=False, reason="no JSON object found", stripped=stripped)

    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    last_problem = "no JSON object found"
    for pos in starts:
        try:
            value, _end = decoder.raw_decode(cleaned, pos)
        except (ValueError, RecursionError):
            continue
        problems = shape.missing(value)
        if not problems:
            return ParseResult(ok=True, value=value, stripped=stripped)
        last_problem = "; ".join(problems)

    if shape.array_key:
        for pos in starts:
            salvaged = _salvage(cleaned, pos, shape)
            if salvaged is not None:
                return ParseResult(**{**salvaged, "stripped": stripped})

    return ParseResult(
        ok=False,
        reason=f"no JSON object fits the expected shape ({last_problem})"
        if any(_decodes(decoder, cleaned, p) for p in starts[:3])
        else "no valid JSON object found",
        stripped=stripped,
    )


def _decodes(decoder: json.JSONDecoder, text: str, pos: int) -> bool:
    try:
        decoder.raw_decode(text, pos)
    except (ValueError, RecursionError):
        return False
    return True


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constant {name} is not JSON")


def _candidate_starts(text: str, limit: int) -> list[int]:
    out: list[int] = []
    i = text.find("{")
    while i != -1 and len(out) < limit:
        out.append(i)
        i = text.find("{", i + 1)
    return out


# --------------------------------------------------------------------------------- noise stripping
def strip_noise(text: str) -> tuple[str, tuple[str, ...]]:
    """Remove reasoning blocks and code-fence lines; report what was removed."""
    notes: list[str] = []
    out = _THINK_PAIR.sub("", text)
    if out != text:
        notes.append("think")
    m = _THINK_OPEN.search(out)
    if m:  # an unclosed reasoning block (cut off while thinking) swallows the rest
        out = out[: m.start()]
        notes.append("think")

    kept: list[str] = []
    in_reason = False
    for line in out.split("\n"):
        stripped = line.strip()
        if in_reason:
            if not stripped or stripped.startswith(("{", "```")):
                in_reason = False  # reasoning ends at a blank line or at the JSON / fence
            else:
                continue
        if _REASON_LINE.match(line):
            in_reason = True
            notes.append("reasoning_line")
            continue
        if _FENCE_LINE.match(line):
            notes.append("fence")
            continue
        kept.append(line)
    out = "\n".join(kept)
    fenced = _FENCE_INLINE_OPEN.sub("", out)
    if fenced != out:
        notes.append("fence")
        out = fenced
    return out, tuple(dict.fromkeys(notes))


# ------------------------------------------------------------------------- truncated-array salvage
def _salvage(text: str, start: int, shape: TopLevelShape) -> dict[str, Any] | None:
    """If the object at ``start`` was cut off inside the main array, keep the finished items."""
    assert shape.array_key is not None
    stack: list[str] = []
    in_str = False
    esc = False
    str_start = 0
    pending_key: str | None = None
    cur_key: str | None = None
    in_main = False
    # (index where the kept prefix ends, closers to append)
    cuts: list[tuple[int, str]] = []
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                if stack == ["{"]:
                    pending_key = text[str_start + 1 : i]
            i += 1
            continue
        if ch == '"':
            in_str = True
            str_start = i
        elif ch in "{[":
            if ch == "[" and stack == ["{"] and cur_key == shape.array_key and not in_main:
                in_main = True
            stack.append(ch)
        elif ch in "}]":
            if not stack or (ch == "}") != (stack[-1] == "{"):
                return None  # malformed, not truncated
            if in_main and ch == "]" and stack == ["{", "["]:
                cuts.append((i + 1, "}"))  # array complete: keep it, close the object
                in_main = False
            stack.pop()
            if not stack:
                return None  # object closed: not truncated (the full parse already failed)
        elif ch == ":" and stack == ["{"]:
            cur_key = pending_key
        elif ch == "," and in_main and stack == ["{", "["]:
            cuts.append((i, "]}"))
        i += 1

    if not stack and not in_str:
        return None
    if cur_key != shape.array_key and not cuts:
        return None
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    last_problem = ""
    for cut, closers in reversed(cuts[-_SALVAGE_CUT_TRIES:]):
        try:
            value = decoder.decode(text[start:cut] + closers)
        except (ValueError, RecursionError):
            continue
        problems = shape.missing(value)
        if problems:
            last_problem = "; ".join(problems)
            continue
        items = value[shape.array_key]
        if not items:
            break
        return {
            "ok": True,
            "value": value,
            "truncated": True,
            "salvaged_count": len(items),
            "unfinished": True,
            "unfinished_fragment": text[cut:].lstrip(", \n\t")[:200],
            "reason": "truncated; kept the finished items",
        }
    if in_main or cur_key == shape.array_key or cuts:
        reason = "truncated before any item finished"
        if last_problem:
            reason = f"truncated; the kept part does not fit the shape ({last_problem})"
        return {
            "ok": False,
            "value": None,
            "truncated": True,
            "salvaged_count": 0,
            "unfinished": True,
            "unfinished_fragment": "",
            "reason": reason,
        }
    return None
