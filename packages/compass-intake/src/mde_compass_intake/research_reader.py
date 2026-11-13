"""Read a Research output (``research.v5``, the ``research.json`` file) into a small typed :class:`ResearchView`.

Spec reads Research through this view and through ``pack(research, need_card)`` (re-exported here). Research may be
complete, partial, skipped or failed (doc 15 section 3.5): a missing, partial or broken file is never an exception, it is
a view with ``thin=True`` and the reasons, so every lane reads as thin and the libraries fill the gap, marked assumed.
Use rules R21 to R28 bind the caller, not this reader: it does not interpret evidence, it carries it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mde_compass_intake.models import Frozen
from mde_compass_intake.pack import pack
from mde_compass_intake.tolerant import m, rows, s, strs

__all__ = [
    "AppliesTo",
    "ConfirmEntry",
    "Conflict",
    "Dossier",
    "DossierField",
    "LaneDecision",
    "Norm",
    "PlatformConstraint",
    "ResearchItem",
    "ResearchView",
    "pack",
    "read_research",
]


class AppliesTo(Frozen):
    kind: str
    id: str | None = None
    relation: str = ""


class ResearchItem(Frozen):
    id: str
    job: str = ""
    kind: str = ""
    label: str = ""
    claim: str = ""
    evidence: str = "unverified"  # official | corroborated | single_source | unverified
    tier: str = "default"
    flags: tuple[str, ...] = ()
    applies_to: tuple[AppliesTo, ...] = ()
    rank: int | None = None
    store: str = "main"  # main | appendix
    source_ids: tuple[str, ...] = ()


class LaneDecision(Frozen):
    id: str
    lane: str = ""
    text: str = ""
    item_ids: tuple[str, ...] = ()
    unverified_item_ids: tuple[str, ...] = ()
    best_evidence: str = ""
    gap: str | None = None


class DossierField(Frozen):
    value: Any = None
    null_reason: str | None = None  # an explicit unknown: "not stated", never a guess
    evidence: str | None = None
    item_ids: tuple[str, ...] = ()


class Dossier(Frozen):
    id: str
    provider: str = ""
    capability: str = ""
    integrations: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    item_ids: tuple[str, ...] = ()
    fields: dict[str, DossierField] = {}


class PlatformConstraint(Frozen):
    item_id: str
    capability: str = ""
    platform: str = ""
    rule: str = ""
    effect_on_build: str | None = None
    status: str | None = None
    evidence: str = ""
    domain: str = ""


class ConfirmEntry(Frozen):
    item_id: str
    why: str = ""
    rank: int | None = None
    group: str = ""
    kind: str = ""  # the entry's ``class`` (money, legal, ...)
    top: bool = False


class Conflict(Frozen):
    topic: str = ""
    item_ids: tuple[str, ...] = ()
    note: str = ""


class Norm(Frozen):
    area: str = ""
    key: str = ""
    label: str = ""
    item_ids: tuple[str, ...] = ()
    matches_brief: str | None = None
    prevalence: dict[str, Any] = {}
    recency: dict[str, Any] = {}


class ResearchView(Frozen):
    thin: bool = True
    thin_reasons: tuple[str, ...] = ()
    thin_lanes: tuple[str, ...] = ()  # slices with no item, lanes whose decision carries a gap
    schema_name: str = ""
    status: str = ""
    program_id: str = ""
    brief_hash: str = ""
    profile: dict[str, Any] = {}
    lane_decisions: tuple[LaneDecision, ...] = ()
    dossiers: tuple[Dossier, ...] = ()
    platform_constraints: tuple[PlatformConstraint, ...] = ()
    compliance_register: tuple[
        dict[str, Any], ...
    ] = ()  # [{country, regimes: [{regime, entries: [...]}]}], as written
    confirm: tuple[ConfirmEntry, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    items: tuple[ResearchItem, ...] = ()
    norms: tuple[Norm, ...] = ()
    slices: dict[str, tuple[str, ...]] = {}

    def is_for(self, brief_hash: str) -> bool:
        """Was this research made for that Brief (``brief_hash``)? A view with no hash is not."""
        return bool(self.brief_hash) and self.brief_hash == brief_hash


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _item(r: Mapping[str, Any]) -> ResearchItem:
    return ResearchItem(
        id=str(r["id"]),
        job=s(r.get("job")),
        kind=s(r.get("kind")),
        label=s(r.get("label")),
        claim=s(r.get("claim")),
        evidence=s(r.get("evidence")) or "unverified",
        tier=s(r.get("tier")) or "default",
        flags=strs(r.get("flags")),
        applies_to=tuple(
            AppliesTo(
                kind=s(a.get("kind")), id=s(a.get("id")) or None, relation=s(a.get("relation"))
            )
            for a in rows(r.get("applies_to"), "kind")
        ),
        rank=_int(r.get("rank")),
        store="appendix" if r.get("store") == "appendix" else "main",
        source_ids=tuple(s(x.get("id")) for x in rows(r.get("sources"), "id")),
    )


def _dossier(r: Mapping[str, Any]) -> Dossier:
    fields: dict[str, DossierField] = {}
    for key, f in m(r.get("fields")).items():
        fm = m(f)
        fields[str(key)] = DossierField(
            value=fm.get("value"),
            null_reason=s(fm.get("null_reason")) or None,
            evidence=s(fm.get("evidence")) or None,
            item_ids=strs(fm.get("item_ids")),
        )
    return Dossier(
        id=str(r["id"]),
        provider=s(r.get("provider")),
        capability=s(r.get("capability")),
        integrations=strs(r.get("integrations")),
        needs=strs(r.get("needs")),
        item_ids=strs(r.get("item_ids")),
        fields=fields,
    )


def read_research(research: Any) -> ResearchView:
    """A typed view of a research file. Never raises: anything that is not a usable research file is a thin view."""
    if not isinstance(research, Mapping):
        return ResearchView(thin=True, thin_reasons=("research is missing or not an object",))
    items = tuple(_item(r) for r in rows(research.get("items"), "id"))
    decisions = tuple(
        LaneDecision(
            id=str(r["id"]),
            lane=s(r.get("lane")),
            text=s(r.get("text")),
            item_ids=strs(r.get("item_ids")),
            unverified_item_ids=strs(r.get("unverified_item_ids")),
            best_evidence=s(r.get("best_evidence")),
            gap=s(r.get("gap")) or None,
        )
        for r in rows(research.get("lane_decisions"), "id")
    )
    slices = {
        str(lane): strs(m(body).get("item_ids")) for lane, body in m(research.get("slices")).items()
    }
    status = s(research.get("status"))
    reasons: list[str] = []
    if status != "complete":
        reasons.append(f"research status is {status or 'missing'}, not complete")
    if not items:
        reasons.append("research has no items")
    thin_lanes = sorted(
        {lane for lane, ids in slices.items() if not ids}
        | {d.lane for d in decisions if d.gap and d.lane}
    )
    return ResearchView(
        thin=bool(reasons),
        thin_reasons=tuple(reasons),
        thin_lanes=tuple(thin_lanes),
        schema_name=s(research.get("schema")),
        status=status,
        program_id=s(research.get("program_id")),
        brief_hash=s(research.get("brief_hash")),
        profile=dict(m(research.get("profile"))),
        lane_decisions=decisions,
        dossiers=tuple(_dossier(r) for r in rows(research.get("dossiers"), "id")),
        platform_constraints=tuple(
            PlatformConstraint(
                item_id=str(r["item_id"]),
                capability=s(r.get("capability")),
                platform=s(r.get("platform")),
                rule=s(r.get("rule")),
                effect_on_build=s(r.get("effect_on_build")) or None,
                status=s(r.get("status")) or None,
                evidence=s(r.get("evidence")),
                domain=s(r.get("domain")),
            )
            for r in rows(research.get("platform_constraints"), "item_id")
        ),
        compliance_register=tuple(dict(r) for r in rows(research.get("compliance_register"))),
        confirm=tuple(
            ConfirmEntry(
                item_id=str(r["item_id"]),
                why=s(r.get("why")),
                rank=_int(r.get("rank")),
                group=s(r.get("group")),
                kind=s(r.get("class")),
                top=bool(r.get("top")),
            )
            for r in rows(research.get("confirm"), "item_id")
        ),
        conflicts=tuple(
            Conflict(
                topic=s(r.get("topic")), item_ids=strs(r.get("item_ids")), note=s(r.get("note"))
            )
            for r in rows(research.get("conflicts"))
        ),
        items=items,
        norms=tuple(
            Norm(
                area=s(r.get("area")),
                key=s(r.get("key")),
                label=s(r.get("label")),
                item_ids=strs(r.get("item_ids")),
                matches_brief=s(r.get("matches_brief")) or None,
                prevalence=dict(m(r.get("prevalence"))),
                recency=dict(m(r.get("recency"))),
            )
            for r in rows(research.get("norms"))
        ),
        slices=slices,
    )
