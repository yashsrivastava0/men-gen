"""The closing question (ADR 0016): pure helpers, no turn and no store.

The last question of every interview is one detailed, project-specific ``priority`` card: what matters most for the
first version. The planner writes it from a ``ClosingContext`` built here out of the slots, the hypothesis and the
visible assumptions; the answer places the person's own features (``must`` for the picks, ``nice`` for the other
options of the card), and never moves a feature to "later" on its own: that is the forced ranking of the legacy
``priorities`` frame, a different question."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from mde_compass.domain.cards import Card
from mde_compass.engine.flows import priorities
from mde_compass.engine.ports import ClosingContext

MAX_PER_LIST = (
    12  # candidates per list handed to the planner: short statements, never the whole ledger
)
MAX_MUST = 5  # the picks that become "must": a first release is a handful of things


def names_of(value: JsonValue) -> list[str]:
    """Short names out of a slot value: a list of records (``name``), a list of strings, one string."""
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            raw = item.get("name") or item.get("label") or item.get("text")
        else:
            raw = item
        if isinstance(raw, str) and raw.strip():
            out.append(raw.strip())
    return list(dict.fromkeys(out))


def _texts(values: Iterable[JsonValue]) -> list[str]:
    out: list[str] = []
    for value in values:
        out += names_of(value)
    return list(dict.fromkeys(out))


def build_context(
    *,
    features: JsonValue,
    suggested_features: Sequence[str] = (),
    roles: JsonValue = None,
    goals: Iterable[JsonValue] = (),
    constraints: Iterable[JsonValue] = (),
    assumptions: Sequence[str] = (),
) -> ClosingContext:
    """The planner's material for the last question. Stated features come first; suggested ones (from the domain
    hypothesis) are kept apart so the question can mark them as suggestions."""
    stated = names_of(features)
    folded = {n.casefold() for n in stated}
    suggested = [n for n in dict.fromkeys(suggested_features) if n.casefold() not in folded]
    return ClosingContext(
        features=stated[:MAX_PER_LIST],
        suggested_features=suggested[:MAX_PER_LIST],
        roles=names_of(roles)[:MAX_PER_LIST],
        goals=_texts(goals)[:MAX_PER_LIST],
        constraints=_texts(constraints)[:MAX_PER_LIST],
        assumptions=[a.strip() for a in assumptions if a.strip()][:MAX_PER_LIST],
    )


def picked_ids(card: Card, answer: JsonValue) -> list[str]:
    """The option ids the person ranked or ticked, in order, only those that are options of the card."""
    known = {o.id for o in card.options}
    if isinstance(answer, Mapping):  # a bucket form: the must bucket is the picks
        raw: Any = answer.get(priorities.MUST)
    else:
        raw = answer
    ids = (
        [str(a) for a in raw if isinstance(a, str | int) and not isinstance(a, bool)]
        if isinstance(raw, list)
        else [str(raw)]
        if isinstance(raw, str | int) and not isinstance(raw, bool)
        else []
    )
    return [i for i in dict.fromkeys(ids) if i in known]


def placement(card: Card, answer: JsonValue, *, cap: int = MAX_MUST) -> priorities.Placement | None:
    """Where the card's options go: the first ``cap`` picks are ``must``, every other option is ``nice``. None when
    nothing was picked (a junk or empty answer never reprioritises the features)."""
    picks = picked_ids(card, answer)
    if not picks:
        return None
    label_of = {o.id: o.label for o in card.options}
    placed = {label_of[i]: priorities.MUST for i in picks[:cap]}
    for label in label_of.values():
        placed.setdefault(label, priorities.NICE)
    return priorities.Placement({name: placed[name] for name in label_of.values()})


def _bucket_for(name: str, placed: priorities.Placement, common: frozenset[str]) -> str | None:
    """The bucket of the card option that names a feature: the same name; or a name that holds the other's words
    ("Produce listings and prices" for the feature "produce listings"); or one label that names several features
    ("Weekly timetable and booking" for "Weekly class timetable" and "Class booking"), judged by the words that tell
    features apart. ``must`` wins when several options reach the same feature."""
    from mde_compass.engine.fact_projector import _same_element, _stems

    exact = {label.casefold(): bucket for label, bucket in placed.priorities.items()}
    if name.casefold() in exact:
        return exact[name.casefold()]
    mine = _stems(name)
    distinct = mine - common
    found: list[str] = []
    for label, bucket in placed.priorities.items():
        words = _stems(label)
        if _same_element({"name": label}, {"name": name}) or (distinct and distinct <= words):
            found.append(bucket)
    if not found:
        return None
    return priorities.MUST if priorities.MUST in found else found[0]


def apply_to_features(features: JsonValue, placed: priorities.Placement) -> list[JsonValue]:
    """The features list with ``priority`` set for the features the card named (matched by name, by the words the
    option and the feature share, or because one option names several features); suggestions that are not features of
    the Brief are ignored, everything else is kept as it is."""
    from mde_compass.engine.fact_projector import _stems

    items = features if isinstance(features, list) else []
    names = [priorities._name(i) or "" for i in items]
    stems = [_stems(n) for n in names]
    common = (
        frozenset(w for w in set().union(*stems) if sum(w in x for x in stems) >= 3)
        if stems
        else frozenset()
    )
    out: list[JsonValue] = []
    for item, name in zip(items, names, strict=True):
        bucket = _bucket_for(name, placed, common) if name else None
        if bucket is None or not isinstance(item, dict):
            out.append(item)
            continue
        out.append({k: v for k, v in item.items() if k != "status"} | {"priority": bucket})
    return out
