"""Canned UI copy: required ids, languages, placeholders, fallback."""

from __future__ import annotations

import re

import pytest

from mde_content.catalogs import load_catalogs
from mde_content.questions import load_question_banks
from mde_content.ui_copy import copy, copy_ids

REQUIRED = {
    "greeting",
    "greeting_input_hint",
    "nudge",
    "finish_with_defaults",
    "finish_with_defaults_offer",
    "cap_reached",
    "didnt_catch",
    "help_intro",
    "stopped",
    "paused",
    "restarted",
    "resume",
    "assumed_ack",
    "unsafe_decline",
    "unsupported_type",
    "unsupported_dev_build",
    "slow",
    "secret_redacted",
    "reference_app",
    "multi_app",
    "scope_large",
    "no_gate_open",
    "gate_unclear",
    "queued_change",
    "new_looks",
    "describe_look",
    "ack_1",
    "ack_2",
    "ack_3",
    # the first nudge (silence, off-topic chat) and the one for a card nobody may answer for the user
    "nudge_wait",
    # company rules: a banned library, a data region outside the approved ones
    "policy_library_explain",
    "policy_library_question",
    "policy_library_help",
    "policy_library_applied",
    "policy_region_explain",
    "policy_region_question",
    "policy_region_help",
    "policy_region_applied",
    # the Brief in the user's language
    "row_building",
    "row_users",
    "row_features",
    "row_look",
    "row_login",
    "row_name_logo",
    "users_default",
    "features_none",
    "features_text",
    "features_extra",
    "look_dark",
    "login_sso",
    "name_text",
    "logo_none",
    "logo_file",
    "on",
    "off",
    "fact_features",
    "fact_look",
}
LANGS = ("en", "hi", "hi-Latn")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
DEVANAGARI = re.compile("[ऀ-ॿ]")


def test_required_copy_exists_with_all_languages() -> None:
    ids = copy_ids()
    assert set(ids) >= REQUIRED
    assert len(ids) == len(set(ids))
    for cid in ids:
        item = load_question_banks().ui_copy(cid)
        assert set(item.text) == set(LANGS), cid
        assert item.text["en"].strip() == item.text["en"] and item.text["en"], cid
        assert DEVANAGARI.search(item.text["hi"]), cid
        assert item.text["hi-Latn"].isascii(), cid
        assert not DEVANAGARI.search(item.text["hi-Latn"]), cid


def test_the_spec_greeting() -> None:
    assert copy("greeting") == "Hi! What would you like to build?"
    assert copy("greeting", "hi") == "नमस्ते! आप क्या बनाना चाहेंगे?"
    assert copy("greeting", "hi-Latn") == "Namaste! Aap kya banana chahenge?"
    assert copy("didnt_catch") == "I did not catch that. Here are the options."


def test_placeholders_match_across_languages() -> None:
    for cid in copy_ids():
        item = load_question_banks().ui_copy(cid)
        names = {lang: set(PLACEHOLDER.findall(text)) for lang, text in item.text.items()}
        assert names["hi"] == names["en"] == names["hi-Latn"], cid


def test_placeholders_are_filled() -> None:
    got = copy("resume", "en", app="bakery app", question="Which look do you like?")
    assert got == "We were designing your bakery app. Next question: Which look do you like?"
    hinglish = copy("resume", "hi-Latn", app="bakery app", question="Q?")
    assert "bakery app" in hinglish and "{app}" not in hinglish
    assert copy("assumed_ack", "en", option="Email code", reason="It is the simplest.") == (
        "No problem, I will go with Email code. It is the simplest."
    )


def test_missing_placeholder_values_are_left_as_written() -> None:
    assert copy("assumed_ack", "en", option="X") == "No problem, I will go with X. {reason}"
    assert copy("resume") == ("We were designing your {app}. Next question: {question}")


def test_only_named_placeholders_are_substituted() -> None:
    # extra values are ignored; format specs and attribute access are never evaluated
    assert copy("greeting", "en", unused="x") == "Hi! What would you like to build?"
    got = copy("assumed_ack", "en", option="{0.__class__}", reason="{option}")
    assert got == "No problem, I will go with {0.__class__}. {option}"


def test_language_fallback() -> None:
    assert copy("nudge", "fr") == copy("nudge", "en")
    assert copy("nudge", None) == copy("nudge", "en")
    assert copy("nudge", "hi-IN") == copy("nudge", "hi")
    assert copy("nudge", "hi-Latn-IN") == copy("nudge", "hi-Latn")
    assert copy("nudge", "hi") != copy("nudge", "en")
    assert copy("nudge", "hi-Latn") != copy("nudge", "hi")


def test_unknown_id_raises_key_error() -> None:
    with pytest.raises(KeyError):
        copy("no_such_copy")


def test_copy_is_short_warm_and_never_invents_numbers() -> None:
    for cid in copy_ids():
        text = copy(cid)
        assert len(text) <= 260, cid
        assert not re.search(r"\d", text), cid
        assert not re.search(r"[$₹€]", text), cid
        assert "—" not in text, cid


def test_unsupported_and_decline_templates_read_well() -> None:
    got = copy(
        "unsupported_type",
        "en",
        type="Game",
        reason="Games need special tools.",
        closest="a simple quiz app",
    )
    assert got.startswith("I cannot build this kind of app yet: Game.")
    assert "a simple quiz app" in got
    decline = copy("unsafe_decline", "en", reason="It could hurt people.")
    assert "safe version" in decline and "It could hurt people." in decline
    assert "taking longer than usual" in copy("slow").lower().replace("this is ", "")


def test_every_assumption_reason_is_translated_and_in_step_with_the_catalog() -> None:
    """ "We assumed ..." reads in the user's language: one ``reason.<slot id>`` line per slot."""
    slots = load_catalogs().slots
    for sd in slots:
        if not sd.default_reason:
            continue
        item = load_question_banks().ui_copy(f"reason.{sd.slot_id}")
        assert item.text["en"] == sd.default_reason, sd.slot_id  # one English source of truth
        assert copy(f"reason.{sd.slot_id}", "hi") != sd.default_reason, sd.slot_id
        assert copy(f"reason.{sd.slot_id}", "hi-Latn") != sd.default_reason, sd.slot_id
    ids = {sd.slot_id for sd in slots}
    reasons = [c for c in copy_ids() if c.startswith("reason.")]
    assert reasons and all(c.removeprefix("reason.") in ids for c in reasons)


def test_no_template_glues_the_word_apps_onto_a_label() -> None:
    """A phrase label such as "Desktop apps" already says what it is: never add "app(s)" after it."""
    glued = re.compile(r"\{[a-z_]+\}\s+apps?(?!\w)", re.IGNORECASE)
    for cid in copy_ids():
        for lang, text in load_question_banks().ui_copy(cid).text.items():
            assert not glued.search(text), (cid, lang)


def test_company_rule_copy_names_what_it_needs() -> None:
    assert "{library}" in copy("policy_library_explain")
    assert {"{allowed}", "{asked}"} <= set(re.findall(r"\{\w+\}", copy("policy_region_explain")))
    assert "region" in copy("policy_region_explain").lower()
    assert copy("policy_region_explain", "hi") != copy("policy_region_explain", "hi-Latn")
