"""Plan view builder: the typed Gate 2 page (spec 2.3), built from the spec by code.

Pure and deterministic. The one-sentence / PRD summary is an LLM call (C11) owned by someone
else and is passed in. Everything else (screen map, look preview, data in plain words, what
research added, real vs mocked, roles, diff since Gate 1, backlog, Enterprise extras) is derived
from the spec, the Brief, the Research Note and the Policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from mde_contracts.brief import Brief
from mde_contracts.plan import (
    ApproverStatus,
    EnterpriseExtras,
    IntegrationStatus,
    LookPreview,
    PlainEntity,
    PlanDiff,
    PlanView,
    ResearchAdditionView,
    RoleView,
    ScreenMapGroup,
)
from mde_contracts.policy import Policy
from mde_contracts.research import ResearchNote
from mde_contracts.spec import AppSpec, Entity, FieldDef, Role, ScreenBlock
from mde_spec_builder.assemble import spec_ids
from mde_spec_builder.render import spec_hash
from mde_spec_loader.diff import diff_specs
from mde_spec_loader.graph import auth_tokens

MAX_DIFF_LINES = 20
OTHER_SCREENS = "Other screens"

_KIND_LABEL = {
    "SCR": "screen",
    "FLW": "journey",
    "ENT": "data",
    "API": "operation",
    "ROLE": "role",
    "INT": "integration",
}

_ITEM_PREFIXES = ("SCR-", "FLW-", "ENT-", "API-", "ROLE-", "INT-")

# singleton blocks with no items of their own: a change to one is one plain line
_BLOCK_LINES = {
    "APP": "Changed the app's name or description",
    "DESIGN": "Changed the look",
    "BEHAVIOR": "Changed how the app behaves",
}

_GUIDED_WHY = {
    "feature": "Seen in most similar apps",
    "pattern": "A common pattern in similar apps",
    "compliance": "Worth checking for apps like yours",
    "integration": "Commonly used by apps like yours",
    "brand": "Matches your brand's look",
}

_UNAVAILABLE_REASON = {
    "policy": "it is turned off for this company",
    "budget": "the research budget was used up",
    "failed": "the web lookups did not work",
    "mock_no_fixtures": "no sample data was available",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# --------------------------------------------------------------------------- words


def humanize(name: str) -> str:
    """``unit_price`` / ``unitPrice`` -> ``unit price``."""
    return _CAMEL.sub(" ", name).replace("_", " ").replace("-", " ").strip().lower()


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]  # addresses -> address, boxes -> box
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def screen_title(screen: ScreenBlock) -> str:
    """A short plain title: the screen's ``title``, else its ``title`` copy, else from its route."""
    if screen.title:
        return screen.title
    for key in ("title", "screen_title", "heading"):
        text = screen.texts.get(key)
        if text:
            return text
    segments = [s for s in screen.route.split("/") if s and not s.startswith("(")]
    if not segments:
        return "Home"
    static = [humanize(s) for s in segments if not (s.startswith("[") and s.endswith("]"))]
    if not static:
        return screen.purpose[:60]
    dynamic_last = segments[-1].startswith("[")
    if dynamic_last:
        return f"{_singular(static[-1]).capitalize()} detail"
    last = static[-1]
    previous = static[-2] if len(static) > 1 else None
    if last in ("new", "add", "create") and previous:
        return f"New {_singular(previous)}"
    if last == "edit" and previous:
        return f"Edit {_singular(previous)}"
    text = " ".join(static[-2:])
    return text.capitalize()


def _plain_field(field: FieldDef, spec: AppSpec) -> str:
    label = humanize(field.name)
    parts: list[str] = []
    if field.type in ("int", "number"):
        parts.append("number")
    elif field.type == "bool":
        parts.append("yes or no")
    elif field.type in ("datetime", "date"):
        parts.append("date")
    elif field.type == "money":
        parts.append("amount of money")
    elif field.type == "image":
        parts.append("picture")
    elif field.type == "ref":
        target = next((e for e in spec.data.entities if e.id == field.ref), None)
        parts.append(f"link to {target.plain_name.lower()}" if target else "link")
    elif field.type == "enum":
        enum = next((e for e in spec.data.enums if e.name == field.enum), None)
        parts.append(f"one of: {', '.join(enum.values)}" if enum else "a choice")
    if not field.required:
        parts.append("optional")
    return f"{label} ({', '.join(parts)})" if parts else label


def _plain_entity(entity: Entity, spec: AppSpec) -> PlainEntity:
    return PlainEntity(
        id=entity.id,
        plain_name=entity.plain_name,
        fields_plain=[_plain_field(f, spec) for f in entity.fields],
    )


# --------------------------------------------------------------------------- parts


def _screen_map(spec: AppSpec) -> list[ScreenMapGroup]:
    known = {s.id: s for s in spec.screens}
    placed: set[str] = set()
    groups: list[ScreenMapGroup] = []
    for flow in spec.flows:
        ids = [i for i in dict.fromkeys(flow.screens) if i in known]
        placed.update(ids)
        groups.append(
            ScreenMapGroup(
                flow_id=flow.id,
                flow_name=flow.name,
                screen_ids=ids,
                screen_titles=[screen_title(known[i]) for i in ids],
                count=len(ids),
            )
        )
    rest = [s.id for s in spec.screens if s.id not in placed]
    if rest:
        groups.append(
            ScreenMapGroup(
                flow_id=None,
                flow_name=OTHER_SCREENS,
                screen_ids=rest,
                screen_titles=[screen_title(known[i]) for i in rest],
                count=len(rest),
            )
        )
    return groups


def _look(spec: AppSpec) -> LookPreview:
    tokens = spec.design.tokens
    radii = sorted(tokens.radius.values())
    radius = tokens.radius.get("md", radii[len(radii) // 2] if radii else 0)
    body = tokens.type_scale.get("body")
    return LookPreview(
        direction=spec.design.direction,
        swatches_light=dict(sorted(tokens.colors_light.items())),
        swatches_dark=dict(sorted(tokens.colors_dark.items())),
        radius=radius,
        font_sample=f"Aa {body.size}/{body.weight}" if body else None,
    )


def _research_text(spec: AppSpec, note: ResearchNote | None) -> str | None:
    status = spec.assumptions.research_status
    if status == "skipped":
        extra = spec.assumptions.research_note
        return f"Research was skipped: {extra}." if extra else "Research was skipped."
    if status == "unavailable":
        reason = note.unavailable_reason if note else None
        why = _UNAVAILABLE_REASON.get(reason or "", reason or "it could not run")
        return f"Research was skipped because {why}. The plan uses standard defaults."
    if status == "partial":
        return "Research finished only in part; what it found is listed below."
    return None


def _research_added(spec: AppSpec, note: ResearchNote | None) -> list[ResearchAdditionView]:
    rationale = {s.id: s.rationale for s in note.suggestions} if note else {}
    enterprise = spec.header.mode == "enterprise"
    out: list[ResearchAdditionView] = []
    for addition in spec.assumptions.research_additions:
        why = _GUIDED_WHY.get(addition.kind, "Suggested from research")
        if enterprise and addition.id in rationale:
            why = rationale[addition.id]
        out.append(
            ResearchAdditionView(
                id=addition.id,
                title=addition.title,
                kind=addition.kind,
                why=why,
                sources=list(addition.sources),
                included=addition.included,
                verified=addition.verified,
            )
        )
    return out


def _gate_allows(screen: ScreenBlock, role: Role) -> bool:
    tokens = auth_tokens(screen.auth)
    if "public" in tokens:
        return True
    gated = [t for t in tokens if t.startswith("ROLE-")]
    if gated:
        return role.id in gated
    if "authenticated" in tokens:
        return screen.surface == role.surface
    return False


def _roles(spec: AppSpec) -> list[RoleView]:
    out: list[RoleView] = []
    for role in spec.roles.roles:
        permissions = [p for p in spec.roles.permissions if p.role == role.id]
        explicit: set[str] = set()
        for permission in permissions:
            if "*" in permission.screens:
                explicit.update(s.id for s in spec.screens)
            explicit.update(permission.screens)
        titles: list[str] = []
        for screen in spec.screens:
            if permissions:
                visible = screen.id in explicit or "public" in auth_tokens(screen.auth)
            else:
                visible = _gate_allows(screen, role)
            if visible:
                titles.append(screen_title(screen))
        out.append(RoleView(id=role.id, name=role.name, sees=titles))
    return out


def _labels(spec: AppSpec) -> dict[str, str]:
    labels: dict[str, str] = {s.id: screen_title(s) for s in spec.screens}
    labels.update({f.id: f.name for f in spec.flows})
    labels.update({e.id: e.plain_name for e in spec.data.entities})
    labels.update({o.id: o.name for o in spec.api.operations})
    labels.update({r.id: r.name for r in spec.roles.roles})
    labels.update({i.id: i.name for i in spec.integrations.items})
    return labels


def _item_ids(ids: Iterable[str]) -> list[str]:
    """The ids a person can name (screens, journeys, data, operations, roles, integrations): the
    block ids (``APP``, ``DESIGN``, ``NAV`` ...) a caller may mix in are not items of a spec, so
    they are never compared with the current draft's items."""
    return [i for i in dict.fromkeys(ids) if i.startswith(_ITEM_PREFIXES)]


def _diff(
    spec: AppSpec,
    gate1_ids: Sequence[str],
    gate1_spec: AppSpec | None = None,
    notes: Sequence[str] = (),
) -> PlanDiff | None:
    """Added, removed and (when the first draft itself is known) changed ids since Gate 1.

    Both sides are read at the same granularity: item ids (screens, journeys, data, operations,
    roles, integrations), computed with ``spec_ids`` from the first draft itself when it is given,
    else from ``gate1_ids`` reduced to item ids. A block that is still in the file is therefore never
    reported as removed. ``changed_ids`` compares the canonical text of every item present in both
    drafts and, for the singleton blocks that have no items of their own (``APP``, ``DESIGN``,
    ``BEHAVIOR``), the block itself with a plain line ("Changed the look"). ``notes`` are plain lines
    about requests that could not be done; they come first and are never cut.
    """
    before_ids = spec_ids(gate1_spec) if gate1_spec is not None else _item_ids(gate1_ids)
    if not before_ids:
        return PlanDiff(since="gate1", lines=list(notes)) if notes else None
    before = set(before_ids)
    now = spec_ids(spec)
    added = [i for i in now if i not in before]
    still_there = set(now)
    removed = [i for i in dict.fromkeys(before_ids) if i not in still_there]  # first-draft order
    changed: list[str] = []
    blocks: list[str] = []
    if gate1_spec is not None:
        touched = set(diff_specs(gate1_spec, spec).changed)
        changed = [i for i in now if i in touched and i in before]
        blocks = [b for b in _BLOCK_LINES if b in touched]
    labels = {**_labels(gate1_spec), **_labels(spec)} if gate1_spec is not None else _labels(spec)
    lines: list[str] = []
    for spec_id in added:
        kind = _KIND_LABEL.get(spec_id.split("-")[0], "item")
        lines.append(f"Added {kind}: {labels.get(spec_id, spec_id)}")
    for spec_id in removed:
        kind = _KIND_LABEL.get(spec_id.split("-")[0], "item")
        lines.append(f"Removed {kind}: {labels.get(spec_id, spec_id)}")
    for spec_id in changed:
        kind = _KIND_LABEL.get(spec_id.split("-")[0], "item")
        lines.append(f"Changed {kind}: {labels.get(spec_id, spec_id)}")
    lines.extend(_BLOCK_LINES[b] for b in blocks)
    if not lines and not notes:
        lines = ["Nothing changed since the first draft."]
    elif len(lines) > MAX_DIFF_LINES:
        lines = [*lines[:MAX_DIFF_LINES], f"...and {len(lines) - MAX_DIFF_LINES} more changes"]
    return PlanDiff(
        since="gate1",
        added_ids=added,
        removed_ids=removed,
        changed_ids=[*changed, *blocks],
        lines=[*notes, *lines],
    )


def _enterprise(
    spec: AppSpec, policy: Policy, approver_status: Sequence[ApproverStatus] | None
) -> EnterpriseExtras | None:
    if policy.mode != "enterprise" and spec.header.mode != "enterprise":
        return None
    approvers = (
        list(approver_status)
        if approver_status is not None
        else [ApproverStatus(role=r) for r in policy.gates.plan.approvers]
    )
    return EnterpriseExtras(approvers=approvers, full_file_view=policy.gates.plan.full_file_view)


# --------------------------------------------------------------------------- public API


def build_plan_view(
    spec: AppSpec,
    *,
    summary: str,
    brief: Brief,
    research_note: ResearchNote | None,
    policy: Policy,
    gate1_ids: Sequence[str],
    approver_status: Sequence[ApproverStatus] | None = None,
    gate1_spec: AppSpec | None = None,
    notes: Sequence[str] = (),
) -> PlanView:
    """The Gate 2 page for ``spec``. ``summary`` is the C11 sentence; ``gate1_ids`` the ids of
    the first draft (empty means "no baseline", so no diff is shown unless ``gate1_spec``, the
    first draft itself, is given: it also yields the changed ids). ``notes`` are plain lines about
    requests that could not be done; they head the list of changes."""
    return PlanView(
        spec_version=spec.header.spec_version,
        spec_hash=spec_hash(spec),
        language=spec.header.language,
        summary=summary,
        screen_count=len(spec.screens),
        screen_map=_screen_map(spec),
        look=_look(spec),
        data=[_plain_entity(e, spec) for e in spec.data.entities],
        research_status=spec.assumptions.research_status,
        research_note=_research_text(spec, research_note),
        research_added=_research_added(spec, research_note),
        compliance=list(research_note.compliance) if research_note is not None else [],
        integrations=[
            IntegrationStatus(id=i.id, name=i.name, mode=i.mode, needs_dev_build=i.needs_dev_build)
            for i in spec.integrations.items
        ],
        roles=_roles(spec),
        diff_since_gate1=_diff(spec, gate1_ids, gate1_spec, notes),
        assumptions=list(brief.assumptions),
        backlog=list(spec.backlog),
        enterprise=_enterprise(spec, policy, approver_status),
    )
