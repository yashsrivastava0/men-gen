"""Facts of the deterministic understander (the mock handler and the fallback of ``cmp.understand_turn`` v2).

Pure functions, offline, linear in the length of the text. They invent nothing: a fact's statement is the slot
label plus the user's own value, or the user's own words; its quote is the user's text (for a tap, the option
label); its effects repeat the slot update that was already extracted.

**The vague / specific rule** (the offline stand-in for the model's judgement; the policy tests on follow-ups
run against it). An answer is *specific* when ANY of these holds, and *vague* otherwise:

1. the person tapped an option (``focus_selected_labels``) or typed a pick of a pending option: a tap is a
   complete answer;
2. the typed text has at least ``SPECIFIC_MIN_WORDS`` (6) words;
3. the text contains a digit (any script), a comma, or the word ``and`` / ``aur`` / ``और``.

A short reply that only says "I do not know" (``lexicon.yaml``, ``non_answer``) is never specific and makes no
fact. A specific answer covers its need completely (aspects ``["<need>:*"]``, only for a need listed in
``needs``: the validator drops an aspect of an unlisted need, so the fallback never emits one). A vague answer
covers nothing (aspects ``[]``) and raises the ``vague`` flag when the need's catalog impact is at least
``HIGH_IMPACT_AT`` (4). An off-topic reply or a question raises ``off_topic``. Real calls use the model's own
judgement (the prompt); this rule only keeps the mock and the fallback honest and predictable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.schema import (
    EffectWire,
    FactWire,
    SlotUpdateWire,
    UnderstandInput,
)
from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.interview import FactType
from mde_compass.engine.ports import Intent

SPECIFIC_MIN_WORDS = 6
"""Typed text with at least this many words counts as a specific answer."""
HIGH_IMPACT_AT: int = DEFAULT_CONFIG.questions.high_impact_at
"""A vague answer raises the ``vague`` flag only for a need with at least this catalog impact."""
NON_ANSWER_MAX_WORDS = 4
"""A reply of at most this many words that holds a non-answer phrase ("no idea") is a non-answer."""
CONJUNCTIONS: frozenset[str] = frozenset({"and", "aur", "और"})
"""Whole words (after folding) that signal a list or a second idea: "riders and shop owners"."""
MAX_FACT_QUOTE = 200
MAX_STATEMENT = 600
MAX_TAP_FACTS = 8
FACT_INTENTS: frozenset[Intent] = frozenset({"answer", "change_request"})
"""Only these readings turn into facts; greetings, questions and control phrases carry no project detail."""
COMPLETE = "*"
"""The aspect that means "this statement is a complete answer to the need"."""


# --------------------------------------------------------------------------------- the rule
def is_non_answer(text: str) -> bool:
    """A short reply that gives nothing to record ("I do not know", "pata nahi")."""
    clipped = lx.clip(text)
    if any(ch.isdigit() for ch in clipped):
        return False
    words = lx.normalize(clipped).split()
    if not words or len(words) > NON_ANSWER_MAX_WORDS:
        return False
    phrases = tuple(lx.load_lexicon().intents["non_answer"])
    return lx.has_phrase(clipped, phrases)


def answer_is_specific(text: str, *, tapped: bool = False) -> bool:
    """True when the answer is specific enough to count as covering its need (see the module docstring)."""
    if tapped:
        return True
    clipped = lx.clip(text)
    if is_non_answer(clipped):
        return False
    words = lx.normalize(clipped).split()
    if len(words) >= SPECIFIC_MIN_WORDS:
        return True
    if "," in clipped or any(ch.isdigit() for ch in clipped):
        return True
    return any(w in CONJUNCTIONS for w in words)


# --------------------------------------------------------------------------------- fact pieces
def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, list) and bool(value) and all(_is_number(v) for v in value)


def fact_type_for(slot: SlotDef, value: Any, *, remove: bool) -> FactType:
    """The fact type a slot update gets: by slot, then by value, then by kind."""
    sid = slot.id
    if remove or sid == "scope.must_not":
        return "exclusion"
    if sid == "roles.list":
        return "role"
    if sid.startswith("goals."):
        return "goal"
    if sid.startswith("constraints."):
        return "constraint"
    if sid == "glossary.terms":
        return "glossary"
    if sid == "scope.references":
        return "reference"
    if _is_number(value):
        return "number"
    if slot.value_schema.kind in ("geo", "locales") or sid.startswith("audience."):
        return "context"
    return "requirement"


def value_text(slot: SlotDef, value: Any) -> str:
    """The slot value as plain text for a statement (the user's value, nothing added)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value.replace("_", " ") if slot.value_schema.kind in ("enum", "enum_list") else value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(value_text(slot, v) for v in value)
    if isinstance(value, dict):
        return ", ".join(value_text(slot, v) for v in value.values())
    return ""


def _statement(label: str, text: str) -> str:
    return f"{label}: {text}"[:MAX_STATEMENT].strip()


def _tap_quote(labels: Sequence[str], hint: str) -> str:
    """The selected option label that the update's words point at, else the first label."""
    for label in labels:
        if lx.quote_in_text(label, hint) or lx.quote_in_text(hint, label):
            return label
    return labels[0]


def _head(text: str) -> str:
    """The user's words up to the first question mark, whitespace squashed and clipped."""
    return lx.squash_spaces(text.split("?")[0]).strip()


def _need_label(catalog: Catalog, need_id: str) -> tuple[str, SlotDef | None]:
    slot = catalog.slots.get(need_id)
    if slot is not None:
        return slot.label.en, slot
    if need_id.startswith("domain:"):
        return need_id.removeprefix("domain:").replace("_", " ").strip(), None
    return "", None


# --------------------------------------------------------------------------------- the facts
def facts_for_turn(
    inp: UnderstandInput,
    catalog: Catalog,
    updates: Sequence[SlotUpdateWire],
    *,
    picked: frozenset[str],
    intent: Intent,
) -> tuple[list[FactWire], bool]:
    """The facts of one reading and whether the answer was vague (see the module docstring).

    ``updates`` are the slot updates already extracted; ``picked`` holds the slots filled by typing a pick of a
    pending option (a complete answer). Returns ``(facts, vague)``."""
    if intent not in FACT_INTENTS:
        return [], False
    labels = [lbl for lbl in inp.focus_selected_labels if lbl.strip()][:MAX_TAP_FACTS]
    listed = {n.need_id for n in inp.needs}
    tapped = bool(labels)
    text = lx.clip(inp.text)
    specific = answer_is_specific(text, tapped=tapped)
    facts: list[FactWire] = []
    vague = False
    covered: set[str] = set()
    for u in updates:
        slot = catalog.slots.get(u.slot_id)
        if slot is None:
            continue
        value = json.loads(u.value_json)
        complete = specific or u.slot_id in picked or intent == "change_request"
        quote = _tap_quote(labels, u.quote) if tapped else u.quote
        shown = value_text(slot, value)
        statement = _statement(slot.label.en, f"not {shown}" if u.remove else shown)
        covered.add(u.slot_id)
        facts.append(
            FactWire(
                type=fact_type_for(slot, value, remove=u.remove),
                statement=statement,
                quote=quote,
                topics=[slot.group],
                entities=[],
                consumers=[str(c) for c in slot.consumers],
                confidence=u.confidence,
                aspects=[f"{slot.id}:{COMPLETE}"] if complete and slot.id in listed else [],
                effects=[
                    EffectWire(
                        slot_id=u.slot_id,
                        value_json=u.value_json,
                        mode="remove" if u.remove else "set",
                    )
                ],
                corrects="",
            )
        )
        if not complete and slot.impact >= HIGH_IMPACT_AT:
            vague = True
    focus = [n for n in inp.focus_need_ids if _need_label(catalog, n)[0]]
    if not focus or covered & set(inp.focus_need_ids):
        return facts, vague
    primary = focus[0]
    label, slot = _need_label(catalog, primary)
    high = any(
        (s := catalog.slots.get(n)) is not None and s.impact >= HIGH_IMPACT_AT for n in focus
    )
    aspects = [f"{n}:{COMPLETE}" for n in focus if n in listed]
    if tapped:
        facts += [_focus_fact(slot, label, lbl, lbl, aspects) for lbl in labels]
        return facts, vague
    if is_non_answer(text):  # nothing to record; for an important need that is a vague answer
        return facts, vague or high
    head = _head(text)
    if not head:
        return facts, vague
    facts.append(_focus_fact(slot, label, head, head[:MAX_FACT_QUOTE], aspects if specific else []))
    return facts, vague or (high and not specific)


def _focus_fact(
    slot: SlotDef | None, label: str, said: str, quote: str, aspects: list[str]
) -> FactWire:
    """A fact for the focus question when no slot update covers it: the user's words under the need's label,
    with no effect (nothing is invented about the slot's value)."""
    ftype: FactType = "requirement"
    topics = [slot.group] if slot is not None else ["domain"]
    if slot is not None:
        ftype = fact_type_for(slot, None, remove=False)
    return FactWire(
        type=ftype,
        statement=_statement(label, said),
        quote=quote,
        topics=topics,
        entities=[],
        consumers=[str(c) for c in slot.consumers] if slot is not None else [],
        confidence=0.6,
        aspects=aspects,
        effects=[],
        corrects="",
    )
