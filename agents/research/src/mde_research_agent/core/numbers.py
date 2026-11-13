"""Numbers, amounts, percentages, ranges and dates as comparable figures (RV-18).

A claim that states a number the quote does not contain is invented. ``figures(text)`` turns every number, amount,
percentage, range end and date in a text into a canonical key, so ``5,000``, ``5000``, ``Rs. 5000`` and ``Rs 5,000.00``
are one figure, ``2.5%`` and ``2.5 per cent`` are one, ``10-15 days`` gives 10 and 15, Devanagari digits are ASCII
digits, ``1.234,56`` is one thousand two hundred thirty-four and fifty-six hundredths, and ``two`` is 2. ``lakh`` and
``crore`` stay as written (a figure ``1.2`` next to the word lakh is NOT turned into 120000: the quote has to say it
the same way). Dates are kept whole (``d:2026-03-12``, ``d:2026-03``) and matched by the parts the claim states.

Across languages (pt-PT, fr-SN, es-MX, en-IN, hi): a decimal comma is a decimal (``12,50`` and ``2,5%`` are 12.5 and 2.5),
a thousands separator is a comma, a dot between groups of three (``1.234.567``), or a thin, no-break, narrow no-break or plain
space between groups of exactly three digits after one to three leading ones (``5 000``, ``1 234 567``; ``2024 500`` is two
numbers), and a currency written next to an amount is a figure of its own (``c:INR``): when a claim states a currency the quote
must state the same one (``core/currencies.py``), so ``$350`` is never backed by ``₹350``. Number words of English, French,
Spanish and Portuguese on the quote side satisfy a digit.

Pure functions; the currency and number-word tables are data files read once.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_research_agent.core import currencies

NUMBER_WORDS_FILE = Path(__file__).resolve().parent.parent / "data" / "number_words.yaml"


@lru_cache(maxsize=1)
def _word_tables() -> tuple[dict[str, int], dict[str, int]]:
    """``(quote-side words, claim-side words)``: every language's number words satisfy a digit in a quote; only the English
    words 2 to 12 are demanded of a claim ("one" is far too common as a pronoun to demand it)."""
    try:
        raw = yaml.safe_load(NUMBER_WORDS_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        raw = {}
    every: dict[str, int] = {}
    for words in raw.values():
        every.update({str(w).casefold(): int(v) for w, v in (words or {}).items()})
    claim = {str(w).casefold(): int(v) for w, v in (raw.get("en") or {}).items() if int(v) >= 2}
    return every, claim


_WORDS, _CLAIM_WORDS = _word_tables()
_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_RX = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_ISO_MONTH = re.compile(r"(?<![\d-])(\d{4})-(\d{2})(?![\d-])")
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RX})\.?,?\s+(\d{{4}})\b", re.I)
_MDY = re.compile(rf"\b({_MONTH_RX})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_MY = re.compile(rf"\b({_MONTH_RX})\.?,?\s+(\d{{4}})\b", re.I)
_SP = "[\u202f\u2009\u00a0 ]"  # narrow no-break, thin, no-break and plain space
_EURO = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+,\d{1,2}(?![\d.,])")  # 1.234,56
_DOTS = re.compile(
    r"(?<![\d.,])\d{1,3}(?:\.\d{3}){2,}(?![\d.,])"
)  # 1.234.567 (one group, 1.234, stays a decimal)
_INDIAN = re.compile(r"(?<![\d.,])\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?(?![\d,])")  # 1,23,456
# 5 000 and 1 234 567 (and 1 234,56): a leading group of ONE TO THREE digits, then groups of exactly three. "2024 500" is two
# numbers: the join only happens when the leading group is not itself the tail of a longer number.
_SPACED = re.compile(r"(?<![\d.,])\d{1,3}(?:" + _SP + r"\d{3})+(?:[.,]\d{1,2})?(?![\d])")
_WESTERN = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])")  # 5,000 and 1,234.50
_COMMA_DECIMAL = re.compile(
    r"(?<![\d.,])\d+,(?:\d{1,2}(?!\d)|\d{4,})(?![\d]|,\d)"
)  # 12,50  2,5  3,14159
_PLAIN = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_WORD_RX = re.compile(r"\b(" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")\b", re.I)


@dataclass(frozen=True)
class Figure:
    key: str  # "n:2.5", "d:2026-03-12" / "d:2026-03", or "c:INR" (a currency stated next to an amount)
    raw: str  # as written, for messages
    codes: frozenset[str] = field(
        default_factory=frozenset
    )  # currency figures: every ISO code the token can mean


def to_ascii_digits(text: str) -> str:
    """Every decimal digit of any script becomes 0-9 (Devanagari, Arabic-Indic, full-width...)."""
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if ch.isdigit() and not ch.isascii():
            try:
                out.append(str(unicodedata.decimal(ch)))
                continue
            except ValueError:
                pass
        out.append(ch)
    return "".join(out)


def _canon(num: str) -> str | None:
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    if d == d.to_integral():
        # not quantize(): it raises InvalidOperation for a number longer than the context precision (29 digits)
        return format(d.to_integral_value(), "f")
    return format(d.normalize(), "f")


def _dates(text: str) -> tuple[list[Figure], str]:
    """Dates found in ``text`` and the text with those spans blanked (so their digits are not read twice)."""
    found: list[Figure] = []

    def take(m: re.Match[str], key: str) -> str:
        found.append(Figure(key, m.group(0)))
        return " " * len(m.group(0))

    def ymd(y: str, mo: int, d: int | None) -> str | None:
        if not (1 <= mo <= 12) or (d is not None and not 1 <= d <= 31):
            return None
        return f"d:{y}-{mo:02d}" + (f"-{d:02d}" if d is not None else "")

    def sub_iso(m: re.Match[str]) -> str:
        key = ymd(m[1], int(m[2]), int(m[3]))
        return take(m, key) if key else m.group(0)

    def sub_iso_month(m: re.Match[str]) -> str:
        key = ymd(m[1], int(m[2]), None)
        return take(m, key) if key else m.group(0)

    def sub_dmy(m: re.Match[str]) -> str:
        key = ymd(m[3], _MONTHS[m[2].lower()], int(m[1]))
        return take(m, key) if key else m.group(0)

    def sub_mdy(m: re.Match[str]) -> str:
        key = ymd(m[3], _MONTHS[m[1].lower()], int(m[2]))
        return take(m, key) if key else m.group(0)

    def sub_my(m: re.Match[str]) -> str:
        key = ymd(m[2], _MONTHS[m[1].lower()], None)
        return take(m, key) if key else m.group(0)

    for rx, fn in (
        (_ISO_DATE, sub_iso),
        (_DMY, sub_dmy),
        (_MDY, sub_mdy),
        (_MY, sub_my),
        (_ISO_MONTH, sub_iso_month),
    ):
        text = rx.sub(fn, text)
    return found, text


def figures(text: str, *, claim_side: bool = False) -> list[Figure]:
    """Every figure in ``text``. ``claim_side`` drops the word ``one`` (a pronoun far more often than a count)."""
    t = to_ascii_digits(text or "")
    dates, t = _dates(t)
    out: list[Figure] = list(dates)

    def grab(rx: re.Pattern[str], conv: Any) -> None:
        nonlocal t
        for m in rx.finditer(t):
            key = _canon(conv(m.group(0)))
            if key is not None:
                out.append(Figure(f"n:{key}", m.group(0)))
        t = rx.sub(lambda m: " " * len(m.group(0)), t)

    out += [Figure(m.key, m.raw, m.codes) for m in currencies.mentions(t)]
    grab(_EURO, lambda s: s.replace(".", "").replace(",", "."))
    grab(_INDIAN, lambda s: s.replace(",", ""))
    grab(_DOTS, lambda s: s.replace(".", ""))
    grab(_SPACED, lambda s: re.sub(_SP, "", s).replace(",", "."))
    grab(_WESTERN, lambda s: s.replace(",", ""))
    grab(_COMMA_DECIMAL, lambda s: s.replace(",", "."))
    grab(_PLAIN, lambda s: s)
    words = _CLAIM_WORDS if claim_side else _WORDS
    for m in _WORD_RX.finditer(t):
        w = m.group(1).lower()
        if w in words:
            out.append(Figure(f"n:{words[w]}", m.group(0)))
    return out


def _date_covered(claim_key: str, quote_dates: list[str]) -> bool:
    parts = claim_key[2:].split("-")
    for q in quote_dates:
        qp = q[2:].split("-")
        if len(qp) >= len(parts) and qp[: len(parts)] == parts:
            return True
    return False


def missing_figures(claim_figs: Iterable[Figure], quote_figs: Iterable[Figure]) -> list[Figure]:
    """The claim's figures the quote does not contain (a date matches by the parts the claim states)."""
    quote_list = list(quote_figs)
    quote_keys = {f.key for f in quote_list}
    quote_dates = [f.key for f in quote_list if f.key.startswith("d:")]
    quote_currencies = [f.codes for f in quote_list if f.key.startswith("c:")]
    missing: list[Figure] = []
    seen: set[str] = set()
    for f in claim_figs:
        if f.key in seen:
            continue
        seen.add(f.key)
        if f.key.startswith("c:"):
            # the claim states a currency: the quote must state the same one (currency_mismatch), a swap never passes
            if not currencies.agree(f.codes, quote_currencies):
                missing.append(f)
        elif f.key.startswith("d:"):
            if not _date_covered(f.key, quote_dates):
                missing.append(f)
        elif f.key not in quote_keys:
            missing.append(f)
    return missing


# Fields of an item's ``data`` whose numbers must be in the quote (the rest are names and short phrases).
NUMERIC_FIELDS = frozenset(
    {
        "value",
        "formula",
        "fees",
        "limits",
        "constraints",
        "value_range",
        "effective_date",
        "since",
        "example",
        "validation",
        # the typed value of a fee, limit, duration or percentage (D-162): {amount, currency, period, unit, condition}. It is
        # registered here so a typed value cannot slip past RV-18 (amounts are strings or Decimal, never float).
        "amount",
        "currency",
        "period",
        "unit",
    }
)
_MARKED = re.compile(
    r"[%$€£₹¥₦₡]|\b(?:rs|inr|usd|eur|gbp|aed|mxn|gtq|crc|xof|xaf|fcfa|brl|per ?cent|percent)\b",
    re.I,
)


def data_figure_texts(data: Mapping[str, Any]) -> list[str]:
    """The strings of ``data`` whose figures must be grounded: numeric fields, plus any string with a currency
    sign, a percent or a currency code. Walks lists and nested objects."""
    out: list[str] = []

    def walk(key: str, value: Any) -> None:
        if isinstance(value, str):
            if key in NUMERIC_FIELDS or _MARKED.search(value):
                out.append(value)
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float, Decimal)):
            if key in NUMERIC_FIELDS:
                out.append(str(value))
        elif isinstance(value, Mapping):
            for k, v in value.items():
                walk(str(k), v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(key, v)

    for k, v in data.items():
        walk(str(k), v)
    return out


def typed_currencies(data: Mapping[str, Any]) -> list[Figure]:
    """The currency of every typed value in ``data`` (a mapping with an ``amount`` and a ``currency`` token, anywhere in the
    item's data): a typed ``currency`` is a token with no number beside it, so it is read here and not by ``figures``."""
    out: list[Figure] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            cur = value.get("currency")
            if "amount" in value and isinstance(cur, str):
                codes = currencies.codes_of(cur)
                if codes:
                    out.append(Figure("c:" + "/".join(sorted(codes)), cur, codes))
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(data)
    return out


def claim_figures(claim: str, label: str, data: Mapping[str, Any]) -> list[Figure]:
    """Everything a claim states that a quote must back: the claim, the label, the numeric data and the currency of a
    typed value."""
    out = figures(claim, claim_side=True) + figures(label, claim_side=True)
    for text in data_figure_texts(data):
        out += figures(text, claim_side=True)
    return out + typed_currencies(data)


def strip_unverified(
    data: Mapping[str, Any], quote_figs: Iterable[Figure], only: Iterable[str] | None = None
) -> dict[str, Any]:
    """``data`` without the parts whose figures the quote does not contain: a figure-bearing string (the fields that
    ``data_figure_texts`` checks) becomes ``None`` and a list loses the entries that carry such a string. Everything
    else, names and short phrases, is untouched. The rest of the item stays; only the unverified number goes.
    ``only`` limits it to those top-level fields (a figure anywhere else stays, and the caller's re-check fails)."""
    qfigs = list(quote_figs)
    allowed = None if only is None else frozenset(only)
    quote_currencies = [f.codes for f in qfigs if f.key.startswith("c:")]

    def bad(text: str) -> bool:
        return bool(missing_figures(figures(text, claim_side=True), qfigs))

    def walk(key: str, value: Any) -> Any:
        if isinstance(value, str):
            if (
                key == "currency"
            ):  # a typed currency is a token on its own: the quote must state the same currency
                codes = currencies.codes_of(value)
                return None if codes and not currencies.agree(codes, quote_currencies) else value
            grounded = key in NUMERIC_FIELDS or _MARKED.search(value)
            return None if grounded and bad(value) else value
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float, Decimal)):
            return None if key in NUMERIC_FIELDS and bad(str(value)) else value
        if isinstance(value, Mapping):
            return {str(k): walk(str(k), v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [kept for kept in (walk(key, v) for v in value) if kept is not None]
        return value

    return {
        str(k): (v if allowed is not None and str(k) not in allowed else walk(str(k), v))
        for k, v in data.items()
    }
