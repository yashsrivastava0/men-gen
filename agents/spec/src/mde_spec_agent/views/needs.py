"""Needs, blocks, assets, motion intents, stories and opportunities (``GET /api/runs/{id}/views/needs``).

Opportunities are the COULD entries: open ideas, never required."""

from __future__ import annotations

from typing import Any

from mde_spec_agent.derive.text import humanize
from mde_spec_core.graph import SpecGraph
from mde_spec_core.pack import Pack

from .names import name_of, plain


def needs_view(pack: Pack | Any, graph: SpecGraph) -> dict[str, Any]:
    needs = [
        {
            "need": f"{n.get('shape', '')} {n.get('kind') or ''}".strip(),
            "cardinality": str(n.get("cardinality", "")).replace("_", " "),
            "live": n.get("live", "none"),
            "screen_kind": str(n.get("states", "")).removeprefix("std."),
            "used_by": ", ".join(sorted({u.split(".")[0] for u in n.get("used_by", [])})[:8]),
            "intents": ", ".join(n.get("intents", [])),
        }
        for n in (pack.needs or [])
    ]
    blocks = [
        {"block": bid, "what": plain(body.split(";")[0], graph), "body": plain(body, graph)}
        for bid, body in sorted((pack.blocks or {}).items())
    ]
    assets = [
        {
            "asset": a.subject,
            "kind": a.kind,
            "for": plain(a.purpose, graph),
            "how_to_get_it": a.source_policy.replace("_", " "),
            "how_many": a.count,
            "licence": a.license_rule,
            "fallback": a.fallback,
            "made_by_code": bool(a.bound),
        }
        for _, a in sorted(graph.assets.items())
    ]
    motion = []
    opportunities = []
    for _, s in sorted(graph.screens.items()):
        for c in s.could:
            opportunities.append(
                {
                    "screen": s.name,
                    "idea": humanize(c.kind),
                    "does": plain(c.effect, graph),
                    "when": plain(c.when, graph),
                    "because": plain(c.basis, graph),
                    "assumed": bool(c.assumed),
                    "needs": f"tier {c.tier}",
                }
            )
            if c.motion:
                motion.append(
                    {
                        "screen": s.name,
                        "motion": humanize(c.motion),
                        "when": plain(c.when, graph),
                        "with_reduced_motion": plain(c.reduced_motion_alt or "", graph),
                        "needs": f"tier {c.tier}",
                    }
                )
    for _, d in sorted(graph.directions.items()):
        motion.append(
            {
                "screen": "the whole app",
                "motion": humanize(d.motion_personality),
                "when": f"the {d.name} direction",
                "with_reduced_motion": "motion calms down",
                "needs": "tier A",
            }
        )
    stories = [
        {
            "story": f"As {name_of(graph, st.role_id)}, I want {plain(st.want, graph)} so that {plain(st.so_that, graph)}.",
            "voice": st.voice.upper(),
            "screens": ", ".join(name_of(graph, x) for x in st.screen_ids),
            "assumed": bool(st.assumed),
        }
        for _, st in sorted(graph.stories.items())
    ]
    return {
        "needs": needs,
        "blocks": blocks,
        "assets": assets,
        "motion_intents": motion,
        "stories": stories,
        "opportunities": opportunities,
    }
