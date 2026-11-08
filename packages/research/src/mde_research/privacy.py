"""Query privacy (spec 4.4): queries are built from generic terms.

When ``policy.research.web.allow_company_name_in_queries`` is false the company, brand and reference
names (and the brand website) must not appear in any search query, however the query was produced
(the C6 model, the deterministic plan, a gap-fill phrase). ``QueryPrivacy`` knows the forbidden
terms and removes or rejects them; the engine applies it to every query and to every text that
feeds the planning calls.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from mde_research.domains import registrable_domain

_ALNUM = r"[^\W_]"
_COMPACT_MIN = 6  # terms at least this long (letters and digits) also match inside other words


def fold(text: str) -> str:
    """Case-fold and drop accents so ``Café`` matches ``cafe``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", fold(text))


def _tokens(term: str) -> list[str]:
    return [t for t in re.split(r"[\W_]+", fold(term)) if t]


def _pattern(term: str) -> re.Pattern[str] | None:
    tokens = _tokens(term)
    if not tokens:
        return None
    body = r"[\W_]*".join(re.escape(t) for t in tokens)
    return re.compile(rf"(?<!{_ALNUM}){body}(?!{_ALNUM})")


def terms_from_url(url: str | None) -> list[str]:
    """The host and the registrable name of a website (``www.acme-shop.com`` -> two terms)."""
    if not url:
        return []
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").casefold().removeprefix("www.")
    if not host:
        return []
    base = registrable_domain(host)
    label = base.split(".")[0]
    return [t for t in dict.fromkeys([host, base, label]) if t]


@dataclass(frozen=True)
class QueryPrivacy:
    """Forbidden terms for outgoing queries. ``restricted`` is False when names are allowed."""

    restricted: bool
    terms: tuple[str, ...] = ()
    _patterns: tuple[re.Pattern[str], ...] = field(default=(), repr=False, compare=False)
    _compact: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def build(
        cls,
        *,
        allow_company_name: bool,
        names: Iterable[str | None] = (),
        urls: Iterable[str | None] = (),
        org: str | None = None,
    ) -> QueryPrivacy:
        if allow_company_name:
            return cls(restricted=False)
        raw: list[str] = [n.strip() for n in names if n and n.strip()]
        for url in urls:
            raw += terms_from_url(url)
        if org and org.strip() and org.strip().casefold() != "platform":
            raw += [org.strip(), org.strip().replace("-", " ").replace("_", " ")]
        terms = tuple(dict.fromkeys(t for t in raw if _tokens(t)))
        patterns = tuple(p for p in (_pattern(t) for t in terms) if p is not None)
        compacts = tuple(
            dict.fromkeys(c for c in (compact(t) for t in terms) if len(c) >= _COMPACT_MIN)
        )
        return cls(restricted=True, terms=terms, _patterns=patterns, _compact=compacts)

    def violations(self, text: str) -> list[str]:
        """The forbidden terms found in ``text`` (word-wise, or inside a word for long names)."""
        if not self.restricted:
            return []
        folded = fold(text)
        found = [
            term
            for term, pattern in zip(self.terms, self._patterns, strict=False)
            if pattern.search(folded)
        ]
        squeezed = compact(text)
        for token in self._compact:
            if token in squeezed and not any(compact(t) == token for t in found):
                found.append(token)
        return found

    def scrub(self, text: str, replacement: str = "") -> str:
        """``text`` with every word-wise match replaced (long names inside words are left to
        :meth:`violations`)."""
        if not self.restricted:
            return text
        out = unicodedata.normalize("NFKC", text)
        for pattern in self._patterns:
            out = _sub_folded(pattern, out, replacement)
        return " ".join(out.split())

    def clean_query(self, query: str) -> str | None:
        """The query without forbidden terms, or ``None`` when nothing useful (or a leak) is left."""
        if not self.restricted:
            return query
        cleaned = self.scrub(query)
        if len(cleaned.split()) < 2 or self.violations(cleaned):
            return None
        return cleaned


def _sub_folded(pattern: re.Pattern[str], text: str, replacement: str) -> str:
    """Substitute on the accent-folded text while keeping the original characters elsewhere.

    ``fold`` can change lengths (``ß`` -> ``ss``), so work on a per-character folded map."""
    mapping: list[int] = []  # folded index -> original index
    folded_chars: list[str] = []
    for i, ch in enumerate(text):
        piece = fold(ch)
        folded_chars.append(piece)
        mapping.extend([i] * len(piece))
    folded = "".join(folded_chars)
    out: list[str] = []
    last = 0
    for match in pattern.finditer(folded):
        if not mapping:
            break
        start = mapping[match.start()]
        end = mapping[match.end() - 1] + 1
        if start < last:
            continue
        out.append(text[last:start])
        out.append(replacement)
        last = end
    out.append(text[last:])
    return "".join(out)
