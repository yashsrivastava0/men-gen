"""Code checks for a provider fact: a dossier field key from the data file, and a typed value grounded in the quote.

A typed value (``{amount, currency, unit, period, condition}``) is the machine-readable form of a fee, a limit or a time.
The model writes it next to the quote, so code decides whether the quote backs it (D-159, D-162):

* ``amount`` is a plain decimal string (never a float) and one of the quote's figures (``core/numbers.py``): "2.9" for
  "2.9%", "0.30" for "$0.30"; a wrong amount rejects the fact;
* ``currency`` is an ISO code that the quote shows, as the code or as a symbol or word listed in
  ``data/typed_values.yaml``; a currency the quote does not show rejects the fact;
* ``unit``, ``period`` and ``condition`` are kept when their words (and every figure in them) are in the quote, else that
  part becomes null (a missing part is honest; an invented one is not). The rest of the fact stays.

Pure functions, no I/O beyond the packaged data files read once.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from mde_research_agent.catalog.content import dossier_config, typed_values
from mde_research_agent.core.numbers import figures, missing_figures
from mde_research_agent.core.text import stem, tokens

_AMOUNT = re.compile(r"^\d+(?:\.\d+)?$")
_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_CODE = re.compile(r"^[A-Za-z]{3}$")
_FIELD_SEPARATORS = re.compile(r"[\s\-./]+")
_PREFIX = 4  # a stem that shares this many letters with another counts as the same word ("month", "monthly")


def normalize_field(raw: str) -> str:
    """``Webhook events`` -> ``webhook_events``: the form of a dossier field key."""
    return _FIELD_SEPARATORS.sub("_", raw.strip().casefold()).strip("_")


def field_problem(field: str) -> str | None:
    """Why ``field`` is not a dossier field the extractor may use, or None."""
    key = normalize_field(field)
    if key in dossier_config().extractable():
        return None
    return f"{field!r} is not a dossier field"


def normalize_amount(raw: str) -> str | None:
    """The amount as a plain decimal string, or None when it is not one ("1,500" -> "1500", "2.50" stays "2.50")."""
    text = raw.strip()
    if _GROUPED.match(text):
        text = text.replace(",", "")
    if not _AMOUNT.match(text):
        return None
    try:
        Decimal(text)
    except InvalidOperation:
        return None
    return text


def _quote_shows_currency(code: str, quote: str) -> bool:
    cfg = typed_values().currencies
    words = {code.casefold(), *(t.casefold() for t in cfg.get(code.upper(), ()))}
    low = quote.casefold()
    for tok in words:
        if not tok:
            continue
        if tok.isalpha():
            if len(tok) == 1:  # a lone letter ("r", "q") only counts right next to a number
                if re.search(rf"(?<![a-z]){re.escape(tok)}\s?\d", low):
                    return True
            elif re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", low):
                return True
        elif tok in low:  # a symbol, or a token with a symbol or a space in it
            return True
    return False


def _same_word(a: str, b: str) -> bool:
    sa, sb = stem(a), stem(b)
    if sa == sb:
        return True
    short, long_ = sorted((sa, sb), key=len)
    return len(short) >= _PREFIX and long_.startswith(short)


def _words_in_quote(text: str, quote_tokens: list[str], ignore: frozenset[str]) -> bool:
    """Is every meaningful word of ``text`` (and every figure in it) a word of the quote?"""
    words = [t for t in tokens(text) if t not in ignore and not t.isdigit()]
    return all(any(_same_word(w, q) for q in quote_tokens) for w in words)


def _unit_shown(unit: str, quote: str, quote_tokens: list[str]) -> bool:
    cfg = typed_values()
    ignore = frozenset(cfg.ignore_words)
    if unit.strip().casefold() in {u.casefold() for u in cfg.percent_units}:
        low = quote.casefold()
        return any(e in low for e in cfg.percent_evidence)
    if (
        unit.strip().upper() in cfg.currencies
    ):  # a currency used as the unit: the currency check covers it
        return _quote_shows_currency(unit.strip(), quote)
    return _words_in_quote(unit, quote_tokens, ignore)


def ground_typed(
    typed: dict[str, Any] | None, quote: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """``typed`` with the parts the quote does not back removed, and the problems that must reject the fact (an amount or a
    currency the quote does not show, or an amount that is not a decimal string). ``None`` stays ``None``."""
    if not typed:
        return None, []
    out = dict(typed)
    problems: list[str] = []
    qfigs = figures(quote)
    qtokens = tokens(quote)
    ignore = frozenset(typed_values().ignore_words)
    amount = out.get("amount")
    if amount not in (None, ""):
        plain = normalize_amount(str(amount))
        if plain is None:
            problems.append(f"amount {amount!r} is not a decimal string")
        else:
            out["amount"] = plain
            if missing_figures(figures(plain), qfigs):
                problems.append(f"amount {plain} is not in the quote")
    else:
        out["amount"] = None
    currency = out.get("currency")
    if currency not in (None, ""):
        code = str(currency).strip()
        if not _CODE.match(code):
            problems.append(f"currency {currency!r} is not a three-letter code")
        elif not _quote_shows_currency(code, quote):
            problems.append(f"currency {code.upper()} is not shown in the quote")
        else:
            out["currency"] = code.upper()
    else:
        out["currency"] = None
    unit = out.get("unit")
    if unit not in (None, "") and not _unit_shown(str(unit), quote, qtokens):
        out["unit"] = None
    period = out.get("period")
    if period not in (None, "") and not (
        not missing_figures(figures(str(period)), qfigs)
        and _words_in_quote(str(period), qtokens, ignore)
    ):
        out["period"] = None
    cond = out.get("condition")
    if cond not in (None, "") and not (
        not missing_figures(figures(str(cond)), qfigs)
        and _words_in_quote(str(cond), qtokens, ignore)
    ):
        out["condition"] = None
    for k in ("unit", "period", "condition"):
        if out.get(k) == "":
            out[k] = None
    if all(v in (None, "") for v in out.values()):
        return None, problems
    return out, problems


def ground_provider_fact(data: dict[str, Any], quote: str) -> dict[str, Any]:
    """The payload of a provider fact with its field key normalised and its typed value grounded in ``quote``.
    Raises ``ValueError`` (the candidate becomes ``invalid``) for an unknown field key, an amount or a currency the quote
    does not back."""
    problem = field_problem(str(data.get("field", "")))
    if problem:
        raise ValueError(problem)
    out = dict(data)
    out["field"] = normalize_field(str(data["field"]))
    typed, problems = ground_typed(data.get("typed"), quote)
    if problems:
        raise ValueError("; ".join(problems))
    out["typed"] = typed
    return out
