"""The Brief and "We assumed ..." in the user's language (D-048).

Row titles, the sentence pieces and the one-line reasons of the assumptions are content-bank copy
(``mde_content`` ui_copy: ``row_*``, ``reason.<slot id>`` ...), chosen by the session language. These
tests drive Hindi and Hinglish sessions from the greeting chip to the compiled Brief.
"""

from __future__ import annotations

import re

import pytest

from mde_content.catalogs import load_catalogs
from mde_content.questions import load_question_banks
from mde_contracts.brief import Brief
from mde_contracts.events import AssistantMessage
from mde_discovery import copy as texts
from mde_discovery.testing import Conversation, make_run_context
from mde_prompts.calls.c1_understand import lexicon as lx

BANKS = load_question_banks()
DEVANAGARI = re.compile("[ऀ-ॿ]")
ROW_TITLES = {
    "building": "row_building",
    "users": "row_users",
    "features": "row_features",
    "look": "row_look",
    "login": "row_login",
    "name_logo": "row_name_logo",
}


async def session(lang: str, *, assume: int = 3) -> Conversation:
    """Chip, a few "you decide" answers (assumptions), then the recommended path to the end."""
    conv = Conversation(language_hint=None if lang == "en" else lang)
    await conv.start()
    await conv.say(BANKS.chips("commerce", lang)[0].label)
    await conv.tap_recommended()  # confirm the type
    for _ in range(assume):
        await conv.tap(you_decide=True)
    await conv.ui("finish_with_defaults")
    assert conv.last.brief_complete
    return conv


async def brief_of(conv: Conversation) -> Brief:
    return await conv.engine.compile_brief(conv.request(), make_run_context())


@pytest.mark.parametrize("lang", ["hi", "hi-Latn"])
async def test_the_rows_read_in_the_users_language(lang: str) -> None:
    conv = await session(lang)
    brief = await brief_of(conv)
    assert brief.language == lang
    for row in brief.rows:
        assert row.title == texts.say(ROW_TITLES[row.key], lang), row.key
        assert row.title != texts.say(ROW_TITLES[row.key], "en") or row.key in ("look", "login")
    login = brief.row("login")
    features = brief.row("features")
    name_logo = brief.row("name_logo")
    assert login is not None and features is not None and name_logo is not None
    assert name_logo.text == texts.say(
        "name_text", lang, name=conv.value("brand.name"), logo=texts.say("logo_none", lang)
    )
    english = await brief_of(await session("en"))
    assert [r.text for r in brief.rows] != [r.text for r in english.rows]
    if lang == "hi":
        assert all(DEVANAGARI.search(r.title) for r in brief.rows if r.key != "look")
        assert DEVANAGARI.search(features.text) and DEVANAGARI.search(name_logo.text)
    else:
        assert all(r.title.isascii() for r in brief.rows)
        assert features.text.isascii() and name_logo.text.isascii()
        assert "Naam:" in name_logo.text


@pytest.mark.parametrize("lang", ["hi", "hi-Latn"])
async def test_the_assumptions_list_reads_in_the_users_language(lang: str) -> None:
    conv = await session(lang)
    brief = await brief_of(conv)
    assert brief.assumptions
    catalog = {sd.slot_id: sd.default_reason for sd in load_catalogs().slots}
    for item in brief.assumptions:
        english = catalog[item.slot_id]
        assert item.reason and item.reason != english, item.slot_id  # not the catalog's English
        assert item.reason == texts.reason(item.slot_id, lang), item.slot_id
        assert item.label == lx.slot_label(item.slot_id, lang), item.slot_id
        if lang == "hi":
            assert DEVANAGARI.search(item.reason) and DEVANAGARI.search(item.label), item.slot_id
        else:
            assert item.reason.isascii(), item.slot_id
    slots = {a.slot_id for a in brief.assumptions}
    assert {"scope.apps", "scope.features", "auth.method"} <= slots


@pytest.mark.parametrize("lang", ["hi", "hi-Latn"])
async def test_we_assumed_in_the_chat_uses_the_same_localised_reason(lang: str) -> None:
    conv = Conversation(language_hint=lang)
    await conv.start()
    await conv.say(BANKS.chips("commerce", lang)[0].label)
    await conv.tap_recommended()
    assert conv.pending is not None and conv.pending.card.slot == "scope.apps"
    await conv.tap(you_decide=True)
    said = [e.text for e in conv.last.events if isinstance(e, AssistantMessage)]
    reason = texts.reason("scope.apps", lang)
    assert any(reason in t for t in said)
    rec = conv.slot("scope.apps")
    assert rec is not None and rec.evidence == reason  # what is stored is what was said
    english = next(sd.default_reason for sd in load_catalogs().slots if sd.slot_id == "scope.apps")
    assert not any(english in t for t in said)


async def test_a_reason_given_before_the_language_changed_is_said_again_in_the_new_language() -> (
    None
):
    conv = Conversation()
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()
    await conv.tap(you_decide=True)  # English reason stored for scope.apps
    assert conv.slot("scope.apps") is not None
    english = conv.slot("scope.apps").evidence  # type: ignore[union-attr]
    assert english == texts.reason("scope.apps", "en")
    await conv.ui("finish_with_defaults")
    conv.state = conv.state.model_copy(update={"language": "hi"})  # the user switched to Hindi
    brief = await brief_of(conv)
    item = next(a for a in brief.assumptions if a.slot_id == "scope.apps")
    assert item.reason == texts.reason("scope.apps", "hi") != english


async def test_a_reason_nobody_translated_is_kept_as_written() -> None:
    assert texts.reason_in("A custom reason from an older session.", "hi") == (
        "A custom reason from an older session."
    )
    assert texts.reason_in("", "hi", "fallback") == "fallback"
    assert texts.reason("no.such.slot", "hi", "kept") == "kept"
    english = texts.reason("auth.method", "en")
    assert texts.reason_in(english, "hi-Latn") == texts.reason("auth.method", "hi-Latn")
    assert texts.reason_in(texts.reason("auth.method", "hi"), "en") == english


def test_every_slot_with_a_reason_has_it_in_all_three_languages() -> None:
    for sd in load_catalogs().slots:
        if not sd.default_reason:
            continue
        en = texts.reason(sd.slot_id, "en")
        assert en == sd.default_reason
        assert DEVANAGARI.search(texts.reason(sd.slot_id, "hi")), sd.slot_id
        latin = texts.reason(sd.slot_id, "hi-Latn")
        assert latin.isascii() and latin != en, sd.slot_id


def test_the_brief_pieces_moved_to_the_bank_are_not_kept_twice() -> None:
    own = set(texts.own_ids())
    banked = set(BANKS.ui_copy_ids())
    assert not own & banked  # one home for every line
    for copy_id in ROW_TITLES.values():
        assert copy_id in banked
    for copy_id in ("users_default", "features_none", "features_text", "look_dark", "login_sso"):
        assert copy_id in banked and copy_id not in own


def test_no_engine_template_glues_the_word_apps_onto_a_label() -> None:
    glued = re.compile(r"\{[a-z_]+\}\s+apps?(?!\w)", re.IGNORECASE)
    for copy_id in texts.own_ids():
        for lang, text in texts._own()[copy_id].items():  # pyright: ignore[reportPrivateUsage]
            assert not glued.search(text), (copy_id, lang)
