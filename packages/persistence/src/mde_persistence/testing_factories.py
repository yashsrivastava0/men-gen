"""Record factories for tests (this package's and, through the contract suite, the flow package's).

Every helper returns a valid, frozen contract model with sensible defaults; override anything with
keyword arguments. Ids come from ``mde_core.ids.new_id`` (prefixed ULIDs), so concurrent tests
never collide.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from mde_contracts.brief import Brief, BriefRow
from mde_contracts.cards import Card, CardInstance, CardOption
from mde_contracts.common import GateKind
from mde_contracts.engines import DiscoveryState
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    OptionSelected,
    UserMessage,
)
from mde_contracts.gates import ApprovalDecision, ApprovalRecord
from mde_contracts.plan import LookPreview, PlanView, ScreenMapGroup
from mde_contracts.records import (
    BriefVersionRecord,
    PlanJobPayload,
    ResearchNoteRecord,
    SessionRecord,
    SpecStatus,
    SpecVersionRecord,
)
from mde_contracts.research import ResearchFact, ResearchNote, SourceRef
from mde_contracts.slots import SlotRecord
from mde_contracts.spec import (
    AcceptanceBlock,
    ApiBlock,
    AppBlock,
    AppSpec,
    AssumptionsBlock,
    BehaviorBlock,
    BuildSequence,
    BuildStep,
    DataBlock,
    DesignBlock,
    DesignTokens,
    FlowBlock,
    IntegrationsBlock,
    NavigationBlock,
    NavNode,
    RevisionBlock,
    RolesBlock,
    ScreenBlock,
    SpecHeader,
    TypeStyle,
)
from mde_core.hashing import sha256_hex
from mde_core.ids import new_id

TENANT_A = "tnt_TESTA"
TENANT_B = "tnt_TESTB"
AT = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


def new_session(
    *,
    tenant_id: str = TENANT_A,
    owner_id: str = "usr_owner",
    title: str | None = None,
    slots: dict[str, SlotRecord] | None = None,
    **overrides: object,
) -> SessionRecord:
    base: dict[str, object] = {
        "id": new_id("ses"),
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "app_id": new_id("app"),
        "title": title,
    }
    base.update(overrides)
    if slots is not None:
        base["discovery"] = DiscoveryState(slots=slots)
    return SessionRecord.model_validate(base)


def new_brief(
    session_id: str,
    tenant_id: str = TENANT_A,
    *,
    version: int = 1,
    status: str = "draft",
    summary: str = "A booking app for a small clinic.",
) -> BriefVersionRecord:
    brief = Brief(
        version=version,
        status=status,  # type: ignore[arg-type]
        app_summary=summary,
        rows=[
            BriefRow(
                key="building",
                title="What we are building",
                text=summary,
                slot_ids=["idea.summary"],
            )
        ],
    ).with_hash()
    slots = {
        "idea.summary": SlotRecord(
            slot_id="idea.summary", value=summary, state="volunteered", source="text"
        )
    }
    return BriefVersionRecord(session_id=session_id, tenant_id=tenant_id, brief=brief, slots=slots)


def new_research(
    session_id: str,
    tenant_id: str = TENANT_A,
    *,
    version: int = 1,
    brief_hash: str = "brief-hash",
    facts: int = 2,
) -> ResearchNoteRecord:
    items = [
        ResearchFact(
            id=f"F-{i + 1:03d}",
            job="R1" if i % 2 == 0 else "R2",
            kind="feature",
            statement=f"Similar apps offer feature number {i + 1}.",
            sources=[
                SourceRef(
                    url=f"https://example.com/source-{i + 1}",
                    title="Example",
                    domain="example.com",
                    retrieved_at=date(2026, 11, 8),
                )
            ],
            confidence="high",
            verified=True,
        )
        for i in range(facts)
    ]
    note = ResearchNote(version=version, brief_hash=brief_hash, facts=items).with_hash()
    return ResearchNoteRecord(session_id=session_id, tenant_id=tenant_id, note=note)


def minimal_spec(*, version: int = 1, summary: str = "A tiny app.") -> AppSpec:
    tokens = DesignTokens(
        colors_light={"background": "#FFFFFF", "text": "#111111"},
        colors_dark={"background": "#000000", "text": "#EEEEEE"},
        type_scale={"body": TypeStyle(size=16)},
        spacing=[4, 8, 16],
        radius={"md": 12},
        elevation={"card": "0 1 3"},
        motion={"fast": "120ms"},
    )
    screen = ScreenBlock(id="SCR-001", route="/", recipe="list", purpose="Home")
    return AppSpec(
        header=SpecHeader(spec_version=version, app_id="app_TEST", brief_hash="b"),
        summary=summary,
        app=AppBlock(name="Tiny", purpose="Demo", audience="Everyone"),
        design=DesignBlock(direction="clean-bright", tokens=tokens),
        navigation=NavigationBlock(
            root=NavNode(
                id="NAV-001",
                route="/",
                kind="stack",
                children=[NavNode(id="NAV-002", route="/", kind="screen", screen="SCR-001")],
            )
        ),
        data=DataBlock(entities=[]),
        api=ApiBlock(operations=[]),
        roles=RolesBlock(roles=[]),
        integrations=IntegrationsBlock(items=[]),
        behavior=BehaviorBlock(),
        screens=[screen],
        flows=[
            FlowBlock(
                id="FLW-001",
                name="Main",
                primary=True,
                screens=["SCR-001"],
                start="SCR-001",
                end="SCR-001",
            )
        ],
        build=BuildSequence(steps=[BuildStep(id="S0", name="Scaffold", load=["APP", "DESIGN"])]),
        acceptance=AcceptanceBlock(),
        assumptions=AssumptionsBlock(),
        revision=RevisionBlock(version=version),
    )


def new_plan_view(*, version: int = 1, spec_hash: str = "hash") -> PlanView:
    return PlanView(
        spec_version=version,
        spec_hash=spec_hash,
        summary="A tiny app.",
        screen_count=1,
        screen_map=[
            ScreenMapGroup(
                flow_id="FLW-001",
                flow_name="Main",
                screen_ids=["SCR-001"],
                screen_titles=["Home"],
                count=1,
            )
        ],
        look=LookPreview(
            direction="clean-bright",
            swatches_light={"background": "#FFFFFF"},
            swatches_dark={"background": "#000000"},
            radius=12,
        ),
        data=[],
    )


def new_spec(
    session_id: str,
    tenant_id: str = TENANT_A,
    *,
    version: int = 1,
    status: SpecStatus = "draft",
    brief_version: int = 1,
    with_plan: bool = False,
) -> SpecVersionRecord:
    text = f"# APP_SPEC (version {version})\n\nA tiny app.\n"
    return SpecVersionRecord(
        session_id=session_id,
        tenant_id=tenant_id,
        version=version,
        hash=sha256_hex(text),
        status=status,
        spec_text=text,
        spec=minimal_spec(version=version),
        plan=new_plan_view(version=version, spec_hash=sha256_hex(text)) if with_plan else None,
        brief_version=brief_version,
    )


def new_approval(
    session_id: str,
    *,
    gate: GateKind = "brief",
    version: int = 1,
    hash: str = "hash-1",
    approver_id: str = "usr_owner",
    decision: ApprovalDecision = "approve",
    role: str | None = "requester",
) -> ApprovalRecord:
    return ApprovalRecord(
        id=new_id("apr"),
        session_id=session_id,
        gate=gate,
        version=version,
        hash=hash,
        approver_id=approver_id,
        role=role,
        decision=decision,
    )


def plan_payload(*, spec_version: int = 1, brief_version: int = 1) -> PlanJobPayload:
    return PlanJobPayload(
        origin="BRIEF_REVIEW", spec_version=spec_version, brief_version=brief_version
    )


def user_message(text: str = "I want an app", *, event_id: str | None = None) -> UserMessage:
    return UserMessage(event_id=event_id or new_id("evt"), text=text)


def assistant_message(
    session_id: str, *, text: str = "Hello!", in_reply_to: str | None = None
) -> AssistantMessage:
    return AssistantMessage(
        event_id=new_id("evt"), session_id=session_id, text=text, in_reply_to=in_reply_to
    )


def new_card_instance(
    *, card_id: str = "Q-look-01", slot: str = "design.direction"
) -> CardInstance:
    card = Card.model_validate(
        {
            "id": card_id,
            "slot": slot,
            "type": "choice3",
            "text": "Which look do you like?",
            "options": [
                CardOption(id="clean", label="Clean", sets={slot: "clean-bright"}),
                CardOption(id="bold", label="Bold", sets={slot: "bold-dark"}),
            ],
        }
    )
    return CardInstance(instance_id=new_id("crd"), card=card)


def card_event(
    session_id: str,
    *,
    instance: CardInstance | None = None,
    in_reply_to: str | None = None,
) -> CardEvent:
    return CardEvent(
        event_id=new_id("evt"),
        session_id=session_id,
        card=instance or new_card_instance(),
        in_reply_to=in_reply_to,
    )


def option_selected(
    card_instance_id: str,
    *,
    option_ids: list[str] | None = None,
    event_id: str | None = None,
    skipped: bool = False,
    you_decide: bool = False,
) -> OptionSelected:
    return OptionSelected(
        event_id=event_id or new_id("evt"),
        card_instance_id=card_instance_id,
        option_ids=option_ids if option_ids is not None else ["clean"],
        skipped=skipped,
        you_decide=you_decide,
    )
