"""B2: the deterministic baseline. A complete, schema-valid spec built from the Domain Pack and the
slot snapshot, with no model involved: **valid by construction**, so the LLM only refines it.

Pipeline (all pure, same request -> same spec):

1. read the slots; resolve the pack (``idea.archetype``; unknown falls back to ``custom``);
2. ``select_features`` (feature half of the pack expansion, in ``mde_content``);
3. keep one app surface (``scope.apps``) and the roles of the app (``users.roles``);
4. sign-in variant (``auth.method``), payments switch (``pay.*``);
5. scope guard: more screens than ``policy.limits.max_screens`` -> phase one stays, the rest is
   the backlog;
6. ids in navigation order, ``@key`` rewritten, blocks assembled (design from the theme, behavior
   from ``behavior.*``, assumptions, research additions, policy copy-in).

Surfaces: ``scope.apps`` "main" is the first surface of the pack; ``admin`` is an alias of
``business`` (D-029); the first resolved app is built, the others become backlog items (separate
projects), never extra screens.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Literal

from mde_content import packs as content_packs
from mde_content.policies import PolicyInheritanceError, inheritance_chain
from mde_content.stack import capability, load_stack_profile
from mde_contracts.content import DomainPack, PackNavNode, PackScreen
from mde_contracts.engines import BuildRequest
from mde_contracts.policy import Policy
from mde_contracts.research import BrandFinding, ResearchNote
from mde_contracts.spec import (
    AcceptanceBlock,
    ApiBlock,
    ApiOperation,
    AppBlock,
    AppSpec,
    AssumptionItem,
    AssumptionsBlock,
    BacklogItem,
    BehaviorBlock,
    BuildSequence,
    DataBlock,
    DesignBlock,
    Entity,
    FlowBlock,
    Integration,
    IntegrationsBlock,
    NavigationBlock,
    NavNode,
    RevisionBlock,
    RolesBlock,
    ScreenBlock,
    SpecHeader,
)
from mde_spec_builder import design as design_mod
from mde_spec_builder.assemble import assign_flow_ids, assign_screen_ids, number_navigation
from mde_spec_builder.pack_expand import (
    apply_auth_method,
    is_role,
    nav_screen_keys,
    restrict_to_surface,
    scope_guard,
    surfaces_of,
)
from mde_spec_builder.research_additions import (
    brand_applies,
    build_research_additions,
    research_feature_map,
)
from mde_spec_builder.rules import Rules, load_rules
from mde_spec_builder.sequence import build_sequence
from mde_spec_builder.slots import SlotView, render_value
from mde_spec_builder.summary import compose_summary

ResearchStatusName = Literal["ok", "partial", "unavailable", "skipped"]
FALLBACK_ARCHETYPE = "custom"
PLACEHOLDER_NAME = "My App"
MAX_PURPOSE = 240
MAX_AUDIENCE = 160
MAX_NAME = 60
_SPACE = re.compile(r"\s+")
_SLUG = re.compile(r"[^a-z0-9]+")


def _oneline(text: str, limit: int) -> str:
    cleaned = _SPACE.sub(" ", text).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _slug(text: str) -> str:
    return _SLUG.sub("_", text.lower()).strip("_")


# --------------------------------------------------------------------------- pack selection


def resolve_pack_id(slots: SlotView) -> str:
    wanted = slots.text("idea.archetype", FALLBACK_ARCHETYPE)
    return wanted if wanted in content_packs.pack_ids() else FALLBACK_ARCHETYPE


def resolve_apps(apps: Sequence[str], surfaces: Sequence[str]) -> tuple[list[str], list[str]]:
    """(resolved surfaces in the order asked, app ids that match no surface).

    ``main`` is the first surface of the pack; ``admin`` is an alias of ``business`` when the
    pack has no ``admin`` surface (D-029).
    """
    resolved: list[str] = []
    unknown: list[str] = []
    for app in apps:
        key = app.strip().lower()
        if key == "main":
            surface = surfaces[0]
        elif key in surfaces:
            surface = key
        elif key == "admin" and "business" in surfaces:
            surface = "business"
        else:
            unknown.append(app)
            continue
        if surface not in resolved:
            resolved.append(surface)
    return (resolved or [surfaces[0]]), unknown


def _payment_features(pack: DomainPack, rules: Rules) -> set[str]:
    prefix = rules.payments.integration_prefix
    return {
        fid
        for fid, parts in pack.feature_parts.items()
        if any(i.startswith(prefix) for i in parts.integrations)
    }


def select_feature_ids(
    pack: DomainPack,
    slots: SlotView,
    rules: Rules,
    note: ResearchNote | None = None,
    excluded: Collection[str] = (),
) -> tuple[list[str] | None, dict[str, str]]:
    """The feature selection for ``select_features`` and the features research added.

    ``None`` keeps the pack defaults. Unknown ids are ignored (valid by construction). Included
    research suggestions that name a feature of this pack (``applies_to`` of the form
    ``scope.features:<id>``) switch that feature on, unless the person already selected it; the
    second value maps each such feature to its suggestion. With ``pay.model`` set to ``none`` or
    ``on_delivery`` the online-payment features are left out (research cannot bring them back).
    """
    known = {f.id for f in pack.features}
    chosen = slots.items("scope.features")
    if chosen is not None:
        chosen = [f for f in dict.fromkeys(chosen) if f in known]
    base = chosen if chosen is not None else content_packs.default_features(pack)
    on = set(base) | set(content_packs.core_features(pack))
    added = research_feature_map(note, excluded, known, on)
    if added:
        chosen = [*base, *added]
    if slots.text("pay.model") in ("none", "on_delivery"):
        off = _payment_features(pack, rules)
        chosen = [f for f in (chosen if chosen is not None else base) if f not in off]
        added = {f: ref for f, ref in added.items() if f not in off}
    return chosen, added


def kept_role_ids(
    pack: DomainPack, expanded: DomainPack, surface: str, slots: SlotView, *, signed_in: bool
) -> tuple[list[str], list[str]]:
    """Roles of the built app: the surface's roles, narrowed by ``users.roles`` when it is set.

    The first role of the surface always stays. A role that gates a screen of a *selected* feature
    stays too (the feature has to work). With no sign-in there are no roles beyond the first.
    Returns (roles kept, roles kept only because a selected feature needs them).
    """
    surface_roles = [r.id for r in pack.roles if r.surface == surface]
    if not surface_roles:
        return [], []
    main = surface_roles[0]
    if not signed_in:
        return [main], []
    wanted = slots.items("users.roles")
    if wanted is None:
        return surface_roles, []
    chosen = {f"ROLE-{_slug(v)}" for v in wanted}
    needed = {
        s.auth
        for s in expanded.screens
        if s.feature is not None and is_role(s.auth) and s.surface == surface
    }
    kept = [r for r in surface_roles if r == main or r in chosen or r in needed]
    forced = [r for r in kept if r != main and r not in chosen]
    return kept, forced


def expand_pack(req: BuildRequest, slots: SlotView, rules: Rules) -> ExpandedPack:
    """Everything of B2 that happens at pack level (steps 1 to 5 above)."""
    pack_id = resolve_pack_id(slots)
    full = content_packs.load_pack(pack_id)
    chosen, research_features = select_feature_ids(
        full, slots, rules, req.research, req.excluded_research_ids
    )
    expanded = content_packs.select_features(pack_id, chosen)
    surfaces = surfaces_of(expanded) or ["customer"]
    apps = slots.items("scope.apps") or ["main"]
    resolved, unknown = resolve_apps(apps, surfaces)
    built = resolved[0]

    method = auth_method_of(slots)
    signed_in = method != "none"
    roles, forced = kept_role_ids(full, expanded, built, slots, signed_in=signed_in)
    narrowed, other = restrict_to_surface(expanded, built, roles)
    user_entity = _user_entity(narrowed)
    shaped = apply_auth_method(narrowed, method, rules, user_entity) if method else narrowed
    cap = req.policy.limits.max_screens
    guarded, overflow = scope_guard(shaped, cap)

    backlog: list[BacklogItem] = []
    role_names = {}
    for role in expanded.roles:
        role_names.setdefault(role.surface, role.name)
    for surface in resolved[1:]:
        hints = other.get(surface, [])
        backlog.append(
            BacklogItem(
                title=f"{role_names.get(surface, surface.replace('_', ' ').capitalize())} app",
                reason="separate app: build it as its own project",
                screens_hint=hints,
            )
        )
    backlog += [
        BacklogItem(title=f"{app.strip().capitalize()} app", reason="separate app: no match")
        for app in unknown
    ]
    backlog += overflow
    return ExpandedPack(
        pack=guarded,
        pack_id=pack_id,
        surface=built,
        method=method,
        backlog=backlog,
        roles=roles,
        forced_roles=[r.name for r in expanded.roles if r.id in forced],
        research_features=research_features,
    )


class ExpandedPack:
    """The pack after step 5 with what the later steps need to know about it."""

    def __init__(
        self,
        *,
        pack: DomainPack,
        pack_id: str,
        surface: str,
        method: str | None,
        backlog: list[BacklogItem],
        roles: list[str],
        forced_roles: list[str],
        research_features: dict[str, str],
    ) -> None:
        self.pack = pack
        self.pack_id = pack_id
        self.surface = surface
        self.method = method
        self.backlog = backlog
        self.roles = roles
        self.forced_roles = forced_roles
        self.research_features = research_features  # feature id -> suggestion that added it


def auth_method_of(slots: SlotView) -> str | None:
    """``auth.method``, with ``auth.sso == required`` winning (company sign-in)."""
    if slots.text("auth.sso") == "required":
        return "sso"
    return slots.text("auth.method") or None


def _user_entity(pack: DomainPack) -> str | None:
    me = next((o for o in pack.api_operations if o.id == "API-getMe"), None)
    if me is not None and me.response_entity is not None:
        return me.response_entity
    return pack.entities[0].id if pack.entities else None


# --------------------------------------------------------------------------- spec blocks


def _convert_nav(node: PackNavNode, ids: Mapping[str, str]) -> NavNode:
    return NavNode(
        id=node.id,
        route=node.route,
        kind=node.kind,
        screen=ids[node.screen[1:]] if node.screen is not None else None,
        label=node.label,
        auth=node.auth,
        children=[_convert_nav(child, ids) for child in node.children],
    )


def _public_nav(node: PackNavNode) -> PackNavNode:
    return node.model_copy(
        update={"auth": "public", "children": [_public_nav(c) for c in node.children]}
    )


def _screen_block(
    screen: PackScreen,
    ids: Mapping[str, str],
    flow_of: Mapping[str, str],
    research_ref: str | None = None,
) -> ScreenBlock:
    actions = [
        a.model_copy(
            update={"go": ids[a.go[1:]] if a.go is not None and a.go.startswith("@") else a.go}
        )
        for a in screen.actions
    ]
    return ScreenBlock(
        id=ids[screen.key],
        route=screen.route,
        surface=screen.surface,
        recipe=screen.recipe,
        purpose=screen.purpose,
        auth=screen.auth,
        flow=flow_of.get(screen.key),
        data=list(screen.data),
        sections=list(screen.sections),
        actions=actions,
        states={k: dict(v) for k, v in screen.states.items()},
        texts=dict(screen.texts),
        checks=list(screen.checks),
        source="research" if research_ref else "default",
        research_ref=research_ref,
    )


def screens_and_flows(
    pack: DomainPack, research_features: Mapping[str, str] | None = None
) -> tuple[list[ScreenBlock], list[FlowBlock], NavNode, dict[str, str]]:
    """Ids in navigation order, ``@key`` rewritten, the navigation tree renumbered NAV-001..."""
    keys = {s.key for s in pack.screens}
    in_nav = [k for k in nav_screen_keys(pack.navigation) if k in keys]
    extra = [s.key for s in pack.screens if s.key not in in_nav]
    ids = assign_screen_ids(in_nav, extra)
    flow_ids = assign_flow_ids(f.key for f in pack.flows)
    flow_of: dict[str, str] = {}
    for flow in pack.flows:
        for key in flow.screens:
            flow_of.setdefault(key, flow_ids[flow.key])
    tags = research_features or {}
    screens = sorted(
        (_screen_block(s, ids, flow_of, tags.get(s.feature or "")) for s in pack.screens),
        key=lambda b: b.id,
    )
    flows: list[FlowBlock] = []
    for flow in pack.flows:
        members = [ids[k] for k in flow.screens]
        flows.append(
            FlowBlock(
                id=flow_ids[flow.key],
                name=flow.name,
                primary=flow.primary,
                screens=members,
                start=members[0],
                end=members[-1],
                purpose=flow.purpose,
            )
        )
    nav = number_navigation(_convert_nav(pack.navigation, ids))
    return screens, flows, nav, ids


# --------------------------------------------------------------------------- design


def design_block(
    req: BuildRequest,
    slots: SlotView,
    pack: DomainPack,
    rules: Rules,
    *,
    brand: BrandFinding | None,
) -> DesignBlock:
    """Tokens of the chosen theme with the accent, mode, navigation style and brand applied."""
    known = design_mod.theme_ids()
    direction = slots.text("design.direction")
    if direction not in known:
        nearest = brand.nearest_direction if brand is not None else None
        direction = nearest if nearest in known else (pack.default_theme or known[0])
    theme = design_mod.get_theme(direction)
    tokens = theme.tokens
    accent = _accent(slots, rules, brand)
    if accent is not None:
        tokens = design_mod.apply_primary(tokens, accent)
    nav_style = slots.text("design.nav_style")
    if not re.fullmatch(r"[a-z][a-z-]{1,30}", nav_style):
        nav_style = theme.nav_style
    variants = {k: list(v) for k, v in rules.design.component_variants.items()}
    mode = slots.text("design.mode", "system")
    if mode in ("system", "light", "dark"):
        variants[rules.design.appearance_key] = [mode]
    if brand is not None and brand.fonts:
        variants[rules.design.font_key] = [brand.fonts[0]]
    return DesignBlock(
        direction=direction,
        tokens=tokens,
        nav_style=nav_style,
        imagery_rules=list(rules.design.imagery_rules),
        component_variants=variants,
    )


def _accent(slots: SlotView, rules: Rules, brand: BrandFinding | None) -> str | None:
    wanted = slots.text("design.accent").strip()
    if wanted:
        named = rules.design.color_names.get(wanted.lower())
        found = design_mod.normalize_hex(named or wanted)
        if found is not None:
            return found
    if brand is not None:
        for color in brand.colors:
            found = design_mod.normalize_hex(color)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- integrations


def _needs_dev_build(integration_id: str, capability_id: str | None = None) -> bool:
    cap_id = capability_id or integration_id.removeprefix("INT-")
    try:
        found = capability(cap_id)
    except KeyError:
        return False
    return found.needs_dev_build or not found.supported


def _payment_integration(
    item: Integration, slots: SlotView, policy: Policy, rules: Rules
) -> Integration:
    pay = rules.payments
    raw = slots.text("pay.provider").strip()
    mode = "mock"
    provider: str | None = None
    note = pay.mock_note
    if raw and raw.lower() != "mock":
        real_asked = raw.lower().startswith("real:")
        provider = raw.split(":", 1)[1].strip() if real_asked else raw
        if real_asked and policy.mode == "enterprise":
            mode, note = "real", pay.real_note
        else:
            mode, note = "adapter", pay.adapter_note
    extra = pay.model_notes.get(slots.text("pay.model"), "")
    lead = item.notes if mode == "mock" else None  # the pack's mock wording no longer applies
    notes = " ".join(part for part in (lead, note if mode != "mock" else None, extra) if part)
    if not notes:
        notes = note
    return item.model_copy(
        update={
            "mode": mode,
            "provider": provider,
            "notes": notes,
            "needs_dev_build": mode != "mock" and _needs_dev_build(item.id),
        }
    )


def _named_integrations(slots: SlotView, existing: set[str]) -> list[Integration]:
    items = slots.items("integrations.list") or []
    out: list[Integration] = []
    for name in items:
        slug = _slug(name)
        integration_id = f"INT-{slug}"
        if not slug or not slug[0].isalpha() or integration_id in existing:
            continue
        existing.add(integration_id)
        out.append(
            Integration(
                id=integration_id,
                name=_oneline(name, MAX_NAME),
                purpose=f"Connect the app to {_oneline(name, MAX_NAME)}.",
                mode="adapter",
                provider=_oneline(name, MAX_NAME),
                notes="A typed adapter with a mock behind it; the real connection is set up at release.",
                source="brief",
            )
        )
    return out


def integrations_block(
    pack: DomainPack, slots: SlotView, policy: Policy, rules: Rules
) -> IntegrationsBlock:
    prefix = rules.payments.integration_prefix
    items: list[Integration] = []
    for item in pack.integrations:
        if item.id.startswith(prefix):
            items.append(_payment_integration(item, slots, policy, rules))
        else:
            items.append(item)
    items += _named_integrations(slots, {i.id for i in items})
    return IntegrationsBlock(items=items)


# --------------------------------------------------------------------------- behavior


def _compliance_flags(slots: SlotView, rules: Rules) -> list[str]:
    flags = slots.items("compliance.flags") or []
    return [f for f in dict.fromkeys(flags) if f in rules.compliance_flags]


def _policy_items(req: BuildRequest, slots: SlotView) -> dict[str, list[str]]:
    """Policy values that matter to the app (copy-in), by rule name."""
    out: dict[str, list[str]] = {}
    if req.policy.banned_libraries:
        out["banned_libraries"] = list(req.policy.banned_libraries)
    regions = slots.items("regions") or list(req.policy.allowed_regions)
    if regions:
        out["allowed_regions"] = list(regions)
    classification = slots.text("data.classification")
    if classification:
        out["data_classification"] = [classification]
    if slots.text("auth.sso") == "required":
        out["sso"] = ["required"]
    return out


def behavior_block(
    req: BuildRequest,
    slots: SlotView,
    pack: DomainPack,
    screens: Sequence[ScreenBlock],
    flows: Sequence[FlowBlock],
    integrations: Sequence[Integration],
    rules: Rules,
    language: str,
) -> BehaviorBlock:
    b = rules.behavior
    offline_mode = slots.text("behavior.offline", "basic")
    offline = b.offline.get(offline_mode, b.offline["basic"])
    flags = _compliance_flags(slots, rules)

    notifications: list[str] = []
    notify = slots.flag("behavior.notifications", True)
    kinds = next((e.values for e in pack.enums if e.name == "NotificationKind"), [])
    if notify:
        notifications = (
            [f"{k.replace('_', ' ')} notifications" for k in kinds]
            if kinds
            else list(b.notifications_on)
        )
    else:
        notifications = [b.notifications_off_note]

    analytics_on = slots.flag("behavior.analytics", False) and not any(
        rules.compliance_flags[f].analytics_off for f in flags
    )
    events: list[str] = []
    if analytics_on:
        events = list(b.analytics_base)
        events += [b.analytics_flow_event.format(flow=_slug(f.name)) for f in flows]
        events = list(dict.fromkeys(events))[:14]

    prompts = [b.permission_prompts[i.id] for i in integrations if i.id in b.permission_prompts]
    conventions = list(b.error_conventions)
    accessibility = list(b.accessibility["standard"])
    if slots.text("a11y", "standard") == "enhanced":  # enhanced = standard plus the stricter checks
        accessibility += b.accessibility.get("enhanced", [])
    for flag in flags:
        extra = rules.compliance_flags[flag].behavior
        notifications += extra.get("notifications", [])
        prompts += extra.get("permission_prompts", [])
        conventions += extra.get("error_conventions", [])
    for name, items in _policy_items(req, slots).items():
        rule = rules.policy_copy[name]
        if rule.behavior_error_convention:
            conventions.append(rule.behavior_error_convention.format(items=", ".join(items)))
    deep_links = sorted({s.route for s in screens if "[" in s.route})
    return BehaviorBlock(
        offline=offline,
        locale=language,
        deep_links=deep_links,
        notifications=list(dict.fromkeys(notifications)),
        analytics_events=events,
        permission_prompts=list(dict.fromkeys(prompts)),
        error_conventions=list(dict.fromkeys(conventions)),
        accessibility=accessibility,
    )


# --------------------------------------------------------------------------- app, acceptance, assumptions


def app_block(
    req: BuildRequest,
    slots: SlotView,
    roles: Sequence[str],
    pack: DomainPack,
    rules: Rules,
    *,
    brand: BrandFinding | None,
    direction: str,
    language: str,
) -> AppBlock:
    name = _oneline(slots.text("brand.name", PLACEHOLDER_NAME), MAX_NAME) or PLACEHOLDER_NAME
    purpose = slots.text("idea.summary") or req.brief.app_summary
    users_row = req.brief.row("users")
    if users_row is not None and users_row.text.strip():
        audience = users_row.text
    else:
        names = [r.name for r in pack.roles if r.id in roles] or [r.name for r in pack.roles]
        audience = ", ".join(names)
    tone = rules.design.theme_tones.get(direction, "friendly")
    if brand is not None and brand.tone_words:
        tone = ", ".join(brand.tone_words[:3])
    return AppBlock(
        name=name,
        purpose=_oneline(purpose, MAX_PURPOSE) or "A mobile app",
        audience=_oneline(audience, MAX_AUDIENCE) or "People who use the app",
        tone=_oneline(tone, 60),
        language=language,
    )


def assumptions_block(
    req: BuildRequest,
    slots: SlotView,
    rules: Rules,
    note: ResearchNote | None,
    excluded: Sequence[str],
    *,
    forced: Sequence[str] = (),
    affects: dict[str, list[str]] | None = None,
) -> AssumptionsBlock:
    """Every assumed slot with a one-line impact, the policy copy-in lines, the compliance
    flags, and the research additions with their toggles."""
    labels = {a.slot_id: a.label for a in req.brief.assumptions}
    items: list[AssumptionItem] = []
    for record in slots.assumed():
        slot_note = rules.slot_notes.get(record.slot_id)
        items.append(
            AssumptionItem(
                slot_id=record.slot_id,
                label=labels.get(record.slot_id)
                or (slot_note.label if slot_note else record.slot_id),
                value_text=render_value(record.value),
                impact=slot_note.impact if slot_note else rules.default_impact,
            )
        )
    seen = {i.slot_id for i in items}
    for brief_item in req.brief.assumptions:
        if brief_item.slot_id not in seen and brief_item.included:
            items.append(
                AssumptionItem(
                    slot_id=brief_item.slot_id,
                    label=brief_item.label,
                    value_text=brief_item.value_text,
                    impact=brief_item.reason or rules.default_impact,
                )
            )
    for flag in _compliance_flags(slots, rules):
        rule = rules.compliance_flags[flag]
        items.append(
            AssumptionItem(
                slot_id=None, label=f"Flagged: {rule.label}", value_text=flag, impact=rule.impact
            )
        )
    for name, values in _policy_items(req, slots).items():
        rule = rules.policy_copy[name]
        items.append(
            AssumptionItem(
                slot_id=None,
                label=rule.label,
                value_text=", ".join(values),
                impact=rule.impact.format(items=", ".join(values)),
            )
        )
    for role_name in forced:
        items.append(
            AssumptionItem(
                slot_id=None,
                label=rules.forced_role.label.format(name=role_name),
                value_text=role_name,
                impact=rules.forced_role.impact,
            )
        )
    status, status_note = research_status(note)
    return AssumptionsBlock(
        assumptions=items,
        research_additions=build_research_additions(note, excluded, affects),
        research_status=status,
        research_note=status_note,
    )


def research_status(
    note: ResearchNote | None,
) -> tuple[ResearchStatusName, str | None]:
    if note is None:
        return "skipped", "research: skipped"
    if note.status == "unavailable":
        reason = note.unavailable_reason or "unavailable"
        return "unavailable", f"research: unavailable ({reason})"
    if note.status == "partial" and note.unavailable_reason:
        return "partial", f"research: partial ({note.unavailable_reason})"
    return note.status, None


def acceptance_block(
    req: BuildRequest,
    slots: SlotView,
    screens: Sequence[ScreenBlock],
    flows: Sequence[FlowBlock],
    integrations: Sequence[Integration],
    rules: Rules,
) -> AcceptanceBlock:
    a = rules.acceptance
    checks = list(a.global_checks)
    if any(s.auth != "public" for s in screens):
        checks.append(a.signed_in)
    primary = next((f for f in flows if f.primary), None)
    if primary is not None:
        checks.append(f"The main journey ({primary.name}) can be completed from start to end")
    if any(i.mode == "mock" for i in integrations):
        checks.append(a.mock_integrations)
    for flag in _compliance_flags(slots, rules):
        checks += rules.compliance_flags[flag].acceptance
    for name, values in _policy_items(req, slots).items():
        checks.append(rules.policy_copy[name].acceptance.format(items=", ".join(values)))
    return AcceptanceBlock(checks=list(dict.fromkeys(checks)))


def policy_inherits(policy: Policy) -> list[str]:
    """Policy ids copied into the file (Enterprise); the file stays self-contained."""
    if policy.mode != "enterprise":
        return []
    try:
        return inheritance_chain(policy)
    except (PolicyInheritanceError, KeyError):
        return [policy.id] + ([policy.inherits] if policy.inherits else [])


# --------------------------------------------------------------------------- research features


class TaggedFeatures:
    """What the features that research switched on own, tagged ``source: research``."""

    def __init__(
        self,
        entities: list[Entity],
        operations: list[ApiOperation],
        integrations: IntegrationsBlock,
        affects: dict[str, list[str]],
    ) -> None:
        self.entities = entities
        self.operations = operations
        self.integrations = integrations
        self.affects = affects


def tag_research_features(
    pack: DomainPack,
    research_features: Mapping[str, str],
    screens: Sequence[ScreenBlock],
    integrations: IntegrationsBlock,
) -> TaggedFeatures:
    """Tag the entities, operations and integrations that a research-added feature owns (its
    screens are tagged when they are built) and list everything it created under the suggestion
    ``affects``, so switching the addition off removes exactly those items."""
    owner: dict[str, str] = {}
    for feature, ref in research_features.items():
        parts = pack.feature_parts.get(feature)
        if parts is None:
            continue
        for item_id in (*parts.entities, *parts.operations, *parts.integrations):
            owner.setdefault(item_id, ref)
    affects: dict[str, list[str]] = {}
    for screen in screens:
        if screen.research_ref:
            affects.setdefault(screen.research_ref, []).append(screen.id)

    def note(item_id: str) -> None:
        affects.setdefault(owner[item_id], []).append(item_id)

    entities: list[Entity] = []
    for entity in pack.entities:
        if entity.id in owner:
            note(entity.id)
            entity = entity.model_copy(update={"source": "research"})
        entities.append(entity)
    operations: list[ApiOperation] = []
    for op in pack.api_operations:
        if op.id in owner:
            note(op.id)
            op = op.model_copy(update={"source": "research"})
        operations.append(op)
    items: list[Integration] = []
    for item in integrations.items:
        if item.id in owner:
            note(item.id)
            item = item.model_copy(update={"source": "research", "research_ref": owner[item.id]})
        items.append(item)
    return TaggedFeatures(
        entities, operations, integrations.model_copy(update={"items": items}), affects
    )


# --------------------------------------------------------------------------- the baseline


def build_baseline(req: BuildRequest) -> AppSpec:
    """The valid-by-construction baseline of a build request."""
    rules = load_rules()
    slots = SlotView.of(req)
    expanded = expand_pack(req, slots, rules)
    pack = expanded.pack
    if expanded.method == "none":
        pack = pack.model_copy(update={"navigation": _public_nav(pack.navigation)})
    screens, flows, nav, _ids = screens_and_flows(pack, expanded.research_features)
    language = slots.text("locale") or req.brief.language or "en"
    brand_in = brand_applies(req.research, req.excluded_research_ids)
    brand = req.research.brand if brand_in and req.research is not None else None

    design = design_block(req, slots, pack, rules, brand=brand)
    integrations = integrations_block(pack, slots, req.policy, rules)
    tagged = tag_research_features(pack, expanded.research_features, screens, integrations)
    integrations = tagged.integrations
    behavior = behavior_block(req, slots, pack, screens, flows, integrations.items, rules, language)
    data_source = rules.data_source.get(
        slots.text("data.source", "generated"), rules.data_source["generated"]
    )
    roles_block = RolesBlock(roles=list(pack.roles), permissions=[])
    spec = AppSpec(
        header=SpecHeader(
            spec_version=req.spec_version,
            app_id=req.app_id,
            mode=req.policy.mode,
            stack_profile=load_stack_profile().id,
            inherits=policy_inherits(req.policy),
            language=language,
            brief_hash=req.brief.hash,
            research_note_hash=req.research.hash if req.research is not None else None,
        ),
        summary="",
        app=app_block(
            req,
            slots,
            expanded.roles,
            pack,
            rules,
            brand=brand,
            direction=design.direction,
            language=language,
        ),
        design=design,
        navigation=NavigationBlock(root=nav),
        data=DataBlock(
            entities=tagged.entities,
            enums=list(pack.enums),
            seed_guidance=data_source.seed,
        ),
        api=ApiBlock(operations=tagged.operations, mock_guidance=data_source.mock),
        roles=roles_block,
        integrations=integrations,
        behavior=behavior,
        screens=screens,
        flows=flows,
        build=BuildSequence(steps=[]),
        acceptance=acceptance_block(req, slots, screens, flows, integrations.items, rules),
        assumptions=assumptions_block(
            req,
            slots,
            rules,
            req.research,
            req.excluded_research_ids,
            forced=expanded.forced_roles,
            affects=tagged.affects,
        ),
        revision=RevisionBlock(
            version=req.spec_version,
            previous_version=req.previous_spec.header.spec_version if req.previous_spec else None,
        ),
        backlog=expanded.backlog,
    )
    spec = spec.model_copy(update={"build": build_sequence(spec)})
    return spec.model_copy(update={"summary": compose_summary(spec)})


__all__ = [
    "FALLBACK_ARCHETYPE",
    "build_baseline",
    "resolve_apps",
    "resolve_pack_id",
]
