"""``catalog/fact_kinds.yaml`` and its loader: every kind, mapping and route is valid, generic and total."""

from __future__ import annotations

from typing import get_args

import pytest

from mde_compass.catalog.fact_kinds import (
    FACT_KINDS_PATH,
    FALLBACK_KIND,
    GENERAL_GROUP,
    KIND_NAMES,
    VIEW_NAMES,
    default_consumers,
    is_kind,
    kind_for,
    load_fact_kinds,
    normalize_kind,
    route_groups,
    theme_for,
    views_for,
    views_of,
)
from mde_compass.catalog.loader import Consumer, load_catalog
from mde_compass.domain.interview import FACT_TYPES

CONSUMERS = set(get_args(Consumer))


def test_the_file_defines_exactly_the_fourteen_kinds_with_valid_consumers() -> None:
    data = load_fact_kinds()
    assert set(data.kinds) == set(KIND_NAMES) and len(KIND_NAMES) == 14
    for kind in data.kinds.values():
        assert kind.description.strip() and kind.consumers
        assert set(kind.consumers) <= CONSUMERS


def test_every_fact_type_maps_to_a_kind() -> None:
    data = load_fact_kinds()
    assert set(data.from_type) == set(FACT_TYPES)
    for fact_type in FACT_TYPES:
        assert kind_for(fact_type) in KIND_NAMES


@pytest.mark.parametrize(
    ("fact_type", "topics", "kind"),
    [
        ("requirement", [], "rule"),
        ("rule", ["no_shows"], "rule"),
        ("number", ["plans_and_prices"], "number"),
        ("exclusion", [], "constraint"),
        ("goal", [], "context"),
        ("preference", ["look"], "preference"),
        ("rule", ["screens"], "screen"),  # a topic word beats the type
        ("requirement", ["checkout_flow"], "flow"),
        ("rule", ["data_privacy"], "compliance"),
        ("number", ["offline_performance"], "nfr"),
        ("constraint", ["payment_gateway"], "integration_detail"),
        ("example", ["acceptance_criteria"], "acceptance"),
        ("context", ["stock_record"], "entity"),
        ("rule", ["सुरक्षा"], "nfr"),
    ],
)
def test_kind_for_reads_topic_words_before_the_type(
    fact_type: str, topics: list[str], kind: str
) -> None:
    assert kind_for(fact_type, topics) == kind


def test_unknown_types_and_empty_topics_end_as_other() -> None:
    assert kind_for("nonsense", ["whatever"]) == FALLBACK_KIND == "other"
    assert kind_for("nonsense", []) == "other"


def test_default_consumers_are_catalog_consumers_and_an_unknown_kind_gets_other() -> None:
    for kind in KIND_NAMES:
        assert set(default_consumers(kind)) <= CONSUMERS
    assert default_consumers("bogus") == default_consumers("other") == ("all",)
    assert set(default_consumers("acceptance")) == {"verify", "qa"}


def test_normalize_kind_keeps_a_valid_model_kind_and_repairs_the_rest() -> None:
    assert normalize_kind("Integration Detail", "rule", []) == "integration_detail"
    assert normalize_kind("nonsense", "number", ["plans"]) == "number"
    assert normalize_kind("", "constraint", []) == "constraint"
    assert is_kind("nfr") and not is_kind("nfrs")


def test_every_consumer_value_reaches_at_least_one_view() -> None:
    """The catalog has ten consumers and there are four views: a fact tagged for any of them must be visible."""
    assert {c: views_for(c) for c in CONSUMERS}.keys() == CONSUMERS
    for consumer in CONSUMERS:
        assert views_for(consumer), consumer
        assert set(views_for(consumer)) <= set(VIEW_NAMES)
    assert views_for("screens") == ("design", "blueprint")
    assert views_for("frontend") == views_for("backend") == ("blueprint",)
    assert views_for("qa") == views_for("change_control") == ("verify",)
    assert set(views_for("all")) == set(VIEW_NAMES)
    assert views_of(["qa", "design"]) == {"verify", "design"}
    assert views_for("unknown") == ()


def test_every_default_consumer_of_every_kind_reaches_a_view() -> None:
    for kind in KIND_NAMES:
        assert views_of(default_consumers(kind)), kind


def test_routes_send_a_fact_to_every_matching_group_and_unmatched_to_general() -> None:
    assert "people_and_scope" in route_groups("requirement", "rule", [])
    assert "idea" in route_groups("goal", "context", [])
    assert "experience_and_systems" in route_groups("constraint", "constraint", [])
    both = route_groups("rule", "rule", ["audience"])
    assert {"people_and_scope", "idea"} <= set(both)
    assert route_groups("nonsense", "other", ["zzz"]) == (GENERAL_GROUP,)


def test_compile_group_sections_are_real_slot_sections_or_groups() -> None:
    cat = load_catalog()
    names = {s.brief_path.split(".", 1)[0] for s in cat.slots.values()} | {
        s.group for s in cat.slots.values()
    }
    for g in load_fact_kinds().groups:
        assert g.sections
        for section in g.sections:
            assert section in names or section == "_none", (g.id, section)
    # every fillable slot is a target of at least one group, so no Brief field is unreachable
    targeted = {s for g in load_fact_kinds().groups for s in g.sections}
    for slot in cat.slots.values():
        if slot.level == "generated":
            continue
        assert {slot.brief_path.split(".", 1)[0], slot.group} & targeted, slot.id


def test_theme_follows_the_kind() -> None:
    assert theme_for("integration_detail") == "Integrations"
    assert theme_for("screen") == "Look"
    assert theme_for("bogus") == theme_for("other")


def test_the_data_names_no_company_or_app_type() -> None:
    text = FACT_KINDS_PATH.read_text(encoding="utf-8").casefold()
    for word in ("clinic", "pharmacy", "tiffin", "restaurant", "kirana", "mobigen"):
        assert word not in text
