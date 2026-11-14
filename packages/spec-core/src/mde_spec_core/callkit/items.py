"""Item-level repair of a model answer (doc 18 section 3.2; FN-03, FN-04).

An answer that fails its checks is rarely wrong everywhere: one story cites a screen that does not exist, the other
forty items are fine. Throwing the whole answer away for the deterministic default wastes the good items (FN-03), and a
re-ask that fixes one item while breaking another must not be taken whole (FN-04).

``adopt_valid_items`` works on any pydantic output whose list fields hold flat records tied together by an id. A call
describes its output once with an ``ItemSchema``: for each list field, which ITEM a record belongs to (an entity with
its fields, states and transitions is one item; a story with its acceptance rows is one item). Then:

* start from a BASE answer that is valid by construction (the call's deterministic fallback, or the items already kept);
* offer the candidate's items one by one; an item is kept only if the WHOLE answer, re-checked with the call's own
  validators, shows no error that the base did not already show; an item that does not fit yet is retried after the
  others (it may need an item that comes later);
* the items that never fit keep the base's version and are reported with the messages that rejected them.

Code decides, the model proposes: nothing here creates content. An item the candidate did not mention keeps the base
version; an item is never half-adopted. Pure functions, no model, no vendor name.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

O = TypeVar("O", bound=BaseModel)  # noqa: E741

MAX_TRIALS = 400  # whole-answer re-checks one repair may spend (each is a full pass of the call's validators)
PASSES = 3  # an item that needs a later one is retried this many times over the rejected ones

Records = dict[str, list[Any]]  # list-field name -> the records of ONE item


@dataclass(frozen=True)
class ItemKind:
    """One list field of the output and the item each of its records belongs to.

    ``item_of`` returns a key that is the same for every record of one item and different for different items, across
    all the kinds of one schema (prefix it with the kind: ``f"entity:{rec.entity}"``)."""

    array: str
    item_of: Callable[[Any], str]


@dataclass(frozen=True)
class ItemSchema:
    kinds: tuple[ItemKind, ...]

    @property
    def arrays(self) -> tuple[str, ...]:
        return tuple(k.array for k in self.kinds)


@dataclass
class Adoption:
    """What `adopt_valid_items` did: the merged answer, the items taken from the candidate and the ones that did not fit."""

    output: Any
    adopted: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(
        default_factory=dict
    )  # item key -> the messages that rejected it
    trials: int = 0


def split_items(out: BaseModel, schema: ItemSchema) -> dict[str, Records]:
    """The items of an answer, in order of first appearance, each with its records per list field."""
    items: dict[str, Records] = {}
    for kind in schema.kinds:
        for rec in getattr(out, kind.array):
            items.setdefault(kind.item_of(rec), {}).setdefault(kind.array, []).append(rec)
    return items


def join_items(template: O, items: dict[str, Records], schema: ItemSchema) -> O:
    """An answer built from items: the list fields of ``template`` are replaced by the records of ``items`` (item order
    kept, records of one item in their own order); everything else on the template stays."""
    update = {a: [rec for recs in items.values() for rec in recs.get(a, [])] for a in schema.arrays}
    return template.model_copy(update=update)


def adopt_valid_items(
    base: O,
    candidate: BaseModel,
    schema: ItemSchema,
    errors_of: Callable[[Any], Sequence[str]],
    *,
    only: Collection[str] | None = None,
) -> Adoption:
    """Merge the valid items of ``candidate`` into ``base``. ``errors_of`` is the call's whole-answer check.

    ``only`` limits the offer to those item keys (a re-ask repairs the items that failed, nothing else; whatever else
    the second answer changed is ignored). An item whose records equal the base's is not offered."""
    current = split_items(base, schema)
    offered = split_items(candidate, schema)
    pending = [
        k for k, recs in offered.items() if recs != current.get(k) and (only is None or k in only)
    ]
    known = set(errors_of(base))  # a base that is not clean must not make every item look guilty
    result = Adoption(output=base)
    why: dict[str, list[str]] = {}
    for _ in range(PASSES):
        progressed = False
        for key in list(pending):
            if result.trials >= MAX_TRIALS:
                break
            trial = dict(current)
            trial[key] = offered[key]
            errs = list(errors_of(join_items(base, trial, schema)))
            result.trials += 1
            fresh = [e for e in errs if e not in known]
            if fresh:
                why[key] = fresh
                continue
            current, known = trial, set(errs)
            pending.remove(key)
            result.adopted.append(key)
            why.pop(key, None)
            progressed = True
        if not pending or not progressed or result.trials >= MAX_TRIALS:
            break
    result.rejected = {
        k: why.get(k, ["not checked: too many items to re-check one by one"]) for k in pending
    }
    result.output = join_items(base, current, schema)
    return result
