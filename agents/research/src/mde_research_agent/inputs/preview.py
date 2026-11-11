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

LIGHT_BELOW = 12  # doc 13 section 4.2: the load below which a tiny app may use light mode


def compass_info(data: dict[str, Any]) -> dict[str, Any]:
    """Information only (never an input to the load): the Compass interview size and whether it ran degraded."""
    meta = (data.get("brief") or {}).get("meta") or {}
    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
    return {
        "tier": str(plan.get("interview_budget") or "unknown"),
        "degraded": bool(meta.get("degraded")),
    }


def light_mode(inp: ResearchInput, signals: list[str]) -> bool:
    """Doc 13 section 4.2: a tiny app with nothing that needs outside facts."""
    load = inp.load.score if inp.load else 0
    rule_facts = [
        f for f in inp.facts if (f.kind or "") in ("rule", "compliance", "integration_detail")
    ]
    has_place = bool(inp.geography.country or inp.geography.cities)
    return (
        load < LIGHT_BELOW
        and not signals
        and not inp.references
        and not inp.integrations
        and not rule_facts
        and not has_place
    )


def trust_decision(inp: ResearchInput, signals: list[str]) -> dict[str, Any]:
    """Whether the extra trust topics (privacy, security, store rules) run, and a plain reason why or why not."""
    klass = inp.load.load_class if inp.load else "standard"
    if klass == "deep":
        return {
            "on": True,
            "why": "This is a big app: privacy, security and store rules are always checked.",
        }
    if inp.sensitive_flags:
        return {
            "on": True,
            "why": "The Brief marks sensitive data, so privacy and security rules are checked.",
        }
    return {"on": False, "why": "Nothing in the Brief needs the extra privacy and security checks."}


def build_preview(
    data: dict[str, Any], inp: ResearchInput, catalog: Catalog, estimates: Any
) -> dict[str, Any]:
    plan = build_plan(inp, catalog)
    signals: list[str] = []
    light = light_mode(inp, signals)
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
        "trust": trust_decision(inp, signals),
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
