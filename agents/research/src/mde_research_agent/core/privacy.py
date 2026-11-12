"""The privacy guard: what a search query may never contain.

Owner instruction of 2026-11-13: the client's own name and private facts never leave in a query to a search provider;
a code guard and a test enforce it. This OVERRIDES architecture decision AD-54 (2026-11-11), which had removed name and
personal-data filtering and left validator RV-11 reserved; RV-11 now runs again (``validators/registry.py``).

``build_private_terms`` reads a Brief once, at intake, and lists what must stay private:

* the brand name and the names of the person's own business (``own_brand``: the brand slot and the entities of
  context facts that carry an own-brand cue such as "called" or "I run");
* telephone numbers, e-mail addresses and street-level addresses found anywhere in the Brief's own words;
* the entities (and own words) of facts typed as sensitive, and people's names (entities of facts typed as a person)
  when the Brief has such types.

``find_private`` answers "which of these does this query contain?". The worker calls it inside the query gate
(``engine/worker.py``, so a model's variants, follow-ups and extra topics are all covered); ``GuardedSearch`` repeats the
check just before the provider as a second line. A found term makes the query fail with reason ``private_term``.

The words that make an address or a sensitive fact live in ``data/privacy.yaml``. Nothing here names a company or place.
Terms are never logged or shown: callers count them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_research_agent.core.text import tokens
from mde_research_agent.domain.input import PrivateTerm, PrivateTerms

PRIVACY_FILE = Path(__file__).resolve().parent.parent / "data" / "privacy.yaml"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w+])\+?\d[\d\s().-]{6,}\d(?!\w)")
_DATEISH = re.compile(r"^\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\s*$")
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    try:
        raw = yaml.safe_load(PRIVACY_FILE.read_text(encoding="utf-8")) or {}
    except OSError:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _words(key: str) -> tuple[str, ...]:
    value = _config().get(key) or []
    return tuple(str(w).casefold() for w in value if str(w).strip())


@lru_cache(maxsize=1)
def _address_patterns() -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    street = "|".join(
        re.escape(w) for w in sorted(set(_words("street_words")), key=len, reverse=True)
    )
    unit = "|".join(re.escape(w) for w in sorted(set(_words("unit_words")), key=len, reverse=True))
    name = r"(?:[^\W\d_][\w.'’-]*\s+){0,3}"
    flags = re.IGNORECASE
    # never matches when the data lists no words
    never = r"(?!x)x"
    number_first = re.compile(
        rf"\b\d{{1,5}}[A-Za-z]?(?:[/-]\d{{1,4}})?,?\s+{name}(?:{street or never})\b\.?", flags
    )
    street_first = re.compile(rf"\b(?:{street or never})\s+{name}\d{{1,5}}[A-Za-z]?\b", flags)
    unit_number = re.compile(
        rf"\b(?:{unit or never})\.?\s*(?:no\.?|number|#)?\s*[A-Za-z]?\d{{1,5}}[A-Za-z]?\b", flags
    )
    return number_first, street_first, unit_number


# --- reading a Brief ---------------------------------------------------------------------------------------


def _strings(node: Any, skip: frozenset[str]) -> Iterable[str]:
    """Every text in a JSON tree that the person said or wrote (bookkeeping keys are skipped)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for key, value in node.items():
            if str(key) not in skip:
                yield from _strings(value, skip)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _strings(item, skip)


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _phones(text: str) -> Iterable[str]:
    for mt in _PHONE.finditer(text):
        raw = mt.group(0).strip()
        n = len(_digits(raw))
        if MIN_PHONE_DIGITS <= n <= MAX_PHONE_DIGITS and not _DATEISH.match(raw):
            yield raw


def _addresses(text: str) -> Iterable[tuple[str, str]]:
    """``(kind, text)`` for each street-level address in ``text``; the street without its number is a term too."""
    number_first, street_first, unit_number = _address_patterns()
    for mt in number_first.finditer(text):
        whole = mt.group(0).strip(" ,.")
        yield "address", whole
        street = re.sub(r"^\d{1,5}[A-Za-z]?(?:[/-]\d{1,4})?,?\s+", "", whole)
        if len(tokens(street)) >= 2:
            yield "address", street
    for pattern in (street_first, unit_number):
        for mt in pattern.finditer(text):
            yield "address", mt.group(0).strip(" ,.")


def _fact_terms(facts: Iterable[Any]) -> Iterable[tuple[str, str]]:
    """Terms from facts typed as a person or as sensitive (the types are data, not code)."""
    persons = set(_words("person_fact_types"))
    sensitive = set(_words("sensitive_fact_types"))
    for fact in facts:
        ftype = str(getattr(fact, "type", "") or "").casefold()
        if ftype in persons:
            for ent in getattr(fact, "entities", ()) or ():
                yield "person", str(ent)
        elif ftype in sensitive:
            for ent in getattr(fact, "entities", ()) or ():
                yield "sensitive", str(ent)
            statement = str(getattr(fact, "statement", "") or "")
            if 0 < len(statement.split()) <= 8:
                yield "sensitive", statement


def build_private_terms(
    brief: Mapping[str, Any],
    *,
    own_brand: Iterable[str] = (),
    facts: Iterable[Any] = (),
    generic: Iterable[str] = (),
) -> PrivateTerms:
    """The private terms of a Brief.

    ``own_brand`` is what intake already found (the brand slot and own-business entities); ``facts`` are the Brief's
    facts (objects with ``type``, ``entities`` and ``statement``); ``generic`` are words so common in queries that a
    term made only of them is dropped instead of blocking every query (a brand called "Studio")."""
    cfg = _config()
    min_chars = int(cfg.get("min_term_chars") or 3)
    cap = int(cfg.get("max_terms") or 300)
    skip = frozenset(str(k) for k in (cfg.get("skip_keys") or ()))
    plain = {t for g in generic for t in tokens(str(g))}
    found: dict[tuple[str, str], PrivateTerm] = {}

    def add(kind: str, text: str) -> None:
        text = " ".join(str(text).split())
        key = " ".join(tokens(text)) if kind not in ("phone", "email") else text.casefold()
        if kind == "phone":
            key = _digits(text)
        if not key or len(key.replace(" ", "")) < min_chars:
            return
        if kind in ("brand", "person", "sensitive") and all(t in plain for t in key.split()):
            return
        found.setdefault((kind, key), PrivateTerm(kind=kind, text=text))

    for name in own_brand:
        add("brand", name)
    for kind, text in _fact_terms(facts):
        add(kind, text)
    for text in _strings(brief, skip):
        if len(text) < 6:
            continue
        for mt in _EMAIL.finditer(text):
            add("email", mt.group(0))
        for phone in _phones(text):
            add("phone", phone)
        for kind, addr in _addresses(text):
            add(kind, addr)
        if len(found) >= cap:
            break
    return PrivateTerms(terms=tuple(found.values())[:cap])


# --- checking a query --------------------------------------------------------------------------------------


def _digit_runs(query: str) -> list[str]:
    return [_digits(mt.group(0)) for mt in _PHONE.finditer(query)] + [
        _digits(r) for r in re.findall(r"\d{6,}", query)
    ]


def find_private(query: str, terms: PrivateTerms | Iterable[PrivateTerm]) -> list[str]:
    """The private terms (as the Brief wrote them) that ``query`` contains; empty means the query may go.

    A name matches as whole words in order ("acme studio pricing" contains "Acme Studio"); an e-mail by its text; a
    telephone number by its digits, however it is spaced ("98765 43210" and "9876543210" are the same number)."""
    items = terms.terms if isinstance(terms, PrivateTerms) else tuple(terms)
    if not items or not query.strip():
        return []
    qtokens = tokens(query)
    joined = f" {' '.join(qtokens)} "
    lowered = query.casefold()
    runs: list[str] | None = None
    hits: list[str] = []
    for term in items:
        if term.kind == "email":
            hit = term.text.casefold() in lowered
        elif term.kind == "phone":
            if runs is None:
                runs = _digit_runs(query)
            want = _digits(term.text)
            hit = bool(want) and any(want in run or (len(run) >= 8 and run in want) for run in runs)
        else:
            words = tokens(term.text)
            hit = bool(words) and f" {' '.join(words)} " in joined
        if hit:
            hits.append(term.text)
    return hits
