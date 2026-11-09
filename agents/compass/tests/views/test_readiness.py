"""Readiness is derived generically from the catalog."""

from __future__ import annotations

from mde_compass.catalog.loader import load_catalog
from mde_compass.views.readiness import compute_readiness, gaps_for
from mde_compass.views.testing import empty_brief, item, rich_brief

CATALOG = load_catalog()


def test_rich_brief_passes_everywhere() -> None:
    r = compute_readiness(rich_brief(), CATALOG)
    for name in ("research", "blueprint", "design", "verify"):
        entry = getattr(r, name)
        assert entry.status == "pass" and entry.gaps == [], name


def test_removing_geography_creates_a_research_gap_only_where_needed() -> None:
    brief = rich_brief()
    del brief.context["geo"]
    r = compute_readiness(brief, CATALOG)
    assert r.research.status == "gaps" and r.research.gaps == ["context.geo"]
    assert r.blueprint.gaps == ["context.geo"] and r.design.gaps == ["context.geo"]
    assert r.verify.status == "pass"  # verify does not consume geography


def test_gap_follows_the_catalog_consumers() -> None:
    brief = rich_brief()
    brief.roles.clear()  # roles.list: blueprint only
    r = compute_readiness(brief, CATALOG)
    assert "roles.list" in r.blueprint.gaps
    assert "roles.list" not in r.research.gaps and "roles.list" not in r.design.gaps


def test_all_consumer_slot_is_a_gap_for_every_consumer() -> None:
    brief = rich_brief()
    del brief.idea["summary"]
    r = compute_readiness(brief, CATALOG)
    for name in ("research", "blueprint", "design", "verify"):
        assert "idea.summary" in getattr(r, name).gaps


def test_filled_list_section_counts_as_filled() -> None:
    brief = rich_brief()
    assert "scope.features" not in gaps_for(brief, CATALOG, "research")
    brief.scope.clear()
    assert "scope.features" in gaps_for(brief, CATALOG, "research")


def test_should_slots_are_advisory() -> None:
    brief = rich_brief()
    for key in ("tone_words", "devices"):
        brief.experience.pop(key, None)
    brief.context.pop("scale_launch")
    assert compute_readiness(brief, CATALOG).design.status == "pass"


def test_ask_if_value_condition_hides_business_model_for_internal_tools() -> None:
    brief = rich_brief()
    del brief.idea["business_model"]
    assert "idea.business_model" in gaps_for(brief, CATALOG, "research")
    brief.idea["category"] = item("internal_tool")
    assert "idea.business_model" not in gaps_for(brief, CATALOG, "research")


def test_empty_brief_lists_the_must_slots() -> None:
    r = compute_readiness(empty_brief(), CATALOG)
    musts = {s.id for s in CATALOG.slots.values() if s.level == "must" and s.group != "org"}
    assert r.research.status == "gaps" and "idea.summary" in r.research.gaps
    assert set(r.blueprint.gaps) <= musts and "apps.list" in r.blueprint.gaps
    assert r.verify.gaps == ["idea.summary"]
