"""Offline handler for C9a: at most two extra screens, taken from research additions.

Deterministic and schema-valid. With no research additions it proposes nothing (the baseline
already fits the Brief). Each feature addition becomes a list screen reachable from the home
screen, tagged ``source: research`` with the addition's id.
"""

from __future__ import annotations

import re

from mde_contracts.calls import C9aInput, C9aOutput, Proposal, SpecProposals

MAX_EXTRA = 2
_NON_WORD = re.compile(r"[^a-z0-9]+")
_GENERIC = {"screen", "screens", "page", "pages", "view", "views", "list", "lists", "section"}


def _slug(text: str) -> str:
    return _NON_WORD.sub("-", text.lower()).strip("-")


def _already_there(title: str, routes: set[str]) -> bool:
    """Does an existing route already carry the feature (``/favorites`` for "Favorites list")?"""
    words = [w[:-1] if w.endswith("s") else w for w in _NON_WORD.split(title.lower())]
    significant = [w for w in words if len(w) >= 5 and w not in _GENERIC]
    return any(w in route for w in significant for route in routes)


def _home(inp: C9aInput) -> str | None:
    """The screen the new ones open from: /home, else the first non-sign-in screen."""
    auth_recipes = {r.id for r in inp.recipes if r.kind == "auth"}
    for screen in inp.outline.screens:
        if screen.route == "/home":
            return screen.id
    for screen in inp.outline.screens:
        if screen.recipe not in auth_recipes:
            return screen.id
    return None


def handle(input: C9aInput) -> C9aOutput:
    recipe = next((r.id for r in input.recipes if r.kind == "list"), None)
    if recipe is None or input.max_new_screens <= 0:
        return SpecProposals(proposals=[])
    taken = {s.route for s in input.outline.screens}
    parent = _home(input)
    proposals: list[Proposal] = []
    for addition in input.research:
        if addition.kind != "feature" or len(proposals) >= min(MAX_EXTRA, input.max_new_screens):
            continue
        route = f"/{_slug(addition.title)}"
        if route == "/" or route in taken or _already_there(addition.title, taken):
            continue
        taken.add(route)
        proposals.append(
            Proposal(
                kind="add_screen",
                target=parent,
                source="research",
                research_ref=addition.id,
                rationale=f"Research found that {addition.title.lower()} is common in similar apps.",
                name=addition.title[:60],
                route=route,
                recipe=recipe,
                purpose=f"{addition.title}: {addition.rationale}"[:200],
            )
        )
    return SpecProposals(proposals=proposals, notes="mock: screens from research additions")
