"""The zero-cost preview of an input: the real intake, the load score and the deterministic planner.

No model call, no web call, no spend. It tells the person what a run would do before they pay for it: the size class
(Tiny, Small, Medium, Big), whether the app is small enough for light mode, which extra checks (trust) will run and
why, the planned topics by job, the warnings about the input, and the estimate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.signals import compute_signals, trust_decision


def compass_info(data: dict[str, Any]) -> dict[str, Any]:
    """Information only (never an input to the load): the Compass interview size and whether it ran degraded."""
    meta: dict[str, Any] = (data.get("brief") or {}).get("meta") or {}
    plan: dict[str, Any] = meta["plan"] if isinstance(meta.get("plan"), dict) else {}
    return {
        "tier": str(plan.get("interview_budget") or "unknown"),
        "degraded": bool(meta.get("degraded")),
    }


def build_preview(
    data: dict[str, Any], inp: ResearchInput, catalog: Catalog, estimates: Any
) -> dict[str, Any]:
    plan = build_plan(inp, catalog)
    signals = list(compute_signals(inp, catalog.signals))
    light = plan.light
    load = inp.load
    klass = load.load_class if load else "standard"
    size = estimates.size_class(klass, light=light)
    by_job: dict[str, list[str]] = {}
    for t in plan.targets:
        by_job.setdefault(t.job, []).append(t.label)
    q = inp.quality
    warnings: list[dict[str, str]] = []
    if q.requests.template or q.requests.thin:
        warnings.append(
            {
                "code": "template_requests",
                "text": f"{q.requests.template + q.requests.thin} of the Brief's research requests look like "
                "filler, so the topics are rebuilt from the Brief itself.",
            }
        )
    if q.geo_uncertain or inp.geography.uncertain:
        warnings.append(
            {
                "code": "weak_geography",
                "text": "The place is not certain in the Brief; local rules and providers may be less exact.",
            }
        )
    if q.degraded:
        warnings.append(
            {
                "code": "degraded",
                "text": "Compass finished this Brief with some model steps degraded.",
            }
        )
    cats = Counter((f.kind or "other") for f in inp.facts)
    live_features = [f for f in inp.features if not f.duplicate_of and f.scope_status != "out"]
    info = compass_info(data)
    return {
        "idea": {
            "summary": inp.idea.summary,
            "category": inp.idea.category,
            "subcategory": inp.idea.subcategory,
        },
        "place": {
            "country": inp.geography.country,
            "cities": list(inp.geography.cities),
            "uncertain": bool(inp.geography.uncertain),
        },
        "languages": list(inp.locales.codes),
        "counts": {
            "features": len(live_features),
            "roles": len([r for r in inp.roles if not r.duplicate_of]),
            "apps": len(inp.apps),
            "integrations": len(inp.integrations),
            "references": len(inp.references),
            "facts": dict(sorted(cats.items())),
        },
        "compass": info,
        "load": {
            "score": load.score if load else 0,
            "class": klass,
            "components": load.components.model_dump() if load else {},
        },
        "size_class": size,
        "size_label": estimates.label(size),
        "light": light,
        "signals": signals,
        "trust": dict(
            zip(("on", "why"), trust_decision(inp, tuple(signals), catalog), strict=True)
        ),
        "topics": {
            "total": len(plan.targets),
            "by_job": {
                job: {"count": len(labels), "labels": labels} for job, labels in by_job.items()
            },
            "not_run": [t.label for t in plan.unrun],
        },
        "declined": list(inp.declined),
        "dropped": [{"label": d.label, "reason": d.reason} for d in plan.dropped],
        "warnings": warnings,
        "requests": {
            "trusted": q.requests.trusted,
            "template": q.requests.template,
            "thin": q.requests.thin,
        },
        "estimate": estimates.for_class(size),
        "program_id": inp.program_id,
    }
