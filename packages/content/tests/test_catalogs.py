"""Catalogs: archetypes, sensitive domains and the slot table (spec 3.3)."""

from __future__ import annotations

import pytest

from mde_content.catalogs import (
    archetype,
    archetype_ids,
    load_catalogs,
    sensitive_domain,
    slot_def,
    slots_for_mode,
)
from mde_content.expr import evaluate, parse, unknown_slots
from mde_contracts.content import Catalogs
from mde_contracts.slots import SLOT_IDS

ARCHETYPES = ["commerce", "food_delivery", "booking_services", "internal_workforce", "custom"]
DOMAINS = ["health", "finance", "children", "identity_documents", "precise_location"]
ENTERPRISE_ONLY = [
    "auth.sso",
    "integrations.list",
    "data.classification",
    "regions",
    "release.channel",
    "approvers",
]


def test_loads_and_round_trips() -> None:
    cat = load_catalogs()
    assert load_catalogs() is cat  # cached
    assert Catalogs.model_validate(cat.model_dump()) == cat


def test_archetypes_are_the_five_mvp_types() -> None:
    assert archetype_ids() == ARCHETYPES
    for item in load_catalogs().archetypes:
        assert item.label and item.description
        assert len(item.label) <= 40
        assert set(item.i18n) == {"hi", "hi-Latn"}
        for lang in ("hi", "hi-Latn"):
            assert item.i18n[lang].keys() == {"label", "description"}
    assert archetype("commerce").label == "Online shop"
    with pytest.raises(KeyError):
        archetype("nope")


def test_keywords_cover_hindi_and_hinglish() -> None:
    for aid in ARCHETYPES[:-1]:
        keywords = archetype(aid).keywords
        assert len(keywords) >= 8
        assert any(any("ऀ" <= ch <= "ॿ" for ch in k) for k in keywords), aid
        assert any(k.isascii() and k.lower() == k for k in keywords)


def test_multi_sided_matches_the_scope_apps_precondition() -> None:
    multi = {a.id for a in load_catalogs().archetypes if a.multi_sided}
    assert multi == {"commerce", "food_delivery", "booking_services"}
    expr = slot_def("scope.apps").ask_if
    assert expr is not None
    for aid in ARCHETYPES:
        assert evaluate(expr, {"idea.archetype": aid}) is (aid in multi)


def test_sensitive_domains() -> None:
    cat = load_catalogs()
    assert [d.id for d in cat.sensitive_domains] == DOMAINS
    flags: list[str] = []
    for d in cat.sensitive_domains:
        assert d.label
        assert len(d.keywords) >= 8
        assert any(any("ऀ" <= ch <= "ॿ" for ch in k) for k in d.keywords), d.id
        assert d.compliance_flags
        assert d.conservative_defaults
        flags.extend(d.compliance_flags)
        for slot_id, value in d.conservative_defaults.items():
            assert slot_id in SLOT_IDS
            assert slot_def(slot_id).level["guided"] == "may"  # defaults never skip a card
            assert value is not None
    assert len(flags) == len(set(flags))
    assert sensitive_domain("health").compliance_flags == ["health_data_privacy"]
    with pytest.raises(KeyError):
        sensitive_domain("nope")


def test_every_slot_id_has_exactly_one_definition() -> None:
    ids = [s.slot_id for s in load_catalogs().slots]
    assert sorted(ids) == sorted(SLOT_IDS)
    assert len(ids) == len(set(ids))


def test_slot_definitions_are_complete_and_consistent() -> None:
    for s in load_catalogs().slots:
        assert set(s.level) == {"guided", "enterprise"}, s.slot_id
        assert s.default_reason.endswith("."), s.slot_id
        assert len(s.default_reason) <= 140, s.slot_id
        for dep in s.depends_on:
            assert dep in SLOT_IDS
            assert dep != s.slot_id
            assert slot_def(dep).order < s.order or dep == "idea.summary", (s.slot_id, dep)
        if s.ask_if:
            parse(s.ask_if)
            assert not unknown_slots(s.ask_if), (s.slot_id, s.ask_if)
        for mode in s.must_confirm_in:
            assert mode in ("guided", "enterprise")


def test_defaults_match_the_value_kind() -> None:
    for s in load_catalogs().slots:
        d = s.default
        if d is None:
            continue
        if s.value_kind == "bool":
            assert isinstance(d, bool), s.slot_id
        elif s.value_kind == "list":
            assert isinstance(d, list), s.slot_id
        else:
            assert isinstance(d, str), s.slot_id


def test_spec_3_3_table_defaults() -> None:
    assert slot_def("idea.archetype").default == "custom"
    assert slot_def("auth.method").default == "email_phone_code"
    assert slot_def("users.roles").default == ["user"]
    assert slot_def("pay.provider").default == "mock"
    assert slot_def("data.source").default == "generated"
    assert slot_def("behavior.analytics").default is False
    assert slot_def("locale").default == "en"
    assert slot_def("design.mode").default == "system"
    assert slot_def("idea.summary").default is None  # blocks


def test_levels_follow_spec_3_3() -> None:
    for sid in [
        "idea.summary",
        "idea.archetype",
        "scope.apps",
        "scope.features",
        "users.roles",
        "auth.method",
        "design.direction",
        "pay.model",
        "compliance.flags",
    ]:
        assert slot_def(sid).level == {"guided": "must", "enterprise": "must"}, sid
    for sid in ["design.accent", "design.mode", "design.nav_style", "behavior.notifications"]:
        assert slot_def(sid).level["guided"] == "may"
    for sid in ENTERPRISE_ONLY:
        assert slot_def(sid).level["guided"] == "may", sid
        assert slot_def(sid).level["enterprise"] in ("must", "should"), sid
    assert slot_def("brand.assets").level["guided"] == "should"
    assert slot_def("brand.name").level["guided"] == "must"  # one combined skippable card


def test_must_confirm_in_enterprise() -> None:
    confirm = {s.slot_id for s in load_catalogs().slots if "enterprise" in s.must_confirm_in}
    assert confirm == {"compliance.flags", "pay.model", "auth.sso"}
    assert all(
        not s.must_confirm_in or s.must_confirm_in == ["enterprise"] for s in load_catalogs().slots
    )


def test_order_realises_the_dependency_order() -> None:
    def pos(sid: str) -> int:
        return slot_def(sid).order

    chain = [
        "idea.summary",
        "idea.archetype",
        "scope.apps",
        "scope.features",
        "users.roles",
        "auth.method",
        "design.direction",
        "brand.name",
        "pay.model",
    ]
    assert [pos(s) for s in chain] == sorted(pos(s) for s in chain)
    assert len({s.order for s in load_catalogs().slots}) == len(SLOT_IDS)  # no ties
    assert pos("compliance.flags") < pos("scope.apps")  # sensitive domains are confirmed early


def test_slots_for_mode_orders_and_filters() -> None:
    guided_must = [s.slot_id for s in slots_for_mode("guided", ("must",))]
    assert guided_must == [
        "idea.summary",
        "idea.archetype",
        "compliance.flags",
        "scope.apps",
        "scope.features",
        "users.roles",
        "auth.method",
        "design.direction",
        "brand.name",
        "pay.model",
    ]
    ent = [s.slot_id for s in slots_for_mode("enterprise", ("must", "should"))]
    assert set(ENTERPRISE_ONLY) <= set(ent)
    assert "design.accent" not in ent
    assert [s.slot_id for s in slots_for_mode("guided")] == [
        s.slot_id for s in sorted(load_catalogs().slots, key=lambda s: (s.order, s.slot_id))
    ]
    assert len(slots_for_mode("enterprise")) == len(SLOT_IDS)


def test_slot_def_unknown_id() -> None:
    with pytest.raises(KeyError):
        slot_def("nope.nope")
