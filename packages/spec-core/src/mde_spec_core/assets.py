"""Asset needs made by code (doc 17 2.7, doc 18 5 B4). Data-bound needs come from media fields and the hero image
briefs; unbound needs the helpers know (brand mark, app icon, adaptive icon, splash as `brand_mark` needs) are a
baseline for the Design call, which writes the others (empty-state art, clips, audio and haptic cues).

A need states kind, purpose, subject, count and an avoid-list. It never states lighting, placement, size, crop or
variant (`lint_brief` checks that). No model, no network; the rules are data: `data/pools/asset_rules.yaml`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache
from typing import Any

import yaml

from . import vocab
from .config import DATA_DIR
from .graph import AssetNeed, Entity, FieldDef, Prov, SpecGraph

RULES_PATH = DATA_DIR / "pools" / "asset_rules.yaml"


@cache
def asset_rules() -> dict[str, Any]:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


def lint_brief(*texts: str) -> list[str]:
    """Words that belong to the front end (lighting, placement, crop, size, variant) found in a brief."""
    found: list[str] = []
    for w in asset_rules()["forbidden_brief_words"]:
        pat = r"(?<![\w])" + re.escape(w) + r"(?![\w])"
        if any(re.search(pat, t, re.I) for t in texts):
            found.append(w)
    return found


def _next_number(graph: SpecGraph) -> int:
    nums = [int(m.group(1)) for i in graph.assets if (m := re.fullmatch(r"ASSET-S(\d+)", i))]
    return max(nums, default=0) + 1


def _kind_for(f: FieldDef) -> str:
    rules = asset_rules()
    for w in f.key.lower().split("_"):
        kind = rules["kind_by_key_word"].get(w)
        if kind:
            break
    else:
        kind = rules["default_kind"]
    spec = vocab.asset_kinds().get(kind)
    if spec is None or not spec.data_bound_capable:
        return str(rules["default_kind"])
    return str(kind)


def _policy_for(kind: str) -> str:
    allowed = vocab.asset_kinds()[kind].source_policies
    return "user_upload" if "user_upload" in allowed else allowed[0]


def _split_brief(brief: str) -> tuple[str, list[str]]:
    """`subject; count: n; avoid: a, b` -> (subject, avoid list). A plain brief is just a subject."""
    subject, avoid = brief, []
    parts = [p.strip() for p in brief.split(";")]
    subject = parts[0]
    for p in parts[1:]:
        if p.lower().startswith("avoid:"):
            avoid = [a.strip() for a in p.split(":", 1)[1].split(",") if a.strip()]
    return subject, avoid


def _humanise(key: str) -> str:
    return key.replace("_", " ")


def _screens_binding(graph: SpecGraph, ref: str) -> list[str]:
    return sorted(s.id for s in graph.screens.values() for r in s.regions if ref in r.bind)


def data_bound_asset_needs(graph: SpecGraph, *, start: int | None = None) -> list[AssetNeed]:
    """One need per media field, sorted by entity and field: kind from the field key, subject, count and avoid-list
    from the hero image briefs (else from the entity and field names), source policy, licence, alt-text rule and
    fallback from the kind. Ids continue after the existing `ASSET-S` ids (or from `start`)."""
    rules = asset_rules()
    n = start if start is not None else _next_number(graph)
    briefs: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for hid in sorted(graph.hero):
        rec = graph.hero[hid]
        counts[rec.entity] = counts.get(rec.entity, 0) + 1
        if rec.image_brief:
            briefs.setdefault(rec.entity, []).append(rec.image_brief)
    out: list[AssetNeed] = []
    for _, e in sorted(graph.entities.items()):
        if e.later or e.status != "in":
            continue
        first = True
        for f in sorted((x for x in e.fields if x.media), key=lambda x: x.key):
            out.append(
                _bound_need(graph, e, f, n, first, briefs.get(e.id, []), counts.get(e.id, 0), rules)
            )
            first = False
            n += 1
    return out


def _bound_need(
    graph: SpecGraph,
    e: Entity,
    f: FieldDef,
    n: int,
    first: bool,
    briefs: list[str],
    n_records: int,
    rules: dict[str, Any],
) -> AssetNeed:
    kind = _kind_for(f)
    spec = vocab.asset_kinds()[kind]
    policy = _policy_for(kind)
    avoid = list(rules["avoid_by_kind"].get(kind, rules["avoid_default"]))
    if briefs:
        subject, extra = _split_brief(briefs[0])
        avoid += [a for a in extra if a not in avoid]
        count = min(int(rules["data_bound_count_cap"]), len(briefs))
    else:
        subject = rules["subject_template"].format(entity=e.name, field=_humanise(f.key))
        count = min(int(rules["data_bound_count_cap"]), max(n_records, 1))
    ref = f"{e.id}.{f.key}"
    return AssetNeed(
        id=f"ASSET-S{n:03d}",
        kind=kind,
        purpose=rules["purpose_primary_entity"]
        if (e.klass == "primary" and first)
        else rules["purpose_other"],
        subject=subject,
        count=count,
        avoid=avoid,
        source_policy=policy,  # type: ignore[arg-type]
        license_rule=rules["license_rule_by_policy"][policy] if spec.needs_license_rule else "",
        alt_text_rule=rules["alt_text_rule"] if spec.needs_alt_text else "",
        fallback=spec.fallback,
        used_by=[ref, *_screens_binding(graph, ref)],
        bound=True,
        provenance=[Prov(kind="code", ref="assets.data_bound")],
    )


def unbound_asset_needs(
    graph: SpecGraph, *, has_logo: bool = False, start: int | None = None
) -> list[AssetNeed]:
    """The brand needs of every app as `brand_mark` needs: the mark itself, the app icon, the adaptive icon and the
    launch image (phone and tablet apps; a web app gets the mark only). `has_logo` says the user supplies a logo
    (source policy `user_upload`, else `generate`). A baseline for the Design call, which writes the rest."""
    rules = asset_rules()
    n = start if start is not None else _next_number(graph)
    out: list[AssetNeed] = []
    spec = vocab.asset_kinds()["brand_mark"]
    policy = "user_upload" if has_logo else "generate"
    for _, app in sorted(graph.apps.items()):
        if app.status != "in":
            continue
        for key in rules["unbound_by_surface"][app.surface]:
            row = rules["unbound"][key]
            out.append(
                AssetNeed(
                    id=f"ASSET-S{n:03d}",
                    kind="brand_mark",
                    purpose=row["purpose"],
                    subject=row["subject"].format(app=app.name),
                    count=int(row["count"]),
                    avoid=list(row["avoid"]),
                    source_policy=policy,  # type: ignore[arg-type]
                    license_rule=rules["license_rule_by_policy"][policy]
                    if spec.needs_license_rule
                    else "",
                    alt_text_rule=rules["alt_text_rule"] if spec.needs_alt_text else "",
                    fallback=rules["unbound"]["fallback_when_missing"],
                    used_by=[app.id],
                    bound=False,
                    provenance=[Prov(kind="code", ref=f"assets.unbound.{key}")],
                )
            )
            n += 1
    return out


def asset_needs(graph: SpecGraph, *, has_logo: bool = False) -> list[AssetNeed]:
    """Data-bound needs, then the unbound brand needs, with consecutive ids."""
    bound = data_bound_asset_needs(graph)
    nxt = _next_number(graph) + len(bound)
    return bound + unbound_asset_needs(graph, has_logo=has_logo, start=nxt)


def missing_media_needs(graph: SpecGraph, needs: Iterable[AssetNeed]) -> list[str]:
    """Media fields with no need that names them (V-41's check, usable before the needs are added)."""
    used = {u for n in [*graph.assets.values(), *needs] for u in n.used_by}
    return [
        f"{e.id}.{f.key}"
        for e in graph.entities.values()
        for f in e.fields
        if f.media and f"{e.id}.{f.key}" not in used
    ]
