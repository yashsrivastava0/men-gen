"""Code rules of ``cmp.probe_handoff``: pass or fail is decided HERE, from the plan and the digest.

The model's ``can_start`` claim is never read. A probe passes only when

* research: the digest has a geography and a category (or idea), and the plan has at least 8 queries, every
  one with a non-empty region, entity and terms;
* blueprint: the digest has at least one role and one feature that is "in", every "in" feature has an
  intent, and the plan has at least one journey (with steps) and at least 3 first-release screens;
* design: the plan has a non-empty tone and direction, and the digest has an audience, locales and a
  brand or tone signal.

``gaps`` are the model's blocking questions (normalized, mapped to catalog slots) plus the gaps the code
derives from the digest and from a plan that falls short.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.compile_text.text_utils import clip, template_lang
from mde_compass.calls.probe_handoff.schema import (
    MIN_QUERIES,
    MIN_SCREENS,
    BlockingQuestion,
    Digest,
    PlanQuery,
    ProbeHandoffInput,
    ProbeHandoffOutput,
)

TEMPLATES_PATH = Path(__file__).parent / "templates.yaml"
MAX_QUESTIONS = 10
MAX_QUESTION_CHARS = 300


@functools.lru_cache(maxsize=1)
def load_templates() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in raw.items()}


def table_for(language: str) -> dict[str, Any]:
    return load_templates()[template_lang(language)]


@dataclass(frozen=True)
class Verdict:
    passed: bool
    gaps: list[BlockingQuestion]
    reasons: list[str]  # why it did not pass (for logs and tests)


def _blank(text: str) -> bool:
    return not text.strip()


def _non_blank(items: list[str]) -> list[str]:
    return [i.strip() for i in items if i.strip()]


def _ask(
    table: dict[str, Any], key: str, slot_id: str | None, **values: object
) -> BlockingQuestion:
    return BlockingQuestion(
        question=str(table["questions"][key]).format(**values),
        slot_id=slot_id,
        fix="ask",
        suggested_value_json=None,
    )


def live_features(digest: Digest) -> list[Any]:
    return [f for f in digest.features if f.status.strip() == "in" and f.name.strip()]


def digest_gaps(inp: ProbeHandoffInput) -> list[BlockingQuestion]:
    """What the digest lacks for this probe (each one makes the probe fail: the planner would have to guess)."""
    d, table = inp.digest, table_for(inp.language)
    gaps: list[BlockingQuestion] = []
    if inp.kind == "research":
        if _blank(d.geography):
            gaps.append(_ask(table, "geography", "context.geo"))
        if _blank(d.category) and _blank(d.idea):
            gaps.append(_ask(table, "category", "idea.category"))
    elif inp.kind == "blueprint":
        if not [r for r in d.roles if r.name.strip()]:
            gaps.append(_ask(table, "roles", "roles.list"))
        live = live_features(d)
        if not live:
            gaps.append(_ask(table, "features", "scope.features"))
        missing = [f.name.strip() for f in live if _blank(f.intent)]
        if missing:
            names = ", ".join(clip(n, 40) for n in missing[:5])
            gaps.append(_ask(table, "intents", "scope.features", names=names))
    else:
        if not _non_blank(d.audience):
            gaps.append(_ask(table, "audience", "audience.segments"))
        if not _non_blank(d.locales):
            gaps.append(_ask(table, "locales", "locales"))
        if not _non_blank(d.tone_words) and not _non_blank(d.brand_signals):
            gaps.append(_ask(table, "tone", "look.tone_words"))
    return gaps


def complete_query(q: PlanQuery) -> bool:
    return bool(q.query.strip() and q.region.strip() and q.entity.strip() and _non_blank(q.terms))


def _valid_journeys(out: ProbeHandoffOutput) -> int:
    return sum(1 for j in out.journeys if j.name.strip() and _non_blank(j.steps))


def _first_release_screens(out: ProbeHandoffOutput) -> int:
    return sum(1 for s in out.screens if s.first_release and s.name.strip())


def plan_shortfalls(
    inp: ProbeHandoffInput, out: ProbeHandoffOutput
) -> tuple[list[str], list[BlockingQuestion]]:
    """Reasons the plan itself falls short, with the question that would fix each."""
    table = table_for(inp.language)
    reasons: list[str] = []
    gaps: list[BlockingQuestion] = []
    if inp.kind == "research":
        complete = sum(1 for q in out.plan_queries if complete_query(q))
        if len(out.plan_queries) < MIN_QUERIES or complete < len(out.plan_queries):
            reasons.append(
                f"{complete} complete queries of {len(out.plan_queries)}, need {MIN_QUERIES}"
            )
            gaps.append(_ask(table, "queries", None, n=complete))
    elif inp.kind == "blueprint":
        if _valid_journeys(out) < 1:
            reasons.append("no journey with steps")
            gaps.append(_ask(table, "journeys", "scope.features"))
        if _first_release_screens(out) < MIN_SCREENS:
            reasons.append(f"fewer than {MIN_SCREENS} first-release screens")
            gaps.append(_ask(table, "screens", "scope.features"))
    else:
        if _blank(out.tone):
            reasons.append("no tone")
            gaps.append(_ask(table, "tone", "look.tone_words"))
        if _blank(out.direction):
            reasons.append("no direction")
            gaps.append(_ask(table, "tone", "look.tone_words"))
    return reasons, gaps


def slot_for(
    question: str, table: dict[str, Any], valid_slots: frozenset[str] | None
) -> str | None:
    """The catalog slot a free-text question is about (keyword match from the data file), if any."""
    low = question.casefold()
    for slot_id, words in table["slot_keywords"].items():
        if valid_slots is not None and slot_id not in valid_slots:
            continue
        if any(str(w).casefold() in low for w in words):
            return str(slot_id)
    return None


def normalize_question(
    q: BlockingQuestion, table: dict[str, Any], valid_slots: frozenset[str] | None
) -> BlockingQuestion | None:
    """A model question with a valid slot id, a parsed suggestion and a consistent ``fix``."""
    text = " ".join(q.question.split())
    if not text:
        return None
    slot_id = q.slot_id.strip() if q.slot_id else None
    if slot_id and valid_slots is not None and slot_id not in valid_slots:
        slot_id = None
    if not slot_id:
        slot_id = slot_for(text, table, valid_slots)
    suggested: str | None = None
    if q.suggested_value_json is not None:
        try:
            value = json.loads(q.suggested_value_json)
        except (ValueError, RecursionError):
            value = None
        if value not in (None, "", [], {}):
            suggested = json.dumps(value, ensure_ascii=False)
    fix = "assume" if q.fix == "assume" and suggested is not None else "ask"
    return BlockingQuestion(
        question=clip(text, MAX_QUESTION_CHARS),
        slot_id=slot_id,
        fix=fix,
        suggested_value_json=suggested if fix == "assume" else None,
    )


def merge_gaps(*groups: list[BlockingQuestion]) -> list[BlockingQuestion]:
    """First come, first kept: one gap per slot, one per distinct question; at most ``MAX_QUESTIONS``."""
    out: list[BlockingQuestion] = []
    slots: set[str] = set()
    texts: set[str] = set()
    for group in groups:
        for q in group:
            key = q.question.casefold()
            if key in texts or (q.slot_id is not None and q.slot_id in slots):
                continue
            texts.add(key)
            if q.slot_id is not None:
                slots.add(q.slot_id)
            out.append(q)
    return out[:MAX_QUESTIONS]


def evaluate(
    inp: ProbeHandoffInput, out: ProbeHandoffOutput, valid_slots: frozenset[str] | None = None
) -> Verdict:
    """Decide pass or fail from the plan and the digest, and list every gap. ``out.can_start`` is ignored."""
    table = table_for(inp.language)
    model_gaps = [
        g for g in (normalize_question(q, table, valid_slots) for q in out.blocking_questions) if g
    ]
    derived = digest_gaps(inp)
    reasons, shortfall = plan_shortfalls(inp, out)
    if derived:  # the root cause is already listed; a shortfall on top would only add noise
        reasons = [f"digest gap: {g.slot_id or g.question}" for g in derived] + reasons
        shortfall = []
    gaps = merge_gaps(model_gaps, derived, shortfall)
    return Verdict(passed=not reasons, gaps=gaps, reasons=reasons)
