"""Catalog loader: strings, t() fallbacks, ask_if grammar and structural validation."""

from __future__ import annotations

import re

import pytest

from mde_compass.catalog import (
    CatalogError,
    ask_if_slot_refs,
    load_catalog,
    parse_ask_if,
)

PLACEHOLDER = re.compile(r"\{(\w+)\}")

REQUIRED_STRING_KEYS = [
    "agent.name",
    "welcome.greeting", "welcome.start_describe", "welcome.start_upload",
    "welcome.start_like_app", "welcome.start_just_build", "welcome.language_prompt",
    "ui.start_new_project", "ui.your_projects", "ui.mode_guided", "ui.mode_company",
    "ui.back", "ui.undo", "ui.skip", "ui.you_decide", "ui.something_else", "ui.continue",
    "ui.looks_right", "ui.change_something", "ui.id_change_this", "ui.show_chat_history",
    "ui.where_from", "ui.updated_tag", "ui.undo_toast", "ui.send", "ui.type_here", "ui.mic",
    "ui.upload", "ui.spent_cap",
    "progress.question_n_of_about",
    "card.why_we_ask", "card.assumed_collapsed", "card.custom_placeholder",
    "section.idea", "section.who", "section.apps_roles", "section.features", "section.look",
    "section.access", "section.integrations_data", "section.limits", "section.words",
    "section.assumed", "section.unknown", "section.lookup", "section.sources",
    "section.cannot_build",
    "badge.you_said", "badge.from_document", "badge.assumed", "badge.typical",
    "badge.suggestion",
    "badge.icon.you_said", "badge.icon.from_document", "badge.icon.assumed",
    "badge.icon.typical", "badge.icon.suggestion",
    "status.draft", "status.in_review", "status.approved",
    "readiness.research", "readiness.blueprint", "readiness.design", "readiness.ready_for",
    "readiness.gaps",
    "scope.in", "scope.later", "scope.out", "scope.unsupported", "scope.needs_engineering",
    "priority.must", "priority.nice", "priority.later",
    "blocker.must_slots_empty", "blocker.high_impact_assumption", "blocker.probes_not_passed",
    "blocker.blocking_question", "blocker.hash_mismatch", "blocker.approvers_missing",
    "change.reason.not_what_i_meant", "change.reason.too_much", "change.reason.something_missing",
    "change.reason.wrong_tone", "change.reason.dont_want_assumption", "change.reason.free_text",
    "change.updated", "change.changing_chip",
    "notice.picked_up", "notice.paused", "notice.resumed_recap", "notice.declined_category",
    "notice.assumed_added", "notice.degraded_probes",
    "lookup.title", "lookup.add_chip", "lookup.remove_chip",
    "history.title", "sources.title",
    "dev.title", "dev.mock", "dev.live",
    "error.generic", "error.rate_limited", "error.budget_stopped",
    "fallback.not_sure_rephrase", "fallback.out_of_scope_price_time",
    "fallback.tech_choice_fixed_stack", "fallback.unsafe_decline",
]  # fmt: skip


def test_catalog_loads_and_is_cached() -> None:
    assert load_catalog() is load_catalog()


def test_string_key_parity_and_required_keys() -> None:
    cat = load_catalog()
    assert set(cat.strings["en"]) == set(cat.strings["hi"])
    missing = [k for k in REQUIRED_STRING_KEYS if k not in cat.strings["en"]]
    assert missing == []


def test_strings_have_matching_placeholders_and_no_blanks() -> None:
    cat = load_catalog()
    for key, en_text in cat.strings["en"].items():
        hi_text = cat.strings["hi"][key]
        assert en_text.strip() and hi_text.strip(), key
        assert "TODO" not in en_text + hi_text, key
        assert set(PLACEHOLDER.findall(en_text)) == set(PLACEHOLDER.findall(hi_text)), key


def test_hindi_strings_are_really_hindi() -> None:
    cat = load_catalog()
    devanagari = re.compile(r"[ऀ-ॿ]")
    same_ok = {k for k in cat.strings["en"] if k.startswith("badge.icon.")}
    for key, hi_text in cat.strings["hi"].items():
        if key not in same_ok:
            assert devanagari.search(hi_text), key


def test_titles_follow_the_brief_page_spec() -> None:
    en = load_catalog().strings["en"]
    assert en["section.idea"] == "Your idea"
    assert en["section.who"] == "Who it is for"
    assert en["section.cannot_build"] == "What we cannot build"
    assert en["lookup.title"] == "What we will look up for you"
    assert en["progress.question_n_of_about"] == "Question {n} of about {total}"
    assert en["change.updated"] == "Updated: {where} › {what}"


def test_t_fills_placeholders_in_each_language() -> None:
    cat = load_catalog()
    assert cat.t("progress.question_n_of_about", n=3, total=7) == "Question 3 of about 7"
    assert "7" in cat.t("progress.question_n_of_about", "hi", n=3, total=7)
    assert cat.t("card.assumed_collapsed", value="UPI") == "Assumed: UPI"


def test_t_falls_back_to_english_then_to_the_key() -> None:
    cat = load_catalog()
    assert cat.t("ui.back", "fr") == cat.t("ui.back", "en")
    assert cat.t("ui.back", "hinglish") == "Back"
    assert cat.t("no.such.key", "hi") == "no.such.key"
    assert cat.t("no.such.key", "xx", a=1) == "no.such.key"


def test_t_leaves_unknown_placeholders_and_ignores_extra_values() -> None:
    cat = load_catalog()
    assert cat.t("notice.picked_up") == "I picked up: {items}"
    assert cat.t("notice.picked_up", other="x") == "I picked up: {items}"
    assert cat.t("ui.back", extra="x") == "Back"


def test_t_does_not_use_attribute_lookup_on_values() -> None:
    cat = load_catalog()
    # str.format_map with a plain dict: no attribute or index access can leak internals
    assert cat.t("notice.picked_up", items="{0.__class__}") == "I picked up: {0.__class__}"


def test_l10n_get_falls_back_to_english() -> None:
    card = next(iter(load_catalog().slots.values())).fallback_card
    assert card.question.get("hi") == card.question.hi
    assert card.question.get("de") == card.question.en


def test_slot_ids_by_order_is_sorted_and_stable() -> None:
    cat = load_catalog()
    ids = cat.slot_ids_by_order()
    assert sorted(ids) == sorted(cat.slots)
    orders = [cat.slots[i].order for i in ids]
    assert orders == sorted(orders)
    assert ids == cat.slot_ids_by_order()


def test_ask_if_parser_accepts_the_documented_grammar() -> None:
    expr = (
        "filled('idea.category') and (value('idea.category') != 'internal_tool' "
        "or not empty('context.geo')) and value('apps.list') in ['a', 'b']"
    )
    assert ask_if_slot_refs(expr) == ["idea.category", "idea.category", "context.geo", "apps.list"]
    assert parse_ask_if("value('x.y') == 'z'") == ("value", "x.y", "==", "z")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "filled(idea.category)",
        "filled('a') &&",
        "value('a') = 'b'",
        "value('a') in 'b'",
        "__import__('os')",
        "filled('a') filled('b')",
        "(filled('a')",
        "filled('a') and",
    ],
)
def test_ask_if_parser_rejects_bad_expressions(bad: str) -> None:
    with pytest.raises(CatalogError):
        parse_ask_if(bad)


def test_every_slot_is_structurally_sound() -> None:
    cat = load_catalog()
    for slot in cat.slots.values():
        assert 1 <= slot.impact <= 5
        assert 1 <= slot.order <= 8
        card = slot.fallback_card
        if card.type == "choose_one":
            assert len(card.options) == 3
        if slot.ask_if:
            for ref in ask_if_slot_refs(slot.ask_if):
                assert ref in cat.slots
