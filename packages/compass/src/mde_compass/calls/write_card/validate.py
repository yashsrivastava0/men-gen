"""Code validators of ``cmp.write_card``: the card rules of the spec, checked on the wire output.

``structural`` (in the spec) only checks that the output is parseable and non-empty. ``validate_card`` is the
full rule set the adapter applies (the engine applies its own ``validate_card`` on the domain card too):
word limits, exactly the right number of distinct concrete options, one recommended with a reason, honest
basis labels, no invented statistics, no brands the user did not use, no tier B or C capability, neutral
wording, only slots of the frame, language matches the user.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.values import ValueError_, validate_value
from mde_compass.calls.write_card.schema import OptionWire, WriteCardInput, WriteCardOutput
from mde_compass.catalog.loader import Catalog, SlotDef, load_catalog

OPTION_CARDS = frozenset({"choose_one", "look_signals", "pick_many", "priority", "confirm"})
EXACT_CARDS = frozenset({"choose_one", "look_signals"})
FREE_CARDS = frozenset({"open_prompt", "quick_form", "upload_or_link"})
MAX_CHECKLIST = 12
MAX_TICKED_FRACTION = 0.5

_STATS = re.compile(
    r"\d{1,3}(?:[.,]\d{1,2})?\s{0,3}(?:%|percent|per cent|प्रतिशत)"
    r"|(?<!\w)(?:most|many|majority|nine|9|8|7) (?:out of|of the|of) (?:10|ten|people|users|apps|businesses)"
    r"|(?<!\w)(?:most|majority of) (?:people|users|apps|businesses|owners|companies|customers)"
    r"|(?<!\w)(?:studies|research|surveys?) (?:show|says?|found|suggest)"
    r"|(?<!\w)(?:everyone|everybody|all apps) (?:chooses|picks|uses|prefers)"
    r"|(?<!\w)(?:ज़्यादातर|ज्यादातर|अधिकतर) (?:लोग|ऐप|व्यवसाय)"
    r"|(?<!\w)(?:zyadatar|jyadatar) (?:log|apps?)",
    re.IGNORECASE,
)
_LEADING = re.compile(
    r"(?<!\w)(?:you should|you must|you need to|obviously|of course|clearly|the best|best choice|"
    r"no brainer|we recommend|we suggest you|definitely|surely)(?!\w)",
    re.IGNORECASE,
)
_CAP_WORD = re.compile(r"\b[A-Z][A-Za-z0-9]{2,30}\b")


def words(text: str) -> int:
    return len(text.split())


def structural(_inp: WriteCardInput, out: WriteCardOutput) -> list[str]:
    """Spec-level check: parseable (every ``sets_json`` is a JSON object) and non-empty."""
    errors: list[str] = []
    if not out.question.strip():
        errors.append("empty question")
    for o in out.options:
        if not o.label.strip():
            errors.append("empty option label")
        try:
            if not isinstance(json.loads(o.sets_json), dict):
                errors.append(f"option {o.id}: sets_json is not an object")
        except ValueError:
            errors.append(f"option {o.id}: sets_json is not JSON")
    return errors


def _norm(text: str) -> str:
    return lx.normalize(text)


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) >= 3}


def _allowed_text(inp: WriteCardInput) -> str:
    parts = [*inp.hints, *inp.evidence, *inp.avoid_labels, *(k.value_json for k in inp.known_slots)]
    parts += [s.label for s in inp.slots] + [o for s in inp.slots for o in s.options]
    return _norm(" ".join(parts))


def _matches_hint(label: str, hints: list[str]) -> bool:
    n = _norm(label)
    return any(n and h and (n == h or n in h or h in n) for h in (_norm(x) for x in hints))


def _backed_by_evidence(opt: OptionWire, evidence: list[str]) -> bool:
    ev = _tokens(" ".join(evidence))
    return bool(ev & _tokens(f"{opt.label} {opt.reason}"))


def script_ok(text: str, language: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    deva = sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters)
    return deva >= 0.4 if language == "hi" else deva < 0.4


def _check_text(
    label: str, text: str, inp: WriteCardInput, allowed: str, errors: list[str]
) -> None:
    if _STATS.search(text):
        errors.append(f"{label}: invented statistic or popularity claim")
    if _LEADING.search(text):
        errors.append(f"{label}: leading wording")
    if "?" in text and label != "question":
        errors.append(f"{label}: a question inside an option")
    brands = {
        m.group(0)
        for m in _CAP_WORD.finditer(lx.clip(text))
        if _mid_sentence(text, m.start())
        and _norm(m.group(0)) not in allowed
        and not _is_language_name(m.group(0))
    }
    if brands:
        errors.append(f"{label}: names the user did not use ({sorted(brands)[0]})")


def _mid_sentence(text: str, start: int) -> bool:
    """The word does not open a sentence (a capital there is only grammar)."""
    before = text[max(0, start - 3) : start].rstrip()
    return bool(before) and before[-1] not in ".!?:।"


def _is_language_name(word: str) -> bool:
    names = {_norm(str(n)) for ns in lx.load_lexicon().raw["languages"].values() for n in ns}
    return _norm(word) in names


def _check_capabilities(texts: list[str], evidence: str, catalog: Catalog) -> list[str]:
    errors: list[str] = []
    for cap in (*catalog.tier_b(), *catalog.declined()):
        user_said = lx.find_first(evidence, cap.keywords) is not None
        for t in texts:
            if lx.find_first(t, cap.keywords) is not None and not user_said:
                errors.append(f"names a {cap.kind} capability the user did not ask for ({cap.id})")
                break
    return errors


def _frame_texts(inp: WriteCardInput, catalog: Catalog) -> list[str]:
    """The catalog's own wording for this card's slots (a limits card may name offline use)."""
    texts: list[str] = []
    for s in inp.slots:
        slot = catalog.slots.get(s.slot_id)
        if slot is not None:
            card = slot.fallback_card
            texts += [card.question.en, card.question.hi, slot.label.en, slot.label.hi]
            texts += [o.label.en for o in card.options] + [o.label.hi for o in card.options]
    return texts


def _catalog_tokens(slot: SlotDef) -> set[str]:
    return {o.value for o in slot.fallback_card.options if o.value is not None}


def _check_sets(inp: WriteCardInput, o: OptionWire, catalog: Catalog, errors: list[str]) -> None:
    try:
        sets: Any = json.loads(o.sets_json)
    except ValueError:
        errors.append(f"option {o.id}: sets_json is not JSON")
        return
    if not isinstance(sets, dict):
        errors.append(f"option {o.id}: sets_json is not an object")
        return
    frame_slots = {s.slot_id for s in inp.slots}
    for slot_id, value in sets.items():
        if slot_id not in frame_slots or slot_id not in catalog.slots:
            errors.append(f"option {o.id}: sets a slot outside this card ({slot_id})")
            continue
        slot = catalog.slots[slot_id]
        token = value[0] if isinstance(value, list) and len(value) == 1 else value
        if isinstance(token, str) and token in _catalog_tokens(slot):
            continue  # the catalog's own option value (a token the engine maps)
        try:
            validate_value(slot, value)
        except ValueError_ as exc:
            errors.append(f"option {o.id}: {slot_id}: {exc}")


def validate_card(
    inp: WriteCardInput, out: WriteCardOutput, catalog: Catalog | None = None
) -> list[str]:
    """Every rule violation in ``out`` (empty list means the card may be shown)."""
    cat = catalog or load_catalog()
    errors = structural(inp, out)
    ctype = inp.frame.card_type
    lang = inp.language
    allowed = _allowed_text(inp)
    if words(out.question) > inp.frame.max_question_words:
        errors.append(f"question has {words(out.question)} words")
    if ctype in OPTION_CARDS and "?" not in out.question and ctype != "confirm":
        pass  # a question mark is style, not a rule
    if not script_ok(out.question, lang):
        errors.append("question is not in the user's language")
    _check_text("question", out.question, inp, allowed, errors)
    _check_text("why_we_ask", out.why_we_ask, inp, allowed, errors)
    if ctype in FREE_CARDS and not out.placeholder.strip():
        errors.append("a free-text card needs a placeholder")
    opts = out.options
    if ctype in FREE_CARDS:
        if opts and ctype != "upload_or_link":
            errors.append("a free-text card has no options")
    elif ctype in OPTION_CARDS:
        _check_option_cards(inp, out, cat, errors)
    errors += _check_capabilities(
        [out.question, out.why_we_ask, *(f"{o.label} {o.reason}" for o in opts)],
        " ".join([*inp.evidence, *inp.hints, *_frame_texts(inp, cat)]),
        cat,
    )
    for o in opts:
        if words(o.label) > inp.frame.max_option_words:
            errors.append(f"option {o.id}: {words(o.label)} words")
        if not script_ok(o.label, lang):
            errors.append(f"option {o.id}: not in the user's language")
        _check_text(f"option {o.id}", f"{o.label}. {o.reason}", inp, allowed, errors)
        _check_basis(inp, o, errors)
        _check_sets(inp, o, cat, errors)
    return errors


def _check_option_cards(
    inp: WriteCardInput, out: WriteCardOutput, catalog: Catalog, errors: list[str]
) -> None:
    ctype = inp.frame.card_type
    opts = out.options
    labels = [_norm(o.label) for o in opts]
    ids = [o.id for o in opts]
    if len(set(labels)) != len(labels) or len(set(ids)) != len(ids):
        errors.append("options are not distinct")
    if any(not o.label.strip() for o in opts):
        errors.append("empty option label")
    if set(labels) & {_norm(x) for x in inp.avoid_labels}:
        errors.append("repeats an option already shown")
    recommended = [o for o in opts if o.recommended]
    if ctype in EXACT_CARDS:
        want = inp.frame.option_count or 3
        if len(opts) != want:
            errors.append(f"needs exactly {want} options, got {len(opts)}")
        if len(recommended) != 1:
            errors.append("needs exactly one recommended option")
        if any(o.ticked for o in opts):
            errors.append("a choice card has no pre-ticked options")
    else:
        if not 2 <= len(opts) <= MAX_CHECKLIST:
            errors.append(f"a checklist needs 2 to {MAX_CHECKLIST} options, got {len(opts)}")
        if len(recommended) > 1:
            errors.append("more than one recommended option")
        ticked = [o for o in opts if o.ticked]
        if len(ticked) > max(1, int(len(opts) * MAX_TICKED_FRACTION)):
            errors.append("too many pre-ticked options")
        if any(o.basis == "suggestion" for o in ticked):
            errors.append("a suggestion must not be pre-ticked")
    for o in recommended:
        if not o.reason.strip():
            errors.append(f"option {o.id}: the recommended option needs a reason")
    del catalog


def _check_basis(inp: WriteCardInput, o: OptionWire, errors: list[str]) -> None:
    if o.basis == "typical" and not _matches_hint(o.label, inp.hints):
        errors.append(f"option {o.id}: basis typical but not in the typical items")
    if o.basis == "your_words" and not (inp.evidence and _backed_by_evidence(o, inp.evidence)):
        errors.append(f"option {o.id}: basis your_words without the user's words")
    if o.basis == "your_document" and not (
        inp.option_source == "document" and inp.evidence and _backed_by_evidence(o, inp.evidence)
    ):
        errors.append(f"option {o.id}: basis your_document without a document")
