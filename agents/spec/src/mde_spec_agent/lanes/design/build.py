"""From DSGN's output to a graph patch: Direction nodes, tokens BY CODE, unbound asset needs, and the flags for G2.

The model's words become Open nodes; code builds the DTCG tokens of every direction (contrast asserted in light and
dark, a Locked brand colour kept exactly, `ext.*` never set), writes the brand needs as a baseline and records the
capability tiers of risky capabilities the Brief or the choices imply."""

from __future__ import annotations

import re
from typing import Any

from mde_spec_agent.lanes.design._data import design_data
from mde_spec_agent.lanes.design._tokens import tokens_for
from mde_spec_agent.lanes.design.brief import BrandFacts
from mde_spec_agent.lanes.design.dsgn._node import CALL_ID, direction_id, to_direction
from mde_spec_agent.lanes.design.dsgn.schema import DsgnIn, DsgnOut
from mde_spec_agent.run.patch import Patch, merge_patches, node_patch
from mde_spec_core import assets, platform_data, vocab
from mde_spec_core.config import SpecConfig
from mde_spec_core.graph import AssetNeed, Direction, Prov, SpecGraph

_PREFERRED_POLICY = ("generate", "licensed_stock", "none", "user_upload")


def _next_asset_number(graph: SpecGraph) -> int:
    nums = [int(m.group(1)) for i in graph.assets if (m := re.fullmatch(r"ASSET-S(\d+)", i))]
    return max(nums, default=0) + 1


def model_need(inp: DsgnIn, a: Any, n: int, *, degraded: bool, from_fallback: bool) -> AssetNeed:
    rules = assets.asset_rules()
    spec = vocab.asset_kinds()[a.kind]
    policy = next(p for p in _PREFERRED_POLICY if p in spec.source_policies)
    return AssetNeed(
        id=f"ASSET-S{n:03d}",
        degraded=degraded,
        assumed=from_fallback,
        kind=a.kind,
        purpose=a.purpose,
        subject=a.subject.strip(),
        count=a.count,
        avoid=[x.strip() for x in a.avoid],
        source_policy=policy,  # type: ignore[arg-type]
        license_rule=rules["license_rule_by_policy"][policy] if spec.needs_license_rule else "",
        alt_text_rule=rules["alt_text_rule"] if spec.needs_alt_text else "",
        fallback=spec.fallback,
        used_by=[a.app] if a.app else list(inp.app_ids),
        bound=False,
        provenance=[Prov(kind="code" if from_fallback else "model", ref=CALL_ID)],
    )


def asset_nodes(
    graph: SpecGraph, inp: DsgnIn, out: DsgnOut, *, degraded: bool, from_fallback: bool
) -> list[AssetNeed]:
    """The brand needs (code, `assets.unbound_asset_needs`), then the model's needs. Bound needs are NOT made here: they
    come from media fields and hero image briefs, which exist only after the Freeze and the hero step."""
    start = _next_asset_number(graph)
    base = assets.unbound_asset_needs(graph, has_logo=inp.has_logo, start=start)
    for b in base:
        b.degraded = degraded
    n = start + len(base)
    extra = [
        model_need(inp, a, n + i, degraded=degraded, from_fallback=from_fallback)
        for i, a in enumerate(out.assets)
    ]
    return [*base, *extra]


def capability_flags(
    graph: SpecGraph, dirs: list[Direction], needs: list[AssetNeed]
) -> list[dict[str, Any]]:
    """Capabilities the Brief implies (a feature names one) or a choice implies (an asset kind, the ambition, the depth),
    with their tier from the platform capability sheet. Only risky ones (tier B or C, or not recommended) are flagged."""
    sheet = platform_data.default_capability_sheet()
    imp = design_data().capability_implications
    implied: dict[str, set[str]] = {}

    def add(cap: str, source: str) -> None:
        implied.setdefault(cap, set()).add(source)

    for f in sorted(graph.features.values(), key=lambda x: x.id):
        if f.capability:
            add(f.capability, f"feature {f.id}")
    for d in dirs:
        for cap in imp.ambition.get(d.ambition, []):
            add(cap, f"direction {d.id} ({d.ambition} ambition)")
        if d.depth in imp.depth:
            add(imp.depth[d.depth], f"direction {d.id} ({d.depth} depth)")
        if d.dark_stance in imp.dark_stance:
            add(imp.dark_stance[d.dark_stance], f"direction {d.id}")
    for n in needs:
        if n.kind in imp.asset_kind:
            add(imp.asset_kind[n.kind], f"asset {n.id}")
    out: list[dict[str, Any]] = []
    for cap_id in sorted(implied):
        try:
            c = sheet.get(cap_id)
        except KeyError:
            out.append(
                {
                    "kind": "capability_unknown",
                    "capability": cap_id,
                    "implied_by": sorted(implied[cap_id]),
                }
            )
            continue
        if c.tier in ("B", "C") or not c.production_recommended:
            out.append(
                {
                    "kind": "capability_risk",
                    "capability": c.id,
                    "label": c.label,
                    "tier": c.tier,
                    "expo_go": c.expo_go,
                    "production_recommended": c.production_recommended,
                    "implied_by": sorted(implied[cap_id]),
                    "note": c.note,
                }
            )
    return out


def token_flags(
    dirs: list[Direction], toks: dict[str, dict[str, Any]], config: SpecConfig, profile: str
) -> list[dict[str, Any]]:
    """Honest flags from the token reports: derived text on a brand colour, script fallbacks, dark-mode parity (deep)."""
    out: list[dict[str, Any]] = []
    statements: dict[str, list[str]] = {}
    notes: dict[tuple[str, ...], list[str]] = {}
    need = config.checks.wcag_body_text_ratio
    for d in dirs:
        rep = toks[d.id]["report"]
        for s in rep.get("statements", []):
            if rep.get("brand_locked"):
                statements.setdefault(s, []).append(d.id)
        found = set(rep.get("flags", []))
        found |= {
            f"script_without_font:{row['script']}"
            for row in rep.get("scripts", [])
            if row.get("source") == "missing"
        }
        if found:
            notes.setdefault(tuple(sorted(found)), []).append(d.id)
        if profile == "deep":
            pairs = toks[d.id]["contrast"]
            per = {
                s: [p for p in pairs if p["scheme"] == s and p["body"]] for s in ("light", "dark")
            }
            weak = [s for s, ps in per.items() if not ps or min(p["ratio"] for p in ps) < need]
            if weak or len(per["light"]) != len(per["dark"]):
                out.append(
                    {
                        "kind": "dark_mode_parity",
                        "direction": d.id,
                        "schemes_below_target": weak,
                        "pairs": {s: len(ps) for s, ps in per.items()},
                    }
                )
    for text, ids in sorted(statements.items()):
        out.append(
            {"kind": "brand_colour_text_derived", "directions": sorted(set(ids)), "statement": text}
        )
    for key, ids in sorted(notes.items()):
        out.append({"kind": "token_notice", "directions": sorted(set(ids)), "notes": list(key)})
    return out


def build_patch(
    graph: SpecGraph,
    inp: DsgnIn,
    out: DsgnOut,
    brand: BrandFacts,
    config: SpecConfig,
    *,
    flagged: bool,
    from_fallback: bool,
    reason: str = "",
) -> Patch:
    brand_hex = brand.colour if inp.brand_locked else None
    scripts = tuple(inp.scripts)
    dirs = [
        to_direction(
            inp,
            d,
            direction_id(i),
            brand_colour=brand_hex,
            user_ref=brand.ref,
            from_fallback=from_fallback,
        )
        for i, d in enumerate(out.directions)
    ]
    for d in dirs:
        d.degraded = flagged
    toks = {d.id: tokens_for(d, brand_hex, scripts) for d in dirs}
    needs = asset_nodes(graph, inp, out, degraded=flagged, from_fallback=from_fallback)
    flags: list[dict[str, Any]] = []
    if flagged:
        flags.append(
            {
                "kind": "degraded_node",
                "call": CALL_ID,
                "nodes": [d.id for d in dirs],
                "reason": reason
                or "model call failed after its re-ask; deterministic directions used",
            }
        )
    flags += token_flags(dirs, toks, config, inp.profile)
    flags += capability_flags(graph, dirs, needs)
    patch = merge_patches(
        [node_patch("directions", dirs, tokens=toks), node_patch("assets", needs)]
    )
    patch["flags"] = flags
    return patch
