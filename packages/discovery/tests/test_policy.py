"""GAP-4: company rules. ``Policy.banned_libraries`` and ``Policy.allowed_regions`` are checked.

Spec section 9, "Enterprise: request violates org policy": explain the constraint in plain words and
show the allowed alternatives as a card. Detection is data-driven (aliases per library, region
table and pattern in ``policy_alternatives.yaml``), case-insensitive, whole-word and costs no LLM
call. A banned library or a disallowed region is never accepted silently.
"""

from __future__ import annotations

import re

import pytest

from mde_content.policies import resolve_policy
from mde_contracts.calls import C1Output, SlotUpdate
from mde_contracts.events import AssistantMessage, CardEvent, Notice
from mde_contracts.policy import Policy
from mde_discovery import copy as texts
from mde_discovery.policy_guard import PolicyGuard, Violation, load_policy_data
from mde_discovery.testing import Conversation, ScriptedGateway

GUARD = PolicyGuard()
BANK = resolve_policy("regulated-bank")  # bans react-native-code-push; allows eu-west, ap-south
DEVANAGARI = re.compile("[ऀ-ॿ]")
LIBRARY_IDEA = (
    "An online shop for a client, and we want to ship updates with react-native-code-push"
)
REGION_IDEA = (
    "An online shop for a client, and all customer data must be stored in the us-east region"
)


def custom_policy(
    *, banned: list[str] | None = None, regions: list[str] | None = None, mode: str = "enterprise"
) -> Policy:
    return resolve_policy(
        Policy(
            id="test-policy",
            org="test",
            mode=mode,  # type: ignore[arg-type]
            inherits="platform-default@1",
            banned_libraries=banned or [],
            allowed_regions=regions or [],
        )
    )


def messages(conv: Conversation) -> list[str]:
    return [e.text for e in conv.last.events if isinstance(e, AssistantMessage)]


def shown_cards(conv: Conversation) -> list[str]:
    return [e.card.card.id for e in conv.last.events if isinstance(e, CardEvent)]


# =========================================================================== the data file


def test_the_data_file_validates_and_is_fully_translated() -> None:
    data = load_policy_data()
    tables = []
    for entry in data.libraries.values():
        for alt in entry.alternatives:
            tables += [alt.label, alt.description, alt.phrase]
    for alt in data.generic.alternatives:
        tables += [alt.label, alt.description, alt.phrase]
    for region in data.regions:
        tables.append(region.label)
    assert tables
    for table in tables:
        assert set(table) == {"en", "hi", "hi-Latn"}, table
        assert DEVANAGARI.search(table["hi"]) and table["hi-Latn"].isascii(), table
    for entry in data.libraries.values():
        assert entry.alternatives, "every listed library offers at least one alternative"
        for alt in entry.alternatives:
            assert alt.phrase and alt.description, alt.id
    assert [a.id for a in data.generic.alternatives][-1] == "ask_it"


def test_region_ids_are_unique_groups_are_known_and_patterns_compile() -> None:
    data = load_policy_data()
    ids = [r.id for r in data.regions]
    assert len(ids) == len(set(ids))
    groups = {r.group for r in data.regions}
    assert groups == {"us", "eu", "in", "other"}  # the values of the `regions` slot
    assert set(data.prefix_groups.values()) <= groups
    for pattern in data.region_patterns:
        re.compile(pattern)


def test_nothing_in_the_data_is_the_name_of_a_company_or_an_app_type() -> None:
    raw = (
        __import__("importlib.resources", fromlist=["files"])
        .files("mde_content")
        .joinpath("data/policy_alternatives.yaml")
        .read_text(encoding="utf-8")
        .lower()
    )
    for banned in ("acme", "example-", "bank", "commerce", "food_delivery"):
        assert banned not in raw


# ========================================================================= libraries (the guard)


@pytest.mark.parametrize(
    "text",
    [
        "we want to ship updates with react-native-code-push",
        "use React Native Code Push for updates",
        "REACT-NATIVE-CODE-PUSH please",
        "can we use CodePush?",
        "use code push for hotfixes",
        "uses App Center CodePush",
    ],
)
def test_a_banned_library_is_recognised_by_name_and_aliases_in_any_case(text: str) -> None:
    assert GUARD.scan_libraries(text, BANK) == [Violation("library", ("react-native-code-push",))]


@pytest.mark.parametrize(
    "text",
    ["a codepusher tool", "push the code to the shop", "react native is great", "decode push"],
)
def test_matching_is_whole_word(text: str) -> None:
    assert GUARD.scan_libraries(text, BANK) == []


def test_an_everyday_word_library_only_matches_through_its_aliases() -> None:
    policy = custom_policy(banned=["moment"])
    assert GUARD.scan_libraries("just a moment please", policy) == []
    assert GUARD.scan_libraries("I need one moment to think", policy) == []
    assert GUARD.scan_libraries("format dates with moment.js", policy) == [
        Violation("library", ("moment",))
    ]
    assert GUARD.scan_libraries("we use momentjs today", policy)


def test_a_library_the_data_does_not_know_matches_by_its_own_id() -> None:
    policy = custom_policy(banned=["acme-charts-kit"])
    assert GUARD.scan_libraries("draw graphs with Acme Charts Kit", policy)
    assert GUARD.scan_libraries("draw graphs", policy) == []
    alternatives = GUARD.alternatives("acme-charts-kit", "en")
    assert [a.id for a in alternatives] == ["approved_option", "ask_it"]


def test_several_banned_libraries_are_reported_in_policy_order() -> None:
    policy = custom_policy(banned=["lodash", "axios", "moment"])
    hits = GUARD.scan("use axios and lodash and moment.js", policy)
    assert [v.keys for v in hits] == [("lodash",), ("axios",), ("moment",)]


def test_alternatives_are_the_librarys_own_plus_ask_it_the_first_is_recommended() -> None:
    views = GUARD.alternatives("react-native-code-push", "en")
    assert [a.id for a in views] == ["store_release", "expo_updates", "ask_it"]
    assert views[0].label == "Regular app store releases"
    hindi = GUARD.alternatives("react-native-code-push", "hi")
    assert all(DEVANAGARI.search(a.label) for a in hindi)
    hinglish = GUARD.alternatives("react-native-code-push", "hi-Latn")
    assert all(a.label.isascii() for a in hinglish)


def test_replace_library_says_the_phrase_wherever_it_was_named() -> None:
    text = "Ship updates with react-native-code-push, or Code Push for hotfixes."
    out = GUARD.replace_library(text, "react-native-code-push", "regular app store releases")
    assert (
        out
        == "Ship updates with regular app store releases, or regular app store releases for hotfixes."
    )
    assert GUARD.replace_library("nothing here", "react-native-code-push", "x") == "nothing here"


# =========================================================================== regions (the guard)


@pytest.mark.parametrize(
    "text", ["stored in us-east", "US-EAST-1 please", "the us-west-2 region", "eu-central"]
)
def test_a_region_id_the_policy_does_not_list_is_a_violation(text: str) -> None:
    [v] = GUARD.scan_regions(text, BANK)
    assert v.kind == "region" and v.keys


@pytest.mark.parametrize(
    "text", ["keep it in eu-west", "eu-west-1 is fine", "ap-south-1", "AP-SOUTH"]
)
def test_a_region_id_the_policy_lists_is_fine(text: str) -> None:
    assert GUARD.scan_regions(text, BANK) == []


def test_zone_numbers_and_case_do_not_matter() -> None:
    [v] = GUARD.scan_regions("hosted in US-East-2", BANK)
    assert v.keys == ("us-east",)


def test_unknown_cloud_style_ids_are_found_by_the_pattern() -> None:
    [v] = GUARD.scan_regions("run it in me-south-1", BANK)
    assert v.keys == ("me-south",)
    assert GUARD.region_label("me-south", "en") is None  # not in the table: no pretty name


def test_without_allowed_regions_nothing_is_checked() -> None:
    policy = custom_policy(banned=["moment"])
    assert GUARD.scan_regions("us-east and eu-west", policy) == []
    assert GUARD.disallowed_regions(["us", "other"], policy) == []


def test_a_slot_value_is_fine_when_some_allowed_region_is_in_its_group() -> None:
    assert (
        GUARD.disallowed_regions(["in", "eu"], BANK) == []
    )  # ap-south is India, eu-west is Europe
    assert GUARD.disallowed_regions(["us", "other"], BANK) == ["us", "other"]
    assert GUARD.disallowed_regions(["eu-west", "ap-south", "us-east"], BANK) == ["us-east"]
    assert GUARD.disallowed_regions(["eu-central"], BANK) == ["eu-central"]  # a region not listed
    assert GUARD.disallowed_regions(["somewhere"], BANK) == ["somewhere"]


def test_a_policy_that_lists_a_group_allows_the_regions_inside_it() -> None:
    policy = custom_policy(regions=["eu"])
    assert GUARD.disallowed_regions(["eu", "eu-west", "eu-central"], policy) == []
    assert GUARD.disallowed_regions(["us-east", "in"], policy) == ["us-east", "in"]


def test_region_labels_follow_the_language() -> None:
    assert GUARD.region_label("eu-west", "en") == "Western Europe"
    assert DEVANAGARI.search(GUARD.region_label("eu-west", "hi") or "")
    assert (GUARD.region_label("eu-west", "hi-Latn") or "").isascii()


def test_scan_is_a_no_op_without_rules_or_text() -> None:
    plain = resolve_policy("guided-default")
    assert not GUARD.active(plain)
    assert GUARD.scan("react-native-code-push in us-east", plain) == []
    assert GUARD.scan("   ", BANK) == []


def test_violations_round_trip_through_json_and_garbage_is_dropped() -> None:
    v = Violation("region", ("us-east", "us"))
    assert Violation.from_json(v.to_json()) == v
    assert Violation.from_json({"kind": "library", "keys": ["x"]}) == Violation("library", ("x",))
    for bad in (
        None,
        3,
        {"kind": "other", "keys": ["x"]},
        {"kind": "library", "keys": []},
        {"kind": "region"},
    ):
        assert Violation.from_json(bad) is None


# ============================================================================ the engine: libraries


async def bank_conversation() -> Conversation:
    conv = Conversation(policy="regulated-bank")
    await conv.start()
    return conv


async def test_a_banned_library_gets_an_explanation_and_a_card_of_allowed_alternatives() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    text = " ".join(messages(conv))
    assert "react-native-code-push" in text and "do not allow" in text
    assert shown_cards(conv) == ["Q-policy-lib:react-native-code-push"]  # not the type question
    card = conv.pending
    assert card is not None and card.card.kind == "resolution" and card.card.type == "choice3"
    assert [o.id for o in card.card.options] == ["store_release", "expo_updates", "ask_it"]
    assert [o.recommended for o in card.card.options] == [True, False, False]
    assert not card.card.skip.enabled and not card.card.other.enabled
    assert not conv.last.brief_complete and conv.state.card_count == 0
    assert conv.gateway.count() == 2  # C1 + C2: the check itself costs nothing
    assert "Asked for react-native-code-push" in conv.state.summary


async def test_the_card_that_was_about_to_be_asked_is_asked_after_the_choice() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    assert conv.state.cards_asked == []  # the displaced type card was never counted
    await conv.tap("store_release")
    assert conv.pending is not None and conv.pending.card.id == "Q-type-01"
    assert conv.state.card_count == 1


async def test_the_chosen_alternative_is_applied_and_the_request_is_remembered() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    result = await conv.tap("expo_updates")
    idea = conv.value("idea.summary")
    assert isinstance(idea, str) and "react-native-code-push" not in idea
    assert "Expo's own update service" in idea
    assert conv.state_of("idea.summary") == "volunteered"
    summary = conv.state.summary
    assert "Asked for react-native-code-push" in summary  # the original request stays on record
    assert "Company rule: Expo's own update service instead of react-native-code-push" in summary
    assert any(
        "Expo's own update service instead of" in t
        for t in [m.text for m in result.events if isinstance(m, AssistantMessage)]
    )
    assert result.llm_calls == 0


async def test_a_second_message_naming_the_same_library_does_not_stack_cards() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    first = conv.pending
    assert first is not None
    await conv.say("yes please use code push")
    assert conv.pending is not None and conv.pending.card.id == first.card.id
    assert shown_cards(conv).count(first.card.id) <= 1
    assert conv.state.extra.get("policy_queue") is None


async def test_typed_words_never_answer_a_company_rule_card() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    await conv.say("whatever you think is best")
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")
    await conv.say("you decide")  # nobody decides a company rule for the user
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")
    assert any(texts.say("didnt_catch", "en") in t for t in messages(conv))
    await conv.tap(you_decide=True)
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")
    await conv.tap(skipped=True)
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")


async def test_help_on_a_company_rule_card_explains_it_in_one_line() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    await conv.say("I don't know")
    assert any("company rule" in t for t in messages(conv))
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")


async def test_idle_on_a_company_rule_card_waits_and_never_picks() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    first = await conv.ui("idle")
    assert [e.text for e in first.events if isinstance(e, AssistantMessage)] == [
        texts.say("nudge_wait", "en")
    ]
    assert (await conv.ui("idle")).events == []
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")


async def test_finish_with_defaults_with_a_rule_card_open_applies_the_first_alternative() -> None:
    conv = await bank_conversation()
    await conv.say(LIBRARY_IDEA)
    await conv.ui("finish_with_defaults")
    idea = conv.value("idea.summary")
    assert isinstance(idea, str) and "react-native-code-push" not in idea
    assert "regular app store releases" in idea
    assert conv.pending is None or not conv.pending.card.id.startswith("Q-policy-lib:")
    assert conv.state.extra.get("policy_queue") is None
    assert (
        "Company rule: regular app store releases instead of react-native-code-push"
        in conv.state.summary
    )


async def test_an_upload_that_names_a_banned_library_gets_the_card_too() -> None:
    conv = await bank_conversation()
    await conv.say("An online shop for a client")
    await conv.tap_recommended()  # the type
    await conv.upload(
        "notes.md", "Release plan: updates go out with react-native-code-push every week."
    )
    assert (
        conv.pending is not None and conv.pending.card.id == "Q-policy-lib:react-native-code-push"
    )
    assert any("react-native-code-push" in t for t in messages(conv))


async def test_text_typed_next_to_ticked_options_is_checked_too() -> None:
    conv = Conversation(policy=custom_policy(banned=["moment"]))
    await conv.start()
    await conv.say("An online shop")
    await conv.tap_recommended()  # type
    await conv.tap_recommended()  # apps
    assert conv.pending is not None and conv.pending.card.slot == "scope.features"
    await conv.tap(["search"], other_text="show order dates with moment.js")
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-lib:moment"
    await conv.tap("light_dates")
    assert "moment" not in conv.state.extra.get("custom_features", "").lower()
    assert "lightweight date toolkit" in conv.state.extra.get("custom_features", "")


async def test_the_banned_library_is_taken_out_of_a_typed_integration_list() -> None:
    conv = Conversation(policy=custom_policy(banned=["axios"]))
    await conv.start()
    await conv.say("An online shop")
    conv.state = conv.state.model_copy(
        update={
            "slots": {
                **conv.state.slots,
                "integrations.list": conv.state.slots["idea.summary"].model_copy(
                    update={"slot_id": "integrations.list", "value": ["axios", "email"]}
                ),
            }
        }
    )
    await conv.say("we call the server through axios")
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-lib:axios"
    await conv.tap("built_in_network")
    assert conv.items("integrations.list") == ["email"]


# ============================================================================ the engine: regions


async def test_a_disallowed_region_in_the_text_gets_an_explanation_and_the_allowed_regions() -> (
    None
):
    conv = await bank_conversation()
    await conv.say(REGION_IDEA)
    text = " ".join(messages(conv))
    assert "region" in text and "United States (East)" in text
    assert "Western Europe" in text and "India (South Asia)" in text  # what is allowed
    assert shown_cards(conv) == ["Q-policy-region"]
    card = conv.pending
    assert card is not None and card.card.type == "multi" and card.card.slot == "regions"
    assert [o.id for o in card.card.options] == ["eu-west", "ap-south"]
    assert [o.recommended for o in card.card.options] == [True, False]  # the first is pre-ticked
    assert conv.state_of("regions") == "empty"  # never accepted
    assert "Asked for data in United States (East)" in conv.state.summary
    assert conv.gateway.count() == 2


async def test_the_chosen_allowed_regions_are_applied_by_tap() -> None:
    conv = await bank_conversation()
    await conv.say(REGION_IDEA)
    result = await conv.tap(["eu-west", "ap-south"])
    rec = conv.slot("regions")
    assert rec is not None and rec.value == ["eu-west", "ap-south"]
    assert rec.state == "confirmed" and rec.source == "tap"
    assert "Data region set to Western Europe, India (South Asia)" in conv.state.summary
    assert any("Western Europe" in e.text for e in result.events if isinstance(e, AssistantMessage))
    assert conv.pending is not None and conv.pending.card.id == "Q-type-01"
    assert conv.slot("regions") is not None and result.changed_slots[0] == "regions"


async def test_a_single_allowed_region_is_offered_as_one_button() -> None:
    conv = Conversation(policy=custom_policy(regions=["eu-west"]))
    await conv.start()
    await conv.say(REGION_IDEA)
    card = conv.pending
    assert card is not None and card.card.type == "choice3"
    assert [o.id for o in card.card.options] == ["eu-west"]


async def test_a_disallowed_region_in_the_regions_slot_is_never_accepted() -> None:
    conv = await bank_conversation()
    await conv.say("An online shop for a client")
    for _ in range(40):
        card = conv.pending
        assert card is not None
        if card.card.slot == "regions":
            break
        await conv.tap_recommended()
    assert conv.pending is not None and conv.pending.card.slot == "regions"
    await conv.tap(["us", "in"])  # the user ticks United States and India
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-region"
    assert conv.items("regions") == ["in"]  # India is allowed (ap-south), the US is not
    assert [o.recommended for o in conv.pending.card.options] == [True, False]  # defaults kept
    assert any("United States" in t for t in messages(conv))
    await conv.tap(["eu-west"])
    assert conv.items("regions") == ["eu-west"]


async def test_allowed_regions_pass_without_a_word() -> None:
    conv = await bank_conversation()
    await conv.say("An online shop for a client")
    for _ in range(40):
        card = conv.pending
        assert card is not None
        if card.card.slot == "regions":
            break
        await conv.tap_recommended()
    before = conv.state.card_count
    await conv.tap(["eu", "in"])
    assert sorted(conv.items("regions")) == ["eu", "in"]
    assert conv.state_of("regions") == "confirmed"
    assert conv.pending is not None and not conv.pending.card.id.startswith("Q-policy")
    assert conv.state.card_count == before + 1


async def test_a_typed_allowed_region_settles_the_open_region_card() -> None:
    conv = await bank_conversation()
    await conv.say(REGION_IDEA)
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-region"
    gateway = ScriptedGateway(
        {
            "C1.understand": [
                C1Output(
                    intent="answer",
                    confidence=0.9,
                    language="en",
                    slot_updates=[SlotUpdate(slot_id="regions", value_list=["eu"])],
                )
            ]
        }
    )
    conv.swap_gateway(gateway)
    await conv.say("let's keep it in Europe")
    assert conv.items("regions") == ["eu"]
    assert conv.pending is not None and conv.pending.card.id != "Q-policy-region"


async def test_a_country_the_interpreter_finds_in_the_first_idea_is_checked_as_a_region() -> None:
    """Mock C2 reads "United States" as the region group ``us``: the slot check refuses it."""
    conv = await bank_conversation()
    await conv.say("An online shop for a client, all our data lives in the United States")
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-region"
    assert conv.state_of("regions") == "empty"
    assert any("United States" in t for t in messages(conv))
    other = await bank_conversation()
    await other.say("An online shop for a client, selling across Europe")
    assert other.items("regions") == ["eu"]  # an allowed group goes through without a word
    assert other.pending is not None and other.pending.card.id == "Q-type-01"
    assert not any("approved regions" in t for t in messages(other))


async def test_a_typed_disallowed_region_is_taken_out_and_asked_about() -> None:
    conv = await bank_conversation()
    await conv.say("An online shop for a client")
    gateway = ScriptedGateway(
        {
            "C1.understand": [
                C1Output(
                    intent="volunteered",
                    confidence=0.9,
                    language="en",
                    slot_updates=[SlotUpdate(slot_id="regions", value_list=["us"])],
                )
            ]
        }
    )
    conv.swap_gateway(gateway)
    await conv.say("we serve customers in the United States")
    assert conv.items("regions") == []  # volunteered by the understander, refused by the rules
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-region"
    assert any("United States" in t for t in messages(conv))


async def test_typing_the_same_disallowed_region_again_does_not_stack_cards() -> None:
    conv = await bank_conversation()
    await conv.say(REGION_IDEA)
    await conv.say("I really mean us-east")
    assert conv.pending is not None and conv.pending.card.id == "Q-policy-region"
    assert conv.state.extra.get("policy_queue") is None
    assert shown_cards(conv).count("Q-policy-region") <= 1


# ================================================================== the engine: both, and the rest


async def test_a_library_and_a_region_in_one_message_are_asked_one_after_the_other() -> None:
    conv = await bank_conversation()
    await conv.say(f"{LIBRARY_IDEA}, with the data kept in us-east")
    assert shown_cards(conv) == ["Q-policy-lib:react-native-code-push"]
    assert conv.state.extra.get("policy_queue")  # the region question waits its turn
    await conv.tap("store_release")
    assert shown_cards(conv) == ["Q-policy-region"]
    assert conv.state.extra.get("policy_queue") is None
    await conv.tap(["eu-west"])
    assert conv.pending is not None and conv.pending.card.id == "Q-type-01"


async def test_guided_sessions_with_the_default_policy_check_nothing() -> None:
    conv = Conversation()
    await conv.start()
    await conv.say(LIBRARY_IDEA + " in us-east")
    assert conv.pending is not None and not conv.pending.card.id.startswith("Q-policy")
    assert conv.state.extra.get("policy_queue") is None


async def test_a_guided_session_whose_policy_lists_rules_is_checked_too() -> None:
    policy = custom_policy(banned=["react-native-code-push"], mode="guided")
    conv = Conversation(policy=policy)
    await conv.start()
    await conv.say(LIBRARY_IDEA)
    assert conv.pending is not None and conv.pending.card.id.startswith("Q-policy-lib:")


async def test_a_gate_is_not_interrupted_by_a_company_rule_card() -> None:
    conv = await bank_conversation()
    await conv.say("An online shop for a client")
    conv.session_state = "BRIEF_REVIEW"
    conv.gate_open = "brief"
    conv.state = conv.state.model_copy(update={"pending_card": None})
    await conv.say("please use react-native-code-push for updates")
    assert not any(c.startswith("Q-policy") for c in shown_cards(conv))
    assert conv.pending is None


async def test_the_company_rule_check_costs_no_llm_call_and_no_notice() -> None:
    conv = await bank_conversation()
    result = await conv.say(LIBRARY_IDEA)
    assert result.llm_calls == 2
    assert not [e for e in result.events if isinstance(e, Notice)]


HINDI_LIBRARY_IDEA = "मुझे एक ऑनलाइन दुकान का ऐप चाहिए, और अपडेट react-native-code-push से भेजने हैं"
HINGLISH_LIBRARY_IDEA = (
    "mujhe ek online dukaan ka app chahiye, aur updates react-native-code-push se bhejne hain"
)


async def test_the_explanation_and_card_follow_the_session_language() -> None:
    for lang, idea in (("hi", HINDI_LIBRARY_IDEA), ("hi-Latn", HINGLISH_LIBRARY_IDEA)):
        conv = Conversation(policy="regulated-bank", language_hint=lang)
        await conv.start()
        await conv.say(idea)
        card = conv.pending
        assert card is not None and card.language == lang
        text = " ".join(messages(conv))
        assert "react-native-code-push" in text
        assert text != texts.say("policy_library_explain", "en", library="react-native-code-push")
        labels = [o.label for o in card.card.options]
        assert all(bool(DEVANAGARI.search(label)) == (lang == "hi") for label in labels)
        assert card.card.text == texts.say(
            "policy_library_question", lang, library="react-native-code-push"
        )


async def test_a_region_explanation_is_localised_with_localised_region_names() -> None:
    conv = Conversation(policy="regulated-bank", language_hint="hi")
    await conv.start()
    await conv.say("मुझे एक ऑनलाइन दुकान का ऐप चाहिए, और सारा डेटा us-east region में रखना है")
    text = " ".join(messages(conv))
    assert DEVANAGARI.search(text) and "पश्चिमी यूरोप" in text
    assert conv.pending is not None
    assert [o.label for o in conv.pending.card.options] == ["पश्चिमी यूरोप", "भारत (दक्षिण एशिया)"]


async def test_a_reopened_session_keeps_the_waiting_violations() -> None:
    """The queue lives in the persisted state, not in the engine."""
    conv = await bank_conversation()
    await conv.say(f"{LIBRARY_IDEA}, with the data kept in us-east")
    stored = conv.state.model_dump_json()
    from mde_contracts.engines import DiscoveryState

    conv.state = DiscoveryState.model_validate_json(stored)
    await conv.tap("store_release")
    assert shown_cards(conv) == ["Q-policy-region"]
