"""Truncation splitting and the call outcomes (doc 18 4.2; FN-02, FN-08, FN-25, FN-35).

Plain handling table (data below as ``OUTCOME_HANDLING``; code wires it, this module only names it):

| Outcome        | What happened                           | Handling                                                  |
|----------------|-----------------------------------------|-----------------------------------------------------------|
| ok             | text parsed and fits the schema         | item checks, then the result cache                        |
| truncated      | output hit the limit                    | keep the finished items; redo ONLY the unfinished ones in |
|                |                                         | smaller batches (a fused call splits back into its parts) |
| refused        | the model refused or filtered           | fallback route if configured, else R-flag (library        |
|                |                                         | default, node `degraded`)                                 |
| rate_limited   | provider rate limit                     | back off, circuit breaker, checkpoint; the run ends       |
|                |                                         | `partial` and resumes                                     |
| no_credit      | provider credit or quota is exhausted   | checkpoint; the run ends `partial` and resumes (never a   |
|                |                                         | silent fallback)                                          |
| invalid        | text unparseable or fails the schema    | R-code, else ONE re-ask (batched per call kind), else     |
|                |                                         | R-flag                                                    |

The Plan call has no safe default: invalid after its re-ask ends the run `partial` (FN-35).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Hashable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class CallOutcome(StrEnum):
    OK = "ok"
    TRUNCATED = "truncated"
    REFUSED = "refused"
    RATE_LIMITED = "rate_limited"
    NO_CREDIT = "no_credit"
    INVALID = "invalid"


@dataclass(frozen=True)
class Handling:
    action: str
    retry_same_route: bool = False
    try_fallback_route: bool = False
    ends_run_partial: bool = False  # checkpoint, end `partial`, resume later
    reask: bool = False
    split: bool = False
    flag_on_failure: bool = False  # R-flag if nothing else works


OUTCOME_HANDLING: dict[CallOutcome, Handling] = {
    CallOutcome.OK: Handling("check items, then cache"),
    CallOutcome.TRUNCATED: Handling(
        "keep finished items, redo unfinished in smaller batches", split=True
    ),
    CallOutcome.REFUSED: Handling(
        "fallback route if configured, else R-flag", try_fallback_route=True, flag_on_failure=True
    ),
    CallOutcome.RATE_LIMITED: Handling(
        "back off, circuit breaker, checkpoint, end partial, resume", ends_run_partial=True
    ),
    CallOutcome.NO_CREDIT: Handling(
        "checkpoint, end partial, resume after credit returns", ends_run_partial=True
    ),
    CallOutcome.INVALID: Handling(
        "R-code, else one re-ask per node, else R-flag", reask=True, flag_on_failure=True
    ),
}


def handling_for(outcome: CallOutcome) -> Handling:
    return OUTCOME_HANDLING[outcome]


def split_on_truncation(
    items: Sequence[T],
    done: Collection[Hashable],
    factor: int,
    *,
    key: Callable[[T], Hashable] | None = None,
) -> list[list[T]]:
    """Smaller batches for ONLY the unfinished items of a truncated call.

    ``items`` is what the call asked for (in order); ``done`` holds the keys of the items that came
    back finished (``key`` maps an item to its key, default the item itself). The new batch size is
    ``ceil(len(items) / factor)`` of the ORIGINAL batch, so a call that finished nothing is halved
    (factor 2), a call that finished 7 of 10 redoes its 3 in one call. A single item that cannot be
    split stays one batch: check ``is_irreducible`` and R-flag it.
    """
    if factor < 2:
        raise ValueError("factor must be at least 2")
    k = key or (lambda x: x)  # type: ignore[assignment,return-value]
    finished = set(done)
    remaining = [it for it in items if k(it) not in finished]
    if not remaining:
        return []
    size = max(1, math.ceil(len(items) / factor))
    return [remaining[i : i + size] for i in range(0, len(remaining), size)]


def is_irreducible(
    items: Sequence[T],
    done: Collection[Hashable],
    *,
    key: Callable[[T], Hashable] | None = None,
) -> bool:
    """True if truncation left exactly one item and nothing finished: splitting cannot help."""
    k = key or (lambda x: x)  # type: ignore[assignment,return-value]
    finished = set(done)
    return len(items) == 1 and k(items[0]) not in finished
