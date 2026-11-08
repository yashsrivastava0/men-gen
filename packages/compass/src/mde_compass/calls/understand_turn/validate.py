"""Code validators of ``cmp.understand_turn``: evidence, catalog fit and "never asks".

``validate`` lists every problem (used in tests on the mock and fallback output and by the adapter);
``sanitize`` drops the updates that do not hold up (never invents or repairs a value); the spec carries
``no_questions`` (a model that asks is replaced by the deterministic fallback).
"""

from __future__ import annotations

from typing import Literal

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.schema import (
    SlotUpdateWire,
    UnderstandInput,
    UnderstandOutput,
)
from mde_compass.calls.understand_turn.values import ValueError_, parse_json, validate_value
from mde_compass.catalog.loader import Catalog, load_catalog

EVIDENCED: frozenset[str] = frozenset({"user_text", "voice"})
MAX_INFERRED_CONFIDENCE = 0.6


def _texts(out: UnderstandOutput) -> list[str]:
    """Every output text field that must never hold a question (quotes are the user's own words)."""
    texts = [*out.references, *out.safety_flags, *out.ambiguous, out.language]
    for u in out.slot_updates:
        texts.append(u.value_json)
    return texts


def no_questions(_inp: UnderstandInput, out: UnderstandOutput) -> list[str]:
    """Spec-level validator: an understander never asks."""
    return ["output contains a question"] if any("?" in t for t in _texts(out)) else []


def update_problem(
    u: SlotUpdateWire, user_text: str, catalog: Catalog, mode: Literal["text", "voice"]
) -> str | None:
    """Why this update must be dropped, or None when it holds up."""
    slot = catalog.slots.get(u.slot_id)
    if slot is None:
        return f"unknown slot {u.slot_id}"
    try:
        validate_value(slot, parse_json(u.value_json))
    except ValueError_ as exc:
        return f"{u.slot_id}: {exc}"
    if "?" in u.value_json:
        return f"{u.slot_id}: question mark in value"
    if u.basis in EVIDENCED:
        if not lx.quote_in_text(u.quote, user_text):
            return f"{u.slot_id}: quote is not in the user's text"
    elif "I" not in slot.filled_by:
        return f"{u.slot_id}: this slot is filled by the user, not inferred"
    elif u.remove:
        return f"{u.slot_id}: a negation needs the user's words"
    return None


def validate(
    inp: UnderstandInput, out: UnderstandOutput, catalog: Catalog | None = None
) -> list[str]:
    """Every problem in ``out`` (empty list means acceptable)."""
    cat = catalog or load_catalog()
    errors = no_questions(inp, out)
    for u in out.slot_updates:
        problem = update_problem(u, inp.text, cat, inp.input_mode)
        if problem:
            errors.append(problem)
        if not 0.0 <= u.confidence <= 1.0:
            errors.append(f"{u.slot_id}: confidence out of range")
    if not 0.0 <= out.confidence <= 1.0:
        errors.append("confidence out of range")
    return errors


def sanitize(
    inp: UnderstandInput, out: UnderstandOutput, catalog: Catalog | None = None
) -> tuple[UnderstandOutput, list[str]]:
    """Drop updates that fail validation; clamp confidences; cap inferred ones. Returns (output, drops)."""
    cat = catalog or load_catalog()
    kept: list[SlotUpdateWire] = []
    dropped: list[str] = []
    seen: set[tuple[str, bool]] = set()
    for u in out.slot_updates:
        problem = update_problem(u, inp.text, cat, inp.input_mode)
        if problem:
            dropped.append(problem)
            continue
        if (u.slot_id, u.remove) in seen:
            continue
        seen.add((u.slot_id, u.remove))
        conf = min(1.0, max(0.0, u.confidence))
        if u.basis not in EVIDENCED:
            conf = min(conf, MAX_INFERRED_CONFIDENCE)
        kept.append(u.model_copy(update={"confidence": conf}))
    known = set(cat.slots)
    return (
        out.model_copy(
            update={
                "slot_updates": kept,
                "ambiguous": [s for s in out.ambiguous if s in known],
                "references": [r for r in out.references if r.strip()][:6],
                "confidence": min(1.0, max(0.0, out.confidence)),
            }
        ),
        dropped,
    )
