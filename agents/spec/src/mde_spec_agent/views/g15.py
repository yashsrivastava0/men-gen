"""The G1.5 structure view (``GET /api/runs/{id}/views/g15``): the app map, roles and access, entities in plain words,
the inventory and the phase split for the owner's yes (doc 15 9.5). Pure: graph and derived plan in, plain dict out."""

from __future__ import annotations

from typing import Any

from mde_spec_core.config import default_config
from mde_spec_core.graph import SpecGraph

from ._data import words
from .common import app_map, entities_plain, inventory_rows, roles_plain
from .names import name_of, plain
from .phases import phase_split, screen_title


def _core_report(graph: SpecGraph, derived: Any) -> dict[str, Any] | None:
    plan = getattr(derived, "plan", None)
    if plan is None:
        return None
    r = plan.core_report
    w = words()["pull_in_rules"]
    pulled = [
        {
            "screen": screen_title(graph, derived, p.screen_id),
            "because": plain(w.get(p.rule, p.because), graph),
        }
        for p in r.extended_pulled_in
    ]
    total = r.compass_core + r.compass_extended
    text = (
        f"Compass marked {r.compass_core} of {total} screens as core. {r.core_in_release} of them are in the first release"
        + (f"; {len(r.core_not_in_release)} are not" if r.core_not_in_release else "")
        + f". {len(pulled)} screen{'s' if len(pulled) != 1 else ''} Compass left for later "
        + f"{'were' if len(pulled) != 1 else 'was'} pulled in because the first release needs "
        + f"{'them' if len(pulled) != 1 else 'it'}, and {r.spec_only_in_release} screens that Compass had no screen for "
        + "(sign-in, settings and similar) were added."
    )
    return {
        "compass_core": r.compass_core,
        "compass_extended": r.compass_extended,
        "core_in_release": r.core_in_release,
        "core_not_in_release": [screen_title(graph, derived, s) for s in r.core_not_in_release],
        "extended_pulled_in": pulled,
        "added_by_spec": r.spec_only_in_release,
        "plain": text,
        "by_part": [
            {"name": m.name, "core": m.core, "extended": m.extended, "in_release": m.in_release}
            for m in plan.modules
        ],
    }


def _consent(graph: SpecGraph, derived: Any) -> list[dict[str, Any]]:
    plan = getattr(derived, "plan", None)
    if plan is None:
        return []
    w = words()["consent_kinds"]
    out = []
    for c in plan.needs_consent:
        out.append(
            {
                "kind": c.kind,
                "app": graph.apps[c.app].name if c.app in graph.apps else "",
                "features": [name_of(graph, f) for f in c.feature_ids],
                "plain": w.get(c.kind, plain(c.why, graph)),
                "why": plain(c.why, graph),
            }
        )
    return out


def g15(graph: SpecGraph, derived: Any = None) -> dict[str, Any]:
    cap = default_config().scope.scope_cap_per_app_phase
    plan = getattr(derived, "plan", None)
    if plan is not None:
        cap = plan.cap
    w = words()
    phases = phase_split(graph, derived, cap)
    notes = [plain(n, graph) for n in (getattr(plan, "notes", None) or [])]
    return {
        "app_map": app_map(graph, derived),
        "roles": roles_plain(graph, derived),
        "entities": entities_plain(graph),
        "inventory": inventory_rows(graph, derived),
        "phases": phases,
        "pull_in_rule": " ".join(str(w["pull_in_rule"]).split()),
        "compass_core": _core_report(graph, derived),
        "needs_consent": _consent(graph, derived),
        "consent_required": bool(getattr(plan, "needs_consent", None)),
        "everything_in_one_release": {
            "available": True,
            "warning": w["everything_in_one_release"],
            "chosen": bool(getattr(plan, "everything_in_one_release", False)),
        },
        "notes": notes,
        "limit_per_app": cap,
        "apps_over_limit": [
            graph.apps[a].name if a in graph.apps else a
            for a, c in (getattr(plan, "per_app", None) or {}).items()
            if c.over_cap
        ],
    }
