"""Synthetic fixtures for the view tests (a real package module: importlib test mode forbids
test-to-test imports). A small food-delivery Brief that touches every section and every status."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import JsonValue

from mde_compass.domain.brief import (
    AppRecord,
    AssetRecord,
    AssumptionRecord,
    Brief,
    DeclinedRecord,
    FeatureRecord,
    FeatureSize,
    GlossaryRecord,
    IntegrationRecord,
    OpenQuestion,
    ReferenceRecord,
    RoleRecord,
    SourceRecord,
)
from mde_compass.domain.common import BriefItem, HistoryEntry, Provenance, Status, Tier
from mde_compass.domain.research import ResearchRequest, SearchHints
from mde_compass.domain.session import SessionRecord, SessionState

WHEN = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
TURN_1 = "evt_TEST0000000000000000000001"
TURN_2 = "evt_TEST0000000000000000000002"
TURN_3 = "evt_TEST0000000000000000000003"
QUOTE_1 = "chat transcript: I want tiffin delivery for office workers in Pune"
QUOTE_2 = "chat transcript: maybe also students near the college"
HISTORY_QUOTE = "chat transcript: earlier I said only restaurants"


def prov(ptype: str = "user_text", ref: str = TURN_1, quote: str = QUOTE_1) -> Provenance:
    return Provenance(type=ptype, ref=ref, quote=quote, date=WHEN)  # type: ignore[arg-type]


def item(
    value: JsonValue,
    *,
    ptype: str = "user_text",
    ref: str = TURN_1,
    quote: str = QUOTE_1,
    status: Status = "confirmed",
    history: list[HistoryEntry] | None = None,
) -> BriefItem:
    """A dict-section item; Locked when the evidence allows it, else Default."""
    evidence = ptype in ("user_tap", "user_text", "voice", "document", "org_profile") and bool(ref)
    tier: Tier = "Locked" if evidence else "Default"
    return BriefItem(
        value=value,
        tier=tier,
        provenance=[prov(ptype, ref, quote)],
        confidence=0.9,
        status=status,
        history=history or [],
    )


def rich_brief(*, version: int = 3) -> Brief:
    """Food-delivery Brief: 2 apps, 3 roles, features in every status, assumptions of both impacts."""
    old = HistoryEntry(
        value="restaurants only", status="confirmed", turn_id=TURN_3, reason=HISTORY_QUOTE
    )
    return Brief(
        version=version,
        summary="Order lunch boxes from home cooks in Pune",
        pitch="A simple app that brings home-cooked tiffin to office workers.",
        idea={
            "summary": item("Order lunch boxes from home cooks", history=[old]),
            "category": item("food_delivery", ptype="assumed", ref="", status="assumed"),
            "subcategory": item("home_cooked_meals", ptype="assumed", ref="", status="assumed"),
            "business_model": item(
                "A small commission on each order", ptype="pattern", ref="", status="assumed"
            ),
        },
        context={
            "geo": item(
                {"city": "Pune", "region": "Maharashtra", "country": "India"},
                ptype="user_tap",
                ref=TURN_2,
            ),
            "scale_launch": item("pilot_small", ptype="pattern", ref="", status="assumed"),
        },
        audience={
            "segments": item(["Office workers", "Students"], quote=QUOTE_2, ref=TURN_2),
            "traits": item(["multilingual"], ptype="model_knowledge", ref="", status="assumed"),
        },
        apps=[
            AppRecord(
                id="APP-001",
                name="Customer app",
                purpose="Order and track lunch",
                surface="mobile",
                audience="Office workers",
                roles=["ROL-001"],
                tier="Locked",
                provenance=[prov()],
                status="confirmed",
            ),
            AppRecord(
                id="APP-002",
                name="Cook app",
                purpose="Receive and prepare orders",
                surface="mobile",
                roles=["ROL-002"],
                tier="Default",
                provenance=[prov("pattern", "", "")],
                status="assumed",
            ),
        ],
        roles=[
            RoleRecord(
                id="ROL-001",
                name_in_user_words="Customer",
                goal="Get lunch on time",
                rough_permissions=["order", "pay"],
            ),
            RoleRecord(
                id="ROL-002",
                name_in_user_words="Home cook",
                goal="Sell meals",
                rough_permissions=["manage menu"],
            ),
            RoleRecord(id="ROL-003", name_in_user_words="Rider", goal="Deliver orders"),
        ],
        scope=[
            FeatureRecord(
                id="FTR-001",
                name="Browse menus",
                intent="See what cooks offer today",
                apps=["APP-001"],
                family="catalog_commerce",
                scope_status="in",
                priority="must",
                tier="Locked",
                provenance=[prov()],
                status="confirmed",
            ),
            FeatureRecord(
                id="FTR-002",
                name="Live order tracking",
                intent="Watch the rider on a map",
                apps=["APP-001"],
                family="delivery_logistics",
                scope_status="in",
                priority="nice",
                capability_tier="B",
                size=FeatureSize(screens=2, entities=1, integrations=1),
            ),
            FeatureRecord(
                id="FTR-003",
                name="Loyalty points",
                intent="Reward repeat customers",
                apps=["APP-001"],
                family="payments_wallet",
                scope_status="later",
                priority="later",
            ),
            FeatureRecord(
                id="FTR-004",
                name="Group ordering",
                intent="One office order, many people",
                apps=["APP-001", "APP-002"],
                family=None,
                scope_status="in",
                priority="nice",
                custom=True,
                status="volunteered",
            ),
            FeatureRecord(
                id="FTR-005",
                name="Drone delivery",
                intent="Deliver by drone",
                apps=["APP-001"],
                scope_status="unsupported",
                priority="nice",
            ),
            FeatureRecord(
                id="FTR-006",
                name="Cook payouts",
                intent="Pay cooks weekly",
                apps=["APP-002"],
                family="payments_wallet",
                scope_status="out",
                priority="later",
            ),
        ],
        references=[
            ReferenceRecord(label="Swiggy", borrow=["order tracking"], avoid=["heavy ads"])
        ],
        experience={
            "brand.name": item("Tiffin Express"),
            "tone_words": item(["calm_simple"], ptype="user_tap"),
            "locales": item({"chat": "en", "app": ["en", "hi"], "right_to_left": False}),
            "devices": item("phones", ptype="pattern", ref="", status="assumed"),
        },
        access={
            "auth_methods": item(["phone_otp"], ptype="user_tap"),
            "account_model": item("individual", ptype="pattern", ref="", status="assumed"),
        },
        integrations=[
            IntegrationRecord(name="payments", preference="real_later", notes="UPI"),
            IntegrationRecord(name="maps", preference="mock"),
        ],
        data={
            "existing_systems": item(
                [{"name": "Kitchen spreadsheet", "kind": "sheet"}],
                ptype="document",
                ref="SRC-001#p1",
            )
        },
        constraints={
            "offline": item("online_only", ptype="pattern", ref="", status="assumed"),
            "must_not": item(["Selling alcohol"]),
            "accessibility": item(["large_text"], ptype="pattern", ref="", status="assumed"),
        },
        glossary=[GlossaryRecord(term="tiffin", synonyms=["dabba"], avoid=["lunchbox"])],
        assumptions=[
            AssumptionRecord(
                id="ASM-001",
                claim="Payments are cash on delivery at first",
                reason="Keeps the pilot simple",
                impact="low",
                theme="Business",
                what_changes="Online payment screens are added",
            ),
            AssumptionRecord(
                id="ASM-002",
                claim="Only Pune at launch",
                reason="You mentioned one city",
                impact="high",
                theme="Users",
                what_changes="Other cities need delivery zones",
            ),
            AssumptionRecord(
                id="ASM-003",
                claim="Cooks are approved by an admin",
                impact="high",
                decision="accepted",
                theme="Access",
                reason="Quality control",
                what_changes="Anyone could join as a cook",
            ),
        ],
        open_questions=[
            OpenQuestion(id="Q-001", text="Who delivers: cooks or riders?", blocking=True)
        ],
        research_requests=[
            ResearchRequest(
                id="RR-001",
                topic="Tiffin delivery in Pune",
                question="Who are the main tiffin services in Pune?",
                decision_unlocked="pricing and features",
                entity="tiffin delivery",
                geography="Pune, India",
                source_languages=["en", "hi"],
                terms=["tiffin", "dabba"],
                shown_to_user_as="Tiffin services in Pune",
            ),
            ResearchRequest(
                id="RR-002",
                topic="UPI payments for small sellers",
                question="How do small sellers take UPI?",
                decision_unlocked="payments",
                entity="UPI",
                geography="India",
                terms=["UPI"],
                user_added=True,
            ),
        ],
        search_hints=SearchHints(
            disambiguated_names=["Tiffin Express (app)"], languages=["en", "hi"]
        ),
        assets=[
            AssetRecord(
                id="AST-001",
                kind="logo",
                summary="Round green logo",
                pointer="SRC-001#p2",
                consumers=["design"],
            )
        ],
        sources=[SourceRecord(id="SRC-001", kind="pdf", name="Kitchen notes.pdf", coverage=0.8)],
        declined=[
            DeclinedRecord(
                what="Drone delivery",
                reason="Needs special hardware",
                alternative="Riders on bikes",
            )
        ],
    )


def empty_brief() -> Brief:
    return Brief()


def make_session(state: SessionState = "BRIEF_REVIEW", locale: str = "en") -> SessionRecord:
    return SessionRecord(
        id="ses_TEST", tenant_id="tnt_1", user_id="usr_1", state=state, locale=locale
    )
