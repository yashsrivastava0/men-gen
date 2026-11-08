"""Question cards: requests, validation, assembly, the deterministic fallback, and answers to slot writes.

The model writes wording and options (``CardDraft``); code decides everything else (prompt 6.3). Validation
(``validate_card``) returns a list of error strings, empty meaning OK; two failures in a row mean the caller
uses ``fallback_card`` (a plain card from the catalog, English and Hindi, marked ``fallback=True``).

Heuristics, stated honestly:

* brand names: a capitalised word in the middle of a text (not the first word) that is not in the user's own
  words, the request evidence or hints, or the small allowlist in ``card_rules.yaml``. Devanagari has no case,
  so Hindi brand names are not caught here (the declined and brand guard on the model side covers them);
* "typical" basis: the label must contain, or be contained in, a hint from ``families.yaml`` (or share at least
  half of its significant words with one);
* "one idea per card": at most one question mark; multi-slot frames are one idea by catalog design;
* a pick_many option outside the hints must be flagged custom (an id starting ``custom`` or a ``sets`` value
  carrying ``"custom": true``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, FrameDef, SlotDef
from mde_compass.config import QuestionPolicy
from mde_compass.domain.cards import (
    Card,
    CardOption,
    CardProgress,
    CustomInput,
    SkipSpec,
    YouDecide,
)
from mde_compass.engine.language import devanagari_share, normalize
from mde_compass.engine.ports import CardDraft, CardRequest
from mde_compass.safety.screen import declined_category

if TYPE_CHECKING:
    from mde_compass.engine.voi import QueueItem

RULES_PATH = Path(__file__).resolve().parent / "card_rules.yaml"
FAMILY_GROUNDED_SLOTS: frozenset[str] = frozenset(
    {"scope.features", "roles.list", "integrations.wanted"}
)
CHECKLIST_MIN = 2
CHECKLIST_MAX = 14
RANK_MIN = 2
MAX_ACRONYM_LETTERS = 4
HI_QUESTION_SHARE = 0.3
HI_TEXT_SHARE = 0.4
EN_MAX_DEVANAGARI = 0.1
_WORD_SPLIT = re.compile(r"[^\W_]+", re.UNICODE)
_LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_CAP_WORD = re.compile(r"(?<![\w])[A-Z][A-Za-z0-9\-]{1,30}(?![\w])")
_STOP = frozenset(
    {"a", "an", "the", "of", "for", "to", "and", "or", "in", "on", "with", "your", "my"}
)
Provenance = Literal["user_tap", "user_text", "assumed"]


class CardRefused(ValueError):
    """``you_decide`` or skip was asked on a card that must be confirmed."""


@dataclass(frozen=True)
class CardWrite:
    """One slot write produced by an answer (the flow applies it through the slot store)."""

    slot_id: str
    value: JsonValue
    provenance_type: Provenance = "user_tap"
    custom_text: str | None = None
    needs_confirmation: bool = False


@dataclass(frozen=True)
class Rules:
    leading: tuple[str, ...]
    statistics: tuple[re.Pattern[str], ...]
    allowlist: frozenset[str]
    fallback_reason: dict[str, str]
    hi_extra_question: int
    hi_extra_option: int


@lru_cache(maxsize=1)
def load_rules() -> Rules:
    raw = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    leading = tuple(
        dict.fromkeys(normalize(p) for lang in raw["leading_words"].values() for p in lang)
    )
    return Rules(
        leading=leading,
        statistics=tuple(re.compile(p, re.IGNORECASE) for p in raw["statistic_patterns"]),
        allowlist=frozenset(str(w).casefold() for w in raw["brand_allowlist"]),
        fallback_reason={str(k): str(v) for k, v in raw["fallback_reason"].items()},
        hi_extra_question=int(raw["hindi_extra_words"]["question"]),
        hi_extra_option=int(raw["hindi_extra_words"]["option"]),
    )  # fmt: skip


# --- request -------------------------------------------------------------------------------------------


def request_for(
    item: QueueItem,
    catalog: Catalog,
    *,
    ctx_language: str,
    hints: Sequence[str] = (),
    evidence: Sequence[str] = (),
    avoid_labels: Sequence[str] = (),
) -> CardRequest:
    """The writer's brief for one queue item: card type and option source come from the frame."""
    del ctx_language  # the writer receives the language through TurnContext
    frame = catalog.frames[item.frame_id]
    return CardRequest(
        frame_id=frame.id,
        slots=list(item.slots),
        card_type=frame.card_type,
        option_source=frame.option_source,
        hints=list(hints),
        evidence=list(evidence),
        must_confirm=item.must_confirm,
        avoid_labels=list(avoid_labels),
    )


def family_hints(
    catalog: Catalog, slot_ids: Sequence[str], family_ids: Sequence[str], *, language: str = "en"
) -> list[str]:
    """Typical labels from ``families.yaml`` for the slots of a card (the writer's grounding and the basis
    check): features for ``scope.features``, roles, integrations, business models. Empty if no family given."""
    out: list[str] = []
    for fid in family_ids:
        family = catalog.families.get(fid)
        if family is None:
            continue
        for slot_id in slot_ids:
            if slot_id == "scope.features":
                out.extend(f.name.get(language) for f in family.typical_features)
            elif slot_id == "roles.list":
                out.extend(family.typical_roles)
            elif slot_id == "integrations.wanted":
                out.extend(family.typical_integrations)
            elif slot_id == "idea.business_model":
                out.extend(family.typical_business_models)
    return list(dict.fromkeys(out))


# --- validation ----------------------------------------------------------------------------------------


def _words(text: str) -> int:
    return len(text.split())


def _sig(text: str) -> set[str]:
    return {w for w in _WORD_SPLIT.findall(normalize(text)) if w not in _STOP and len(w) > 1}


def _matches_hint(label: str, hints: Sequence[str]) -> bool:
    ln = normalize(label)
    ls = _sig(label)
    for hint in hints:
        hn = normalize(hint)
        if not hn or not ln:
            continue
        if ln in hn or hn in ln:
            return True
        hs = _sig(hint)
        if ls and hs and len(ls & hs) / min(len(ls), len(hs)) >= 0.5:
            return True
    return False


def _value_ok(slot: SlotDef, value: JsonValue) -> bool:
    kind = slot.value_schema.kind
    allowed = slot.value_schema.options
    if value is None or value == "" or value == [] or value == {}:
        return False
    if kind == "enum":
        return isinstance(value, str) and (not allowed or value in allowed)
    if kind == "enum_list":
        items = value if isinstance(value, list) else [value]
        return all(isinstance(v, str) and (not allowed or v in allowed) for v in items)
    if kind == "text":
        return isinstance(value, str) and bool(value.strip())
    if kind == "text_list":
        items = value if isinstance(value, list) else [value]
        return all(isinstance(v, str) and v.strip() for v in items)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "locales":
        if isinstance(value, dict):
            return True
        items = value if isinstance(value, list) else [value]
        return all(
            isinstance(v, str) and (v in allowed or v == "other" or _LOCALE.match(v)) for v in items
        )
    if kind in ("object", "object_list", "geo"):
        return isinstance(value, dict | list | str)
    return False


def _is_custom(option: CardOption) -> bool:
    if option.id.casefold().startswith("custom"):
        return True
    for value in option.sets.values():
        items = value if isinstance(value, list) else [value]
        if any(isinstance(v, dict) and v.get("custom") is True for v in items):
            return True
    return False


def _texts(draft: CardDraft) -> list[str]:
    out = [draft.question, draft.why_we_ask]
    for option in draft.options:
        out.extend([option.label, option.reason])
    return [t for t in out if t]


def _option_values(option: CardOption) -> list[str]:
    out: list[str] = []
    for value in option.sets.values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(str(v) for v in item.values() if isinstance(v, str))
    return out


def _check_options(
    language: str,
    draft: CardDraft,
    request: CardRequest,
    catalog: Catalog,
    frame: FrameDef | None,
    cfg: QuestionPolicy,
) -> list[str]:
    errors: list[str] = []
    ctype = request.card_type
    options = draft.options
    max_opt = frame.max_option_words if frame else cfg.max_option_words
    if language == "hi":
        max_opt += load_rules().hi_extra_option
    labels = [normalize(o.label) for o in options]
    ids = [o.id for o in options]
    if any(not o.id.strip() for o in options) or len(set(ids)) != len(ids):
        errors.append("options.ids: ids must be non-empty and distinct")
    if len(set(labels)) != len(labels) or any(not lab for lab in labels):
        errors.append("options.distinct: labels must be non-empty and distinct")
    for option in options:
        if _words(option.label) > max_opt:
            errors.append(f"options.long: {option.id} has more than {max_opt} words")
    if ctype in ("choose_one", "look_signals"):
        want = frame.option_count if frame and frame.option_count else cfg.option_count
        if len(options) != want:
            errors.append(f"options.count: expected exactly {want}, got {len(options)}")
        recommended = [o for o in options if o.recommended]
        if ctype == "choose_one" and len(recommended) != 1:
            errors.append(f"recommended.count: expected exactly 1, got {len(recommended)}")
    elif ctype == "pick_many":
        if not CHECKLIST_MIN <= len(options) <= CHECKLIST_MAX:
            errors.append(
                f"options.count: checklist needs {CHECKLIST_MIN}..{CHECKLIST_MAX}, got {len(options)}"
            )
    elif ctype == "priority":
        if not RANK_MIN <= len(options) <= CHECKLIST_MAX:
            errors.append(
                f"options.count: ranking needs {RANK_MIN}..{CHECKLIST_MAX}, got {len(options)}"
            )
    elif ctype == "confirm" and not options:
        errors.append("options.count: a confirm card needs at least one option")
    for option in options:
        if option.recommended and not option.reason.strip():
            errors.append(f"recommended.reason: {option.id} is recommended without a reason")
        if option.ticked and option.basis == "suggestion":
            errors.append(
                f"ticked.basis: {option.id} is pre-ticked on a suggestion, not on evidence"
            )
        errors.extend(
            _check_sets(
                option, request, catalog, needs_sets=ctype in ("choose_one", "look_signals")
            )
        )
    return errors


def _check_sets(
    option: CardOption, request: CardRequest, catalog: Catalog, *, needs_sets: bool
) -> list[str]:
    errors: list[str] = []
    if needs_sets and not option.sets:
        errors.append(f"sets.missing: {option.id} sets no slot")
    for slot_id, value in option.sets.items():
        slot = catalog.slots.get(slot_id)
        if slot is None or slot_id not in request.slots:
            errors.append(f"sets.slot: {option.id} sets {slot_id}, not a slot of this card")
        elif not _value_ok(slot, value):
            errors.append(
                f"sets.value: {option.id} value for {slot_id} is not valid for {slot.value_schema.kind}"
            )
    return errors


def _check_wording(
    draft: CardDraft, user_text: str, request: CardRequest, language: str
) -> list[str]:
    rules = load_rules()
    errors: list[str] = []
    allowed_ctx = normalize(" ".join([user_text, *request.evidence, *request.hints]))
    for text in _texts(draft):
        norm = normalize(text)
        for phrase in rules.leading:
            if phrase and f" {phrase} " in f" {norm} ":
                errors.append(f"wording.leading: '{phrase}' leads the user")
        if any(p.search(text) for p in rules.statistics):
            errors.append("wording.statistic: no invented statistics")
        for brand in _brand_tokens(text):
            if brand.casefold() not in rules.allowlist and brand.casefold() not in allowed_ctx:
                errors.append(f"brand.name: '{brand}' looks like a brand name the user did not use")
    if draft.question.count("?") + draft.question.count("？") > 1:
        errors.append("one_idea: more than one question in the question text")
    del language
    return list(dict.fromkeys(errors))


def _is_acronym(token: str) -> bool:
    """ALL CAPS of at most four letters, optionally plural (``API``, ``APIs``, ``UPI``): neutral jargon."""
    core = token[:-1] if token.endswith("s") and token[:-1].isupper() else token
    return core.isupper() and core.isalpha() and len(core) <= MAX_ACRONYM_LETTERS


def _brand_tokens(text: str) -> list[str]:
    """Capitalised words that are not the first word of a sentence and not short acronyms."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text[:2_000]):
        words = sentence.split()
        out.extend(
            m.group(0)
            for w in words[1:]
            for m in [_CAP_WORD.fullmatch(w.strip(",;:()\"'?!."))]
            if m and not _is_acronym(m.group(0))
        )
    return out


def _check_language(draft: CardDraft, language: str, user_text: str) -> list[str]:
    errors: list[str] = []
    if draft.language != language:
        errors.append(f"language.declared: draft says {draft.language}, expected {language}")
    body = " ".join(_texts(draft))
    if language == "hi":
        latin_user = bool(user_text.strip()) and devanagari_share(user_text) == 0.0
        if not latin_user:
            if devanagari_share(draft.question) < HI_QUESTION_SHARE:
                errors.append("language.script: the question is not in Hindi script")
            elif devanagari_share(body) < HI_TEXT_SHARE:
                errors.append("language.script: the card text is not in Hindi script")
    elif devanagari_share(body) > EN_MAX_DEVANAGARI:
        errors.append("language.script: the card is not in English")
    return errors


def _check_grounding(draft: CardDraft, request: CardRequest, user_text: str) -> list[str]:
    errors: list[str] = []
    hints = request.hints
    evidence = [user_text, *request.evidence]
    grounded_slots = FAMILY_GROUNDED_SLOTS & set(request.slots)
    for option in draft.options:
        if option.basis == "typical" and not _matches_hint(option.label, hints):
            errors.append(f"basis.typical: '{option.label}' is not backed by the typical data")
        if option.basis == "your_document" and not request.evidence:
            errors.append(
                f"basis.document: '{option.label}' claims a document but there is no evidence"
            )
        if option.basis == "your_words" and not any(e.strip() for e in evidence):
            errors.append(
                f"basis.words: '{option.label}' claims the user's words but there are none"
            )
        if (
            grounded_slots
            and request.card_type in ("pick_many", "choose_one")
            and hints
            and option.basis not in ("your_words", "your_document")
            and not _matches_hint(option.label, hints)
            and not _is_custom(option)
        ):
            errors.append(
                f"families.outside: '{option.label}' is outside the typical data and not flagged custom"
            )
    return errors


def validate_card(
    draft: CardDraft,
    request: CardRequest,
    catalog: Catalog,
    *,
    language: str,
    user_text: str = "",
    config: QuestionPolicy | None = None,
) -> list[str]:
    """Every rule of prompt 6.3 on a written card; returns error strings (empty means the card is fine)."""
    cfg = config or QuestionPolicy()
    frame = catalog.frames.get(request.frame_id)
    errors: list[str] = []
    max_q = frame.max_question_words if frame else cfg.max_question_words
    if language == "hi":
        max_q += load_rules().hi_extra_question
    if not draft.question.strip():
        errors.append("question.empty: the question is empty")
    elif _words(draft.question) > max_q:
        errors.append(f"question.long: more than {max_q} words")
    errors.extend(_check_options(language, draft, request, catalog, frame, cfg))
    errors.extend(_check_wording(draft, user_text, request, language))
    errors.extend(_check_language(draft, language, user_text))
    errors.extend(_check_grounding(draft, request, user_text))
    seen_decl: set[str] = set()
    for text in [*_texts(draft), *(v for o in draft.options for v in _option_values(o))]:
        match = declined_category(text, catalog, language=language)
        if match is not None and match.id not in seen_decl:
            seen_decl.add(match.id)
            errors.append(f"capability.declined: mentions {match.label} (not buildable)")
    return list(dict.fromkeys(errors))


# --- assembly ------------------------------------------------------------------------------------------


def _slot_defs(card_slots: Sequence[str], catalog: Catalog) -> list[SlotDef]:
    return [catalog.slots[s] for s in card_slots if s in catalog.slots]


def assemble_card(
    draft: CardDraft,
    request: CardRequest,
    *,
    card_id: str,
    n: int,
    of_about: int,
    language: str,
    catalog: Catalog,
) -> Card:
    """A validated draft becomes a ``Card``: id, progress, custom input, You decide, Skip, must_confirm."""
    recommended = next((o for o in draft.options if o.recommended), None)
    must = request.must_confirm or any(s.must_confirm for s in _slot_defs(request.slots, catalog))
    return Card(
        id=card_id,
        frame=request.frame_id,
        slots=list(request.slots),
        type=request.card_type,
        question=draft.question.strip(),
        why_we_ask=draft.why_we_ask.strip(),
        options=list(draft.options),
        custom_input=CustomInput(
            enabled=True,
            placeholder=draft.placeholder or catalog.t("card.custom_placeholder", language),
            voice=True,
        ),
        you_decide=YouDecide(enabled=not must, assumes=recommended.id if recommended else None),
        skip=SkipSpec(enabled=not must),
        progress=CardProgress(n=n, of_about=max(of_about, n)),
        language=language,
        must_confirm=must,
    )


def _fallback_value(slot: SlotDef, value: str | None) -> JsonValue:
    if value is None:
        return None
    return [value] if slot.value_schema.kind in ("enum_list", "text_list", "locales") else value


def fallback_card(
    frame_or_slot: str,
    catalog: Catalog,
    *,
    language: str,
    card_id: str,
    n: int,
    of_about: int,
    slots: Sequence[str] | None = None,
) -> Card:
    """The deterministic plain card for a slot (or the first slot of a frame, or of ``slots`` in it)."""
    if frame_or_slot in catalog.slots:
        slot = catalog.slots[frame_or_slot]
        frame_id = next((f.id for f in catalog.frames.values() if slot.id in f.slots), slot.group)
    else:
        frame = catalog.frames[frame_or_slot]
        frame_id = frame.id
        pick = next((s for s in (slots or ()) if s in frame.slots), frame.slots[0])
        slot = catalog.slots[pick]
    spec = slot.fallback_card
    options = [
        CardOption(
            id=o.id,
            label=o.label.get(language),
            basis="suggestion",
            recommended=(i == 0 and spec.type == "choose_one"),
            reason=load_rules().fallback_reason.get(language, load_rules().fallback_reason["en"])
            if i == 0 and spec.type == "choose_one"
            else "",
            sets={slot.id: v} if (v := _fallback_value(slot, o.value)) is not None else {},
        )
        for i, o in enumerate(spec.options)
    ]
    first = options[0] if options else None
    return Card(
        id=card_id,
        frame=frame_id,
        slots=[slot.id],
        type=spec.type,
        question=spec.question.get(language),
        why_we_ask=spec.why_we_ask.get(language),
        options=options,
        custom_input=CustomInput(
            enabled=True,
            placeholder=(
                spec.placeholder.get(language)
                if spec.placeholder
                else catalog.t("card.custom_placeholder", language)
            ),
            voice=True,
        ),
        you_decide=YouDecide(
            enabled=not slot.must_confirm, assumes=first.id if first and first.recommended else None
        ),
        skip=SkipSpec(enabled=not slot.must_confirm),
        progress=CardProgress(n=n, of_about=max(of_about, n)),
        language=language,
        must_confirm=slot.must_confirm,
        fallback=True,
    )


# --- answers -> writes ---------------------------------------------------------------------------------


def _is_list_kind(slot: SlotDef | None) -> bool:
    return slot is not None and slot.value_schema.kind in (
        "enum_list",
        "text_list",
        "object_list",
        "locales",
    )


def _merge(options: list[CardOption], catalog: Catalog, provenance: Provenance) -> list[CardWrite]:
    """Combine the ``sets`` of several chosen options into one write per slot (lists concatenate)."""
    per_slot: dict[str, list[JsonValue]] = {}
    for option in options:
        for slot_id, value in option.sets.items():
            bucket = per_slot.setdefault(slot_id, [])
            if isinstance(value, list):
                bucket.extend(v for v in value if v not in bucket)
            elif value not in bucket:
                bucket.append(value)
    writes: list[CardWrite] = []
    for slot_id, values in per_slot.items():
        merged: JsonValue = values if _is_list_kind(catalog.slots.get(slot_id)) else values[0]
        writes.append(CardWrite(slot_id, merged, provenance))
    return writes


def _normalize_custom(slot: SlotDef | None, text: str) -> JsonValue:
    kind = slot.value_schema.kind if slot else "text"
    if kind == "object_list":
        return [{"name": text, "custom": True}]
    if kind in ("text_list", "enum_list", "locales"):
        return [text]
    return text


def _custom_write(card: Card, text: str, catalog: Catalog) -> CardWrite | None:
    clean = text.strip()
    if not clean or not card.slots:
        return None
    slot_id = card.slots[0]
    slot = catalog.slots.get(slot_id)
    nt = normalize(clean)
    for (
        option
    ) in card.options:  # the words are really one of the options: take it as a tap-equivalent
        if nt and nt == normalize(option.label) and slot_id in option.sets:
            return CardWrite(slot_id, option.sets[slot_id], "user_text", clean, False)
    return CardWrite(
        slot_id, _normalize_custom(slot, clean), "user_text", clean, needs_confirmation=True
    )


def _ids(answer: JsonValue) -> list[str]:
    if isinstance(answer, list):
        return [str(a) for a in answer if isinstance(a, str | int)]
    if isinstance(answer, str | int) and not isinstance(answer, bool):
        return [str(answer)]
    return []


def answer_to_writes(
    card: Card, answer: JsonValue, custom_text: str | None, catalog: Catalog
) -> list[CardWrite]:
    """Map a card answer onto slot writes (tap: option ids; form: dict; text: typed words).

    * choose_one / look_signals / confirm: the chosen option's ``sets``;
    * pick_many: the union of the ticked options' ``sets`` (list slots concatenate);
    * priority: one list per slot, values in ranked order;
    * quick_form: ``{slot_id: value}`` for slots of this card, empty values skipped;
    * open_prompt / upload_or_link: the typed words into the first slot;
    * a custom answer is stored in the user's words plus a normalized candidate and always needs a yes
      (``needs_confirmation``); an implied new feature is a ``custom`` item (``{"name":..., "custom": true}``).
    """
    by_id = {o.id: o for o in card.options}
    writes: list[CardWrite] = []
    ctype = card.type
    if ctype in ("choose_one", "look_signals", "confirm", "pick_many", "priority"):
        ids = _ids(answer)
        if ctype == "confirm" and (
            answer is True
            or (isinstance(answer, str) and normalize(answer) in ("yes", "confirm", "ok", "haan"))
        ):
            chosen = [o for o in card.options if o.ticked or o.recommended] or card.options[:1]
        else:
            chosen = [by_id[i] for i in ids if i in by_id]
        if ctype in ("choose_one", "look_signals"):
            chosen = chosen[:1]
        if ctype == "priority":
            writes.extend(_merge(chosen, catalog, "user_tap"))
        else:
            writes.extend(_merge(chosen, catalog, "user_tap"))
        stray = [i for i in ids if i not in by_id]
        text = custom_text or (stray[0] if stray and ctype == "choose_one" and not chosen else None)
        if text and card.custom_input.enabled:
            extra = _custom_write(card, text, catalog)
            if extra is not None:
                writes.append(extra)
        return writes
    if ctype == "quick_form" and isinstance(answer, dict):
        for slot_id in card.slots:
            value = answer.get(slot_id)
            if value not in (None, "", [], {}):
                writes.append(CardWrite(slot_id, value, "user_text", None, False))
        if custom_text and card.slots and not writes:
            extra = _custom_write(card, custom_text, catalog)
            if extra is not None:
                writes.append(extra)
        return writes
    text = custom_text or (answer.strip() if isinstance(answer, str) else "")
    if text and card.slots:
        slot = catalog.slots.get(card.slots[0])
        writes.append(
            CardWrite(
                card.slots[0],
                _normalize_custom(slot, text) if _is_list_kind(slot) else text.strip(),
                "user_text",
                text.strip(),
                False,
            )
        )
    return writes


def can_decline_to_answer(card: Card) -> bool:
    """False when the card must be confirmed: neither You decide nor Skip is allowed."""
    return not card.must_confirm


def you_decide_writes(card: Card) -> list[CardWrite]:
    """The writes for "You decide": the recommended option (or the one ``assumes`` names).

    Slot state becomes ``assumed`` with provenance ``assumed``. Raises :class:`CardRefused` on a must-confirm
    card. A card without options (open prompts, forms) assumes nothing here; the catalog default applies.
    """
    if card.must_confirm or not card.you_decide.enabled:
        raise CardRefused(f"card {card.id} must be confirmed: 'You decide' is not allowed")
    chosen = [o for o in card.options if o.id == card.you_decide.assumes] or (
        [o for o in card.options if o.recommended] or [o for o in card.options if o.ticked]
    )
    per_slot: dict[str, list[JsonValue]] = {}
    for option in chosen:
        for slot_id, value in option.sets.items():
            bucket = per_slot.setdefault(slot_id, [])
            bucket.extend(value if isinstance(value, list) else [value])
    writes: list[CardWrite] = []
    for slot_id, values in per_slot.items():
        single = len(chosen) == 1 and not isinstance(chosen[0].sets.get(slot_id), list)
        writes.append(CardWrite(slot_id, values[0] if single else values, "assumed", None, False))
    return writes


def skip_allowed(card: Card) -> bool:
    """Skip for now is refused on must-confirm cards."""
    return card.skip.enabled and not card.must_confirm
