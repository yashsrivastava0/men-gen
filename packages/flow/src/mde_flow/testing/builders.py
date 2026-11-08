"""Small deterministic builders for Briefs, specs, plans and policies (tests and fakes only)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mde_contracts.brief import AssumedItem, Brief, BriefFeature, BriefRow
from mde_contracts.common import Mode
from mde_contracts.plan import (
    LookPreview,
    PlainEntity,
    PlanView,
    ResearchAdditionView,
    ScreenMapGroup,
)
from mde_contracts.policy import GateRule, Policy, PolicyGates, PostHandoffPolicy
from mde_contracts.research import ResearchNote
from mde_contracts.slots import SlotData, SlotRecord
from mde_contracts.spec import (
    AcceptanceBlock,
    ApiBlock,
    AppBlock,
    AppSpec,
    AssumptionItem,
    AssumptionsBlock,
    BehaviorBlock,
    BuildSequence,
    DataBlock,
    DesignBlock,
    DesignTokens,
    Entity,
    FieldDef,
    FlowBlock,
    IntegrationsBlock,
    NavigationBlock,
    NavNode,
    ResearchAddition,
    RevisionBlock,
    Role,
    RolesBlock,
    ScreenBlock,
    SpecHeader,
    TypeStyle,
)
from mde_core.errors import NotFound
from mde_core.hashing import canonical_json, sha256_hex

# --------------------------------------------------------------------------- policies


def guided_policy(**kw: object) -> Policy:
    """The default Guided policy: both gates need the requester."""
    return Policy(id="guided-default", mode="guided", **kw)  # type: ignore[arg-type]


def enterprise_policy(
    *,
    policy_id: str = "enterprise-test",
    brief_approvers: Sequence[str] = ("requester",),
    plan_approvers: Sequence[str] = ("client_product_owner", "delivery_lead"),
    heavy_if: Sequence[str] = ("scope", "auth", "pay", "integrations"),
    max_new_screens_light: int = 5,
    reminder_hours: int | None = 24,
    research_enabled: bool = True,
    role_precedence: Sequence[str] = (),
) -> Policy:
    from mde_contracts.policy import ResearchPolicy

    return Policy(
        id=policy_id,
        org="example-org",
        mode="enterprise",
        role_precedence=list(role_precedence),
        research=ResearchPolicy(enabled=research_enabled, depth="standard"),
        gates=PolicyGates(
            brief=GateRule(approvers=list(brief_approvers), reminder_hours=reminder_hours),
            plan=GateRule(
                approvers=list(plan_approvers), full_file_view=True, reminder_hours=reminder_hours
            ),
            post_handoff=PostHandoffPolicy(
                heavy_if=list(heavy_if),  # type: ignore[arg-type]
                max_new_screens_light=max_new_screens_light,
            ),
        ),
    )


class PolicyBook:
    """A ``(policy_id) -> Policy`` resolver over a dict."""

    def __init__(self, *policies: Policy) -> None:
        self._by_id: dict[str, Policy] = {p.id: p for p in policies}

    def add(self, policy: Policy) -> None:
        self._by_id[policy.id] = policy

    def __call__(self, policy_id: str) -> Policy:
        try:
            return self._by_id[policy_id]
        except KeyError:
            raise NotFound(f"unknown policy {policy_id!r}") from None


# --------------------------------------------------------------------------- slots and Brief


def slot(
    slot_id: str, value: SlotData, *, state: str = "confirmed", source: str = "text"
) -> SlotRecord:
    return SlotRecord.model_validate(
        {"slot_id": slot_id, "value": value, "state": state, "source": source}
    )


def _text(value: SlotData) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def brief_from_slots(
    slots: Mapping[str, SlotRecord], *, mode: Mode = "guided", language: str = "en"
) -> Brief:
    """Deterministic Brief rows from a slot table (version 0, no hash: the flow sets both)."""

    def val(sid: str, default: str = "") -> str:
        rec = slots.get(sid)
        return _text(rec.value) if rec is not None and rec.state != "empty" else default

    features = slots.get("scope.features")
    feature_ids = list(features.value) if features and isinstance(features.value, list) else []
    rows = [
        BriefRow(
            key="building",
            title="What you are building",
            text=val("idea.summary", "An app"),
            slot_ids=["idea.summary"],
        ),
        BriefRow(
            key="users",
            title="Who uses it",
            text=val("users.roles", "Everyone"),
            slot_ids=["users.roles"],
        ),
        BriefRow(
            key="features",
            title="Main features",
            text=", ".join(feature_ids) or "Standard features",
            slot_ids=["scope.features"],
            features=[BriefFeature(id=f, label=f.replace("-", " ").title()) for f in feature_ids],
        ),
        BriefRow(
            key="look",
            title="Look",
            text=val("design.direction", "Recommended look"),
            slot_ids=["design.direction"],
            swatch=["#112233"],
        ),
        BriefRow(
            key="login",
            title="Login",
            text=val("auth.method", "Email code"),
            slot_ids=["auth.method"],
        ),
        BriefRow(
            key="name_logo",
            title="Name and logo",
            text=val("brand.name", "Placeholder name"),
            slot_ids=["brand.name"],
        ),
    ]
    assumptions = [
        AssumedItem(
            slot_id=rec.slot_id,
            label=rec.slot_id,
            value_text=_text(rec.value),
            reason="You did not say, so I picked the usual choice.",
        )
        for rec in sorted(slots.values(), key=lambda r: r.slot_id)
        if rec.state == "assumed"
    ]
    return Brief(
        version=0,
        mode=mode,
        language=language,
        app_summary=val("idea.summary", "An app"),
        rows=rows,
        assumptions=assumptions,
    )


def make_brief(
    version: int = 1,
    *,
    summary: str = "A fitness tracking app",
    direction: str = "clean-bright",
    features: Sequence[str] = ("workouts", "progress"),
    login: str = "email",
    mode: Mode = "guided",
) -> Brief:
    slots = {
        "idea.summary": slot("idea.summary", summary),
        "scope.features": slot("scope.features", list(features)),
        "design.direction": slot("design.direction", direction),
        "auth.method": slot("auth.method", login),
    }
    brief = brief_from_slots(slots, mode=mode).model_copy(update={"version": version})
    return brief.with_hash()


# --------------------------------------------------------------------------- spec and plan


def _tokens() -> DesignTokens:
    return DesignTokens(
        colors_light={"bg": "#ffffff", "fg": "#111111"},
        colors_dark={"bg": "#000000", "fg": "#eeeeee"},
        type_scale={"body": TypeStyle(size=16)},
        spacing=[4, 8, 16],
        radius={"md": 8},
        elevation={},
        motion={},
    )


def make_spec(
    *,
    version: int = 1,
    app_id: str = "app_TEST",
    brief_hash: str = "brief-hash",
    features: Sequence[str] = ("workouts", "progress"),
    direction: str = "clean-bright",
    auth: str = "email",
    app_name: str = "Test App",
    research_additions: Sequence[ResearchAddition] = (),
    previous_version: int | None = None,
    changed_ids: Sequence[str] = (),
    assumptions: Sequence[AssumptionItem] = (),
) -> AppSpec:
    """A small, valid ``AppSpec`` whose screens follow ``features`` (one screen per feature)."""
    screen_ids = [f"SCR-{i:03d}" for i in range(1, len(features) + 3)]
    names = ["login", "home", *features]
    screens = [
        ScreenBlock(
            id=sid,  # type: ignore[arg-type]
            route=f"/{name}",
            recipe="list" if i > 1 else "form",
            purpose=f"Show {name}",
            auth="public" if i == 0 else "authenticated",
            flow="FLW-001",
        )
        for i, (sid, name) in enumerate(zip(screen_ids, names, strict=True))
    ]
    nav = NavigationBlock(
        root=NavNode(
            id="NAV-001",
            route="/",
            kind="stack",
            children=[
                NavNode(id=f"NAV-{i + 2:03d}", route=s.route, kind="screen", screen=s.id)
                for i, s in enumerate(screens)
            ],
        )
    )
    return AppSpec(
        header=SpecHeader(
            spec_version=version, app_id=app_id, brief_hash=brief_hash, mode="guided"
        ),
        summary=f"{app_name}: {', '.join(features)}",
        app=AppBlock(name=app_name, purpose="Test purpose", audience="Everyone"),
        design=DesignBlock(direction=direction, tokens=_tokens()),
        navigation=nav,
        data=DataBlock(
            entities=[
                Entity(
                    id="ENT-Item",
                    name="Item",
                    plain_name="Items",
                    fields=[FieldDef(name="id"), FieldDef(name="title")],
                )
            ]
        ),
        api=ApiBlock(operations=[]),
        roles=RolesBlock(roles=[Role(id="ROLE-user", name=f"User ({auth})")]),
        integrations=IntegrationsBlock(items=[]),
        behavior=BehaviorBlock(),
        screens=screens,
        flows=[
            FlowBlock(
                id="FLW-001",
                name="Main",
                primary=True,
                screens=[s.id for s in screens],
                start=screens[0].id,
                end=screens[-1].id,
            )
        ],
        build=BuildSequence(steps=[]),
        acceptance=AcceptanceBlock(),
        assumptions=AssumptionsBlock(
            assumptions=list(assumptions),
            research_additions=list(research_additions),
            research_status="ok" if research_additions else "skipped",
        ),
        revision=RevisionBlock(
            version=version, previous_version=previous_version, changed_ids=list(changed_ids)
        ),
    )


def render_spec_text(spec: AppSpec) -> str:
    """A stand-in for the real deterministic renderer: stable text that contains everything."""
    return f"# APP_SPEC (fake render)\n\n{canonical_json(spec.model_dump(mode='json'))}\n"


def spec_hash_of(text: str) -> str:
    return sha256_hex(text)


def make_plan(
    spec: AppSpec,
    *,
    spec_hash: str,
    note: ResearchNote | None = None,
    excluded: Sequence[str] = (),
    status: str | None = None,
) -> PlanView:
    group = ScreenMapGroup(
        flow_id="FLW-001",
        flow_name="Main",
        screen_ids=[s.id for s in spec.screens],
        screen_titles=[s.purpose for s in spec.screens],
        count=len(spec.screens),
    )
    added = [
        ResearchAdditionView(
            id=r.id,
            title=r.title,
            kind=r.kind,
            why="Seen in most similar apps",
            included=r.included,
        )
        for r in spec.assumptions.research_additions
    ]
    research_status = status or ("ok" if note is not None and note.status == "ok" else "skipped")
    return PlanView(
        spec_version=spec.header.spec_version,
        spec_hash=spec_hash,
        summary=spec.summary,
        screen_count=len(spec.screens),
        screen_map=[group],
        look=LookPreview(
            direction=spec.design.direction,
            swatches_light=spec.design.tokens.colors_light,
            swatches_dark=spec.design.tokens.colors_dark,
            radius=8,
        ),
        data=[
            PlainEntity(id=e.id, plain_name=e.plain_name, fields_plain=[f.name for f in e.fields])
            for e in spec.data.entities
        ],
        research_status=research_status,  # type: ignore[arg-type]
        research_note=None if research_status == "ok" else "Research was skipped.",
        research_added=added,
        assumptions=[],
    )
