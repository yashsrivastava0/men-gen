"""Planted-error corruption (doc 14 section 5): take a clean, verified item and plant ONE error of a known kind.

Kinds: ``number_changed``, ``entity_swapped``, ``condition_flipped``, ``region_swapped``, ``unsupported_attribute_added``,
and three that only the checker can catch because the number itself does not move: ``currency_swapped`` (the same amount
in another currency), ``period_changed`` (per month becomes per year) and ``qualifier_dropped`` ("from", "up to" and
"at least" become "exactly"). A kind that does not apply to an item (no currency, no period, no qualifier) gives None.
Deterministic for a seed. Every function is pure and returns a NEW dict; the quote is never changed (the error is in what
the item CLAIMS, so a correct checker must notice that the quote no longer supports it).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

KINDS = (
    "number_changed",
    "entity_swapped",
    "condition_flipped",
    "region_swapped",
    "unsupported_attribute_added",
    "currency_swapped",
    "period_changed",
    "qualifier_dropped",
)
REGIONS = ("us", "gb", "ae", "in", "ke", "de")
SWAP_ENTITY = "Example Provider Ltd"
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _pick(seed: int, key: str, n: int) -> int:
    return int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()[:8], 16) % max(1, n)


def _bump(raw: str, seed: int, key: str) -> str:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return raw
    factor = (2, 3, 5, 10)[_pick(seed, key, 4)]
    new = value * factor if value else 7
    return str(int(new)) if new == int(new) else f"{new:g}"


def number_changed(item: dict[str, Any], seed: int) -> dict[str, Any] | None:
    claim = item["claim"]
    m = _NUM.search(claim)
    if not m:
        return None
    new = _bump(m.group(0), seed, item["id"])
    return {**item, "claim": claim[: m.start()] + new + claim[m.end() :]}


def entity_swapped(item: dict[str, Any], seed: int) -> dict[str, Any]:
    ent = item.get("about_entity")
    claim = item["claim"]
    if ent and ent.lower() in claim.lower():
        swapped = re.sub(re.escape(ent), SWAP_ENTITY, claim, flags=re.I)
    else:
        swapped = f"{SWAP_ENTITY} states that {claim[:1].lower() + claim[1:]}"
    return {**item, "claim": swapped, "about_entity": SWAP_ENTITY}


_FLIPS = (
    (" is ", " is not "),
    (" are ", " are not "),
    (" must ", " must not "),
    (" can ", " cannot "),
    (" includes ", " excludes "),
    (" required", " not required"),
)


def condition_flipped(item: dict[str, Any], seed: int) -> dict[str, Any]:
    claim = item["claim"]
    for a, b in _FLIPS:
        if a in claim:
            return {**item, "claim": claim.replace(a, b, 1)}
    return {**item, "claim": claim.rstrip(".") + ", excluding tax and only on request."}


def region_swapped(item: dict[str, Any], seed: int) -> dict[str, Any]:
    now = item.get("about_region") or "unknown"
    choices = [r for r in REGIONS if r != now]
    return {**item, "about_region": choices[_pick(seed, item["id"] + "r", len(choices))]}


def unsupported_attribute_added(item: dict[str, Any], seed: int) -> dict[str, Any]:
    data = dict(item.get("data") or {})
    extra = (
        "with biometric re-verification",
        "paid in advance by card",
        "and a mandatory 30-day lock-in",
    )[_pick(seed, item["id"] + "u", 3)]
    return {
        **item,
        "claim": item["claim"].rstrip(".") + f" {extra}.",
        "data": {**data, "constraints": [*(data.get("constraints") or []), extra]},
    }


# Pairs are tried in order; the first one found in the claim is swapped. Longer forms first so "per month" is not
# read as "month". Whole currency words and symbols only; nothing here names a company or a place.
_CURRENCIES = (
    ("₹", "$"),
    ("Rs.", "USD"),
    ("Rs ", "USD "),
    ("INR", "USD"),
    ("R$", "€"),
    ("MXN", "USD"),
    ("XOF", "EUR"),
    ("FCFA", "EUR"),
    ("€", "$"),
    ("EUR", "USD"),
    ("£", "$"),
    ("GBP", "USD"),
    ("USD", "INR"),
    ("$", "₹"),
)


def currency_swapped(item: dict[str, Any], seed: int) -> dict[str, Any] | None:
    claim = item["claim"]
    for a, b in _CURRENCIES:
        if a in claim:
            return {**item, "claim": claim.replace(a, b, 1)}
    return None


_PERIODS = (
    ("per month", "per year"),
    ("a month", "a year"),
    ("/month", "/year"),
    ("/mo", "/yr"),
    ("monthly", "yearly"),
    ("per year", "per month"),
    ("a year", "a month"),
    ("/year", "/month"),
    ("yearly", "monthly"),
    ("annually", "monthly"),
    ("per day", "per week"),
    ("daily", "weekly"),
    ("per week", "per day"),
    ("weekly", "daily"),
    ("per class", "per month"),
    ("per session", "per month"),
)


def period_changed(item: dict[str, Any], seed: int) -> dict[str, Any] | None:
    claim = item["claim"]
    low = claim.lower()
    for a, b in _PERIODS:
        at = low.find(a)
        if at >= 0:
            return {**item, "claim": claim[:at] + b + claim[at + len(a) :]}
    return None


_QUALIFIER = re.compile(
    r"\b(starts? at|starting at|starting from|from|up to|at least|at most|a minimum of|a maximum of|"
    r"around|about|approximately)\b",
    re.I,
)


def qualifier_dropped(item: dict[str, Any], seed: int) -> dict[str, Any] | None:
    claim = item["claim"]
    m = _QUALIFIER.search(claim)
    if not m:
        return None
    return {**item, "claim": claim[: m.start()] + "exactly" + claim[m.end() :]}


MAKERS = {
    "number_changed": number_changed,
    "entity_swapped": entity_swapped,
    "condition_flipped": condition_flipped,
    "region_swapped": region_swapped,
    "unsupported_attribute_added": unsupported_attribute_added,
    "currency_swapped": currency_swapped,
    "period_changed": period_changed,
    "qualifier_dropped": qualifier_dropped,
}


def corrupt(item: dict[str, Any], kind: str, seed: int) -> dict[str, Any] | None:
    """One corrupted copy of ``item`` (None when the kind does not apply, e.g. no number to change)."""
    out = MAKERS[kind](item, seed)
    if out is None:
        return None
    return {**out, "planted": kind, "origin_id": item["id"], "id": f"{item['id']}~{kind}"}
