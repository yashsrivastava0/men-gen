"""Currency mentions for the number check (RV-18): which currency does a text state next to an amount?

``data/currencies.yaml`` maps tokens ("EUR", "euros", "₹", "FCFA", "$") to the ISO codes they can mean. A mention is a token
that stands next to a digit (``₹350``, ``350 EUR``, ``Rs. 5,000``); a token with no number beside it ("pay in euros") is not an
amount and is ignored, so prose never invents a currency figure. Two mentions AGREE when their code sets share a code: an
ambiguous token ("$", "FCFA") agrees with every currency it can mean and with itself, an exact one only with its own.

Pure functions; the data file is read once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CURRENCIES_FILE = Path(__file__).resolve().parent.parent / "data" / "currencies.yaml"
_BEFORE = re.compile(r"\d[\s.,:\-]{0,2}$")  # a digit just before the token ("350 EUR")
_AFTER = re.compile(r"[\s.,:\-]{0,2}\d")  # a digit just after it ("EUR 350", "Rs. 5,000")


@dataclass(frozen=True)
class CurrencyMention:
    codes: frozenset[str]  # every ISO code the token can mean
    raw: str  # as written

    @property
    def key(self) -> str:
        return "c:" + "/".join(sorted(self.codes))


@lru_cache(maxsize=1)
def _tokens() -> dict[str, frozenset[str]]:
    try:
        raw = yaml.safe_load(CURRENCIES_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    table = raw.get("tokens") or {}
    return {
        str(tok).casefold(): frozenset(str(c).upper() for c in codes)
        for tok, codes in table.items()
        if codes
    }


@lru_cache(maxsize=1)
def _pattern() -> re.Pattern[str] | None:
    toks = sorted(_tokens(), key=lambda t: (-len(t), t))
    if not toks:
        return None
    parts = []
    for tok in toks:
        body = r"\s+".join(re.escape(w) for w in tok.split())
        if tok[0].isalnum():
            body = r"(?<![^\W\d_])" + body  # not inside a longer word
        if tok[-1].isalnum():
            body += r"(?![^\W\d_])"
        parts.append(body)
    return re.compile("|".join(parts), re.IGNORECASE)


def codes_of(token: str) -> frozenset[str]:
    """The ISO codes a currency token or code can mean ("INR", "₹", "euros", "FCFA"); empty when it is not a known token.
    Used for a typed value's ``currency`` field, which is a token on its own (no number beside it)."""
    return _tokens().get(" ".join(token.casefold().replace(".", " ").split()), frozenset())


def mentions(text: str) -> list[CurrencyMention]:
    """Every currency mention in ``text`` that stands next to a digit (digits of any script must already be ASCII)."""
    pat = _pattern()
    if pat is None or not text:
        return []
    out: list[CurrencyMention] = []
    for m in pat.finditer(text):
        start, end = m.span()
        near = _BEFORE.search(text[max(0, start - 4) : start]) or _AFTER.match(text[end : end + 4])
        if not near:
            continue
        codes = _tokens().get(" ".join(m.group(0).casefold().split()))
        if codes:
            out.append(CurrencyMention(codes, m.group(0)))
    return out


def agree(claim: frozenset[str], quote: list[frozenset[str]]) -> bool:
    """Does any currency the quote states share a code with the claim's currency?"""
    return any(claim & q for q in quote)
