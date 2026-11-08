"""Whole interviews, driven by taps: about seven cards, at most ten, zero LLM calls.

For each archetype the user taps a starter chip, then agrees with every suggestion. Plus the
sensitive-domain detour, the Enterprise path and conversations in Hindi and Hinglish.
"""

from __future__ import annotations

import pytest

from mde_content.catalogs import archetype_ids, load_catalogs
from mde_content.questions import load_question_banks
from mde_contracts.brief import Brief
from mde_discovery.testing import Conversation, make_run_context

BANKS = load_question_banks()
ARCHETYPES = archetype_ids()
DOMAINS = [d.id for d in load_catalogs().sensitive_domains]


async def interview(
    archetype: str,
    *,
    policy: str = "guided-default",
    lang: str = "en",
    domains: list[str] | None = None,
) -> tuple[Conversation, list[str]]:
    conv = Conversation(policy=policy, language_hint=None if lang == "en" else lang)
    await conv.start()
    chip = BANKS.chips(archetype, lang)[0]
    await conv.say(chip.label)
    if domains:
        flags = list(dict.fromkeys([*conv.state.sensitive_flags, *domains]))
        conv.state = conv.state.model_copy(update={"sensitive_flags": flags})
    asked = await conv.drive_recommended()
    return conv, asked


@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_the_guided_path_asks_about_seven_cards_with_zero_llm_calls(archetype: str) -> None:
    conv, asked = await interview(archetype)
    assert conv.gateway.count() == 0, conv.gateway.call_ids()  # taps and chips cost nothing
    assert 6 <= len(asked) <= 8, asked  # about seven
    assert len(asked) <= 10  # the hard cap
    assert len(set(asked)) == len(asked)
    assert conv.last.brief_complete and conv.pending is None
    assert conv.state.card_count == len(asked)
    visual = [c for c in conv.cards() if c.card.type == "visual3"]
    assert len(visual) <= 2
    assert not any(c.card.kind in ("help", "inspiration", "nudge") for c in conv.cards())
    # every must slot is filled, with the type confirmed and a look chosen
    assert conv.value("idea.archetype") == archetype
    assert conv.value("design.direction") and conv.value("auth.method") and conv.value("brand.name")
    assert conv.value("scope.features")


@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_the_guided_path_follows_the_documented_order(archetype: str) -> None:
    conv, _ = await interview(archetype)
    orders = [
        next(sd.order for sd in load_catalogs().slots if sd.slot_id == c.card.slot)
        for c in conv.cards()
    ]
    assert orders == sorted(orders)
    slots = [c.card.slot for c in conv.cards()]
    assert slots[0] == "idea.archetype" and slots[-1] in ("brand.name", "pay.model")


@pytest.mark.parametrize("lang", ["hi", "hi-Latn"])
@pytest.mark.parametrize("archetype", ["commerce", "internal_workforce"])
async def test_hindi_and_hinglish_interviews_are_fully_localised(archetype: str, lang: str) -> None:
    conv, asked = await interview(archetype, lang=lang)
    assert conv.gateway.count() == 0 and conv.last.brief_complete
    assert (
        conv.state.language == lang
        or conv.state.language == "hi-Latn"
        or conv.state.language == "hi"
    )
    cards = conv.cards()
    assert all(c.language in ("hi", "hi-Latn") for c in cards)
    if lang == "hi":
        assert any(ord(ch) > 0x900 for ch in cards[0].card.text)
    assert 6 <= len(asked) <= 8


@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_a_sensitive_domain_adds_exactly_one_compliance_card(
    archetype: str, domain: str
) -> None:
    base, base_asked = await interview(archetype)
    conv, asked = await interview(archetype, domains=[domain])
    already = domain in base.state.sensitive_flags  # a chip text can flag it by itself
    assert len(asked) == len(base_asked) + (0 if already else 1) <= 10
    expected = len(set(base.state.sensitive_flags) | {domain})
    assert [c.card.kind for c in conv.cards()].count("compliance") == expected
    flags = next(d for d in load_catalogs().sensitive_domains if d.id == domain).compliance_flags
    assert set(flags) <= set(conv.items("compliance.flags"))
    assert domain in conv.state.compliance_confirmed
    assert conv.last.brief_complete


async def test_all_five_domains_still_finish_and_each_is_confirmed_once() -> None:
    conv, asked = await interview("commerce", domains=DOMAINS)
    assert [c.card.kind for c in conv.cards()].count("compliance") == 5
    assert conv.state.compliance_confirmed == DOMAINS
    assert conv.last.brief_complete
    assert len(asked) == len(set(asked))


async def test_a_realistic_pair_of_domains_stays_within_the_card_cap() -> None:
    conv, asked = await interview("food_delivery", domains=["precise_location", "finance"])
    assert len(asked) <= 10 and conv.last.brief_complete
    assert not any(c.card.kind == "nudge" for c in conv.cards())


async def test_enterprise_asks_more_cards_batched_by_group_with_no_cap() -> None:
    conv, asked = await interview("commerce", policy="enterprise-it-services-firm")
    assert conv.last.brief_complete and conv.gateway.count() == 0
    assert len(asked) > 10 and len(asked) <= 24
    assert {"Q-ent-01", "Q-ent-03", "Q-ent-06"} <= set(asked)  # sso, classification, approvers
    assert not any(c.card.kind == "nudge" for c in conv.cards())
    slots = [c.card.slot for c in conv.cards()]
    assert slots.index("auth.sso") < slots.index("design.direction") or "design.direction" in slots
    assert all(c.progress is None for c in conv.cards())  # no "n of 10" in Enterprise
    # the policy lowered the look to "should": it comes after the "must" slots
    assert slots.index("design.direction") > slots.index("brand.name")


async def test_enterprise_must_confirm_slots_are_never_silently_assumed() -> None:
    conv = Conversation(policy="enterprise-it-services-firm")
    await conv.start()
    await conv.say(BANKS.chips("commerce", "en")[0].label)
    await conv.ui("finish_with_defaults")
    pending = conv.pending
    assert pending is not None  # still asking: single sign-on must be confirmed by a person
    slot = pending.card.slot
    assert slot in ("auth.sso", "pay.model", "compliance.flags")
    assert not conv.last.brief_complete
    for _ in range(6):
        if conv.pending is None:
            break
        await conv.tap_recommended()
    assert conv.last.brief_complete
    assert conv.state_of("auth.sso") == "confirmed"


async def test_the_idea_card_when_the_first_message_gave_no_idea() -> None:
    conv = Conversation()
    await conv.start()
    await conv.ui("finish_with_defaults")
    assert conv.pending is not None and conv.pending.card.id == "Q-idea-01"
    await conv.tap(None, other_text="a bakery where people pre-order cakes")
    # the idea typed into its card is a first idea: C1 and C2 read it
    assert conv.gateway.call_ids() == ["C1.understand", "C2.interpret_brief"]
    assert conv.value("idea.archetype") == "food_delivery"
    assert conv.state.finished_with_defaults and conv.last.brief_complete


async def test_a_full_typed_conversation_from_hi_to_a_brief_with_a_summary() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say("hi")
    await conv.say("I want an app for my bakery where people can order cakes")
    assert conv.pending is not None
    await conv.say("yes it is")
    await conv.say("both customers and a seller app, customer first")
    await conv.drive_recommended()
    assert conv.last.brief_complete
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert isinstance(brief, Brief) and brief.app_summary
    assert [r.key for r in brief.rows] == [
        "building",
        "users",
        "features",
        "look",
        "login",
        "name_logo",
    ]
    assert conv.gateway.count("C11.write_summary") == 1


async def test_scripted_conversations_are_deterministic() -> None:
    async def run() -> list[str]:
        conv = Conversation()
        await conv.start()
        await conv.say("I want an app for my salon, I love Swiggy and I want no login")
        await conv.drive_recommended()
        return [e.model_dump_json(exclude={"at"}) for e in conv.events]

    assert await run() == await run()


@pytest.mark.parametrize("lang", ["en", "hi", "hi-Latn"])
@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_the_brief_of_every_archetype_in_every_language_is_complete(
    archetype: str, lang: str
) -> None:
    from mde_contracts.slots import SLOT_IDS

    conv, _ = await interview(archetype, lang=lang)
    brief = await conv.engine.compile_brief(conv.request(), make_run_context())
    assert [r.key for r in brief.rows] == [
        "building",
        "users",
        "features",
        "look",
        "login",
        "name_logo",
    ]
    for row in brief.rows:
        assert row.title and row.text and row.slot_ids
        assert all(s in SLOT_IDS for s in row.slot_ids)
        assert "{" not in row.text and "}" not in row.text
    features = brief.row("features")
    assert features is not None and features.features and any(f.on for f in features.features)
    look = brief.row("look")
    assert look is not None and len(look.swatch) == 4
    assert brief.app_summary and brief.language == conv.state.language
    assert Brief.model_validate(brief.model_dump()) == brief
