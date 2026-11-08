"""Diffs shown when a gate re-opens after a version change (spec 2.2 rule 2).

* :func:`brief_diff` compares two Briefs (changed rows, features, assumptions) in plain language.
* :func:`diff_ids` compares two ``AppSpec`` drafts by stable spec id. It is computed locally on
  purpose: the flow does not import ``mde_spec_loader`` (layering: it only needs ids, not parsing).
* :func:`plan_diff` turns an :class:`IdDiff` into the typed ``PlanDiff`` the Plan view carries.
"""

from __future__ import annotations

from typing import Literal

from mde_contracts.brief import Brief, BriefFeature, BriefRow
from mde_contracts.common import Frozen
from mde_contracts.plan import PlanDiff
from mde_contracts.spec import AppSpec
from mde_flow import messages


class IdDiff(Frozen):
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def new_screens(self) -> int:
        return sum(1 for i in self.added if i.startswith("SCR-"))


def _index(spec: AppSpec) -> dict[str, object]:
    """Every addressable id in a spec -> its JSON form (so equality means 'unchanged')."""
    out: dict[str, object] = {}
    for screen in spec.screens:
        out[screen.id] = screen.model_dump(mode="json")
    for flow in spec.flows:
        out[flow.id] = flow.model_dump(mode="json")
    for entity in spec.data.entities:
        out[entity.id] = entity.model_dump(mode="json")
    for op in spec.api.operations:
        out[op.id] = op.model_dump(mode="json")
    for role in spec.roles.roles:
        out[role.id] = role.model_dump(mode="json")
    for item in spec.integrations.items:
        out[item.id] = item.model_dump(mode="json")
    out["APP"] = spec.app.model_dump(mode="json")
    out["DESIGN"] = spec.design.model_dump(mode="json")
    out["NAV"] = spec.navigation.model_dump(mode="json")
    out["BEHAVIOR"] = spec.behavior.model_dump(mode="json")
    # block-level parts that are not an item list: a change here marks the block id itself
    out["DATA"] = spec.data.model_dump(mode="json", exclude={"entities"})
    out["API"] = spec.api.model_dump(mode="json", exclude={"operations"})
    out["ROLES"] = spec.roles.model_dump(mode="json", exclude={"roles"})
    return out


def spec_ids(spec: AppSpec) -> set[str]:
    """Every addressable id of a spec (screens, flows, entities, operations, roles, blocks)."""
    return set(_index(spec))


def diff_ids(old: AppSpec, new: AppSpec) -> IdDiff:
    """Added, removed and changed spec ids between two drafts (sorted, deterministic)."""
    a, b = _index(old), _index(new)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(i for i in set(a) & set(b) if a[i] != b[i])
    return IdDiff(added=added, removed=removed, changed=changed)


def _screen_label(spec: AppSpec, screen_id: str) -> str:
    for screen in spec.screens:
        if screen.id == screen_id:
            return f"{screen_id} ({screen.purpose})"
    return screen_id


def _describe(spec: AppSpec, spec_id: str) -> str:
    if spec_id.startswith("SCR-"):
        return f"screen {_screen_label(spec, spec_id)}"
    if spec_id in messages.BLOCK_LABELS:
        return messages.BLOCK_LABELS[spec_id]
    return spec_id


def plan_diff(
    old: AppSpec | None,
    new: AppSpec,
    *,
    since: Literal["gate1", "previous_version"] = "previous_version",
) -> PlanDiff:
    """The Plan view's diff, with a plain-language line per change."""
    if old is None:
        return PlanDiff(since=since)
    d = diff_ids(old, new)
    lines = [f"Added {_describe(new, i)}" for i in d.added]
    lines += [f"Removed {_describe(old, i)}" for i in d.removed]
    lines += [f"Updated {_describe(new, i)}" for i in d.changed]
    return PlanDiff(
        since=since,
        added_ids=d.added,
        removed_ids=d.removed,
        changed_ids=d.changed,
        lines=lines,
    )


# --------------------------------------------------------------------------- the Brief


def _feature_map(row: BriefRow | None) -> dict[str, BriefFeature]:
    return {f.id: f for f in row.features} if row is not None else {}


def brief_diff(old: Brief | None, new: Brief) -> list[str]:
    """Plain-language lines describing what changed in the Brief (never empty for a new version)."""
    if old is None:
        return []
    lines: list[str] = []
    if old.app_summary != new.app_summary:
        lines.append(f"What you are building: {new.app_summary}")
    old_rows = {r.key: r for r in old.rows}
    for row in new.rows:
        before = old_rows.get(row.key)
        if before is None:
            lines.append(f"New: {row.title}: {row.text}")
            continue
        if row.key == "features":
            lines.extend(_feature_lines(before, row))
        if before.text != row.text or before.swatch != row.swatch:
            lines.append(f"{row.title}: {row.text}")
    new_keys = {r.key for r in new.rows}
    lines.extend(f"Removed: {r.title}" for r in old.rows if r.key not in new_keys)
    lines.extend(_assumption_lines(old, new))
    return lines or [messages.NO_VISIBLE_CHANGE]


def _feature_lines(before: BriefRow, after: BriefRow) -> list[str]:
    a, b = _feature_map(before), _feature_map(after)
    out: list[str] = []
    for fid, feat in b.items():
        prev = a.get(fid)
        if prev is None:
            out.append(f"Feature added: {feat.label}")
        elif prev.on != feat.on:
            out.append(f"Feature {'turned on' if feat.on else 'turned off'}: {feat.label}")
    out.extend(f"Feature removed: {f.label}" for fid, f in a.items() if fid not in b)
    return out


def _assumption_lines(old: Brief, new: Brief) -> list[str]:
    a = {x.slot_id: x for x in old.assumptions}
    b = {x.slot_id: x for x in new.assumptions}
    out: list[str] = []
    for slot_id, item in b.items():
        prev = a.get(slot_id)
        if prev is None:
            out.append(f"We assumed: {item.label}: {item.value_text}")
        elif prev.included != item.included:
            state = "back on" if item.included else "turned off"
            out.append(f"Assumption {state}: {item.label}")
        elif prev.value_text != item.value_text:
            out.append(f"We assumed: {item.label}: now {item.value_text}")
    out.extend(f"No longer assumed: {x.label}" for sid, x in a.items() if sid not in b)
    return out
