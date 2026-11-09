"""References: what to borrow and what to avoid from each named app, and confirming a named business first.

* A reference the user names ("like Swiggy") is stored as ``assumed`` until a card confirms it, so it can
  never be Locked on a mention alone.
* "I own Swiggy" names a business as the user's OWN (DC-12): one confirm card asks whether it is theirs or an
  app they like before it is treated as a reference.
* "Exactly like Zomato, same logo" (IN-12): say ONCE that only patterns are borrowed, never logo, copy or screens.
* ONE form card asks what to borrow and what to avoid from each reference (kind ``quick_form``).

Pure functions over plain values; the cue phrases are data in ``flows.yaml``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import (
    Card,
    CardOption,
    CardProgress,
    CustomInput,
    SkipSpec,
    YouDecide,
)
from mde_compass.engine.flows import data
from mde_compass.engine.language import normalize

FRAME = "apps_you_like"
SLOT = "scope.references"
OWN_ID = "own"
LIKE_ID = "like"
BORROW_PREFIX = "borrow_"
AVOID_PREFIX = "avoid_"
MAX_ITEMS = 6
_WORD = r"[A-Z][\w'’-]{1,30}"
_BRAND = rf"({_WORD}(?:\s{{1,3}}{_WORD}){{0,2}})"
_SPLIT_ITEMS = re.compile(r"[,;\n]+")


@dataclass(frozen=True)
class RefInfo:
    """A reference as the Brief holds it."""

    label: str
    borrow: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    claimed_own: bool = False  # the user's words named it as their own business
    confirmed: bool = False


@lru_cache(maxsize=1)
def _own_regex() -> re.Pattern[str]:
    cues = sorted(data.phrases("references", "own_business_cues"), key=len, reverse=True)
    alt = "|".join(re.escape(c) for c in cues)
    # the cue is matched case-insensitively on the original text, the brand needs capital letters
    return re.compile(rf"(?<!\w)(?i:{alt})\s{{1,4}}{_BRAND}")


@lru_cache(maxsize=1)
def _stop() -> frozenset[str]:
    return frozenset(data.phrases("references", "brand_stop") + data.phrases("languages"))


def own_business(text: str) -> tuple[str, str] | None:
    """(brand, quote) when the text names a business as the user's own ("I own Swiggy"), else None."""
    m = _own_regex().search(text[: data.SCAN_LIMIT])
    if m is None:
        return None
    brand = m.group(1).strip()
    words = [w for w in brand.split() if normalize(w) not in _stop()]
    if not words:
        return None
    return " ".join(words), m.group(0).strip()[:200]


def exact_copy_cue(text: str) -> bool:
    """The user wants something 'exactly like' a brand (logo, screens, copy), which we never copy."""
    return data.find_phrase(text, data.phrases("references", "exact_cues")) is not None


def info_of(
    label: str, borrow: Sequence[str], avoid: Sequence[str], quotes: Sequence[str], status: str
) -> RefInfo:
    return RefInfo(
        label=label,
        borrow=tuple(borrow),
        avoid=tuple(avoid),
        claimed_own=any(own_business(q) is not None for q in quotes),
        confirmed=status == "confirmed",
    )


def flow_card(card: Card) -> bool:
    """Is this one of the cards built here (not the generic card writer's)?"""
    if card.frame != FRAME or not card.options:
        return False
    return all(
        o.id in (OWN_ID, LIKE_ID) or o.id.startswith((BORROW_PREFIX, AVOID_PREFIX))
        for o in card.options
    )


def pending_confirm(refs: Sequence[RefInfo], asked_labels: set[str]) -> RefInfo | None:
    """The first reference that was named as the user's own business and has not been asked about yet."""
    for r in refs:
        if r.claimed_own and not r.confirmed and r.label.casefold() not in asked_labels:
            return r
    return None


def needs_form(refs: Sequence[RefInfo], *, form_asked: bool) -> list[RefInfo]:
    """References to ask borrow/avoid about: not waiting for the own-business confirmation, nothing said yet."""
    if form_asked:
        return []
    ready = [r for r in refs if not (r.claimed_own and not r.confirmed)]
    if not ready or any(r.borrow or r.avoid for r in ready):
        return []
    return ready[: data.policy().max_named_references]


def _card(
    card_id: str, kind_options: list[CardOption], question: str, why: str, *, card_type: str,
    n: int, of_about: int, language: str, must_confirm: bool, slot: str = SLOT,
) -> Card:  # fmt: skip
    return Card(
        id=card_id, frame=FRAME, slots=[slot], type=card_type,  # type: ignore[arg-type]
        question=question, why_we_ask=why, options=kind_options,
        custom_input=CustomInput(enabled=False), you_decide=YouDecide(enabled=not must_confirm),
        skip=SkipSpec(enabled=not must_confirm), progress=CardProgress(n=n, of_about=max(of_about, n)),
        language=language, must_confirm=must_confirm,
    )  # fmt: skip


def confirm_card(
    ref: RefInfo, *, catalog: Catalog, card_id: str, n: int, of_about: int, language: str
) -> Card:
    """One question: is the named business theirs, or an app they like? (never assumed)"""
    t = catalog.t
    opts = [
        CardOption(id=OWN_ID, label=t("flows.reference.opt_own", language), basis="suggestion", sets={SLOT: {"label": ref.label}}),
        CardOption(id=LIKE_ID, label=t("flows.reference.opt_like", language), basis="suggestion", sets={SLOT: {"label": ref.label}}),
    ]  # fmt: skip
    return _card(
        card_id, opts, t("flows.reference.confirm_question", language, name=ref.label),
        t("flows.reference.confirm_why", language), card_type="choose_one", n=n, of_about=of_about,
        language=language, must_confirm=True,
    )  # fmt: skip


def form_card(
    refs: Sequence[RefInfo], *, catalog: Catalog, card_id: str, n: int, of_about: int, language: str
) -> Card:
    """One form for all references: a borrow field and an avoid field for each (the user fills what they want)."""
    t = catalog.t
    opts: list[CardOption] = []
    for i, ref in enumerate(refs):
        sets: dict[str, JsonValue] = {SLOT: {"label": ref.label}}
        opts.append(CardOption(id=f"{BORROW_PREFIX}{i}", label=t("flows.reference.borrow_label", language, name=ref.label), reason=t("flows.reference.borrow_hint", language), basis="your_words", sets=sets))  # fmt: skip
        opts.append(CardOption(id=f"{AVOID_PREFIX}{i}", label=t("flows.reference.avoid_label", language, name=ref.label), reason=t("flows.reference.avoid_hint", language), basis="your_words", sets=sets))  # fmt: skip
    names = ", ".join(r.label for r in refs)
    return _card(
        card_id, opts, t("flows.reference.form_question", language, names=names),
        t("flows.reference.form_why", language), card_type="quick_form", n=n, of_about=of_about,
        language=language, must_confirm=False,
    )  # fmt: skip


def _items(text: JsonValue) -> list[str]:
    if isinstance(text, list):
        raw = [str(x) for x in text if isinstance(x, str | int)]
    elif isinstance(text, str):
        raw = _SPLIT_ITEMS.split(text)
    else:
        return []
    return [s.strip() for s in raw if s.strip()][:MAX_ITEMS]


def parse_form(card: Card, answer: JsonValue) -> dict[str, dict[str, list[str]]]:
    """``{label: {"borrow": [...], "avoid": [...]}}`` from a form answer keyed by the card's field ids."""
    out: dict[str, dict[str, list[str]]] = {}
    if not isinstance(answer, Mapping):
        return out
    for opt in card.options:
        kind = (
            "borrow"
            if opt.id.startswith(BORROW_PREFIX)
            else "avoid"
            if opt.id.startswith(AVOID_PREFIX)
            else ""
        )
        ref = opt.sets.get(SLOT)
        if not kind or not isinstance(ref, dict) or not isinstance(ref.get("label"), str):
            continue
        items = _items(answer.get(opt.id))
        slot = out.setdefault(str(ref["label"]), {"borrow": [], "avoid": []})
        slot[kind] = items
    return out


def apply_form(
    value: JsonValue, form: Mapping[str, Mapping[str, list[str]]], *, labels: set[str] | None = None
) -> list[JsonValue]:
    """The references list with borrow/avoid filled and the records of ``labels`` (all when None) confirmed:
    their own meta is dropped so the slot's state, confirmed by the user's answer, applies to them."""
    out: list[JsonValue] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        new: dict[str, Any] = dict(item)
        if labels is None or label.casefold() in labels:
            new.pop("_meta", None)
        if label in form:
            new["borrow"] = form[label]["borrow"] or list(new.get("borrow") or [])
            new["avoid"] = form[label]["avoid"] or list(new.get("avoid") or [])
        out.append(new)
    return out


def drop(value: JsonValue, label: str) -> list[JsonValue]:
    """The references list without ``label``."""
    return [i for i in (value if isinstance(value, list) else []) if not (isinstance(i, dict) and str(i.get("label", "")).casefold() == label.casefold())]  # fmt: skip


def option_label(option: CardOption) -> str | None:
    """The reference an option of a card built here is about."""
    ref = option.sets.get(SLOT)
    label = ref.get("label") if isinstance(ref, dict) else None
    return label if isinstance(label, str) and label else None


def confirm_label(card: Card, option_id: str) -> str | None:
    opt = next((o for o in card.options if o.id == option_id), None)
    return option_label(opt) if opt else None
