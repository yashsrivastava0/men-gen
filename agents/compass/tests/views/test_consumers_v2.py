"""Consumer views v2 (ADR 0016): every consumer tag reaches a view, details are grouped by kind under the record they
belong to, the company (``org.*``) slots and the drafted details reach the views they belong to, and quotes and
transcript text stay out."""

from __future__ import annotations

import json
from typing import get_args

import pytest

from mde_compass.catalog.loader import Consumer
from mde_compass.domain.brief import AssumptionRecord, Brief, DomainFact
from mde_compass.domain.common import BriefItem
from mde_compass.views.consumers import (
    CONSUMER_NAMES,
    blueprint_view,
    build_handoff,
    design_view,
    research_view,
    verify_view,
)
from mde_compass.views.testing import rich_brief

QUOTE = "the owner said this word for word ZQX"


def fact(fid: str, statement: str, *consumers: str, **changes: object) -> DomainFact:
    base: dict[str, object] = {
        "id": fid,
        "type": "rule",
        "statement": statement,
        "quote": QUOTE,
        "consumers": list(consumers),
    }
    base.update(changes)
    return DomainFact.model_validate(base)


def view_of(brief: Brief, name: str) -> dict[str, object]:
    return build_handoff(brief)[name]  # type: ignore[return-value]


def facts_in(brief: Brief, name: str) -> list[str]:
    rows = view_of(brief, name).get("domain_facts", [])
    return [r["id"] for r in rows]  # type: ignore[index, union-attr]


@pytest.mark.parametrize(
    ("tag", "views"),
    [
        ("research", {"research"}),
        ("blueprint", {"blueprint"}),
        ("design", {"design"}),
        ("verify", {"verify"}),
        ("screens", {"design", "blueprint"}),
        ("frontend", {"blueprint"}),
        ("backend", {"blueprint"}),
        ("qa", {"verify"}),
        ("change_control", {"verify"}),
        ("all", {"research", "blueprint", "design", "verify"}),
    ],
)
def test_a_fact_tagged_for_one_consumer_reaches_exactly_its_views(
    tag: str, views: set[str]
) -> None:
    brief = rich_brief()
    brief.domain_facts = [fact("FCT-001", "Orders close at 9 pm", tag)]
    reached = {name for name in CONSUMER_NAMES if "FCT-001" in facts_in(brief, name)}
    assert reached == views


def test_the_ten_catalog_consumers_are_all_covered_by_the_test_above() -> None:
    tags = {
        "research",
        "blueprint",
        "design",
        "screens",
        "verify",
        "change_control",
        "frontend",
        "backend",
        "qa",
        "all",
    }
    assert tags == set(get_args(Consumer))


def test_a_fact_with_no_tag_gets_the_default_consumers_of_its_kind() -> None:
    brief = rich_brief()
    brief.domain_facts = [
        fact("FCT-001", "Done means the receipt is emailed", type="rule", kind="acceptance"),
        fact("FCT-002", "A calm look", type="preference"),
    ]
    assert facts_in(brief, "verify") == ["FCT-001"]  # acceptance: verify and qa
    assert facts_in(brief, "design") == ["FCT-002"]  # preference: design and screens
    assert "FCT-002" in facts_in(brief, "blueprint")  # screens reaches blueprint too
    assert facts_in(brief, "research") == []


def test_rows_carry_kind_and_refs_only_when_present_and_never_the_quote() -> None:
    brief = rich_brief()
    brief.domain_facts = [
        fact("FCT-001", "Plain", "all"),
        fact("FCT-002", "Typed", "all", kind="number", refs=["FTR-001"]),
    ]
    rows = {r["id"]: r for r in view_of(brief, "blueprint")["domain_facts"]}  # type: ignore[index, union-attr]
    assert "kind" not in rows["FCT-001"] and "refs" not in rows["FCT-001"]
    assert rows["FCT-002"]["kind"] == "number" and rows["FCT-002"]["refs"] == ["FTR-001"]
    text = json.dumps(build_handoff(brief), ensure_ascii=False)
    assert QUOTE not in text and '"quote"' not in text and '"provenance"' not in text


def test_blueprint_features_roles_and_apps_get_details_grouped_by_kind() -> None:
    brief = rich_brief()
    role_id = brief.roles[0].id
    app_id = brief.apps[0].id
    brief.domain_facts = [
        fact("FCT-001", "Refunds within 7 days", "blueprint", kind="rule", refs=["FTR-001"]),
        fact("FCT-002", "Refund cap is 500", "blueprint", kind="number", refs=["FTR-001"]),
        fact("FCT-003", "Another rule", "blueprint", kind="rule", refs=["FTR-001", role_id]),
        fact("FCT-004", "The app starts on the menu", "blueprint", kind="screen", refs=[app_id]),
        fact("FCT-005", "Shared across features", "blueprint", kind="rule", refs=["FTR-404"]),
        fact("FCT-006", "No link at all", "blueprint"),
        fact("FCT-007", "Not for blueprint", "design", refs=["FTR-001"]),
    ]
    view = blueprint_view(brief)
    features = {f["id"]: f for f in view["features"]}  # type: ignore[union-attr]
    details = features["FTR-001"]["details"]
    assert list(details) == ["rule", "number"]
    assert [r["id"] for r in details["rule"]] == ["FCT-001", "FCT-003"]
    assert details["number"] == [{"id": "FCT-002", "statement": "Refund cap is 500"}]
    roles = {r["id"]: r for r in view["roles"]}  # type: ignore[union-attr]
    assert roles[role_id]["details"]["rule"] == [{"id": "FCT-003", "statement": "Another rule"}]
    apps = {a["id"]: a for a in view["apps"]}  # type: ignore[union-attr]
    assert apps[app_id]["details"]["screen"][0]["id"] == "FCT-004"
    assert "details" not in features["FTR-002"]
    # a ref that resolves to nothing, and no ref at all, are both unlinked: never dropped
    unlinked = view["unlinked_details"]
    assert [r["id"] for r in unlinked["rule"]] == ["FCT-005", "FCT-006"]  # type: ignore[index]
    assert "FCT-007" not in json.dumps(unlinked)


def test_views_without_facts_or_details_are_unchanged() -> None:
    brief = rich_brief()
    brief.domain_facts = []
    view = blueprint_view(brief)
    assert "unlinked_details" not in view and "domain_facts" not in view
    assert all("details" not in f for f in view["features"])  # type: ignore[union-attr]


def test_company_slots_reach_the_views_the_catalog_gives_them() -> None:
    brief = rich_brief()
    brief.context["org.profile"] = BriefItem(value={"name": "Acme Foods"})
    brief.context["org.approvers"] = BriefItem(value=[{"name": "Asha", "role": "approver"}])
    brief.experience["org.design_system"] = BriefItem(value=["brand guide v3"])
    brief.data["org.api_catalog"] = BriefItem(value=["orders api"])
    research, blueprint = research_view(brief), blueprint_view(brief)
    design, verify = design_view(brief), verify_view(brief)
    assert set(research["org"]) == {"profile", "approvers"}  # type: ignore[arg-type]
    assert set(blueprint["org"]) == {"profile", "approvers", "design_system", "api_catalog"}  # type: ignore[arg-type]
    assert set(design["org"]) == {"profile", "approvers", "design_system"}  # type: ignore[arg-type]
    assert set(verify["org"]) == {"profile", "approvers"}  # type: ignore[arg-type]
    assert blueprint["org"]["api_catalog"] == ["orders api"]  # type: ignore[index]
    assert "org" not in research_view(rich_brief())


def test_drafted_details_and_corrections_reach_blueprint_and_verify() -> None:
    brief = rich_brief()
    brief.assumptions = [
        AssumptionRecord(id="ASM-100", claim="Typical: a receipt screen", basis="typical"),
        AssumptionRecord(
            id="ASM-101", claim="We used 10; you first said 20", basis="correction", impact="high"
        ),
        AssumptionRecord(
            id="ASM-102", claim="Typical: rejected", basis="typical", decision="rejected"
        ),
        AssumptionRecord(
            id="ASM-103", claim="A slot guess", basis="pattern", slot_id="audience.segments"
        ),
    ]
    blueprint, verify = blueprint_view(brief), verify_view(brief)
    assert [r["id"] for r in blueprint["assumed_details"]] == ["ASM-100"]  # type: ignore[union-attr, index]
    assert [r["id"] for r in verify["assumed_details"]] == ["ASM-100", "ASM-101"]  # type: ignore[union-attr, index]
    assert "assumed_details" not in research_view(brief)
    assert "assumed_details" not in design_view(brief)
