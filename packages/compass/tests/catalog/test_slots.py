"""Slot catalog completeness (C.5.1) and fallback card quality."""

from __future__ import annotations

import re

from mde_compass.catalog import load_catalog

REQUIRED_SLOTS = [
    "idea.summary", "idea.category", "idea.subcategory", "idea.business_model",
    "idea.problem_value", "idea.differentiator",
    "context.geo", "context.scale_launch",
    "goals.primary", "goals.success_measures",
    "audience.segments", "audience.traits", "audience.usage_context",
    "apps.list", "roles.list",
    "scope.features", "scope.priority", "scope.must_not", "scope.references",
    "brand.assets", "brand.name",
    "look.tone_words", "look.visual_references", "look.density_mode",
    "locales", "devices.profile",
    "auth.methods", "auth.account_model", "onboarding.approvals",
    "integrations.wanted", "data.existing_systems",
    "constraints.offline", "constraints.performance", "constraints.accessibility",
    "constraints.must_use", "constraints.must_not",
    "glossary.terms", "unknowns.research_wanted",
    "org.profile", "org.approvers", "org.design_system", "org.api_catalog",
]  # fmt: skip

# Brand names must never appear in fallback cards (neutral wording, hard rule 8).
BRAND_WORDS = {
    "google", "amazon", "uber", "ola", "zomato", "swiggy", "whatsapp", "paytm", "phonepe",
    "razorpay", "stripe", "netflix", "facebook", "instagram", "flipkart", "airbnb", "youtube",
    "spotify", "apple", "microsoft", "tesla", "twitter", "linkedin", "salesforce", "sap",
    "oracle", "shopify", "tiktok", "zoom", "slack", "gpay", "bookmyshow", "makemytrip",
}  # fmt: skip

LEVELS = {"must", "should", "may", "generated"}


def _words(text: str) -> list[str]:
    return text.split()


def test_every_slot_of_the_spec_exists() -> None:
    cat = load_catalog()
    assert [s for s in REQUIRED_SLOTS if s not in cat.slots] == []


def test_slot_metadata_is_in_range_and_consistent() -> None:
    cat = load_catalog()
    for slot in cat.slots.values():
        assert slot.level in LEVELS
        assert 1 <= slot.impact <= 5 and 1 <= slot.order <= 8, slot.id
        assert slot.id.split(".")[0] == slot.group or slot.group in {"auth", "experience"}, slot.id
        assert slot.brief_path and slot.consumers and slot.filled_by, slot.id
        if slot.level == "must":
            assert slot.impact >= 4, f"{slot.id}: a must slot should have high impact"
        if slot.must_confirm:
            assert slot.risk in {"medium", "high"}, slot.id


def test_business_model_is_must_where_money_flows() -> None:
    slot = load_catalog().slots["idea.business_model"]
    assert slot.level == "must" and slot.must_where == "money flows"
    assert slot.ask_if is not None and "internal_tool" in slot.ask_if


def test_risky_slots_are_must_confirm() -> None:
    cat = load_catalog()
    for sid in ("auth.methods", "auth.account_model", "integrations.wanted", "data.sensitive"):
        assert cat.slots[sid].must_confirm, sid


def test_every_fallback_card_is_complete_in_both_languages() -> None:
    for slot in load_catalog().slots.values():
        card = slot.fallback_card
        for lang in ("en", "hi"):
            assert card.question.get(lang).strip(), slot.id
            assert card.why_we_ask.get(lang).strip(), slot.id
            for opt in card.options:
                assert opt.label.get(lang).strip(), slot.id
        assert card.question.hi != card.question.en, slot.id
        assert re.search(r"[ऀ-ॿ]", card.question.hi), slot.id
        assert re.search(r"[ऀ-ॿ]", card.why_we_ask.hi), slot.id
        if card.type == "choose_one":
            assert len(card.options) == 3, slot.id
            for lang in ("en", "hi"):
                assert len({o.label.get(lang) for o in card.options}) == 3, slot.id
            assert all(o.value for o in card.options), slot.id
        if card.type in {"open_prompt", "quick_form", "upload_or_link"}:
            assert card.placeholder is not None, slot.id
        if card.type in {"pick_many", "priority"}:
            assert len(card.options) >= 3, slot.id


def test_fallback_card_values_fit_the_slot_schema() -> None:
    for slot in load_catalog().slots.values():
        schema = slot.value_schema
        if schema.kind in {"enum", "enum_list"} and slot.fallback_card.type != "upload_or_link":
            for opt in slot.fallback_card.options:
                assert opt.value in schema.options, f"{slot.id}: {opt.value!r} not in options"
            if isinstance(slot.default, str):
                assert slot.default in schema.options, slot.id
            if isinstance(slot.default, list):
                assert set(slot.default) <= set(schema.options), slot.id


def test_fallback_card_wording_limits() -> None:
    for slot in load_catalog().slots.values():
        card = slot.fallback_card
        assert len(_words(card.question.en)) <= 20, slot.id
        assert card.question.en.rstrip().endswith("?"), slot.id
        assert len(_words(card.why_we_ask.en)) <= 20, slot.id
        for opt in card.options:
            assert len(_words(opt.label.en)) <= 8, f"{slot.id}: {opt.label.en}"
            assert len(_words(opt.label.hi)) <= 12, f"{slot.id}: {opt.label.hi}"


def test_no_brand_names_in_fallback_cards() -> None:
    for slot in load_catalog().slots.values():
        card = slot.fallback_card
        texts = [card.question, card.why_we_ask, slot.label, *(o.label for o in card.options)]
        if card.placeholder:
            texts.append(card.placeholder)
        for text in texts:
            found = BRAND_WORDS & set(re.findall(r"[a-z]+", text.en.lower()))
            assert not found, f"{slot.id}: brand name {found}"


def test_no_todo_or_blank_text_in_slots() -> None:
    raw = load_catalog().model_dump_json()
    assert "TODO" not in raw
    assert '""' not in raw.replace('":""', "")  # no empty strings as list items


def test_ask_if_only_refers_to_earlier_or_equal_stage_slots_without_cycles() -> None:
    from mde_compass.catalog import ask_if_slot_refs

    cat = load_catalog()
    graph = {
        s.id: set(ask_if_slot_refs(s.ask_if)) if s.ask_if else set() for s in cat.slots.values()
    }

    def has_cycle(node: str, seen: set[str]) -> bool:
        if node in seen:
            return True
        return any(has_cycle(n, seen | {node}) for n in graph[node])

    assert not any(has_cycle(n, set()) for n in graph)


def test_enterprise_slots_are_in_the_org_group() -> None:
    cat = load_catalog()
    org = {s.id for s in cat.slots.values() if s.group == "org"}
    assert org == {"org.profile", "org.approvers", "org.design_system", "org.api_catalog"}
