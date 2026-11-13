"""The cross-idea diversity corpus (build prompt 02, sections 2.6 and 6).

Hand-written first batches (wire outputs of ``cmp.plan_questions``) for the five ideas of the "before" evidence
(``docs/compass/evidence/before-first-questions.json``), and the measures that tell a specific batch from a generic
one: the validators' project-term share, the pairwise question-term overlap across ideas, and how much wording two
ideas share verbatim. The same measures run on a negative corpus (the same generic questions for every idea, which
is what the old engine did) and on the old engine's real cards, and must flag both.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mde_compass.calls.plan_questions.adapter import to_batch
from mde_compass.catalog import Catalog, load_catalog
from mde_compass.config import DEFAULT_CONFIG
from mde_compass.domain.cards import Card, CardOption
from mde_compass.engine import terms as terms_mod
from mde_compass.engine.language import normalize
from mde_compass.engine.ports import PlanContext, PlanRequest
from mde_compass.engine.question_validators import (
    BatchValidation,
    ValidationContext,
    cross_idea_overlap_pairs,
    project_term_share,
    validate_batch,
)
from mde_compass.scenarios.providers import plan_output


@dataclass(frozen=True)
class CorpusIdea:
    key: str
    idea: str
    cards: list[Card]

    @property
    def terms(self) -> frozenset[str]:
        return terms_mod.project_terms(self.idea)


def load_corpus(path: Path | str, catalog: Catalog | None = None) -> dict[str, CorpusIdea]:
    """A corpus file (``ideas: {key: {idea, batch}}``) as cards, built through the planner's own adapter."""
    cat = catalog or load_catalog()
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out: dict[str, CorpusIdea] = {}
    for key, item in raw["ideas"].items():
        request = PlanRequest(mode="initial", context=PlanContext(), next_card_n=2)
        batch = to_batch(cat, plan_output(item["batch"]), request)
        out[key] = CorpusIdea(key, str(item["idea"]), batch.cards())
    return out


def load_before_evidence(path: Path | str, catalog: Catalog | None = None) -> dict[str, CorpusIdea]:
    """The old engine's first cards for the five ideas, as ``Card`` objects (the frame's slots are their needs)."""
    cat = catalog or load_catalog()
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, CorpusIdea] = {}
    for key, item in raw.items():
        cards: list[Card] = []
        for c in item["cards"]:
            frame = cat.frames.get(c["frame"])
            slots = list(frame.slots) if frame else []
            options = [
                CardOption(id=chr(65 + i), label=o["label"], basis=o.get("basis", "suggestion"))
                for i, o in enumerate(c["options"])
            ]
            cards.append(
                Card(
                    id=c["id"],
                    frame=c["frame"],
                    slots=slots,
                    type=c["type"],
                    question=c["question"],
                    why_we_ask=c.get("why_we_ask", ""),
                    options=options,
                    need_ids=slots,
                )  # fmt: skip
            )
        out[key] = CorpusIdea(key, str(item["idea"]), cards)
    return out


def validate_idea(item: CorpusIdea, catalog: Catalog | None = None) -> BatchValidation:
    """The code validators over one idea's batch, with that idea's own words as the project terms."""
    cat = catalog or load_catalog()
    ctx = ValidationContext.build(
        cat,
        item.terms,
        language="en",
        config=DEFAULT_CONFIG,
        user_text=item.idea,
        batch_limit=7,
        open_prompt_share=1.0,  # the corpus measures the other rules; the open-prompt cap is tested on its own
    )
    return validate_batch(item.cards, ctx)


def term_share(corpus: dict[str, CorpusIdea], catalog: Catalog | None = None) -> dict[str, float]:
    cat = catalog or load_catalog()
    return {k: project_term_share(v.cards, v.terms, cat) for k, v in corpus.items()}


def overlap_pairs(
    corpus: dict[str, CorpusIdea], catalog: Catalog | None = None
) -> dict[tuple[str, str], float]:
    cat = catalog or load_catalog()
    return cross_idea_overlap_pairs({k: v.cards for k, v in corpus.items()}, cat)


def verbatim_sharing(corpus: dict[str, CorpusIdea]) -> tuple[int, int, int]:
    """(questions, distinct wordings, questions whose exact wording another idea also got)."""
    seen: Counter[str] = Counter()
    per_idea: dict[str, set[str]] = {}
    for key, item in corpus.items():
        per_idea[key] = {normalize(c.question) for c in item.cards}
        seen.update(per_idea[key])
    total = sum(len(item.cards) for item in corpus.values())
    shared = sum(
        1
        for key, item in corpus.items()
        for c in item.cards
        if sum(
            1
            for other, texts in per_idea.items()
            if other != key and normalize(c.question) in texts
        )
    )
    return total, len(seen), shared
