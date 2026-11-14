"""Mechanical repairs of DSGN's output (R-code, doc 18 3.2): fixes that cannot change meaning, made by code and logged, so a
near miss never costs a re-ask. The validator no longer errors on any of these.

* a dark stance the profile does not accept (deep needs parity, dark first or time of day) -> its nearest accepted value from
  the data table, else the default;
* the deep admin variant missing -> the data defaults (density, motion, ambition and the note);
* a delight budget, an asset count, or a list length out of range -> clamped or cut;
* an asset need naming an app that does not exist -> for every app; a repeated need -> dropped; more needs than allowed -> cut.

What stays a validator error (a re-ask): the wrong number of directions, directions too alike, a seed colour that gives no
accessible tokens, form words in free text, an empty required text."""

from __future__ import annotations

from dataclasses import dataclass

from mde_spec_agent.lanes.design._data import design_data
from mde_spec_agent.lanes.design.dsgn._node import direction_id
from mde_spec_agent.lanes.design.dsgn.schema import AssetOut, DirectionOut, DsgnIn, DsgnOut
from mde_spec_core.callkit.ladder import FixKind


@dataclass(frozen=True)
class Fix:
    kind: FixKind
    node_id: str
    what: str
    before: str
    after: str

    def as_row(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "node_id": self.node_id,
            "what": self.what,
            "before": self.before,
            "after": self.after,
        }


def _direction(inp: DsgnIn, i: int, x: DirectionOut, fixes: list[Fix]) -> DirectionOut:
    d = design_data()
    nid = direction_id(i)
    upd: dict[str, object] = {}

    def fix(kind: FixKind, field: str, before: object, after: object) -> None:
        upd[field] = after
        fixes.append(Fix(kind, nid, f"dsgn.{field}", str(before), str(after)))

    if inp.profile == "deep":
        if x.dark_stance not in d.deep_dark_stances:
            near = d.dark_stance_synonyms.get(x.dark_stance, d.deep_default_dark_stance)
            near = near if near in d.deep_dark_stances else d.deep_default_dark_stance
            fix(FixKind.EXACT_SYNONYM, "dark_stance", x.dark_stance, near)
        av = d.admin_variant
        for field, default in (
            ("admin_density", av.default_density),
            ("admin_motion", av.default_motion),
            ("admin_ambition", av.default_ambition),
        ):
            if not getattr(x, field):
                fix(FixKind.INSTANTIATE_LIBRARY_DEFAULT, field, "", default)
        if not x.admin_note.strip():
            fix(FixKind.INSTANTIATE_LIBRARY_DEFAULT, "admin_note", "", av.note)
    lo, hi = d.delight_budget.min, d.delight_budget.max
    if not lo <= x.delight_budget <= hi:
        fix(
            FixKind.EXACT_SYNONYM,
            "delight_budget",
            x.delight_budget,
            min(max(x.delight_budget, lo), hi),
        )
    if len(x.avoid) > d.max_avoid_items:
        fix(FixKind.DROP_UNKNOWN_OPTIONAL, "avoid", len(x.avoid), d.max_avoid_items)
        upd["avoid"] = x.avoid[: d.max_avoid_items]
    if len(x.emotional_goals) > 8:
        fix(FixKind.DROP_UNKNOWN_OPTIONAL, "emotional_goals", len(x.emotional_goals), 8)
        upd["emotional_goals"] = x.emotional_goals[:8]
    return x.model_copy(update=upd) if upd else x


def _assets(inp: DsgnIn, assets: list[AssetOut], fixes: list[Fix]) -> list[AssetOut]:
    d = design_data()
    out: list[AssetOut] = []
    seen: set[tuple[str, str, str]] = set()
    for n, a in enumerate(assets):
        nid = f"assets[{n}]"
        upd: dict[str, object] = {}
        if a.app and a.app not in inp.app_ids:
            upd["app"] = ""
            fixes.append(
                Fix(FixKind.DROP_UNKNOWN_OPTIONAL, nid, "dsgn.assets.app", a.app, "every app")
            )
        clamped = min(max(a.count, 1), d.max_asset_count)
        if clamped != a.count:
            upd["count"] = clamped
            fixes.append(
                Fix(FixKind.EXACT_SYNONYM, nid, "dsgn.assets.count", str(a.count), str(clamped))
            )
        a = a.model_copy(update=upd) if upd else a
        key = (a.kind, a.purpose, a.subject.strip().lower())
        if key in seen:
            fixes.append(
                Fix(FixKind.DROP_UNKNOWN_OPTIONAL, nid, "dsgn.assets.repeat", a.subject, "")
            )
            continue
        seen.add(key)
        out.append(a)
    if len(out) > inp.max_assets:
        fixes.append(
            Fix(
                FixKind.DROP_UNKNOWN_OPTIONAL,
                "assets",
                "dsgn.assets.extra",
                str(len(out)),
                str(inp.max_assets),
            )
        )
        out = out[: inp.max_assets]
    return out


def repair(inp: DsgnIn, out: DsgnOut) -> tuple[DsgnOut, list[Fix]]:
    fixes: list[Fix] = []
    dirs = [_direction(inp, i, x, fixes) for i, x in enumerate(out.directions)]
    return DsgnOut(directions=dirs, assets=_assets(inp, out.assets, fixes)), fixes
