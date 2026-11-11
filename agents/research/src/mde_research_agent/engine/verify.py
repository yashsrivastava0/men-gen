"""Verify extracted items against the stored page: quote check, numbers, limits, forbidden patterns, payload validity.

Pure functions. A candidate is a wire atom (common fields + the kind's fields). Nothing downstream trusts the model:

* the quote must be a substring of the stored page text after normalisation (RV-02);
* every number, amount, percentage and date in the claim, the label and the numeric data must be IN the quote (RV-18):
  a missing one is fixed by widening the quote to the smallest enclosing sentence of the page (at most 40 words, flag
  ``number_widened``); failing that the unverified figure is DROPPED and the rest kept (the claim loses the sentences
  that state it, a figure-bearing data field becomes null, flag ``figures_dropped``) when the item's label and first
  sentence are clean; else the item is rejected as ``number_not_in_quote``. No figure the quote lacks ever survives;
* the date of an item is the page's own date, else a date CODE reads from the quote, the title or the URL: the
  model's idea of a date is not asked for and never used;
* a claim is cut only at the end of a sentence within 40 words, else rejected as ``claim_too_long`` (no clipped
  half sentences);
* strings are screened for URLs and instructions, lengths are enforced, the payload must fit the kind (RV-01).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import regex
from pydantic import BaseModel

from mde_research_agent.core.dates import date_in_text
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.numbers import (
    claim_figures,
    figures,
    missing_figures,
    strip_unverified,
)
from mde_research_agent.core.quotes import verify_quote
from mde_research_agent.core.safety import forbidden_in_value
from mde_research_agent.domain.atoms import COMMON_FIELDS, payload_from_wire, wire_atom_model
from mde_research_agent.engine.state import PageRecord

MAX_QUOTE_WORDS = 25
MAX_WIDENED_WORDS = 40
MAX_CLAIM_WORDS = 40
MAX_LABEL_WORDS = 8
_REGION = re.compile(r"^[a-z]{2}$")
_SENTENCE_END = re.compile(r"(?<=[.!?।。])\s+|\n+")
# Claim sentences for the figure drop: a full stop ends one only before a capital letter or an opening quote, so "Rs. 599",
# "No. 5" and "e.g. fees" stay whole (a danda or an ideographic stop always ends one).
_CLAIM_SENTENCE = regex.compile(r"(?<=[।。])\s+|(?<=[.!?])\s+(?=[\p{Lu}\"“‘(\[])")


@dataclass(frozen=True)
class Candidate:
    kind: str
    norm_key: str
    label: str
    claim: str
    quote: str
    impact: str
    published_on: date | None
    data: dict[str, Any]
    about_region: str = "unknown"
    about_entity: str | None = None
    flags: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Rejected:
    kind: str
    norm_key: str
    reason: str  # quote_not_found | number_not_in_quote | claim_too_long | forbidden_pattern | invalid | empty
    label: str = ""


def _clip(text: str, n: int) -> str:
    words = text.split()
    return " ".join(words[:n])


def cut_claim(text: str, limit: int = MAX_CLAIM_WORDS) -> str | None:
    """The claim as is when it fits; else cut at the last sentence end inside ``limit`` words; else None."""
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    head = " ".join(words[:limit])
    ends = [m.end() for m in re.finditer(r"[.!?]", head)]
    if ends:
        return head[: ends[-1]].strip()
    return None


def region_of(raw: str | None) -> str:
    """``about_region`` as stored: a lowercase two-letter country code, ``global`` or ``unknown``."""
    v = (raw or "").strip().lower()
    if _REGION.match(v):
        return v
    return "global" if v == "global" else "unknown"


def candidates_from_output(output: BaseModel, page: PageRecord) -> list[Candidate | Rejected]:
    """Flatten an extraction output (one array per kind) into candidates; a malformed item is ``Rejected``."""
    out: list[Candidate | Rejected] = []
    page_date = page.published
    for kind, atoms in output.model_dump().items():
        wire = wire_atom_model(kind)
        for raw in atoms:
            try:
                atom = wire.model_validate(raw)
                data = payload_from_wire(kind, atom)
            except ValueError:
                out.append(Rejected(kind, str(raw.get("norm_key", "")), "invalid"))
                continue
            key = clean_key(atom.norm_key)
            label = _clip(atom.label, MAX_LABEL_WORDS)
            if not key or not atom.quote.strip() or not atom.claim.strip():
                out.append(Rejected(kind, key, "empty", label))
                continue
            common = {f: getattr(atom, f) for f in COMMON_FIELDS}
            if forbidden_in_value(
                [common["claim"], common["label"], common["quote"], common["norm_key"], data]
            ):
                out.append(Rejected(kind, key, "forbidden_pattern", label))
                continue
            claim = cut_claim(atom.claim)
            if claim is None:
                out.append(Rejected(kind, key, "claim_too_long", label))
                continue
            quote = _clip(atom.quote, MAX_QUOTE_WORDS)
            published = (
                page_date
                or date_in_text(quote)
                or date_in_text(page.title)
                or date_in_text(page.final_url or page.url)
            )
            out.append(
                Candidate(
                    kind=kind,
                    norm_key=key,
                    label=label,
                    claim=claim,
                    quote=quote,
                    impact=atom.impact,
                    published_on=published,
                    data=data,
                    about_region=region_of(atom.about_region),
                    about_entity=(atom.about_entity or "").strip() or None,
                )
            )
    return out


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _SENTENCE_END.finditer(text):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans


def widen_quote(page: PageRecord, start: int, end: int, needed: list[Any]) -> str | None:
    """The smallest sentence window of the page around the quote (``start``/``end`` in the original text) that holds
    every figure in ``needed`` and has at most 40 words; None when there is none."""
    spans = _sentence_spans(page.text)
    if not spans:
        return None
    first = next(
        (i for i, (s, e) in enumerate(spans) if s <= start < e or start < s), len(spans) - 1
    )
    last = next((i for i, (s, e) in enumerate(spans) if s < end <= e), len(spans) - 1)
    last = max(last, first)
    windows = [(first, last)]
    if first > 0:
        windows.append((first - 1, last))
    if last < len(spans) - 1:
        windows.append((first, last + 1))
    for a, b in sorted(windows, key=lambda w: w[1] - w[0]):
        text = " ".join(page.text[spans[a][0] : spans[b][1]].split())
        if len(text.split()) > MAX_WIDENED_WORDS:
            continue
        if not missing_figures(needed, figures(text)) and verify_quote(page.norm, text).ok:
            return text
    return None


def check_candidate(
    c: Candidate, page: PageRecord, droppable: Mapping[str, Sequence[str]] | None = None
) -> Candidate | Rejected:
    """The candidate (its quote possibly widened, or an unverified figure dropped where ``droppable`` allows it) when it
    is really on the page and backs its numbers; else why not. ``droppable`` is the catalog's ``figure_drop`` map
    (kind to fields); None or an empty map means a missing figure always rejects the item."""
    res = verify_quote(page.norm, c.quote)
    if not res.ok:
        return Rejected(c.kind, c.norm_key, "quote_not_found", c.label)
    needed = claim_figures(c.claim, c.label, c.data)
    missing = missing_figures(needed, figures(c.quote))
    if not missing:
        return c
    wide = widen_quote(page, res.start, res.end, needed)
    if wide is not None:
        return replace(c, quote=wide, flags=(*c.flags, "number_widened"))
    kept = drop_unverified_figures(c, droppable.get(c.kind, ()) if droppable else ())
    if kept is None:
        return Rejected(c.kind, c.norm_key, "number_not_in_quote", c.label)
    return kept


def drop_unverified_figures(c: Candidate, fields: Sequence[str]) -> Candidate | None:
    """The candidate without the figures its quote does not contain, or None when what is left is not the item any more.

    The Small run lost three provider items to ``number_not_in_quote`` although each quote backed what the item was
    about (the endpoint sentence): the fees sat in ``data.fees`` or in a later sentence of the claim, and a quote is one
    sentence. The label and the FIRST sentence of the claim are the item's headline: if either states a figure the
    quote lacks, the item is rejected as before. Later sentences with an unverified figure are cut, and figure-bearing
    data fields with one become null, but only the ``fields`` the catalog lists for the kind (none: nothing is dropped).
    The result is checked again: it must hold no unverified figure."""
    if not fields:
        return None
    quote_figs = figures(c.quote)
    if missing_figures(figures(c.label, claim_side=True), quote_figs):
        return None
    sentences = [s for s in _CLAIM_SENTENCE.split(c.claim) if s.strip()]
    kept = [s for s in sentences if not missing_figures(figures(s, claim_side=True), quote_figs)]
    if not kept or not sentences or kept[0] != sentences[0]:
        return None
    out = replace(
        c,
        claim=" ".join(" ".join(kept).split()),
        data=strip_unverified(c.data, quote_figs, fields),
        flags=(*c.flags, "figures_dropped"),
    )
    if missing_figures(claim_figures(out.claim, out.label, out.data), quote_figs):
        return None
    return out
