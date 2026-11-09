"""The Brief digest the scanner reads: one plain-text line per value, tagged with its catalog slot.

Copy-adapted from ``calls/compile_text/brief_text.py`` (flattening) but self-contained: which slot lives
where comes from the catalog's ``brief_path``, nothing is named here. Pure; never mutates the Brief.
"""

from __future__ import annotations

from pydantic import JsonValue

from mde_compass.calls.scan_ambiguity.rules import ScanRules, load_rules
from mde_compass.calls.scan_ambiguity.schema import DigestLine
from mde_compass.catalog import Catalog
from mde_compass.domain.brief import DICT_SECTIONS, Brief
from mde_compass.engine.ports import TurnContext


def value_text(value: JsonValue) -> str:
    """A JSON value as one plain line (lists joined by commas)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, bool | int | float):
        return str(value).lower() if isinstance(value, bool) else str(value)
    if isinstance(value, list):
        return ", ".join(t for t in (value_text(v) for v in value) if t)
    return "; ".join(f"{k}: {t}" for k, t in ((k, value_text(v)) for k, v in value.items()) if t)


def _join(*parts: str) -> str:
    return ": ".join(p.strip() for p in parts if p and p.strip())


def fact_lines(brief: Brief, rules: ScanRules | None = None) -> list[DigestLine]:
    """The details the Brief carries (``domain_facts``), one line each: ``FCT-004 [rule] statement``. Values only:
    never the quote. Capped, newest first when it is long (the newest statement of the person wins a conflict)."""
    r = rules or load_rules()
    rows = sorted(brief.domain_facts, key=lambda f: f.id, reverse=True)[: r.max_fact_lines]
    lines: list[DigestLine] = []
    for fact in sorted(rows, key=lambda f: f.id):
        tag = fact.kind or fact.type
        text = " ".join(f"{fact.id} [{tag}] {fact.statement}".split())[: r.max_item_chars]
        lines.append(DigestLine(slot_id=None, text=text))
    return lines


def build_digest(
    ctx: TurnContext, brief: Brief, catalog: Catalog, rules: ScanRules | None = None
) -> tuple[list[DigestLine], list[str]]:
    """``(items, glossary_terms)`` for the call: values in Brief order, capped and trimmed."""
    r = rules or load_rules()
    by_path: dict[str, str] = {}
    for slot_id, slot in catalog.slots.items():
        by_path.setdefault(slot.brief_path, slot_id)
    items: list[DigestLine] = []
    seen_slots: set[str] = set()

    def add(slot_id: str | None, text: str) -> None:
        text = " ".join(text.split())[: r.max_item_chars]
        if text and len(items) < r.max_items:
            items.append(DigestLine(slot_id=slot_id, text=text))
            if slot_id:
                seen_slots.add(slot_id)

    for section in DICT_SECTIONS:
        for key, item in getattr(brief, section).items():
            if item.has_value():
                slot_id = by_path.get(f"{section}.{key}") or by_path.get(
                    f"{section}.{key.replace('_', '.')}"
                )
                add(slot_id, value_text(item.value))
    for app in brief.apps:
        add(by_path.get("apps"), _join(app.name, app.purpose))
    for role in brief.roles:
        add(by_path.get("roles"), _join(role.name_in_user_words, role.goal))
    for feat in brief.scope:
        if feat.scope_status != "out":
            add(by_path.get("scope"), _join(feat.name, feat.intent))
    for integ in brief.integrations:
        add(by_path.get("integrations"), _join(integ.name, integ.notes))
    for ref in brief.references:
        add(by_path.get("references"), ref.label)
    for assumption in brief.assumptions:
        if assumption.basis in ("correction", "review"):
            continue  # our own findings from an earlier scan: never fed back to the scanner
        add(assumption.slot_id, assumption.claim)
    for slot in ctx.slots:  # working slots not yet in the Brief
        if (
            slot.state != "empty"
            and slot.slot_id not in seen_slots
            and slot.slot_id in catalog.slots
        ):
            add(slot.slot_id, value_text(slot.value))
    glossary = [t for g in brief.glossary for t in (g.term, *g.synonyms) if t.strip()]
    return items, glossary
