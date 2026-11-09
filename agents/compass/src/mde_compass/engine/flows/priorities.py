"""The forced first-release ranking (frame ``priorities``, card type ``priority``).

When the user says everything is a must (or ticks every feature) the interview asks ONE card: "If you
launched with only five features, which?". The answer is a ranked list of option ids (the UI sends the
order) or, for a bucket UI, ``{"must": [...], "nice": [...], "later": [...]}``. The first ``first_release_max``
ranked features are Must, the rest are Later (status ``later``: the Phase 2 backlog); the card never adds a
feature, it only places the user's own.

The "everything is a must" signal is stored as the slot ``scope.priority`` = ``must`` (user said it); the card
upgrades it to confirmed. Pure functions: no turn, no store.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import (
    Card,
    CardOption,
    CardProgress,
    CustomInput,
    SkipSpec,
    YouDecide,
)
from mde_compass.engine.flows import data

FRAME = "priorities"
FEATURES_FRAME = "features"
SLOT = "scope.priority"
FEATURES_SLOT = "scope.features"
MUST = "must"
LATER = "later"
NICE = "nice"
SIGNAL_VALUE = MUST
_BUCKETS = (MUST, NICE, LATER)


def everything_is_must(text: str) -> str | None:
    """The cue phrase when ``text`` says every feature is essential (and does not negate it), else None."""
    if data.find_phrase(text, data.phrases("priorities", "not_everything")) is not None:
        return None
    return data.find_phrase(text, data.phrases("priorities", "everything_must"))


def _name(item: Any) -> str:
    if isinstance(item, Mapping):
        raw = item.get("name")
        return raw.strip() if isinstance(raw, str) else ""
    return item.strip() if isinstance(item, str) else ""


def _status(item: Mapping[str, Any]) -> str:
    return str(item.get("scope_status") or item.get("status") or "in")


def rankable(value: JsonValue) -> list[dict[str, Any]]:
    """The features that are candidates for the first release: named, status ``in``, not already Later."""
    items = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not _name(item):
            continue
        if _status(item) != "in" or item.get("priority") == LATER:
            continue
        out.append(item)
    return out


def signal_present(scope_priority: JsonValue, status: str) -> bool:
    """The user already said everything is a must (``scope.priority`` = must, given by the user)."""
    return scope_priority == SIGNAL_VALUE and status in ("volunteered", "confirmed")


def is_due(
    *,
    features: JsonValue,
    scope_priority: JsonValue,
    scope_priority_status: str,
    asked_frames: Sequence[str],
) -> bool:
    """Ask the ranking card now? Only once, only with something to rank, only after the user said everything
    is a must or ticked every feature. A confirmed signal means the card was already answered."""
    if FRAME in asked_frames or scope_priority_status == "confirmed":
        return False
    if not signal_present(scope_priority, scope_priority_status):
        return False
    return len(rankable(features)) >= data.policy().min_features


def flow_card(card: Card) -> bool:
    """Is this the ranking card built here (options F1..Fn), not the generic card writer's?"""
    return (
        card.frame == FRAME
        and card.type == "priority"
        and bool(card.options)
        and all(re.fullmatch(r"F\d{1,2}", o.id) for o in card.options)
    )


def tick_all(card: Card, reason: str) -> Card:
    """The features checklist when the user said everything is a must: every option starts ticked (their
    words are the evidence), so the ranking card that follows has their whole list to place."""
    options = [
        o.model_copy(
            update={
                "ticked": True,
                "basis": "your_words" if o.basis == "suggestion" else o.basis,
                "reason": o.reason or reason,
            }
        )
        for o in card.options
    ]
    return card.model_copy(update={"options": options})


def question_key(count: int) -> str:
    """The catalog key of the card question: the "only five" line when there is more than five to rank."""
    return (
        "flows.priority.question_five"
        if count > data.policy().first_release_max
        else "flows.priority.question_few"
    )


def build_card(
    features: JsonValue,
    *,
    catalog: Catalog,
    card_id: str,
    n: int,
    of_about: int,
    language: str,
) -> Card:
    """The ranking card from the user's own features (never adds one)."""
    t = catalog.t
    chosen = rankable(features)[: data.policy().max_options]
    options = [
        CardOption(
            id=f"F{i + 1}",
            label=_name(item),
            basis="your_words",
            reason=str(item.get("intent") or ""),
        )
        for i, item in enumerate(chosen)
    ]
    return Card(
        id=card_id,
        frame=FRAME,
        slots=[SLOT],
        type="priority",
        question=t(
            question_key(len(options)),
            language,
            n=data.number_word(data.policy().first_release_max, language),
        ),
        why_we_ask=t("flows.priority.why", language),
        options=options,
        custom_input=CustomInput(enabled=False),
        you_decide=YouDecide(enabled=False),
        skip=SkipSpec(enabled=False),
        progress=CardProgress(n=n, of_about=max(of_about, n)),
        language=language,
        must_confirm=True,
    )


@dataclass(frozen=True)
class Placement:
    """Where each feature went: ``priorities`` maps a feature name to must / nice / later."""

    priorities: dict[str, str]

    def names(self, bucket: str) -> list[str]:
        return [name for name, b in self.priorities.items() if b == bucket]


def place(card: Card, answer: JsonValue) -> Placement:
    """Map the card answer onto buckets. A ranked list: the first ``first_release_max`` are Must, every other
    option is Later. A bucket dict is taken as given (unplaced options stay Later)."""
    cap = data.policy().first_release_max
    label_of = {o.id: o.label for o in card.options}
    placed: dict[str, str] = {}
    if isinstance(answer, dict):
        for bucket in _BUCKETS:
            raw = answer.get(bucket)
            for oid in raw if isinstance(raw, list) else []:
                name = label_of.get(str(oid))
                if name is not None and name not in placed:
                    placed[name] = bucket
    else:
        ids = (
            [str(a) for a in answer if isinstance(a, str | int)]
            if isinstance(answer, list)
            else [str(answer)]
            if isinstance(answer, str | int) and not isinstance(answer, bool)
            else []
        )
        ranked = [oid for oid in dict.fromkeys(ids) if oid in label_of]
        for oid in ranked[:cap]:
            placed[label_of[oid]] = MUST
    for name in label_of.values():
        placed.setdefault(name, LATER)
    return Placement({name: placed[name] for name in label_of.values()})


def apply_placement(features: JsonValue, placement: Placement) -> list[JsonValue]:
    """The features list with ``priority`` set (and status ``later`` for the Later bucket). Items the card did
    not show are kept as they are; record ids and meta are preserved."""
    out: list[JsonValue] = []
    for item in features if isinstance(features, list) else []:
        if not isinstance(item, dict) or _name(item) not in placement.priorities:
            out.append(item)
            continue
        bucket = placement.priorities[_name(item)]
        new = {k: v for k, v in item.items() if k not in ("status", "_meta")}
        new["priority"] = bucket
        new["scope_status"] = "later" if bucket == LATER else _status(item)
        out.append(new)
    return out
