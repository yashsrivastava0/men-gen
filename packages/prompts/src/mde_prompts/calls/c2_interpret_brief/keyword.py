"""Keyword interpreter: the offline brain of C2 (mock handler and engine fallback)."""

from __future__ import annotations

import re

from mde_content.catalogs import load_catalogs, sensitive_domain
from mde_content.stack import unsupported_type
from mde_contracts.calls import (
    ArchetypeCandidate,
    C1Input,
    C2Input,
    C2Output,
    SlotUpdate,
)
from mde_prompts.calls.c1_understand import lexicon as lx
from mde_prompts.calls.c1_understand.keyword import extract_references, extract_updates

SURFACES: dict[str, tuple[str, ...]] = {
    "customer": ("customer", "customers", "buyer", "buyers", "shopper", "shoppers", "user"),
    "business": (
        "seller", "sellers", "vendor", "vendors", "merchant", "merchants", "business",
        "shop owner", "restaurant owner", "provider", "owner",
    ),
    "courier": ("driver", "drivers", "courier", "rider", "riders", "delivery partner", "partner"),
    "admin": ("admin", "administrator", "administrators"),
}  # fmt: skip
SURFACE_NOUNS = ("app", "apps", "panel", "side", "dashboard", "portal")
APP_COUNT_WORDS = {
    "two": 2, "2": 2, "three": 3, "3": 3, "four": 4, "4": 4,
    "multiple": 2, "several": 3, "separate": 2,
}  # fmt: skip
SENTENCE_END = re.compile(r"(?<=[.!?।])\s+|\n+")


def one_sentence(idea: str, doc: str) -> str:
    """First sentence of the idea (or the first line of the document when the idea is empty)."""
    text = idea.strip()
    if len(text) < 8 and doc.strip():
        for line in doc.splitlines():
            line = line.strip().lstrip("#").strip()
            if len(line) >= 8:
                text = line
                break
    first = SENTENCE_END.split(text.strip(), maxsplit=1)[0].strip()
    first = " ".join(first.split())
    return first[:160].rstrip(" ,;:") if first else ""


def archetype_scores(text: str, allowed: list[str]) -> list[tuple[str, int]]:
    scored: list[tuple[str, int]] = []
    for a in load_catalogs().archetypes:
        if a.id in allowed:
            scored.append((a.id, len(lx.keyword_hits(text, a.keywords))))
    return sorted(scored, key=lambda t: -t[1])


def detect_surfaces(text: str) -> tuple[bool, int | None]:
    """(multi_app, how many) from phrases such as 'seller app', 'admin panel', 'three apps'."""
    norm = lx.normalize(text)
    groups: set[str] = set()
    for group, names in SURFACES.items():
        for name in names:
            for noun in SURFACE_NOUNS:
                if f"{name} {noun}" in norm:
                    groups.add(group)
    count = len(groups)
    m = re.search(
        r"\b(two|three|four|multiple|several|separate|2|3|4)\s+(?:separate\s+)?apps\b", norm
    )
    if m:
        count = max(count, APP_COUNT_WORDS.get(m.group(1), 2))
    return (count >= 2, count if count >= 2 else None)


def sensitive_hits(text: str, domain_ids: list[str]) -> list[str]:
    """Sensitive domain ids (from ``domain_ids``) whose catalog keywords appear in the text."""
    found: list[str] = []
    for did in domain_ids:
        try:
            keywords = sensitive_domain(did).keywords
        except KeyError:
            continue
        if lx.keyword_hits(text, keywords):
            found.append(did)
    return found


def region_labels(text: str) -> list[str]:
    table: dict[str, list[str]] = lx.load_lexicon().raw["regions"]
    return [label for label, names in table.items() if lx.keyword_hits(text, names)]


def interpret(inp: C2Input) -> C2Output:
    """Deterministic C2: archetype candidates, regions, references, flags, size, volunteered slots."""
    doc = lx.clip(inp.document_text or "")  # the rules read at most MAX_SCAN_CHARS of any text
    full = lx.clip(f"{lx.clip(inp.idea_text)}\n{doc}").strip()
    allowed = [a.id for a in inp.archetypes]
    scores = archetype_scores(full, allowed)
    hit_scores = [(a, n) for a, n in scores if n > 0]
    if hit_scores:
        candidates = [
            ArchetypeCandidate(
                archetype_id=a,
                confidence=round(min(0.95, 0.55 + 0.15 * n) if i == 0 else 0.3 / i, 2),
            )
            for i, (a, n) in enumerate(hit_scores[:3])
        ]
        top_hits = hit_scores[0][1]
    else:
        fallback = "custom" if "custom" in allowed else (allowed[0] if allowed else "custom")
        candidates = [ArchetypeCandidate(archetype_id=fallback, confidence=0.35)]
        top_hits = 0
    top = candidates[0].archetype_id
    multi, count = detect_surfaces(full)
    unsupported: str | None = None
    if top_hits == 0:
        for u in inp.unsupported_types:
            try:
                kws = unsupported_type(u.id).keywords
            except KeyError:
                continue
            if lx.keyword_hits(full, kws):
                unsupported = u.id
                break
    n_words = len(lx.words(full))
    if n_words >= 600 or (multi and n_words >= 150):
        size = "large"
    elif n_words <= 30:
        size = "small"
    else:
        size = "medium"
    refs = [r.text for r in extract_references(full) if r.kind == "like_app"]
    volunteered: list[SlotUpdate] = [
        u
        for u in extract_updates(C1Input(message=full), full, top if top_hits else None, None)
        if u.slot_id != "idea.archetype" and not u.negate
    ]
    return C2Output(
        one_sentence_idea=one_sentence(lx.clip(inp.idea_text), doc),
        archetype_candidates=candidates,
        regions=region_labels(full),
        named_references=refs,
        multi_app=multi,
        app_count_hint=count,
        sensitive_flags=sensitive_hits(full, [d.id for d in inp.sensitive_domains]),
        unsupported_type=unsupported,
        size_estimate=size,
        volunteered=volunteered,
    )
