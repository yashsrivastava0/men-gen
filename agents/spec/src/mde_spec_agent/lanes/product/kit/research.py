"""Reading Research for a call: `pack(research, need_card)` for a need, as plain text lines (untrusted data).

`ctx.research` may be the raw `research.json` mapping, a typed `ResearchView` (compass-intake) or an object that carries
one as `.view` (what `derive.research_adapter` builds). A thin or missing Research reads as an empty pack and the call
says so, so every research-like claim is marked assumed (doc 15 3.5).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mde_compass_intake import ResearchView, pack, read_research
from mde_spec_core.callkit.fusion import input_cap
from mde_spec_core.callkit.prompt import chars_per_token
from mde_spec_core.config import SpecConfig
from mde_spec_core.profiles import ConsumerProfile

from .slices import chunk_lines

RESEARCH_SHARE_OF_INPUT = 0.25  # of the call input cap: what the research pack may take
PACK_UNIT_CHARS = 4  # `pack` counts characters over four for its token budget


@dataclass
class ResearchRead:
    lines: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)  # "RI-1=official"
    thin: bool = True
    note: str = ""


def research_view(research: Any) -> ResearchView | None:
    if research is None:
        return None
    if isinstance(research, ResearchView):
        return research
    view = getattr(research, "view", None)
    if isinstance(view, ResearchView):
        return view
    return None


def as_view(research: Any) -> ResearchView | None:
    """A typed view of whatever `ctx.research` holds (a view, an adapter around one, or the raw file)."""
    v = research_view(research)
    if v is None and isinstance(research, Mapping):
        v = read_research(research)
    return v


def research_mapping(research: Any) -> Mapping[str, Any] | None:
    """The research file as a plain mapping (what `pack` reads), rebuilt from a typed view when that is all we have."""
    if research is None:
        return None
    if isinstance(research, Mapping):
        return research
    raw = getattr(research, "raw", None)
    if isinstance(raw, Mapping):
        return raw
    v = research_view(research)
    if v is None:
        return None
    return {
        "schema": v.schema_name,
        "program_id": v.program_id,
        "brief_hash": v.brief_hash,
        "items": [
            {
                "id": i.id,
                "job": i.job,
                "kind": i.kind,
                "label": i.label,
                "claim": i.claim,
                "evidence": i.evidence,
                "tier": i.tier,
                "flags": list(i.flags),
                "applies_to": [
                    {"kind": a.kind, "id": a.id, "relation": a.relation} for a in i.applies_to
                ],
                "rank": i.rank,
                "store": i.store,
                "sources": [{"id": s} for s in i.source_ids],
            }
            for i in v.items
        ],
        "lane_decisions": [
            {"id": d.id, "lane": d.lane, "text": d.text, "item_ids": list(d.item_ids), "gap": d.gap}
            for d in v.lane_decisions
        ],
        "dossiers": [
            {
                "id": d.id,
                "provider": d.provider,
                "capability": d.capability,
                "integrations": list(d.integrations),
                "needs": list(d.needs),
                "item_ids": list(d.item_ids),
                "fields": {
                    k: {
                        "value": f.value,
                        "null_reason": f.null_reason,
                        "evidence": f.evidence,
                        "item_ids": list(f.item_ids),
                    }
                    for k, f in d.fields.items()
                },
            }
            for d in v.dossiers
        ],
        "platform_constraints": [
            {
                "item_id": p.item_id,
                "capability": p.capability,
                "platform": p.platform,
                "rule": p.rule,
                "effect_on_build": p.effect_on_build,
                "evidence": p.evidence,
            }
            for p in v.platform_constraints
        ],
        "confirm": [
            {"item_id": c.item_id, "why": c.why, "rank": c.rank, "class": c.kind} for c in v.confirm
        ],
        "conflicts": [
            {"topic": c.topic, "item_ids": list(c.item_ids), "note": c.note} for c in v.conflicts
        ],
        "slices": {lane: {"item_ids": list(ids)} for lane, ids in v.slices.items()},
    }


def is_thin(research: Any) -> bool:
    v = research_view(research)
    if v is not None:
        return v.thin
    return not (isinstance(research, Mapping) and research.get("items"))


def pack_budget(profile: ConsumerProfile, config: SpecConfig) -> int:
    """The `pack` token budget (characters over four): a quarter of the call input cap, converted with the profile's
    characters-per-token factor so a script that costs more tokens per character is not undercounted."""
    tokens = int(input_cap(profile, config) * RESEARCH_SHARE_OF_INPUT)
    return max(500, int(tokens * chars_per_token(profile) / PACK_UNIT_CHARS))


def read_for(
    research: Any,
    brief_ids: Sequence[str],
    *,
    lane: str,
    profile: ConsumerProfile,
    config: SpecConfig,
    max_index: int = 60,
) -> ResearchRead:
    """The research for one need, as lines. Never raises; thin research is an empty read with a note."""
    mapping = research_mapping(research)
    if mapping is None or is_thin(research):
        why = "no research was available" if mapping is None else "research is thin or incomplete"
        return ResearchRead(
            note=f"{why}: use the libraries and the Brief only, mark every research-like claim assumed"
        )
    card = {
        "brief_ids": sorted(set(brief_ids)),
        "lane": lane,
        "token_budget": pack_budget(profile, config),
    }
    result = pack(mapping, card)
    packs = result.get("packs", [])
    lines: list[str] = []
    ids: list[str] = []
    labels: list[str] = []
    if packs:
        first = packs[0]
        for bucket in (
            "lane_decisions",
            "items",
            "dossiers",
            "platform_constraints",
            "confirm",
            "conflicts",
        ):
            for row in first.get(bucket, []):
                lines.append(
                    f"{bucket.upper()}: "
                    + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                )
                if bucket == "items" and row.get("id"):
                    ids.append(str(row["id"]))
                    labels.append(f"{row['id']}={row.get('evidence') or 'unverified'}")
    for row in result.get("index", [])[:max_index]:
        lines.append(
            f"INDEX {row.get('id')} [{row.get('kind')}, {row.get('evidence')}] {row.get('label')}"
        )
    note = (
        ""
        if lines
        else "research has nothing for this need: mark every research-like claim assumed"
    )
    return ResearchRead(lines=lines, ids=ids, labels=labels, thin=not ids, note=note)


def dossiers_of(research: Any) -> list[Mapping[str, Any]]:
    """Dossier rows (id, provider, capability, needs, integrations) for provider names and the closed list."""
    mapping = research_mapping(research)
    if mapping is None:
        return []
    return [d for d in mapping.get("dossiers", []) if isinstance(d, Mapping)]


def chunks(lines: Sequence[str]) -> list[str]:
    return chunk_lines(lines)


def digest(research: Any, *, max_items: int) -> ResearchRead:
    """A short digest of the whole Research for the plan: lane decisions and the best items by rank, as lines."""
    mapping = research_mapping(research)
    if mapping is None or is_thin(research):
        why = "no research was available" if mapping is None else "research is thin or incomplete"
        return ResearchRead(
            note=f"{why}: use the libraries and the Brief only, mark every research-like claim assumed"
        )
    lines: list[str] = []
    for d in mapping.get("lane_decisions", []):
        gap = f" GAP: {d.get('gap')}" if d.get("gap") else ""
        lines.append(
            f"DECISION {d.get('lane')}: {d.get('text')} [{d.get('best_evidence') or '-'}]{gap}"
        )
    items = sorted(
        (i for i in mapping.get("items", []) if isinstance(i, Mapping)),
        key=lambda i: (int(i.get("rank") or 10**9), str(i.get("id"))),
    )[:max_items]
    ids, labels = [], []
    for i in items:
        ids.append(str(i["id"]))
        labels.append(f"{i['id']}={i.get('evidence') or 'unverified'}")
        lines.append(
            f"ITEM {i['id']} [{i.get('kind')}, {i.get('evidence')}] {i.get('label')}: {str(i.get('claim'))[:160]}"
        )
    return ResearchRead(
        lines=lines,
        ids=ids,
        labels=labels,
        thin=not ids,
        note="" if lines else "research has nothing to digest",
    )
