"""Keyword understander: the offline brain of C1.

One pure function, ``understand(C1Input) -> C1Output``, driven by the lexicon and the content
catalogs. It is the C1 *mock handler* (so scripted conversations run with no key) and also the
engine's deterministic fallback when the real call fails. It never infers beyond what the text says.
"""

from __future__ import annotations

import json
import re

from mde_content.catalogs import load_catalogs
from mde_content.themes import load_themes
from mde_contracts.calls import (
    C1Input,
    C1Output,
    Intent,
    PendingCardLite,
    Reference,
    SafetyFlags,
    SlotUpdate,
)
from mde_core.redact import REDACTED, find_secrets
from mde_prompts.calls.c1_understand import lexicon as lx

ORDINALS: dict[str, int] = {
    "first": 0, "1": 0, "one": 0, "pehla": 0, "pehli": 0, "पहला": 0, "पहली": 0,
    "second": 1, "2": 1, "two": 1, "doosra": 1, "dusra": 1, "doosri": 1, "दूसरा": 1, "दूसरी": 1,
    "third": 2, "3": 2, "three": 2, "teesra": 2, "teesri": 2, "तीसरा": 2, "तीसरी": 2,
    "last": -1, "aakhri": -1, "आखिरी": -1,
}  # fmt: skip
NEGATION_BEFORE = frozenset(
    {"no", "without", "remove", "skip", "dont", "not", "bina", "hata", "hatao", "बिना", "नहीं"}
)
NEGATION_AFTER = frozenset({"nahi", "nahin", "नहीं", "hata", "हटा", "mat"})
LOOK_CUES = (
    "look", "looks", "style", "theme", "design", "feel", "vibe", "color", "colour", "colors",
    "colours", "dark mode", "light mode", "rang", "लुक", "रंग", "डिज़ाइन",
)  # fmt: skip
NAME_PATTERN = re.compile(
    r"(?:\bcalled\b|\bnamed\b|\bname is\b|\bname it\b|\bcall it\b|\bcall the app\b|\bapp name is\b"
    r"|\bnaam\b|\bनाम\b)\s{0,8}[:\-]?\s{0,8}[\"“']?([^\"”',.;!?\n]{2,38})",
    re.IGNORECASE,
)  # whitespace gaps are bounded: ``\s*[:\-]?\s*`` was quadratic on "called" + a long run
URL_PATTERN = re.compile(r"https?://[^\s]+|www\.[^\s]+", re.IGNORECASE)
_WORD_RUN = r"[A-Z][\w&'-]{0,39}+"  # one capitalised name word (bounded: a start costs O(40))
_LIKE_CUE_PATTERNS = tuple(
    re.compile(rf"\b{cue}\s{{1,8}}({_WORD_RUN}(?:\s{{1,8}}{_WORD_RUN}){{0,2}})")
    for cue in ("like", "similar to", "inspired by", "clone of", "same as")
)
_JAISA_PATTERN = re.compile(
    rf"\b({_WORD_RUN}(?:\s{{1,8}}{_WORD_RUN})?)\s{{1,8}}(?:jaisa|jaise|ki tarah)\b"
)  # "Swiggy jaisa": bounded words and gaps, so linear (the old ``[\w&'-]*`` was quadratic)
NAME_STOP = frozenset(
    {"and", "where", "for", "that", "which", "with", "so", "but", "aur", "jo", "ke"}
)


# --------------------------------------------------------------------------- small helpers


def _current(inp: C1Input, slot_id: str) -> object:
    for s in inp.slots:
        if s.slot_id == slot_id and s.value != "":
            try:
                return json.loads(s.value)
            except ValueError:
                return s.value
    return None


def _archetype_keywords() -> dict[str, tuple[str, ...]]:
    return {a.id: tuple(a.keywords) for a in load_catalogs().archetypes}


def detect_archetype(text: str, allowed: list[str] | None = None) -> tuple[str | None, int]:
    """(archetype id, number of keyword hits) for the best match, or (None, 0)."""
    best: tuple[int, str] | None = None
    for aid, kws in _archetype_keywords().items():
        if allowed is not None and aid not in allowed:
            continue
        if lx.keyword_hits(text, kws) and _negated(text, kws):
            continue  # "it is not a shop": the user ruled this type out
        n = len(lx.keyword_hits(text, kws))
        if n and (best is None or n > best[0]):
            best = (n, aid)
    return (best[1], best[0]) if best else (None, 0)


def _theme_labels() -> dict[str, str]:
    return {t.id: t.label for t in load_themes().themes}


def _negated(text: str, keywords: tuple[str, ...]) -> bool:
    """A keyword occurs next to a negation word ("no payments", "reviews nahi").

    Occurrences are found by substring search in the normalised text and mapped to token
    positions (cost: the occurrences, not keywords x tokens: a long upload stays cheap).
    """
    toks = lx.words(text)
    hay = " ".join(toks)
    token_at: dict[int, int] | None = None  # char offset of a token -> its index (built on demand)
    for kw in keywords:
        kt = lx.normalize(kw).split()
        phrase = " ".join(kt)
        if not kt or phrase not in hay:  # absent keywords cost one substring test
            continue
        if token_at is None:
            token_at, offset = {}, 0
            for index, tok in enumerate(toks):
                token_at[offset] = index
                offset += len(tok) + 1
        at = hay.find(phrase)
        while at != -1:
            i = token_at.get(at)
            if i is not None and toks[i : i + len(kt)] == kt:
                before = toks[max(0, i - 3) : i]
                after = toks[i + len(kt) : i + len(kt) + 4]
                if NEGATION_BEFORE & set(before) or NEGATION_AFTER & set(after):
                    return True
            at = hay.find(phrase, at + 1)
    return False


def match_option(norm: str, card: PendingCardLite, approve: bool = False) -> str | None:
    """The option a short reply selects: by id ("b"), ordinal ("the second one"), or label."""
    opts = card.options
    if not opts:
        return None
    ids = {o.id.casefold(): o.id for o in opts}
    toks = norm.split()
    stripped = [t for t in toks if t not in ("option", "number", "no", "the", "one", "choice")]
    if len(stripped) == 1 and stripped[0] in ids:
        return ids[stripped[0]]
    if card.type == "confirm" and len(opts) >= 2:
        if approve or lx.is_phrase(norm, "approve"):
            return opts[0].id
        if lx.is_phrase(norm, "negative"):
            return opts[1].id
    for t in toks:
        if t in ORDINALS and card.type != "multi" and len(toks) <= 4:
            idx = ORDINALS[t]
            if -len(opts) <= idx < len(opts):
                return opts[idx].id
    exact = [o.id for o in opts if lx.normalize(o.label) == norm]
    if len(exact) == 1:
        return exact[0]
    inside = [
        o.id for o in opts if len(lx.normalize(o.label)) >= 4 and lx.normalize(o.label) in norm
    ]
    if len(inside) == 1:
        return inside[0]
    if len(norm) >= 4:
        sub = [o.id for o in opts if norm in lx.normalize(o.label)]
        if len(sub) == 1:
            return sub[0]
    return None


def extract_name(text: str) -> str | None:
    m = NAME_PATTERN.search(text)
    if not m:
        return None
    parts = m.group(1).strip().split()
    kept: list[str] = []
    for p in parts:
        if p.casefold() in NAME_STOP:
            break
        kept.append(p)
    name = " ".join(kept).strip(" -:")
    return name or None


# --------------------------------------------------------------------------- slot extraction


def extract_updates(
    inp: C1Input,
    text: str,
    archetype: str | None,
    pending: PendingCardLite | None,
    *,
    look_hint: bool = False,
) -> list[SlotUpdate]:
    """Slot values the text states (never inferred). ``archetype`` scopes the feature search."""
    out: list[SlotUpdate] = []
    text = lx.clip(text)
    norm = lx.normalize(text)
    name = extract_name(text)
    scan = text.replace(name, " ") if name else text  # a brand name must not match features

    def put(slot: str, evidence: str, conf: float = 0.8, **value: object) -> None:
        if not any(u.slot_id == slot and u.negate == bool(value.get("negate")) for u in out):
            out.append(
                SlotUpdate.model_validate(
                    {"slot_id": slot, "evidence": evidence[:80], "confidence": conf, **value}
                )
            )

    # archetype (with explicit negation)
    for aid, kws in _archetype_keywords().items():
        if lx.keyword_hits(scan, kws):
            if _negated(scan, kws):
                put("idea.archetype", text, 0.7, value_text=aid, negate=True)
            elif archetype == aid and not any(u.slot_id == "idea.archetype" for u in out):
                put("idea.archetype", text, 0.85, value_text=aid)
    # sign-in and payments
    login = [
        ("login_none", "none"), ("login_social", "social"), ("login_password", "password"),
        ("login_sso", "sso"), ("login_guest", "guest_first"), ("login_code", "email_phone_code"),
    ]  # fmt: skip
    for intent, value in login:
        if lx.contains_phrase(text, intent):
            put("auth.method", text, 0.9, value_text=value)
            break
    pay = [
        ("pay_both", "both"), ("pay_delivery", "on_delivery"), ("pay_none", "none"),
        ("pay_online", "online"),
    ]  # fmt: skip
    for intent, value in pay:
        if lx.contains_phrase(text, intent):
            put("pay.model", text, 0.9, value_text=value)
            break
    # features (only when the type is known, so the ids are meaningful)
    if archetype:
        options = lx.feature_options(archetype)
        pos: list[str] = []
        neg: list[str] = []
        for fid, label in options:
            kws = lx.feature_keywords(archetype, fid, label)
            if lx.keyword_hits(scan, kws):
                (neg if _negated(scan, kws) else pos).append(fid)
        if lx.contains_phrase(text, "pay_none") and "payments" in [f for f, _ in options]:
            if "payments" not in neg:
                neg.append("payments")
            if "payments" in pos:
                pos.remove("payments")
        if pos:
            current = _current(inp, "scope.features")
            cur = [str(x) for x in current] if isinstance(current, list) else []
            exclusive = lx.contains_phrase(text, "exclusive")
            merged = pos if (exclusive or not cur) else [*cur, *[p for p in pos if p not in cur]]
            put("scope.features", text, 0.8, value_list=merged)
        if neg:
            put("scope.features", text, 0.8, value_list=neg, negate=True)
    # look: only with a look cue or when the pending card is the look card
    asks_look = pending is not None and pending.slot == "design.direction"
    if asks_look or look_hint or any(c in norm for c in LOOK_CUES):
        theme = lx.nearest_theme(text, _theme_labels())
        if theme:
            put("design.direction", text, 0.85, value_text=theme)
        if "dark mode" in norm or "dark theme" in norm:
            put("design.mode", text, 0.8, value_text="dark")
        elif "light mode" in norm:
            put("design.mode", text, 0.8, value_text="light")
        color = lx.color_word(text)
        if color and any(c in norm for c in ("color", "colour", "accent", "rang", "रंग")):
            put("design.accent", text, 0.75, value_text=color)
    # brand name
    if name:
        put("brand.name", text, 0.8, value_text=name)
    urls = URL_PATTERN.findall(text)
    if urls and pending is not None and pending.slot == "brand.name":
        put("brand.assets", urls[0], 0.8, value_text=urls[0])
    return out


def extract_references(text: str) -> list[Reference]:
    """Named apps ("like Swiggy"), links."""
    text = lx.clip(text)
    lex = lx.load_lexicon()
    refs: list[Reference] = []
    seen: set[str] = set()
    known = [str(a) for a in lex.raw["known_apps"]]
    cues = tuple(lx.normalize(c) for c in lex.raw["reference_cues"])
    norm = lx.normalize(text)
    for app in known:
        named = lx.keyword_hits(text, [app]) and any(c in norm for c in cues)
        if named and app.casefold() not in seen:
            seen.add(app.casefold())
            refs.append(Reference(kind="like_app", text=app))
    for pattern in _LIKE_CUE_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            if name.casefold() not in seen and name.casefold() not in NAME_STOP:
                seen.add(name.casefold())
                refs.append(Reference(kind="like_app", text=name))
    for m in _JAISA_PATTERN.finditer(text):
        name = m.group(1).strip()
        if name.casefold() not in seen:
            seen.add(name.casefold())
            refs.append(Reference(kind="like_app", text=name))
    for url in URL_PATTERN.findall(text)[:2]:
        refs.append(Reference(kind="url", text=url))
    return refs[:4]


# --------------------------------------------------------------------------- the understander


def understand(inp: C1Input) -> C1Output:
    """Deterministic C1: intent, slot updates with evidence, references, language, safety flags."""
    msg = lx.clip(inp.message).strip()  # the rules read at most MAX_SCAN_CHARS of any text
    norm = lx.normalize(msg)
    att = lx.clip(inp.attachment_text or "")
    lang = lx.detect_language(msg if msg else att, inp.language_hint)
    category = lx.unsafe_category(msg)
    flags = SafetyFlags(
        unsafe=category is not None,
        category=category,
        secret_detected=bool(find_secrets(msg)) or REDACTED in msg,
        injection_suspected=lx.injection_suspected(msg)
        or (bool(att) and lx.injection_suspected(att)),
    )
    review = inp.gate_open is not None or inp.state != "DISCOVERING"
    pending = inp.pending_card

    def out(
        intent: Intent,
        conf: float,
        updates: list[SlotUpdate] | None = None,
        *,
        refs: list[Reference] | None = None,
        answers: bool = False,
        option: str | None = None,
        summary: str | None = None,
    ) -> C1Output:
        return C1Output(
            intent=intent,
            confidence=round(conf, 2),
            language=lang,
            slot_updates=updates or [],
            references=refs or [],
            answers_pending_card=answers,
            selected_option_id=option,
            safety=flags,
            summary_update=summary,
        )

    if category:
        return out("unsafe", 0.9)
    if flags.injection_suspected and not att:
        return out("other", 0.3)
    short = len(norm.split()) <= 4
    if lx.is_phrase(msg, "start_over") or (short and lx.phrase_ends_message(msg, "start_over")):
        return out("start_over", 0.9)
    if lx.is_phrase(msg, "stop") or (short and lx.phrase_ends_message(msg, "stop")):
        return out("stop", 0.9)
    if lx.is_phrase(msg, "pause"):
        return out("pause", 0.9)
    if lx.is_phrase(msg, "greeting"):
        return out("greeting", 0.95)
    if att:
        text = f"{msg} {att}"
        arch, _ = detect_archetype(text)
        ups = extract_updates(inp, text, arch or _known_archetype(inp), pending)
        if arch and not any(u.slot_id == "idea.archetype" for u in ups):
            ups.insert(
                0, SlotUpdate(slot_id="idea.archetype", value_text=arch, evidence="document")
            )
        return out("upload", 0.9, ups, refs=extract_references(att), summary="Shared a document")

    exact_approve = lx.is_phrase(msg, "approve")
    if pending is not None:
        option = match_option(norm, pending, approve=exact_approve)
        if option is not None:
            return out("answer", 0.9, answers=True, option=option)
    if exact_approve:
        return out("approve", 0.92)
    if lx.is_phrase(msg, "you_decide"):
        return out("answer", 0.85)

    known = _known_archetype(inp)
    detected, _ = detect_archetype(msg)
    arch = detected or known
    updates = extract_updates(inp, msg, arch, pending)
    refs = extract_references(msg)
    has_archetype_update = any(u.slot_id == "idea.archetype" and not u.negate for u in updates)
    if detected and not has_archetype_update and known != detected:
        updates.insert(
            0,
            SlotUpdate(
                slot_id="idea.archetype", value_text=detected, evidence=msg[:80], confidence=0.85
            ),
        )
    updated_existing = any(
        _current(inp, u.slot_id) not in (None, "", []) and not u.negate for u in updates
    )
    change_cue = lx.contains_phrase(msg, "change_cue")
    change_verb = lx.contains_phrase(msg, "change_verbs")

    if lx.phrase_ends_message(msg, "approve") and not updates:
        hedged = lx.contains_phrase(msg, "hedge")
        return out("approve", 0.4 if hedged else 0.8)
    if lx.phrase_ends_message(msg, "approve") and updates:
        return out("change" if review else "volunteered", 0.6, updates, refs=refs)
    if lx.contains_phrase(msg, "confused") and not updates:
        return out("confused", 0.85)
    if review and (change_cue or updates):
        return out("change", 0.8, updates, refs=refs)
    if (change_cue and updated_existing) or (change_verb and updates and not detected):
        return out("change", 0.8, updates, refs=refs)
    screen_note = lx.contains_phrase(msg, "screen_words") and (
        change_cue or lx.contains_phrase(msg, "idea_verbs")
    )
    if screen_note and known is not None:
        return out("change", 0.7, [], refs=refs)
    first = norm.split()[0] if norm else ""
    asking = msg.rstrip().endswith("?") or first in lex_question_words()
    if asking:
        if pending is not None and any(u.slot_id == pending.slot for u in updates):
            return out("answer", 0.8, updates, refs=refs, answers=True)
        return out("question", 0.8, updates, refs=refs)

    if pending is not None:
        # a typed answer to the card: slot updates for its slot, or free text for text cards
        mine = [u for u in updates if u.slot_id == pending.slot]
        if mine:
            return out("answer", 0.85, updates, refs=refs, answers=True)
        if pending.type in ("text", "upload") and norm and not lx.contains_phrase(msg, "chitchat"):
            text_update = _text_answer(pending, msg, updates)
            if pending.slot == "idea.summary":
                return out(
                    "idea", 0.8, [*updates, text_update], refs=refs, answers=True, summary=msg[:100]
                )
            return out("answer", 0.8, [*updates, text_update], refs=refs, answers=True)
    if lx.contains_phrase(msg, "chitchat") and not updates and not detected:
        return out("chitchat", 0.8)
    if detected or (updates and not inp.slots):
        verbs = lx.phrase_ends_message(msg, "idea_verbs") or lx.contains_phrase(msg, "idea_verbs")
        conf = 0.85 if (verbs or len(norm.split()) >= 4) else 0.7
        has_idea = bool(_current(inp, "idea.summary"))
        intent: Intent = "volunteered" if has_idea else "idea"
        return out(intent, conf, updates, refs=refs, summary=msg[:100])
    if updates:
        return out("volunteered", 0.75, updates, refs=refs)
    if lx.contains_phrase(msg, "vague"):
        return out("idea", 0.3, refs=refs)
    verbs = lx.contains_phrase(msg, "idea_verbs")
    if verbs or len(norm.split()) >= 6:
        conf = 0.55 if len(norm.split()) >= 6 else 0.3
        return out("idea", conf, refs=refs, summary=msg[:100])
    return out("other", 0.3)


def lex_question_words() -> frozenset[str]:
    return frozenset(lx.load_lexicon().intents["question_words"])


def _known_archetype(inp: C1Input) -> str | None:
    cur = _current(inp, "idea.archetype")
    return cur if isinstance(cur, str) else None


def _text_answer(pending: PendingCardLite, msg: str, updates: list[SlotUpdate]) -> SlotUpdate:
    for u in updates:
        if u.slot_id == pending.slot:
            return u
    value = msg.strip()
    if pending.slot == "brand.name":
        named = extract_name(msg)
        value = named or value
    return SlotUpdate(
        slot_id=pending.slot, value_text=value[:200], evidence=msg[:80], confidence=0.8
    )
