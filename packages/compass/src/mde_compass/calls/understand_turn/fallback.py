"""Deterministic understander: the mock handler and the fallback of ``cmp.understand_turn``.

Pure function ``fallback(wire_input) -> wire_output``. It copies only what the text states: every slot update
carries an exact quote from the user's message, nothing is invented, and it never asks anything. Intent phrase
tables are in ``lexicon.yaml`` (English, Hindi, Hinglish); category, roles and business models come from the
catalog (families, slot options), never from code. Every regex is linear: the text is clipped to
``MAX_SCAN_CHARS``, whitespace gaps are bounded, repeats are bounded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.schema import (
    PendingCardWire,
    SlotUpdateWire,
    UnderstandInput,
    UnderstandOutput,
)
from mde_compass.catalog.loader import Catalog, load_catalog
from mde_compass.engine.ports import Intent
from mde_core.redact import REDACTED, find_secrets

_WORD = r"[A-Z][\w'’-]{1,30}"
_PLACE = rf"({_WORD}(?:\s{{1,3}}{_WORD}){{0,2}})"
_GEO_BEFORE = re.compile(rf"(?<!\w)(?i:in|at|near|based in|from|around|across)\s{{1,4}}{_PLACE}")
_GEO_AFTER = re.compile(rf"(?<!\w){_PLACE}\s{{1,3}}(?i:mein|me|mai)(?!\w)")
_GEO_DEVA = re.compile(r"(?<![ऀ-ॿ])([ऀ-ॿ]{2,30})\s{1,3}(?:में|से)(?![ऀ-ॿ])")
_LIKE_CUES = tuple(
    re.compile(rf"(?<!\w)(?i:{cue})\s{{1,4}}({_WORD}(?:\s{{1,3}}{_WORD}){{0,2}})")
    for cue in ("like", "similar to", "inspired by", "clone of", "same as", "just like")
)
_JAISA = re.compile(
    rf"(?<!\w)({_WORD}(?:\s{{1,3}}{_WORD}){{0,1}})\s{{1,3}}(?i:jaisa|jaise|jaisi|ki tarah)(?!\w)"
)
_JAISA_DEVA = re.compile(
    r"(?<![ऀ-ॿ])([ऀ-ॿ]{2,30}(?: [ऀ-ॿ]{2,30})?)\s{1,3}(?:जैसा|जैसे|जैसी|की तरह)(?![ऀ-ॿ])"
)
_MAX_QUOTE = 200


@dataclass
class _Upd:
    slot_id: str
    value: Any
    quote: str
    confidence: float
    remove: bool = False


def _json_text(value_json: str) -> str | None:
    try:
        value = json.loads(value_json)
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def _catalog(catalog: Catalog | None) -> Catalog:
    return catalog or load_catalog()


def _role_words(catalog: Catalog) -> tuple[str, ...]:
    lex = lx.load_lexicon()
    roles = [str(r) for r in lex.raw["generic_roles"]]
    for fam in catalog.families.values():
        roles.extend(r.replace("_", " ") for r in fam.typical_roles)
    return tuple(dict.fromkeys(roles))


def _alt(words: tuple[str, ...] | list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


# ------------------------------------------------------------------------------------ extraction


def _category(
    text: str, catalog: Catalog, current: str | None = None
) -> tuple[list[_Upd], list[str]]:
    """Best family by distinct keyword hits; a negated hit clears; a tie is reported as ambiguous."""
    scored: list[tuple[int, str, tuple[int, int], bool]] = []
    for fam in catalog.families.values():
        spans = [(p, lx.find_first(text, [p])) for p in fam.keywords]
        hits = [(p, s) for p, s in spans if s is not None]
        if not hits:
            continue
        positive = [(p, s) for p, s in hits if not lx.negated_at(text, s)]
        negative = [(p, s) for p, s in hits if lx.negated_at(text, s)]
        if positive:
            scored.append((len(positive), fam.id, positive[0][1], False))
        elif negative:
            scored.append((1, fam.id, negative[0][1], True))
    if not scored:
        return [], []
    removals = [
        x for x in scored if x[3] and x[1] == current
    ]  # only a value we hold can be cleared
    positives = [x for x in scored if not x[3]]
    updates = [
        _Upd("idea.category", fid, text[s:e], 0.6, remove=True) for _, fid, (s, e), _ in removals
    ]
    ambiguous: list[str] = []
    if positives:
        top = max(n for n, *_ in positives)
        best = [x for x in positives if x[0] == top]
        n, fid, (s, e), _ = best[0]
        conf = min(0.8, 0.45 + 0.1 * n)
        if len(best) > 1:
            ambiguous.append("idea.category")
            conf = 0.4
        updates.append(_Upd("idea.category", fid, text[s:e], conf))
    return updates, ambiguous


def _locales(text: str) -> list[_Upd]:
    langs = lx.load_lexicon().raw["languages"]
    found: list[tuple[int, int, str]] = []
    for code, names in langs.items():
        for s, e in lx.find_spans(text, [str(n) for n in names]):
            found.append((s, e, code))
    if not found:
        return []
    found.sort()
    codes = list(dict.fromkeys(c for _, _, c in found))
    first, last = found[0], found[-1]
    quote = text[first[0] : last[1]]
    if len(quote) > _MAX_QUOTE or any(w in lx.normalize(quote) for w in ("nahi", "not ")):
        quote = text[first[0] : first[1]]
    return [_Upd("locales", codes, quote, 0.7)]


def _geo(text: str, catalog: Catalog) -> list[_Upd]:
    lex = lx.load_lexicon()
    stop = {lx.normalize(w) for w in lex.raw["geo_stop"]}
    countries = {lx.normalize(w) for w in lex.raw["countries"]}
    stop |= {lx.normalize(str(n)) for names in lex.raw["languages"].values() for n in names}
    places: list[tuple[str, str, float]] = []  # (place, quote, confidence)
    for rx, conf in ((_GEO_BEFORE, 0.6), (_GEO_AFTER, 0.55), (_GEO_DEVA, 0.45)):
        for m in rx.finditer(text):
            place = m.group(1).strip()
            if lx.normalize(place) in stop or lx.normalize(place.split()[0]) in stop:
                continue
            places.append((place, m.group(0).strip(), conf))
    if not places:
        return []
    value: dict[str, str] = {}
    quote, conf = places[0][1], places[0][2]
    for place, q, _ in places:
        key = "country" if lx.normalize(place) in countries else "city"
        if key not in value:
            value[key] = place
            if key == "city":
                quote = q
    if "city" not in value:
        quote = places[0][1]
    del catalog
    return [_Upd("context.geo", value, quote, conf)]


def _apps_roles(text: str, catalog: Catalog) -> list[_Upd]:
    lex = lx.load_lexicon()
    surfaces = lex.raw["surface_words"]
    surface_of = {lx.normalize(w): kind for kind, ws in surfaces.items() for w in ws}
    rx = re.compile(
        rf"(?<!\w)(?i:({_alt(_role_words(catalog))}))\s{{1,3}}(?i:({_alt(list(surface_of))}))(?!\w)"
    )
    apps: list[dict[str, str]] = []
    roles: list[dict[str, str]] = []
    first_quote = ""
    for m in rx.finditer(text):
        role, noun = m.group(1), m.group(2)
        surface = surface_of.get(lx.normalize(noun), "mobile")
        label = f"{role} {noun}".strip()
        if {"name": label} not in [{"name": a["name"]} for a in apps]:
            apps.append({"name": label, "surface": surface})
        if {"name": role} not in roles:
            roles.append({"name": role})
        first_quote = first_quote or m.group(0)
        if len(apps) >= 5:
            break
    if not apps:
        return []
    return [_Upd("apps.list", apps, first_quote, 0.6), _Upd("roles.list", roles, first_quote, 0.5)]


def _references(text: str) -> tuple[list[str], list[_Upd]]:
    lex = lx.load_lexicon()
    stop = {lx.normalize(w) for w in lex.raw["reference_stop"]}
    names: list[str] = []
    quotes: list[str] = []
    for rx in (*_LIKE_CUES, _JAISA, _JAISA_DEVA):
        for m in rx.finditer(text):
            name = m.group(1).strip()
            words = [w for w in name.split() if lx.normalize(w) not in stop]
            name = " ".join(words)
            if name and name.casefold() not in {n.casefold() for n in names}:
                names.append(name)
                quotes.append(m.group(0).strip())
    names, quotes = names[:4], quotes[:4]
    if not names:
        return [], []
    value = [{"label": n} for n in names]
    return names, [_Upd("scope.references", value, quotes[0], 0.7)]


def _enum_phrases(text: str, catalog: Catalog) -> list[_Upd]:
    lex = lx.load_lexicon()
    out: list[_Upd] = []
    for slot_id, table in lex.raw["enum_phrases"].items():
        slot = catalog.slots.get(slot_id)
        if slot is None:
            continue
        options = slot.value_schema.options
        pos: list[str] = []
        neg: list[str] = []
        pos_quote = neg_quote = ""
        for option, phrases in table.items():
            if options and option not in options:
                continue
            for phrase in phrases:
                span = lx.find_first(text, [str(phrase)])
                if span is None:
                    continue
                own_negation = lx.normalize(str(phrase)).split()[0] in lex.neg_before
                if lx.negated_at(text, span) and not own_negation:
                    neg.append(option)
                    neg_quote = neg_quote or text[span[0] : span[1]]
                else:
                    pos.append(option)
                    pos_quote = pos_quote or text[span[0] : span[1]]
                break
        kind = slot.value_schema.kind
        for values, quote, remove in ((pos, pos_quote, False), (neg, neg_quote, True)):
            if not values:
                continue
            if kind in ("enum", "text"):
                out.append(_Upd(slot_id, values[0], quote, 0.65, remove))
            else:
                out.append(_Upd(slot_id, list(dict.fromkeys(values)), quote, 0.65, remove))
    return out


def _pending_option(text: str, norm: str, pending: PendingCardWire | None) -> list[_Upd]:
    """A short reply that picks one option of the pending card ("b", "the second one", its label)."""
    if pending is None or not pending.options:
        return []
    toks = [t for t in norm.split() if t not in ("option", "number", "the", "one", "choice", "ka")]
    if not toks or len(norm.split()) > 8:
        return []
    ordinals = {
        "first": 0, "1": 0, "pehla": 0, "pehli": 0, "पहला": 0, "पहली": 0,
        "second": 1, "2": 1, "doosra": 1, "dusra": 1, "doosri": 1, "दूसरा": 1, "दूसरी": 1,
        "third": 2, "3": 2, "teesra": 2, "teesri": 2, "तीसरा": 2, "तीसरी": 2,
    }  # fmt: skip
    ids = {o.id.casefold(): i for i, o in enumerate(pending.options)}
    chosen: int | None = None
    if len(toks) == 1 and toks[0] in ids:
        chosen = ids[toks[0]]
    elif any(t in ordinals for t in toks) and len(toks) <= 3:
        idx = next(ordinals[t] for t in toks if t in ordinals)
        chosen = idx if idx < len(pending.options) else None
    else:
        hits = [
            i
            for i, o in enumerate(pending.options)
            if len(lx.normalize(o.label)) >= 4 and lx.normalize(o.label) in norm
        ]
        if len(hits) == 1:
            chosen = hits[0]
    if chosen is None:
        return []
    try:
        sets = json.loads(pending.options[chosen].sets_json)
    except ValueError:
        return []
    if not isinstance(sets, dict):
        return []
    quote = lx.squash_spaces(text).strip()[:_MAX_QUOTE]
    return [_Upd(str(sid), val, quote, 0.85) for sid, val in sets.items()]


def _summary(text: str, inp: UnderstandInput, is_question: bool) -> list[_Upd]:
    """The user's own words become the idea line when the idea is still empty."""
    current = {s.slot_id for s in inp.current_slots if s.value_json not in ("null", "", "[]")}
    pending_slots = inp.pending_card.slots if inp.pending_card else []
    if "idea.summary" in current or is_question:
        return []
    verbs = lx.has_phrase(text, tuple(lx.load_lexicon().intents["idea_verbs"]))
    words = len(text.split())
    if not (verbs or "idea.summary" in pending_slots) or words < 3:
        return []
    quote = lx.squash_spaces(text).strip()[:_MAX_QUOTE]
    return [_Upd("idea.summary", quote, quote, 0.7 if verbs else 0.5)]


def extract_updates(
    text: str, inp: UnderstandInput, catalog: Catalog, *, is_question: bool = False
) -> tuple[list[_Upd], list[str], list[str]]:
    """(updates, reference names, ambiguous slot ids) the text states."""
    text = lx.clip(text)
    norm = lx.normalize(text)
    updates: list[_Upd] = []
    updates += _pending_option(text, norm, inp.pending_card)
    held = next(
        (_json_text(s.value_json) for s in inp.current_slots if s.slot_id == "idea.category"), None
    )
    cat, ambiguous = _category(text, catalog, held)
    updates += cat
    updates += _locales(text)
    updates += _geo(text, catalog)
    updates += _apps_roles(text, catalog)
    refs, ref_updates = _references(text)
    updates += ref_updates
    updates += _enum_phrases(text, catalog)
    updates += _summary(text, inp, is_question)
    seen: set[tuple[str, bool]] = set()
    unique: list[_Upd] = []
    for u in updates:
        if (u.slot_id, u.remove) not in seen:
            seen.add((u.slot_id, u.remove))
            unique.append(u)
    return unique, refs, ambiguous


# ------------------------------------------------------------------------------------ intent


def _flags(text: str, catalog: Catalog) -> list[str]:
    lex = lx.load_lexicon()
    flags: list[str] = []
    if find_secrets(text) or REDACTED in text:
        flags.append("secret")
    if any(rx.search(lx.squash_spaces(text)) for rx in lex.injection):
        flags.append("injection")
    if lx.has_phrase(text, tuple(str(p) for p in lex.raw["safety"]["unsafe"])):
        flags.append("unsafe")
    for cap in catalog.declined():
        if lx.find_first(text, cap.keywords) is not None:
            flags.append("declined_category")
            break
    return flags


def _intent(
    text: str, norm: str, inp: UnderstandInput, updates: list[_Upd], flags: list[str]
) -> tuple[Intent, float]:
    lex = lx.load_lexicon()
    intents = lex.intents
    words = len(norm.split())
    if "unsafe" in flags:
        return "unsafe", 0.9
    if not norm:
        return "other", 0.2
    if lx.is_exact(norm, "greeting"):
        return "greeting", 0.95
    if lx.has_phrase(text, intents["finish_with_defaults"]):
        return "finish_with_defaults", 0.9
    if lx.is_exact(norm, "stop") or (words <= 4 and lx.has_phrase(text, intents["stop"])):
        return "stop", 0.9
    if lx.is_exact(norm, "you_decide") or (
        words <= 8 and lx.has_phrase(text, intents["you_decide"])
    ):
        return "you_decide", 0.85
    if lx.has_phrase(text, intents["price_or_time"]):
        return "price_or_time", 0.8
    if lx.has_phrase(text, intents["tech_choice"]) and not updates:
        return "tech_choice", 0.75
    has_state = any(s.value_json not in ("null", "", "[]") for s in inp.current_slots)
    if lx.has_phrase(text, intents["change_request"]) and (has_state or updates):
        return "change_request", 0.7
    if lx.is_exact(norm, "confirm"):
        return "confirm", 0.9
    if lx.is_exact(norm, "negate"):
        return "negate", 0.9
    if not updates and lx.has_phrase(text, intents["off_topic"]):
        return "off_topic", 0.75
    first = norm.split()[0] if norm else ""
    asks = "?" in text or first in lx.load_lexicon().exact["question_words"]
    if asks and not updates and not lx.has_phrase(text, intents["idea_verbs"]):
        return "question", 0.7
    if updates or lx.has_phrase(text, intents["idea_verbs"]) or inp.pending_card is not None:
        return "answer", 0.75 if updates else 0.5
    return "other", 0.4


def fallback(inp: UnderstandInput, catalog: Catalog | None = None) -> UnderstandOutput:
    """The rule-based understanding of one message (see the module docstring)."""
    cat = _catalog(catalog)
    text = lx.clip(inp.text)
    norm = lx.normalize(text)
    flags = _flags(text, cat)
    lex = lx.load_lexicon()
    first = norm.split()[0] if norm else ""
    is_question = "?" in text or first in lex.exact["question_words"]
    updates, refs, ambiguous = extract_updates(text, inp, cat, is_question=is_question)
    intent, conf = _intent(text, norm, inp, updates, flags)
    if intent in ("unsafe", "greeting", "stop", "you_decide", "finish_with_defaults", "other"):
        updates, refs, ambiguous = [], [], []
    basis = "voice" if inp.input_mode == "voice" else "user_text"
    return UnderstandOutput(
        intent=intent,
        slot_updates=[
            SlotUpdateWire(
                slot_id=u.slot_id,
                value_json=json.dumps(u.value, ensure_ascii=False),
                quote=u.quote.split("?")[0].strip()[:_MAX_QUOTE],
                confidence=round(u.confidence, 2),
                basis=basis,
                remove=u.remove,
            )
            for u in updates
            if u.quote.split("?")[0].strip()
        ],
        references=refs,
        language=lx.detect_language(text, inp.language),
        safety_flags=flags,
        ambiguous=ambiguous,
        confidence=round(conf, 2),
    )


def extract_references(text: str) -> list[str]:
    """App names the text says it wants to be "like" (public helper; linear)."""
    return _references(lx.clip(text))[0]
