"""The zero-cost preview of a run (``GET /api/runs/{id}/preview``): size, phases, estimated cost and the refusal."""

from __future__ import annotations

from typing import Any

from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import SpecGraph
from mde_spec_core.profiles import ConsumerProfile

from ._data import words
from .estimate import counts_of, estimate, prices_from_profile
from .names import join_words, usd_text
from .phases import phase_split


def _reason(table: dict[str, str], raised: str) -> str:
    """``standard:apps`` or ``deep:modules`` (the level it raised to, then the cause) into plain words."""
    level, _, cause = raised.partition(":")
    return (
        table.get(f"{cause}_{level}")
        or table.get(cause)
        or table.get(raised)
        or cause.replace("_", " ")
    )


def profile_block(decision: Any, graph: SpecGraph | None = None) -> dict[str, str]:
    """``{id, label, why}``: the size of the app and why, from the profile decision (or the graph's own profile)."""
    w = words()
    pid = (
        decision.profile if decision is not None else (graph.meta.profile if graph else "standard")
    )
    meta = w["profiles"].get(pid, {"label": pid, "note": ""})
    parts = []
    if decision is not None:
        floor_label = w["profiles"].get(decision.floor, {}).get("label", decision.floor)
        parts.append(f"The tier you chose sets the floor at {floor_label.split(':')[0].lower()}.")
        if decision.raised_by:
            reasons = [_reason(w["profile_reasons"], r) for r in decision.raised_by]
            parts.append(f"It was raised because {join_words(reasons)}.")
        else:
            parts.append("Nothing in the app raised it above that.")
    parts.append(meta["note"][:1].upper() + meta["note"][1:] + ".")
    return {"id": pid, "label": meta["label"], "why": " ".join(parts)}


def preview(
    derived_or_graph: Any,
    profile_decision: Any,
    writer_profile: ConsumerProfile,
    config: SpecConfig,
    remaining_micros: int | None,
) -> dict[str, Any]:
    """The documented preview shape; ``refused`` is true when the estimate's upper bound exceeds the money left."""
    source = derived_or_graph
    graph: SpecGraph = source if isinstance(source, SpecGraph) else source.graph
    est = estimate(source, writer_profile, config, prices_from_profile(writer_profile))
    inventory = getattr(source, "inventory", None)
    inv_count = len(inventory.screens) if inventory is not None else len(graph.screens)
    derived = None if isinstance(source, SpecGraph) else source
    phases = [
        {"name": p["name"], "screens": p["count"], "note": p["why"]}
        for p in phase_split(graph, derived, config.scope.scope_cap_per_app_phase)
    ]
    refused = remaining_micros is not None and est["high_micros"] > remaining_micros
    out: dict[str, Any] = {
        "profile": profile_block(profile_decision or getattr(source, "decision", None), graph),
        "inventory_count": inv_count,
        "phases": phases,
        "estimate": {
            "low_micros": est["low_micros"],
            "high_micros": est["high_micros"],
            "minutes_low": est["minutes_low"],
            "minutes_high": est["minutes_high"],
            "priced": est["priced"],
        },
        "calls_by_step": est["calls_by_step"],
        "refused": bool(refused),
        "release_screens": counts_of(source).release_screens,
    }
    if refused:
        assert remaining_micros is not None
        out["refusal_message"] = (
            f"This run could cost up to {usd_text(est['high_micros'], up=True)}, "
            f"but only {usd_text(max(0, remaining_micros))} is left of the money cap. "
            "Choose a smaller input, or run it offline, which costs nothing."
        )
    return out
