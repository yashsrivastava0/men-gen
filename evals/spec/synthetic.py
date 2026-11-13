"""Offline harness inputs: directions with tokens and COULD entries attached to a graph, so the diversity tool and the
ablation have something to measure without a live Design or Screens lane. NOT evidence about the real lanes."""

from __future__ import annotations

import random

from mde_spec_core import vocab
from mde_spec_core.graph import Could, Direction, SpecGraph
from mde_spec_core.tokens import build_graph_tokens, direction_fields, pick_directions

_KINDS = ("delight", "trust", "clarity")
_EFFECTS = {
    "delight": "a small, warm acknowledgement that says what happens next",
    "trust": "the user feels sure the work is safe and held",
    "clarity": "the user sees at once what changed and what is left to do",
}


def attach_directions(graph: SpecGraph, seed: int = 1, n: int = 2) -> SpecGraph:
    """A copy with `n` directions (pairwise different on the direction axes) and the tokens built from them by code."""
    g = graph.model_copy(deep=True)
    for i, pick in enumerate(pick_directions(n, rnd=random.Random(seed)), start=1):  # noqa: S311
        fields = direction_fields(pick)
        fields.setdefault("mood", "calm_trust")
        g.add(
            "directions",
            Direction(id=f"DIR-{i:03d}", name=f"{fields['mood']} direction {i}", **fields),
        )
    g.tokens = build_graph_tokens(g)
    return g


def attach_could(
    graph: SpecGraph, per_screen: int = 2, seed: int = 1, screens: int = 6
) -> SpecGraph:
    """A copy where the first `screens` non-system screens carry COULD entries (intent and effect only, with a basis)."""
    g = graph.model_copy(deep=True)
    rnd = random.Random(seed)  # noqa: S311 - seeded fixture, not security
    motions = sorted(vocab.motion_intents())
    n = 0
    for sid in sorted(g.screens)[:screens]:
        s = g.screens[sid]
        if s.system:
            continue
        entries = []
        for _ in range(per_screen):
            n += 1
            kind = rnd.choice(_KINDS)
            m = rnd.choice(motions)
            entries.append(
                Could(
                    id=f"CP-{n}",
                    kind=kind,  # type: ignore[arg-type]
                    when=f"the user finishes the main thing on {s.name}",
                    effect=_EFFECTS[kind],
                    basis="the moment that matters in the primary journey",
                    basis_kind="journey_moment",
                    assumed=True,
                    motion=m,
                    reduced_motion_alt="a plain text confirmation"
                    if vocab.is_essential_motion(m)
                    else None,
                )
            )
        s.could = entries
    return g
