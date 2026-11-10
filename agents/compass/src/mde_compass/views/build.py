"""``build_view_model``: the display-ready Brief page (prompt 6.9). Pure and deterministic.

Fixed frame (``SECTION_IDS``), dynamic content. Row ids are paths in the ChangeSet grammar so the UI can
send ``brief_edit`` / ``brief_feedback`` with them. All text comes from ``catalog/strings`` (English and
Hindi); a few labels fall back to ``views.format.FALLBACK_STRINGS``. Empty sections render their
``empty_text`` instead of failing.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import (
    AppRecord,
    AssetRecord,
    AssumptionRecord,
    Brief,
    DeclinedRecord,
    DomainFact,
    FeatureRecord,
    GlossaryRecord,
    IntegrationRecord,
    OpenQuestion,
    ReferenceRecord,
    RoleRecord,
    SourceRecord,
)
from mde_compass.domain.common import BriefItem, ItemMeta
from mde_compass.domain.research import ResearchRequest
from mde_compass.domain.session import SessionRecord
from mde_compass.domain.structure import ModuleRecord
from mde_compass.store.ports import BriefVersionRecord
from mde_compass.views.diff import changed_paths as diff_changed_paths
from mde_compass.views.format import (
    EMPTY,
    Labels,
    badge,
    badges_for,
    field_line,
    humanize,
    list_value,
    plain_text,
    text_value,
    to_view_value,
)
from mde_compass.views.models import (
    SECTION_IDS,
    BadgeKind,
    BriefStatus,
    BriefViewModel,
    ReadinessChip,
    ViewBadge,
    ViewFooter,
    ViewHeader,
    ViewRow,
    ViewSection,
)
from mde_compass.views.paths import slot_for_key, view_section_for_path
from mde_compass.views.readiness import compute_readiness

TITLE_MAX_CHARS = 60
_READINESS_CONSUMERS: tuple[str, ...] = ("research", "blueprint", "design")
_PRIORITY_AFTER_STATUS = ("in", "later", "needs-engineering")  # statuses that show a priority too


@dataclass
class _Ctx:
    brief: Brief
    catalog: Catalog
    labels: Labels
    changed: frozenset[str]
    slot_order: dict[str, int]

    def is_changed(self, row_id: str) -> bool:
        prefix = row_id + "."
        return any(p == row_id or p.startswith(prefix) for p in self.changed)

    def t(self, key: str, **values: object) -> str:
        return self.labels.t(key, **values)


def _has_history(meta: ItemMeta) -> bool:
    return bool(meta.history or meta.provenance)


# ---------------------------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------------------------


def _dict_row(ctx: _Ctx, section: str, key: str, item: BriefItem) -> ViewRow:
    slot = slot_for_key(ctx.catalog, section, key)
    row_id = f"{section}.{key}"
    description = ctx.brief.pitch if section == "idea" and key == "summary" else ""
    return ViewRow(
        id=row_id,
        label=slot.label.get(ctx.labels.language) if slot else humanize(key),
        value=to_view_value(item.value, ctx.labels, slot),
        badges=badges_for(item, ctx.labels, has_value=item.has_value()),
        tier=item.tier,
        editable=True,
        history_available=_has_history(item),
        needs_review=item.status == "disputed",
        changed=ctx.is_changed(row_id),
        description=description,
    )


def _dict_rows(
    ctx: _Ctx, section: str, items: Mapping[str, BriefItem], *, geo: bool | None = None
) -> list[ViewRow]:
    """Rows of one dict section in catalog order; ``geo`` selects (True) or drops (False) the
    where-is-it-used item of ``context``."""
    keyed: list[tuple[int, str, BriefItem]] = []
    for key, item in items.items():
        if not item.has_value():
            continue
        slot = slot_for_key(ctx.catalog, section, key)
        is_geo = (slot.id == "context.geo") if slot else key.startswith("geo")
        if geo is not None and section == "context" and is_geo != geo:
            continue
        order = ctx.slot_order.get(slot.id, 10_000) if slot else 10_000
        keyed.append((order, key, item))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [_dict_row(ctx, section, key, item) for _, key, item in keyed]


def _idea_rows(ctx: _Ctx) -> list[ViewRow]:
    rows = _dict_rows(ctx, "idea", ctx.brief.idea)
    if ctx.brief.summary and not any(r.id == "idea.summary" for r in rows):
        slot = ctx.catalog.slots.get("idea.summary")
        rows.insert(
            0,
            ViewRow(
                id="idea.summary",
                label=slot.label.get(ctx.labels.language) if slot else humanize("summary"),
                value=text_value(ctx.brief.summary),
                description=ctx.brief.pitch,
                changed=ctx.is_changed("idea.summary") or ctx.is_changed("summary"),
            ),
        )
    return rows + _dict_rows(ctx, "context", ctx.brief.context, geo=False)


def _who_rows(ctx: _Ctx) -> list[ViewRow]:
    return _dict_rows(ctx, "context", ctx.brief.context, geo=True) + _dict_rows(
        ctx, "audience", ctx.brief.audience
    )


def _role_name(brief: Brief, ref: str) -> str:
    for role in brief.roles:
        if ref in (role.id, role.name_in_user_words):
            return role.name_in_user_words
    return ref


def _app_row(ctx: _Ctx, app: AppRecord) -> ViewRow:
    row_id = f"apps.{app.id}"
    lines = [
        app.purpose,
        field_line(ctx.labels, "surface", humanize(app.surface)),
        field_line(ctx.labels, "audience", app.audience),
        field_line(ctx.labels, "roles", [_role_name(ctx.brief, r) for r in app.roles]),
    ]
    return ViewRow(
        id=row_id,
        label=app.name,
        value=list_value(lines),
        badges=badges_for(app, ctx.labels),
        tier=app.tier,
        history_available=_has_history(app),
        needs_review=app.status == "disputed",
        changed=ctx.is_changed(row_id),
        group=ctx.t("view.group.apps"),
    )


def _role_row(ctx: _Ctx, role: RoleRecord) -> ViewRow:
    row_id = f"roles.{role.id}"
    lines = [
        field_line(ctx.labels, "goal", role.goal),
        field_line(ctx.labels, "permissions", role.rough_permissions),
    ]
    return ViewRow(
        id=row_id,
        label=role.name_in_user_words,
        value=list_value(lines),
        badges=badges_for(role, ctx.labels),
        tier=role.tier,
        history_available=_has_history(role),
        needs_review=role.status == "disputed",
        changed=ctx.is_changed(row_id),
        group=ctx.t("view.group.roles"),
    )


def _apps_roles_rows(ctx: _Ctx) -> list[ViewRow]:
    return [_app_row(ctx, a) for a in ctx.brief.apps] + [_role_row(ctx, r) for r in ctx.brief.roles]


def _family_label(ctx: _Ctx, family: str | None) -> str:
    if not family:
        return ctx.t("view.group.other")
    fam = ctx.catalog.families.get(family)
    return fam.label.get(ctx.labels.language) if fam else humanize(family)


def _feature_group(ctx: _Ctx, feature: FeatureRecord) -> str:
    names = {a.id: a.name for a in ctx.brief.apps}
    apps = [names.get(ref, ref) for ref in feature.apps]
    app_part = ", ".join(apps) if apps else ctx.t("view.group.all_apps")
    return f"{app_part} › {_family_label(ctx, feature.family)}"


def _feature_chip(ctx: _Ctx, feature: FeatureRecord) -> str:
    status = ctx.t("scope." + feature.scope_status.replace("-", "_"))
    if feature.scope_status not in _PRIORITY_AFTER_STATUS:
        return status
    priority = ctx.t(f"priority.{feature.priority}")
    return status if priority == status else ctx.t("view.sep").join([status, priority])


def _feature_row(ctx: _Ctx, feature: FeatureRecord) -> ViewRow:
    row_id = f"scope.{feature.id}"
    extra: list[BadgeKind] = []
    if feature.custom and not feature.provenance:
        extra.append("suggestion")
    awaiting = feature.custom and feature.status != "confirmed"
    return ViewRow(
        id=row_id,
        label=feature.name,
        value=text_value(feature.intent),
        badges=badges_for(feature, ctx.labels, extra=extra),
        tier=feature.tier,
        history_available=_has_history(feature),
        needs_review=awaiting or feature.status == "disputed",
        changed=ctx.is_changed(row_id),
        group=_feature_group(ctx, feature),
        status_chip=_feature_chip(ctx, feature),
    )


def _features_rows(ctx: _Ctx) -> list[ViewRow]:
    app_order = {a.id: i for i, a in enumerate(ctx.brief.apps)}
    priority_order = {"must": 0, "nice": 1, "later": 2}
    status_order = {"in": 0, "needs-engineering": 1, "later": 2, "out": 3, "unsupported": 4}

    def sort_key(f: FeatureRecord) -> tuple[int, str, str, int, int]:
        first_app = min((app_order.get(a, 999) for a in f.apps), default=999)
        family = _family_label(ctx, f.family)
        status, priority = status_order[f.scope_status], priority_order[f.priority]
        return (first_app, family, _feature_group(ctx, f), status, priority)

    ordered = sorted(ctx.brief.scope, key=sort_key)
    return [_feature_row(ctx, f) for f in ordered]


def _reference_row(ctx: _Ctx, ref: ReferenceRecord) -> ViewRow:
    row_id = f"references.{ref.label}"
    lines = [
        field_line(ctx.labels, "borrow", ref.borrow),
        field_line(ctx.labels, "avoid_ref", ref.avoid),
    ]
    return ViewRow(
        id=row_id,
        label=ref.label,
        value=list_value(lines),
        badges=badges_for(ref, ctx.labels),
        tier=ref.tier,
        history_available=_has_history(ref),
        changed=ctx.is_changed(row_id),
    )


def _look_rows(ctx: _Ctx) -> list[ViewRow]:
    return _dict_rows(ctx, "experience", ctx.brief.experience) + [
        _reference_row(ctx, r) for r in ctx.brief.references
    ]


def _integration_row(ctx: _Ctx, integ: IntegrationRecord) -> ViewRow:
    row_id = f"integrations.{integ.name}"
    lines = [ctx.t(f"view.pref.{integ.preference}"), field_line(ctx.labels, "notes", integ.notes)]
    return ViewRow(
        id=row_id,
        label=integ.name,
        value=list_value(lines),
        badges=badges_for(integ, ctx.labels),
        tier=integ.tier,
        history_available=_has_history(integ),
        needs_review=integ.status == "disputed",
        changed=ctx.is_changed(row_id),
    )


def _integrations_rows(ctx: _Ctx) -> list[ViewRow]:
    return [_integration_row(ctx, i) for i in ctx.brief.integrations] + _dict_rows(
        ctx, "data", ctx.brief.data
    )


def _glossary_row(ctx: _Ctx, term: GlossaryRecord) -> ViewRow:
    row_id = f"glossary.{term.term}"
    lines = [
        field_line(ctx.labels, "synonyms", term.synonyms),
        field_line(ctx.labels, "avoid", term.avoid),
    ]
    return ViewRow(
        id=row_id,
        label=term.term,
        value=list_value(lines),
        badges=badges_for(term, ctx.labels),
        tier=term.tier,
        history_available=_has_history(term),
        changed=ctx.is_changed(row_id),
    )


def _fact_row(ctx: _Ctx, fact: DomainFact) -> ViewRow:
    """A captured detail: read from the user's answers, so it is shown but not edited here."""
    row_id = f"domain_facts.{fact.id}"
    topic = fact.topics[0] if fact.topics else fact.type
    return ViewRow(
        id=row_id,
        label=topic,
        value=text_value(fact.statement),
        badges=badges_for(fact, ctx.labels),
        tier=fact.tier,
        editable=False,
        history_available=_has_history(fact),
        changed=ctx.is_changed(row_id),
        group=topic,
    )


def _assumption_row(ctx: _Ctx, a: AssumptionRecord) -> ViewRow:
    row_id = f"assumptions.{a.id}"
    chip = [ctx.t(f"view.impact.{a.impact}")]
    if a.decision != "proposed":
        chip.append(ctx.t(f"view.decision.{a.decision}"))
    return ViewRow(
        id=row_id,
        label=a.claim,
        value=list_value(
            [
                field_line(ctx.labels, "reason", a.reason),
                field_line(ctx.labels, "what_changes", a.what_changes),
            ]
        ),
        badges=badges_for(
            a, ctx.labels, extra=["assumed", *(["typical"] if a.basis == "typical" else [])]
        ),
        tier=a.tier,
        history_available=_has_history(a),
        needs_review=a.impact == "high" and a.decision == "proposed",
        changed=ctx.is_changed(row_id),
        group=ctx.t(f"view.theme.{a.theme}"),
        status_chip=ctx.t("view.sep").join(chip),
    )


def _module_row(ctx: _Ctx, m: ModuleRecord) -> ViewRow:
    """One row per module (ADR 0017): what it holds in a line, the person's accept in the row's actions. The deeper
    browsing and editing is the Structure tab."""
    row_id = f"modules.{m.id}"
    b = ctx.brief
    screens = sum(1 for s in b.screens if s.module == m.id)
    inside = {s.id for s in b.screens if s.module == m.id}
    flows = sum(1 for f in b.flows if any(st.screen in inside for st in f.steps))
    entities = sum(1 for e in b.entities if e.module == m.id)
    counts = ctx.t(
        "view.structure.counts", screens=str(screens), flows=str(flows), entities=str(entities)
    )
    accepted = m.decision == "accepted"
    return ViewRow(
        id=row_id,
        label=m.name,
        value=text_value(counts),
        badges=badges_for(m, ctx.labels, extra=[] if accepted else ["assumed"]),
        tier=m.tier,
        editable=False,
        history_available=_has_history(m),
        needs_review=False,
        changed=ctx.is_changed(row_id),
        status_chip=ctx.t(f"view.decision.{m.decision}"),
        description=m.purpose,
    )


def _question_row(ctx: _Ctx, q: OpenQuestion) -> ViewRow:
    row_id = f"open_questions.{q.id}"
    return ViewRow(
        id=row_id,
        label=q.text,
        value=text_value(ctx.t("view.blocking")) if q.blocking else EMPTY,
        badges=badges_for(q, ctx.labels, has_value=False),
        tier=q.tier,
        history_available=_has_history(q),
        needs_review=q.blocking,
        changed=ctx.is_changed(row_id),
    )


def _lookup_row(ctx: _Ctx, rr: ResearchRequest) -> ViewRow:
    row_id = f"research_requests.{rr.id}"
    lines = [
        rr.question,
        field_line(ctx.labels, "where", rr.geography),
        field_line(ctx.labels, "languages", rr.source_languages),
    ]
    kind: BadgeKind = "you_said" if rr.user_added else "suggestion"
    return ViewRow(
        id=row_id,
        label=rr.shown_to_user_as or rr.topic,
        value=list_value(lines),
        badges=[_badge(ctx, kind)],
        changed=ctx.is_changed(row_id),
    )


def _badge(ctx: _Ctx, kind: BadgeKind) -> ViewBadge:
    return badge(kind, ctx.labels)


def _source_row(ctx: _Ctx, src: SourceRecord) -> ViewRow:
    row_id = f"sources.{src.id}"
    coverage = f"{round(src.coverage * 100)}%" if src.coverage else ""
    return ViewRow(
        id=row_id,
        label=src.name,
        value=list_value([humanize(src.kind), field_line(ctx.labels, "coverage", coverage)]),
        badges=[_badge(ctx, "from_document")],
        tier=src.tier,
        editable=False,
        history_available=_has_history(src),
        changed=ctx.is_changed(row_id),
    )


def _asset_row(ctx: _Ctx, asset: AssetRecord) -> ViewRow:
    row_id = f"assets.{asset.id}"
    return ViewRow(
        id=row_id,
        label=humanize(asset.kind),
        value=text_value(asset.summary),
        badges=[_badge(ctx, "from_document")],
        tier=asset.tier,
        editable=False,
        history_available=_has_history(asset),
        changed=ctx.is_changed(row_id),
    )


def _declined_row(ctx: _Ctx, d: DeclinedRecord) -> ViewRow:
    row_id = f"declined.{d.what}"
    return ViewRow(
        id=row_id,
        label=d.what,
        value=list_value([d.reason, field_line(ctx.labels, "instead", d.alternative)]),
        badges=badges_for(d, ctx.labels),
        tier=d.tier,
        editable=False,
        history_available=_has_history(d),
        changed=ctx.is_changed(row_id),
    )


def _section_rows(ctx: _Ctx) -> dict[str, list[ViewRow]]:
    b = ctx.brief
    return {
        "idea": _idea_rows(ctx),
        "who": _who_rows(ctx),
        "apps_roles": _apps_roles_rows(ctx),
        "features": _features_rows(ctx),
        "structure": [_module_row(ctx, m) for m in b.modules],
        "look": _look_rows(ctx),
        "access": _dict_rows(ctx, "access", b.access),
        "integrations_data": _integrations_rows(ctx),
        "limits": _dict_rows(ctx, "constraints", b.constraints),
        "words": [_glossary_row(ctx, g) for g in b.glossary],
        "details": [
            _fact_row(ctx, f) for f in sorted(b.domain_facts, key=lambda f: (f.topics[:1], f.id))
        ],
        "assumed": [_assumption_row(ctx, a) for a in b.assumptions],
        "unknown": [_question_row(ctx, q) for q in b.open_questions],
        "lookup": [_lookup_row(ctx, r) for r in b.research_requests],
        "sources": [_source_row(ctx, s) for s in b.sources]
        + [_asset_row(ctx, a) for a in b.assets],
        "cannot_build": [_declined_row(ctx, d) for d in b.declined],
    }


# ---------------------------------------------------------------------------------------------
# Header and footer
# ---------------------------------------------------------------------------------------------


def _status(session: SessionRecord) -> BriefStatus:
    if session.state == "APPROVED":
        return "approved"
    if session.state == "BRIEF_REVIEW":
        return "in_review"
    return "draft"


def _shorten(text: str, limit: int = TITLE_MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(",.;:") + "…"


def _item_text(ctx: _Ctx, items: Mapping[str, BriefItem], *keys: str) -> str:
    for key in keys:
        item = items.get(key)
        if item is not None and item.has_value():
            return plain_text(item.value, ctx.labels)
    return ""


def _idea_line(ctx: _Ctx) -> str:
    return ctx.brief.summary or _item_text(ctx, ctx.brief.idea, "summary")


def _title(ctx: _Ctx, session: SessionRecord) -> str:
    brand = _item_text(ctx, ctx.brief.experience, "brand.name", "brand_name", "brand")
    return brand or _shorten(_idea_line(ctx)) or _shorten(session.title) or ctx.t("view.untitled")


def _readiness_chips(ctx: _Ctx) -> list[ReadinessChip]:
    computed = compute_readiness(ctx.brief, ctx.catalog)
    chips: list[ReadinessChip] = []
    for consumer in _READINESS_CONSUMERS:
        stored = getattr(ctx.brief.readiness, consumer)
        entry = stored if stored.status == "pass" or stored.gaps else getattr(computed, consumer)
        ready = entry.status == "pass" and not entry.gaps
        name = ctx.t(f"readiness.{consumer}")
        label = (
            ctx.t("readiness.ready_for", consumer=name)
            if ready
            else ctx.t("readiness.gaps", consumer=name, count=len(entry.gaps))
        )
        gaps = [ctx.labels.slot_label(g) for g in entry.gaps]
        chips.append(
            ReadinessChip(consumer=consumer, label=label, ready=ready, gaps=gaps)  # type: ignore[arg-type]
        )
    return chips


def _footer(ctx: _Ctx, blockers: Sequence[str]) -> ViewFooter:
    lines: list[str] = []
    for blocker in blockers:
        text = blocker.strip()
        for key in (text, f"blocker.{text}"):
            if ctx.labels.has(key):
                text = ctx.t(key)
                break
        if text:
            lines.append(text)
    return ViewFooter(
        primary_label=ctx.t("ui.looks_right"),
        secondary_label=ctx.t("ui.change_something"),
        approvable=not lines,
        blockers=lines,
    )


def _baseline_paths(baseline: BriefVersionRecord | None, brief: Brief) -> list[str]:
    return diff_changed_paths(baseline.brief, brief) if baseline is not None else []


def _changed_sections(paths: Iterable[str]) -> list[str]:
    found = {view_section_for_path(p) for p in paths}
    return [s for s in SECTION_IDS if s in found]


# ---------------------------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------------------------


def build_view_model(
    brief: Brief,
    *,
    catalog: Catalog,
    language: str,
    session: SessionRecord,
    blockers: Sequence[str],
    reviewed_sections: Collection[str] = (),
    changed_paths: Collection[str] = (),
    baseline: BriefVersionRecord | None = None,
    brief_hash: str = "",
    section_hashes: Mapping[str, str] | None = None,
) -> BriefViewModel:
    """Build the Brief page for ``language`` (``en`` or ``hi``; anything else is English).

    ``changed_paths`` (the last patch) and ``baseline`` (the approved version) both mark rows *Updated*;
    ``changed_since_approval`` lists the view sections that moved against ``baseline``. ``blockers``
    are shown one per line (a ``blocker.*`` string key is localized, any other text is shown as given).
    ``reviewed`` / ``of_total`` count sections that have rows.
    """
    labels = Labels(catalog, language)
    since_approval = _baseline_paths(baseline, brief)
    order = {sid: i for i, sid in enumerate(catalog.slot_ids_by_order())}
    ctx = _Ctx(brief, catalog, labels, frozenset([*changed_paths, *since_approval]), order)
    rows_by_section = _section_rows(ctx)
    reviewed = set(reviewed_sections)

    sections: list[ViewSection] = []
    block = brief.meta.interview
    structure_on = block is not None and block.size_band in ("small", "medium", "large")
    for sid in SECTION_IDS:
        rows = rows_by_section[sid]
        if sid == "structure" and not rows and not structure_on:
            continue  # a Brief that never held a structure (or from before it existed) has no such section
        sections.append(
            ViewSection(
                id=sid,  # type: ignore[arg-type]
                title=labels.t(f"section.{sid}"),
                rows=rows,
                reviewed=sid in reviewed,
                empty_text=labels.t(f"view.empty.{sid}") if not rows else "",
                count=len(rows),
            )
        )
    status = _status(session)
    non_empty = [s for s in sections if s.rows]
    header = ViewHeader(
        title=_title(ctx, session),
        idea_line=_idea_line(ctx),
        version=brief.version,
        status=status,
        status_label=labels.t(f"status.{status}"),
        readiness=_readiness_chips(ctx),
        reviewed=sum(1 for s in non_empty if s.reviewed),
        of_total=len(non_empty),
        degraded=brief.meta.degraded,
    )
    return BriefViewModel(
        language=labels.language,
        session_id=session.id,
        header=header,
        sections=sections,
        footer=_footer(ctx, blockers),
        brief_hash=brief_hash,
        section_hashes=dict(section_hashes or {}),
        changed_since_approval=_changed_sections(since_approval),  # type: ignore[arg-type]
    )


__all__ = ["build_view_model"]
