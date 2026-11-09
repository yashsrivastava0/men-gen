"""Quote verification (build prompt 02, section 3.2; ADR 0015 point 4). Pure functions, no I/O.

A fact carries a verbatim quote. Code, not the model, decides whether the quote really occurs in its source:

* ``normalize`` makes two texts comparable: NFKC, hidden format characters dropped (zero-width space, joiners,
  soft hyphen, BOM, bidi marks), typographic quotes and dashes folded to ASCII, case folded, every run of
  whitespace collapsed to one space, ends stripped. The same function is applied to the quote and to the source,
  so Devanagari (including nukta letters written as one code point or two), Hinglish and English all compare
  consistently.
* ``verify_quote`` is true only if the normalized quote (at least ``min_chars`` long) is a substring of the
  normalized source. Edge punctuation and wrapping quote marks of the *quote* are not content, so they are
  stripped when (and only when) the strict form does not match. Anything else (a changed word, an ellipsis in
  the middle, a paraphrase) is a failure and the fact is stored with ``unverified_quote=True``.
* ``option_label_sources``: a tap's source text is the labels of the options the user chose. The assistant's own
  question text is never a source, otherwise the model could "quote" itself.

Everything is linear in the input size (a translate table, one scan, one substring search; no regex).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence

from mde_compass.domain.cards import Card

DEFAULT_MIN_CHARS = 3
_FOLD: dict[int, str] = {
    **dict.fromkeys(map(ord, "‘’‚‛′ʼ`´"), "'"),
    **dict.fromkeys(map(ord, "“”„‟″«»"), '"'),
    **dict.fromkeys(map(ord, "‐‑‒–—―−"), "-"),
}


def _visible(text: str) -> str:
    """Drop format characters (category Cf) and non-whitespace control characters (Cc)."""
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf" or (cat == "Cc" and not ch.isspace()):
            continue
        out.append(ch)
    return "".join(out)


def normalize(text: str) -> str:
    """NFKC, hidden characters removed, quotes and dashes folded, casefolded, whitespace collapsed, stripped."""
    folded = _visible(unicodedata.normalize("NFKC", text)).translate(_FOLD).casefold()
    return " ".join(unicodedata.normalize("NFKC", folded).split())


def _is_edge_noise(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def _strip_edges(text: str) -> str:
    start, end = 0, len(text)
    while start < end and _is_edge_noise(text[start]):
        start += 1
    while end > start and _is_edge_noise(text[end - 1]):
        end -= 1
    return text[start:end]


def verify_quote(quote: str, source_text: str, *, min_chars: int = DEFAULT_MIN_CHARS) -> bool:
    """True iff the normalized ``quote`` (at least ``min_chars`` long) occurs in the normalized ``source_text``."""
    nq = normalize(quote)
    if len(nq) < min_chars:
        return False
    ns = normalize(source_text)
    if nq in ns:
        return True
    trimmed = _strip_edges(nq)
    return len(trimmed) >= min_chars and trimmed in ns


def verify_against(
    quote: str, sources: Iterable[str], *, min_chars: int = DEFAULT_MIN_CHARS
) -> bool:
    """The quote is verbatim in at least one of ``sources`` (for example the labels of a multi-select tap)."""
    return any(verify_quote(quote, source, min_chars=min_chars) for source in sources)


def option_label_sources(card: Card, selected_ids: Iterable[str]) -> list[str]:
    """The source texts of a tap: the labels of the selected options, in the order selected.

    Unknown ids are ignored and an id is used once. The card's question text, ``why_we_ask`` and option
    descriptions or reasons are never sources: only what the user saw as the label of what they tapped.
    """
    by_id = {option.id: option.label for option in card.options}
    out: list[str] = []
    seen: set[str] = set()
    for option_id in selected_ids:
        if option_id in seen or option_id not in by_id:
            continue
        seen.add(option_id)
        out.append(by_id[option_id])
    return out


def sources_for_text(*texts: str | None) -> Sequence[str]:
    """Typed or spoken input: the user's message (and any other user-authored texts), blanks dropped."""
    return [t for t in texts if t and t.strip()]
