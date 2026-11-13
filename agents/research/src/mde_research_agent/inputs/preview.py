"""The zero-cost preview of an input: the real intake, the load score and the deterministic planner.

No model call, no web call, no spend. It tells the person what a run would do before they pay for it: the size class
(Tiny, Small, Medium, Big), whether the app is small enough for light mode, which extra checks (trust) will run and
why, the planned topics by job, the warnings about the input, and the estimate.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mde_research_agent.catalog.models import Catalog
from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.plan import Plan
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.candidates import trusted_requests
from mde_research_agent.plan.signals import compute_signals, trust_decision

TEXTS_FILE = Path(__file__).resolve().parent.parent / "data" / "preview.yaml"


@lru_cache(maxsize=1)
def texts() -> dict[str, Any]:
    """The one-line reasons of the preview (``data/preview.yaml``)."""
    raw = yaml.safe_load(TEXTS_FILE.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _text(group: str, key: str = "", **fill: object) -> str:
    """One line from ``data/preview.yaml``: ``group.key`` (or the plain string at ``group``), filled in."""
    entry: Any = texts().get(group)
    if isinstance(entry, dict):
        entry = entry.get(key)
    return str(entry).format(**fill) if isinstance(entry, str) else ""


def request_rows(inp: ResearchInput, plan: Plan) -> list[dict[str, Any]]:
    """Every Brief research request with the topic(s) that carry it, or why none does.

    ``status`` is ``mapped`` (a topic that runs carries it), ``not_run`` (its topic is planned but ranked below what this
    size runs), ``dropped`` (its topic was left out, ``why`` says why), ``unmapped`` (no planned topic fits it) or
    ``ignored`` (the request is filler or too thin, so the planner did not use it)."""
    usable = {r.id for r in trusted_requests(inp)}
    rows: list[dict[str, Any]] = []
    for r in inp.requests:
        running = [t for t in plan.targets if r.id in t.links.requests]
        waiting = [t for t in plan.unrun if r.id in t.links.requests]
        dropped = [d for d in plan.dropped if r.id in d.links.requests]
        if r.id not in usable:
            status, reason = "ignored", "thin" if r.thin and r.origin != "template" else "template"
            text = _text("request", reason)
        elif running:
            status, reason, text = "mapped", "mapped", _text("request", "mapped")
        elif waiting:
            status, reason, text = "not_run", "not_run", _text("request", "not_run")
        elif dropped:
            why = _text("dropped", dropped[0].reason)
            status, reason, text = (
                "dropped",
                dropped[0].reason,
                _text("request", "dropped", why=why),
            )
        else:
            status, reason, text = "unmapped", "unmapped", _text("request", "unmapped")
        rows.append(
            {
                "id": r.id,
                "topic": r.topic,
                "status": status,
                "reason": reason,
                "text": text,
                "topics": [
                    {"id": t.id, "label": t.label, "job": t.job} for t in (running or waiting)
                ],
            }
        )
    return rows


def not_run_rows(
    plan: Plan, catalog: Catalog, load_class: str = "standard"
) -> list[dict[str, Any]]:
    """Topics that were planned but are not run at this size, each with its rank, a one-line reason and what running it
    would cost (``plan.topic_cost`` in the data: searches and USD), so the caps are visibly data-driven."""
    total = len(plan.targets) + len(plan.unrun)
    cost = catalog.plan.topic_cost
    cap = catalog.ceilings.for_class(load_class).soft.targets  # type: ignore[arg-type]
    return [
        {
            "id": t.id,
            "label": t.label,
            "job": t.job,
            "priority": t.priority,
            "requests": list(t.links.requests),
            "reason": "over_cap",
            "est_searches": cost.searches,
            "est_usd_micros": cost.usd_micros,
            "text": _text("not_run", "", rank=len(plan.targets) + i, total=total, cap=cap),
        }
        for i, t in enumerate(plan.unrun, start=1)
    ]


def not_run_cost(plan: Plan, catalog: Catalog) -> dict[str, Any]:
    """What running every topic that is not run would add: the sum of the per-topic estimates."""
    cost = catalog.plan.topic_cost
    n = len(plan.unrun)
    return {
        "topics": n,
        "est_searches": round(n * cost.searches, 1),
        "est_usd_micros": n * cost.usd_micros,
    }


def risk_topics(plan: Plan) -> dict[str, Any]:
    """The topics kept outside the cap because they belong to a risk class, by class (the Brief decides how many)."""
    by_class = Counter(t.risk for t in plan.targets if t.risk)
    return {"total": sum(by_class.values()), "by_class": dict(sorted(by_class.items()))}


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
    binding = q.input_binding
    if binding != "verified":
        warnings.append({"code": binding, "text": _text("binding", binding)})
    cats = Counter((f.kind or "other") for f in inp.facts)
    rows = request_rows(inp, plan)
    by_status = Counter(r["status"] for r in rows)
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
            "countries": [{"name": c.name, "code": c.code} for c in inp.geography.countries],
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
        "compass": {**info, "format": q.compass_format},
        "input_binding": binding,
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
            "cap": catalog.ceilings.for_class(klass).soft.targets,
        },
        "declined": list(inp.declined),
        "not_run": not_run_rows(plan, catalog, klass),
        "not_run_cost": not_run_cost(plan, catalog),
        "risk_topics": risk_topics(plan),
        "dropped": [
            {
                "label": d.label,
                "reason": d.reason,
                "text": _text("dropped", d.reason),
                "job": d.job,
                "requests": list(d.links.requests),
            }
            for d in plan.dropped
        ],
        "warnings": warnings,
        "requests": {
            "trusted": q.requests.trusted,
            "template": q.requests.template,
            "thin": q.requests.thin,
            "mapped": by_status.get("mapped", 0),
            "not_run": by_status.get("not_run", 0),
            "dropped": by_status.get("dropped", 0),
            "unmapped": by_status.get("unmapped", 0),
            "items": rows,
        },
        "estimate": estimates.for_class(size),
        "program_id": inp.program_id,
    }
