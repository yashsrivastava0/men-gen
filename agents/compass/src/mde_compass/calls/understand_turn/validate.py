"""Code validators of ``cmp.understand_turn``: evidence, catalog fit and "never asks".

``validate`` lists every problem (used in tests on the mock and fallback output and by the adapter);
``sanitize`` drops what does not hold up (never invents or repairs a value); the spec carries
``no_questions`` (a model that asks is replaced by the deterministic fallback).

Version 2 adds facts. The rules, all enforced here and not by the model:

* a fact with no statement is dropped; a fact keeps its quote exactly as given (the engine verifies it against
  the source text and marks it unverified, so this module never blanks a quote), capped at ``MAX_QUOTE_CHARS``;
* an effect on an unknown slot, with a value the slot's ``value_schema`` rejects, or an ``add`` on a slot that
  is not a list is dropped (the fact stays: the person's statement is still worth keeping);
* an aspect must name a listed need and one of its aspects (or ``*``); anything else is dropped;
* ``corrects`` and ``contradicts`` must be ids that appear in the fact digest;
* the ``contradiction`` flag holds only with at least one valid id in ``contradicts``.
"""

from __future__ import annotations

import json
import re
from typing import Literal, get_args

from mde_compass.calls.understand_turn import lexicon as lx
from mde_compass.calls.understand_turn.schema import (
    EffectWire,
    FactWire,
    GlossaryTermWire,
    SlotUpdateWire,
    UnderstandInput,
    UnderstandOutput,
)
from mde_compass.calls.understand_turn.values import ValueError_, parse_json, validate_value
from mde_compass.catalog.loader import Catalog, Consumer, load_catalog
from mde_compass.domain.interview import FACT_TYPES
from mde_compass.engine.ports import UnderstandFlag

EVIDENCED: frozenset[str] = frozenset({"user_text", "voice"})
MAX_INFERRED_CONFIDENCE = 0.6
MAX_QUOTE_CHARS = 500
MAX_STATEMENT_CHARS = 1000
MAX_FACTS = 20
MAX_TAGS = 6  # topics, entities and consumers per fact
MAX_TAG_CHARS = 80
MAX_GLOSSARY = 10
MAX_ASPECTS = 12
CONSUMERS: frozenset[str] = frozenset(get_args(Consumer))
LIST_KINDS: frozenset[str] = frozenset({"enum_list", "text_list", "object_list", "locales"})
"""Slot kinds an ``add`` effect makes sense for."""
FLAGS: frozenset[str] = frozenset(get_args(UnderstandFlag))
ANY_ASPECT = "*"
_FACT_ID = re.compile(r"(?<![\w-])(FCT-\d{3,})(?!\w)")


def digest_ids(digest: str) -> frozenset[str]:
    """The fact ids a digest mentions (``FCT-014 [rule|pricing] ...`` lines)."""
    return frozenset(_FACT_ID.findall(lx.clip(digest, 400_000)))


def _texts(out: UnderstandOutput) -> list[str]:
    """Every output text field that must never hold a question (quotes are the user's own words)."""
    texts = [*out.references, *out.safety_flags, *out.ambiguous, out.language, *out.contradicts]
    for u in out.slot_updates:
        texts.append(u.value_json)
    for f in out.facts:
        texts += [f.statement, *f.topics, *f.entities, *f.aspects, f.corrects]
        texts += [e.value_json for e in f.effects]
    for g in out.glossary_terms:
        texts += [g.term, *g.synonyms]
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


# ------------------------------------------------------------------------------------------------ facts
def effect_problem(e: EffectWire, catalog: Catalog) -> str | None:
    """Why this effect must be dropped, or None."""
    slot = catalog.slots.get(e.slot_id)
    if slot is None:
        return f"effect on unknown slot {e.slot_id}"
    try:
        validate_value(slot, parse_json(e.value_json))
    except ValueError_ as exc:
        return f"effect on {e.slot_id}: {exc}"
    if "?" in e.value_json:
        return f"effect on {e.slot_id}: question mark in value"
    if e.mode == "add" and slot.value_schema.kind not in LIST_KINDS:
        return f"effect on {e.slot_id}: add needs a list slot"
    return None


def aspect_problem(aspect: str, inp: UnderstandInput) -> str | None:
    """Why this ``need_id:aspect`` entry must be dropped, or None. The aspect is whatever follows the LAST colon,
    so a ``domain:<topic>`` need keeps its own colon."""
    need_id, sep, name = aspect.rpartition(":")
    if not sep or not need_id or not name:
        return f"aspect {aspect!r} is not need_id:aspect"
    need = next((n for n in inp.needs if n.need_id == need_id), None)
    if need is None:
        return f"aspect {aspect!r}: {need_id} is not a listed need"
    if name != ANY_ASPECT and name not in need.aspects:
        return f"aspect {aspect!r}: {name} is not an aspect of {need_id}"
    return None


def fact_problems(f: FactWire, inp: UnderstandInput, catalog: Catalog) -> list[str]:
    """Every problem in one fact (empty list: it holds up as it is)."""
    problems: list[str] = []
    where = f"fact {f.statement[:40]!r}"
    if not f.statement.strip():
        problems.append("fact with an empty statement")
    if f.type not in FACT_TYPES:
        problems.append(f"{where}: unknown fact type {f.type}")
    if not 0.0 <= f.confidence <= 1.0:
        problems.append(f"{where}: confidence out of range")
    if len(f.quote) > MAX_QUOTE_CHARS:
        problems.append(f"{where}: quote longer than {MAX_QUOTE_CHARS} characters")
    for e in f.effects:
        problem = effect_problem(e, catalog)
        if problem:
            problems.append(f"{where}: {problem}")
    for a in f.aspects:
        problem = aspect_problem(a, inp)
        if problem:
            problems.append(f"{where}: {problem}")
    if f.corrects and f.corrects not in digest_ids(inp.fact_digest):
        problems.append(f"{where}: corrects {f.corrects}, which is not in the fact digest")
    return problems


def _tags(values: list[str], *, lower: bool) -> list[str]:
    out: list[str] = []
    for v in values:
        item = lx.squash_spaces(v).strip()[:MAX_TAG_CHARS]
        item = item.lower() if lower else item
        if item and item not in out:
            out.append(item)
        if len(out) >= MAX_TAGS:
            break
    return out


def _clean_effect(e: EffectWire, catalog: Catalog) -> EffectWire | None:
    if effect_problem(e, catalog) is not None:
        return None
    value = validate_value(catalog.slots[e.slot_id], parse_json(e.value_json))
    return e.model_copy(update={"value_json": json.dumps(value, ensure_ascii=False)})


def _clean_fact(
    f: FactWire, inp: UnderstandInput, catalog: Catalog, known: frozenset[str]
) -> FactWire | None:
    statement = lx.squash_spaces(f.statement).strip()[:MAX_STATEMENT_CHARS]
    if not statement or f.type not in FACT_TYPES:
        return None
    effects: list[EffectWire] = []
    for e in f.effects:
        cleaned = _clean_effect(e, catalog)
        if cleaned is not None and cleaned not in effects:
            effects.append(cleaned)
    aspects: list[str] = []
    for a in f.aspects:
        if aspect_problem(a, inp) is None and a not in aspects:
            aspects.append(a)
    return f.model_copy(
        update={
            "statement": statement,
            "quote": f.quote[
                :MAX_QUOTE_CHARS
            ],  # as given: the engine verifies it, we never blank it
            "topics": _tags(f.topics, lower=True),
            "entities": _tags(f.entities, lower=False),
            "consumers": [c for c in _tags(f.consumers, lower=True) if c in CONSUMERS],
            "confidence": min(1.0, max(0.0, f.confidence)),
            "aspects": aspects[:MAX_ASPECTS],
            "effects": effects,
            "corrects": f.corrects if f.corrects in known else "",
        }
    )


def _clean_glossary(items: list[GlossaryTermWire]) -> list[GlossaryTermWire]:
    out: list[GlossaryTermWire] = []
    seen: set[str] = set()
    for g in items:
        term = lx.squash_spaces(g.term).strip()[:MAX_TAG_CHARS]
        if not term or "?" in term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        synonyms = [s for s in _tags(g.synonyms, lower=False) if "?" not in s and s != term]
        out.append(GlossaryTermWire(term=term, synonyms=synonyms))
    return out[:MAX_GLOSSARY]


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
    for f in out.facts:
        errors += fact_problems(f, inp, cat)
    if len(out.facts) > MAX_FACTS:
        errors.append(f"more than {MAX_FACTS} facts")
    known = digest_ids(inp.fact_digest)
    errors += [f"{c} is not in the fact digest" for c in out.contradicts if c not in known]
    if "contradiction" in out.flags and not out.contradicts:
        errors.append("contradiction flag without contradicts")
    return errors


def sanitize(
    inp: UnderstandInput, out: UnderstandOutput, catalog: Catalog | None = None
) -> tuple[UnderstandOutput, list[str]]:
    """Drop updates, effects, aspects and facts that fail validation; clamp confidences; cap inferred ones.
    Returns (output, drops). Never invents a value, a quote or an id."""
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
    ids = digest_ids(inp.fact_digest)
    facts: list[FactWire] = []
    fact_keys: set[tuple[str, str]] = set()
    for f in out.facts:
        problems = fact_problems(f, inp, cat)
        cleaned = _clean_fact(f, inp, cat, ids)
        dropped += problems
        if cleaned is None:
            continue
        key = (cleaned.type, cleaned.statement.casefold())
        if key not in fact_keys:
            fact_keys.add(key)
            facts.append(cleaned)
    contradicts = list(dict.fromkeys(c for c in out.contradicts if c in ids))
    flags: list[UnderstandFlag] = [
        f for f in dict.fromkeys(out.flags) if f in FLAGS and (f != "contradiction" or contradicts)
    ]
    if contradicts and "contradiction" not in flags:
        flags.append("contradiction")
    if "unsafe" in out.safety_flags and "unsafe" not in flags:
        flags.append("unsafe")  # the code-detected safety flags always show in flags too
    if "declined_category" in out.safety_flags and "declined_category" not in flags:
        flags.append("declined_category")
    return (
        out.model_copy(
            update={
                "slot_updates": kept,
                "ambiguous": [s for s in out.ambiguous if s in known],
                "references": [r for r in out.references if r.strip()][:6],
                "confidence": min(1.0, max(0.0, out.confidence)),
                "facts": facts[:MAX_FACTS],
                "flags": flags,
                "glossary_terms": _clean_glossary(out.glossary_terms),
                "contradicts": contradicts,
            }
        ),
        dropped,
    )
