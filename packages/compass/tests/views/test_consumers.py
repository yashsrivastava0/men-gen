"""Consumer views: values only, never any chat transcript, provenance or history."""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.domain.brief import Brief, FeatureRecord, GlossaryRecord
from mde_compass.domain.common import BriefItem, HistoryEntry, Provenance
from mde_compass.views.consumers import (
    blueprint_view,
    build_handoff,
    design_view,
    research_view,
    verify_view,
)
from mde_compass.views.testing import (
    HISTORY_QUOTE,
    QUOTE_1,
    QUOTE_2,
    TURN_1,
    TURN_2,
    TURN_3,
    empty_brief,
    rich_brief,
)


def test_handoff_has_the_four_views() -> None:
    handoff = build_handoff(rich_brief())
    assert list(handoff) == ["research", "blueprint", "design", "verify"]
    json.dumps(handoff)  # JSON-able


def test_no_transcript_provenance_or_turn_ids_in_the_rich_brief() -> None:
    text = json.dumps(build_handoff(rich_brief()), ensure_ascii=False)
    for needle in (
        QUOTE_1,
        QUOTE_2,
        HISTORY_QUOTE,
        "chat transcript",
        TURN_1,
        TURN_2,
        TURN_3,
        "evt_",
    ):
        assert needle not in text
    for key in ('"provenance"', '"history"', '"quote"', '"confidence"', '"turn_id"'):
        assert key not in text


def test_research_view_content() -> None:
    view = research_view(rich_brief())
    idea = view["idea"]
    assert isinstance(idea, dict) and idea["category"] == "food_delivery"
    assert view["geography"] == {"city": "Pune", "region": "Maharashtra", "country": "India"}
    names = [f["name"] for f in view["feature_intents"]]  # type: ignore[union-attr]
    assert "Browse menus" in names and "Loyalty points" not in names  # in-scope only
    assert "Drone delivery" not in names
    assert [r["id"] for r in view["research_requests"]] == ["RR-001", "RR-002"]  # type: ignore[union-attr]
    assert view["declined"][0]["alternative"] == "Riders on bikes"  # type: ignore[index]
    assert view["glossary"][0]["term"] == "tiffin"  # type: ignore[index]
    assert view["search_hints"]["languages"] == ["en", "hi"]  # type: ignore[index]


def test_blueprint_view_content() -> None:
    view = blueprint_view(rich_brief())
    assert [a["id"] for a in view["apps"]] == ["APP-001", "APP-002"]  # type: ignore[union-attr]
    assert len(view["roles"]) == 3  # type: ignore[arg-type]
    features = {f["id"]: f for f in view["features"]}  # type: ignore[union-attr]
    assert (
        features["FTR-002"]["capability_tier"] == "B"
        and features["FTR-002"]["size"]["screens"] == 2
    )
    assert "FTR-005" not in features and "FTR-006" not in features  # out/unsupported are Verify's
    assert view["integrations"][0]["name"] == "payments"  # type: ignore[index]
    assert view["scale_launch"] == "pilot_small"
    assert view["assets"] == []  # the only asset is routed to design


def test_design_view_content() -> None:
    view = design_view(rich_brief())
    assert view["brand"] == {"name": "Tiffin Express"}
    assert view["tone_words"] == ["calm_simple"]
    assert view["locales"] == {"chat": "en", "app": ["en", "hi"], "right_to_left": False}
    assert view["devices"] == "phones" and view["accessibility"] == ["large_text"]
    assert [a["id"] for a in view["assets"]] == ["AST-001"]  # type: ignore[union-attr]


def test_verify_view_is_the_full_ledger_with_must_not() -> None:
    view = verify_view(rich_brief())
    statuses = {f["id"]: f["status"] for f in view["scope_ledger"]}  # type: ignore[union-attr]
    assert statuses == {
        "FTR-001": "in",
        "FTR-002": "in",
        "FTR-003": "later",
        "FTR-004": "in",
        "FTR-005": "unsupported",
        "FTR-006": "out",
    }
    assert view["must_not"] == ["Selling alcohol"]


def test_empty_brief_builds_all_views() -> None:
    handoff = build_handoff(empty_brief())
    assert set(handoff) == {"research", "blueprint", "design", "verify"}
    json.dumps(handoff)


_text = st.text(min_size=8, max_size=40).map(lambda s: f"chat transcript {s.strip() or 'x'} ZQX")


@settings(max_examples=60, deadline=None)
@given(
    quotes=st.lists(_text, min_size=1, max_size=4),
    hist=st.lists(_text, min_size=1, max_size=3),
)
def test_consumer_views_never_contain_transcript_text(quotes: list[str], hist: list[str]) -> None:
    prov = [
        Provenance(type="user_text", ref=f"evt_TEST{i:022d}", quote=q) for i, q in enumerate(quotes)
    ]
    history = [
        HistoryEntry(value=h, status="confirmed", turn_id=f"evt_HIST{i:022d}", reason=h)
        for i, h in enumerate(hist)
    ]
    brief: Brief = rich_brief()
    brief.idea["summary"] = BriefItem(
        value="Lunch app", tier="Locked", provenance=prov, status="confirmed", history=history
    )
    brief.context["geo"] = BriefItem(
        value="Pune", tier="Locked", provenance=prov, status="confirmed", history=history
    )
    brief.scope.append(
        FeatureRecord(id="FTR-090", name="Extra", provenance=prov, history=history, tier="Locked")
    )
    brief.glossary.append(GlossaryRecord(term="chai", provenance=prov, history=history))
    for record in (*brief.apps, *brief.roles, *brief.assumptions):
        record.provenance = prov
        record.history = history
    text = json.dumps(build_handoff(brief), ensure_ascii=False)
    for quote in (*quotes, *hist):
        assert quote not in text
    assert "chat transcript" not in text and "evt_" not in text and "ZQX" not in text
