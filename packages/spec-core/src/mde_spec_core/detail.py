"""Bulky detail moves to ``screens/<id>.detail.md`` (doc 18 3.3 and G-2, doc 15 5.4, FN-26). Pure code, no model.

A card or slice over its cap is repaired in a fixed order: (1) the packager moves bulky free text out of the card into a
detail file that the card references and a worker loads on demand; (2) one re-ask of the card's owning batch shortens it
(the flow's service, not done here); (3) only a budget that cannot be met after both is a Spec bug (``PackBreach``).

The move is deterministic: candidates are the free-text fields longer than ``detail_min_chars``, taken longest first (ties
by anchor), one at a time, until the card and the slice fit. A card that fits is never touched, so packs that never
breach keep their bytes. Only prose moves: ids, binds, effects, shapes and every other structured field stay in the card
(so the closure checks still read them). What stays in the card is the head of the text and its anchor; the whole text
is in the detail file under the same anchor, so nothing is cut.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .canon import normalise_text
from .graph import Screen

DETAIL_SUFFIX = ".detail.md"


def detail_path(screen_id: str) -> str:
    return f"screens/{screen_id}{DETAIL_SUFFIX}"


@dataclass(frozen=True)
class Candidate:
    anchor: str
    text: str


def candidates(screen: Screen, min_chars: int) -> list[Candidate]:
    """Free-text fields long enough to be worth moving, longest first, ties by anchor."""
    out: list[Candidate] = []

    def add(anchor: str, text: str) -> None:
        if len(text) > min_chars:
            out.append(Candidate(anchor, text))

    add("purpose", screen.purpose)
    for r in screen.regions:
        add(f"region.{r.key}.what", r.what)
    for a in screen.actions:
        add(f"action.{a.key}.what", a.what)
        add(f"action.{a.key}.precondition", a.precondition)
    for i in screen.inputs:
        add(f"input.{i.key}.constraint", i.constraint)
    for s in screen.states:
        add(f"state.{s.kind}.message", s.message)
    for n, c in enumerate(screen.acceptance, start=1):
        add(f"acceptance.{n}.given", c.given)
        add(f"acceptance.{n}.when", c.when)
        add(f"acceptance.{n}.then", c.then)
    for n, u in enumerate(screen.usually, start=1):
        add(f"usually.{n}.note", u.note)
    return sorted(out, key=lambda c: (-len(c.text), c.anchor))


def head_of(text: str, head_chars: int) -> str:
    """The start of a text for the card: its first sentence if short enough, else cut at a word boundary."""
    flat = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", flat)
    if m and len(m.group(1)) <= head_chars:
        return m.group(1)
    if len(flat) <= head_chars:
        return flat
    cut = flat[:head_chars].rsplit(" ", 1)[0] or flat[:head_chars]
    return cut.rstrip(",;:- ")


def stub(text: str, anchor: str, head_chars: int) -> str:
    return f"{head_of(text, head_chars)} (more: detail {anchor})"


def apply_moves(screen: Screen, moved: dict[str, str], head_chars: int) -> Screen:
    """A copy of the screen whose moved texts are replaced by their head and a pointer to the detail file."""

    def sub(anchor: str, text: str) -> str:
        return stub(text, anchor, head_chars) if anchor in moved else text

    return screen.model_copy(
        update={
            "purpose": sub("purpose", screen.purpose),
            "regions": [
                r.model_copy(update={"what": sub(f"region.{r.key}.what", r.what)})
                for r in screen.regions
            ],
            "actions": [
                a.model_copy(
                    update={
                        "what": sub(f"action.{a.key}.what", a.what),
                        "precondition": sub(f"action.{a.key}.precondition", a.precondition),
                    }
                )
                for a in screen.actions
            ],
            "inputs": [
                i.model_copy(update={"constraint": sub(f"input.{i.key}.constraint", i.constraint)})
                for i in screen.inputs
            ],
            "states": [
                s.model_copy(update={"message": sub(f"state.{s.kind}.message", s.message)})
                for s in screen.states
            ],
            "acceptance": [
                c.model_copy(
                    update={
                        "given": sub(f"acceptance.{n}.given", c.given),
                        "when": sub(f"acceptance.{n}.when", c.when),
                        "then": sub(f"acceptance.{n}.then", c.then),
                    }
                )
                for n, c in enumerate(screen.acceptance, start=1)
            ],
            "usually": [
                u.model_copy(update={"note": sub(f"usually.{n}.note", u.note)})
                for n, u in enumerate(screen.usually, start=1)
            ],
        }
    )


def render_detail(screen_id: str, moved: dict[str, str]) -> str:
    """screens/<id>.detail.md: every moved text in full under its anchor, anchors in sorted order."""
    lines = [
        "---",
        f"id: {screen_id}   kind: detail   of: screens/{screen_id}.md   loaded: on demand",
        "---",
    ]
    for anchor in sorted(moved):
        lines += [f"## {anchor}", moved[anchor]]
    return normalise_text("\n".join(lines))


@dataclass(frozen=True)
class Fitted:
    """The outcome of fitting one screen: the card to render and what moved (empty when nothing had to)."""

    screen: Screen
    moved: dict[str, str]
    card_tokens: int
    slice_tokens: int
    card_ok: bool
    slice_ok: bool


def fit(
    screen: Screen,
    *,
    min_chars: int,
    head_chars: int,
    card_hard: int,
    slice_cap: int,
    measure: Callable[[Screen, str | None], tuple[int, int]],
) -> Fitted:
    """Move candidates one at a time until the card (hard cap) and the slice (cap) fit, or none are left.

    ``measure(screen, detail_ref)`` returns (card tokens, slice tokens) of the screen rendered with the pointer."""
    moved: dict[str, str] = {}
    cur = screen
    card, sl = measure(cur, None)
    if card <= card_hard and sl <= slice_cap:
        return Fitted(cur, moved, card, sl, True, True)
    for cand in candidates(screen, min_chars):
        moved[cand.anchor] = cand.text
        cur = apply_moves(screen, moved, head_chars)
        card, sl = measure(cur, detail_path(screen.id))
        if card <= card_hard and sl <= slice_cap:
            break
    return Fitted(cur, moved, card, sl, card <= card_hard, sl <= slice_cap)
