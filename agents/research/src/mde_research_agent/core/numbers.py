"""Numbers, amounts, percentages, ranges and dates as comparable figures (RV-18).

A claim that states a number the quote does not contain is invented. ``figures(text)`` turns every number, amount,
percentage, range end and date in a text into a canonical key, so ``5,000``, ``5000``, ``Rs. 5000`` and ``Rs 5,000.00``
are one figure, ``2.5%`` and ``2.5 per cent`` are one, ``10-15 days`` gives 10 and 15, Devanagari digits are ASCII
digits, ``1.234,56`` is one thousand two hundred thirty-four and fifty-six hundredths, and ``two`` is 2. ``lakh`` and
``crore`` stay as written (a figure ``1.2`` next to the word lakh is NOT turned into 120000: the quote has to say it
the same way). Dates are kept whole (``d:2026-03-12``, ``d:2026-03``) and matched by the parts the claim states.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
# "one" is far too common as a pronoun to demand it in a quote; it still counts on the QUOTE side (so a claim digit 1
# is matched by the word one).
_CLAIM_WORDS = {w: v for w, v in _WORDS.items() if v >= 2}
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
_EURO = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+,\d{1,2}(?![\d.,])")
_INDIAN = re.compile(r"(?<![\d.,])\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?(?![\d,])")
_WESTERN = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])")
_PLAIN = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_WORD_RX = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)
_SPACED = re.compile(r"(?<=\d)[    ](?=\d{3}(?!\d))")  # 5 000 -> 5000 (thin and no-break spaces)


@dataclass(frozen=True)
class Figure:
    key: str  # "n:2.5" or "d:2026-03-12" / "d:2026-03"
    raw: str  # as written, for messages


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

    t = _SPACED.sub("", t)
    grab(_EURO, lambda s: s.replace(".", "").replace(",", "."))
    grab(_INDIAN, lambda s: s.replace(",", ""))
    grab(_WESTERN, lambda s: s.replace(",", ""))
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
    missing: list[Figure] = []
    seen: set[str] = set()
    for f in claim_figs:
        if f.key in seen:
            continue
        seen.add(f.key)
        if f.key.startswith("d:"):
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
    }
)
_MARKED = re.compile(r"[%$€£₹¥]|\b(?:rs|inr|usd|eur|gbp|aed|per ?cent|percent)\b", re.I)


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
        elif isinstance(value, (int, float)):
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


def claim_figures(claim: str, label: str, data: Mapping[str, Any]) -> list[Figure]:
    """Everything a claim states that a quote must back: the claim, the label and the numeric data."""
    out = figures(claim, claim_side=True) + figures(label, claim_side=True)
    for text in data_figure_texts(data):
        out += figures(text, claim_side=True)
    return out


def strip_unverified(
    data: Mapping[str, Any], quote_figs: Iterable[Figure], only: Iterable[str] | None = None
) -> dict[str, Any]:
    """``data`` without the parts whose figures the quote does not contain: a figure-bearing string (the fields that
    ``data_figure_texts`` checks) becomes ``None`` and a list loses the entries that carry such a string. Everything
    else, names and short phrases, is untouched. The rest of the item stays; only the unverified number goes.
    ``only`` limits it to those top-level fields (a figure anywhere else stays, and the caller's re-check fails)."""
    qfigs = list(quote_figs)
    allowed = None if only is None else frozenset(only)

    def bad(text: str) -> bool:
        return bool(missing_figures(figures(text, claim_side=True), qfigs))

    def walk(key: str, value: Any) -> Any:
        if isinstance(value, str):
            grounded = key in NUMERIC_FIELDS or _MARKED.search(value)
            return None if grounded and bad(value) else value
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
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
