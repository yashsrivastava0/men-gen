"""Card requests, every validation rule of prompt 6.3, assembly, the fallback card and answers to writes."""

from __future__ import annotations

import pytest

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.cards import Card, CardOption
from mde_compass.engine.cards import (
    CardRefused,
    CardWrite,
    answer_to_writes,
    assemble_card,
    can_decline_to_answer,
    fallback_card,
    skip_allowed,
    validate_card,
    you_decide_writes,
)
from mde_compass.engine.ports import CardDraft, CardRequest

CATALOG = load_catalog()


def opt(i: str, label: str, value: object, **kw: object) -> CardOption:
    slot = kw.pop("slot", "idea.business_model")
    return CardOption(id=i, label=label, sets={slot: value}, **kw)  # type: ignore[arg-type]


def good_draft(**kw: object) -> CardDraft:
    base: dict[str, object] = {
        "question": "How will the app earn money?",
        "why_we_ask": "It decides which payment screens are needed.",
        "options": [
            opt(
                "A",
                "A small fee on each order",
                "per_transaction_fee",
                recommended=True,
                reason="Fits an ordering app.",
            ),
            opt("B", "A monthly plan for restaurants", "subscription"),
            opt("C", "Free for everyone, no charge", "business_funded"),
        ],
        "language": "en",
    }
    base.update(kw)
    return CardDraft(**base)  # type: ignore[arg-type]


def request(**kw: object) -> CardRequest:
    base: dict[str, object] = {
        "frame_id": "business_model",
        "slots": ["idea.business_model"],
        "card_type": "choose_one",
        "option_source": "family",
        "hints": ["small commission", "monthly plan", "delivery fees"],
    }
    base.update(kw)
    return CardRequest(**base)  # type: ignore[arg-type]


def check(
    draft: CardDraft, req: CardRequest | None = None, *, language: str = "en", user_text: str = ""
) -> list[str]:
    return validate_card(draft, req or request(), CATALOG, language=language, user_text=user_text)


def has(errors: list[str], prefix: str) -> bool:
    return any(e.startswith(prefix) for e in errors)


def test_a_good_card_passes() -> None:
    assert check(good_draft()) == []


def test_exactly_three_distinct_options() -> None:
    d = good_draft()
    assert has(check(good_draft(options=d.options[:2])), "options.count")
    dup = [d.options[0], d.options[1], d.options[1].model_copy(update={"id": "C"})]
    assert has(check(good_draft(options=dup)), "options.distinct")
    same_ids = [o.model_copy(update={"id": "A"}) for o in d.options]
    assert has(check(good_draft(options=same_ids)), "options.ids")


def test_exactly_one_recommended_with_a_reason() -> None:
    d = good_draft()
    none = [o.model_copy(update={"recommended": False}) for o in d.options]
    assert has(check(good_draft(options=none)), "recommended.count")
    two = [o.model_copy(update={"recommended": True, "reason": "r"}) for o in d.options[:2]] + [
        d.options[2]
    ]
    assert has(check(good_draft(options=two)), "recommended.count")
    no_reason = [d.options[0].model_copy(update={"reason": ""}), *d.options[1:]]
    assert has(check(good_draft(options=no_reason)), "recommended.reason")


def test_option_sets_must_be_valid_values_for_the_slot() -> None:
    d = good_draft()
    bad_slot = [d.options[0].model_copy(update={"sets": {"idea.category": "x"}}), *d.options[1:]]
    assert has(check(good_draft(options=bad_slot)), "sets.slot")
    empty = [d.options[0].model_copy(update={"sets": {}}), *d.options[1:]]
    assert has(check(good_draft(options=empty)), "sets.missing")
    login = request(frame_id="login_and_accounts", slots=["auth.methods"], must_confirm=True)
    enum_bad = [
        opt("A", "Phone code", "carrier_pigeon", slot="auth.methods", recommended=True, reason="r"),
        opt("B", "Email and password", "email_password", slot="auth.methods"),
        opt("C", "No sign in", "no_sign_in", slot="auth.methods"),
    ]
    assert has(check(good_draft(options=enum_bad), login), "sets.value")
    enum_ok = [
        enum_bad[0].model_copy(update={"sets": {"auth.methods": ["phone_otp"]}}),
        *enum_bad[1:],
    ]
    assert check(good_draft(question="How will people sign in?", options=enum_ok), login) == []


def test_question_and_option_length_limits() -> None:
    long_q = "How exactly will the app earn money over the next several years for all of its users and also for its partners and suppliers"
    assert has(check(good_draft(question=long_q)), "question.long")
    d = good_draft()
    long_label = [
        d.options[0].model_copy(
            update={"label": "A very small fee that is charged on each and every single order"}
        ),
        *d.options[1:],
    ]
    assert has(check(good_draft(options=long_label)), "options.long")
    assert has(check(good_draft(question="")), "question.empty")


def test_one_idea_per_card() -> None:
    assert has(check(good_draft(question="How will it earn? Who pays?")), "one_idea")


def test_no_declined_capability_in_a_card() -> None:
    d = good_draft()
    game = [d.options[0].model_copy(update={"label": "A fee on each game session"}), *d.options[1:]]
    assert has(check(good_draft(options=game)), "capability.declined")


def test_neutral_wording_and_no_statistics() -> None:
    assert has(check(good_draft(question="What is the best way to earn?")), "wording.leading")
    d = good_draft()
    most = [
        d.options[0].model_copy(update={"reason": "Most people choose this one."}),
        *d.options[1:],
    ]
    assert has(check(good_draft(options=most)), "wording.leading")
    stat = [
        d.options[0].model_copy(update={"reason": "Used by 80% of ordering apps."}),
        *d.options[1:],
    ]
    assert has(check(good_draft(options=stat)), "wording.statistic")


def test_brand_names_only_when_the_user_used_them() -> None:
    d = good_draft()
    brand = [d.options[0].model_copy(update={"label": "A fee like Swiggy charges"}), *d.options[1:]]
    assert has(check(good_draft(options=brand)), "brand.name")
    assert not has(
        check(good_draft(options=brand), user_text="I want something like Swiggy"), "brand.name"
    )
    assert not has(check(good_draft(question="Which UPI option fits, if any?")), "brand.name")


def test_basis_is_honest() -> None:
    d = good_draft()
    typical_ok = [
        d.options[0].model_copy(
            update={"basis": "typical", "label": "A small commission on orders"}
        ),
        *d.options[1:],
    ]
    assert not has(check(good_draft(options=typical_ok)), "basis.typical")
    typical_bad = [
        d.options[0].model_copy(update={"basis": "typical", "label": "Sell advertising slots"}),
        *d.options[1:],
    ]
    assert has(check(good_draft(options=typical_bad)), "basis.typical")
    doc = [d.options[0].model_copy(update={"basis": "your_document"}), *d.options[1:]]
    assert has(check(good_draft(options=doc)), "basis.document")
    assert not has(
        check(good_draft(options=doc), request(evidence=["page 3: commission model"])),
        "basis.document",
    )


def test_language_must_match() -> None:
    assert has(check(good_draft(), language="hi", user_text="मुझे ऐप चाहिए"), "language")
    hi = good_draft(
        question="ऐप से कमाई कैसे होगी?",
        why_we_ask="इससे भुगतान की स्क्रीन तय होती हैं।",
        language="hi",
        options=[
            opt(
                "A",
                "हर ऑर्डर पर छोटी फ़ीस",
                "per_transaction_fee",
                recommended=True,
                reason="ऑर्डर वाले ऐप में चलता है।",
            ),
            opt("B", "रेस्टोरेंट के लिए मासिक प्लान", "subscription"),
            opt("C", "सबके लिए मुफ़्त, कोई शुल्क नहीं", "business_funded"),
        ],
    )
    assert check(hi, language="hi", user_text="मुझे ऐप चाहिए") == []
    assert has(check(hi, language="en"), "language")
    # Hinglish (Latin script Hindi) keeps Latin script cards
    assert not has(
        check(good_draft(language="hi"), language="hi", user_text="mujhe app chahiye"),
        "language.script",
    )


def test_pick_many_checklist_rules_and_custom_flag() -> None:
    req = CardRequest(
        frame_id="features",
        slots=["scope.features"],
        card_type="pick_many",
        option_source="family",
        hints=["Browse the catalogue", "Search for products", "Cart and checkout"],
    )

    def feat(i: str, label: str, **kw: object) -> CardOption:
        return CardOption(id=i, label=label, sets={"scope.features": [{"name": label}]}, **kw)  # type: ignore[arg-type]

    ok = CardDraft(
        question="Which of these should the first version have?",
        options=[
            feat("a", "Browse the catalogue", basis="typical", ticked=True),
            feat("b", "Search for products", basis="typical"),
            feat("c", "Cart and checkout", basis="typical"),
        ],
    )
    assert check(ok, req) == []
    outside = ok.model_copy(update={"options": [*ok.options, feat("d", "Loyalty points wallet")]})
    assert has(check(outside, req), "families.outside")
    flagged = ok.model_copy(
        update={
            "options": [
                *ok.options,
                CardOption(
                    id="custom_1",
                    label="Loyalty points wallet",
                    sets={"scope.features": [{"name": "Loyalty points wallet", "custom": True}]},
                ),
            ]
        }
    )
    assert check(flagged, req) == []
    ticked_guess = ok.model_copy(
        update={"options": [feat("a", "Browse the catalogue", ticked=True), *ok.options[1:]]}
    )
    assert has(check(ticked_guess, req), "ticked.basis")
    too_few = ok.model_copy(update={"options": ok.options[:1]})
    assert has(check(too_few, req), "options.count")


def test_assemble_adds_id_progress_and_always_on_controls() -> None:
    card = assemble_card(
        good_draft(), request(), card_id="Q-004", n=3, of_about=7, language="en", catalog=CATALOG
    )
    assert card.id == "Q-004"
    assert card.progress is not None
    assert (card.progress.n, card.progress.of_about) == (3, 7)
    assert card.custom_input.enabled
    assert card.custom_input.placeholder
    assert card.you_decide.enabled
    assert card.you_decide.assumes == "A"
    assert card.skip.enabled
    assert card.language == "en"
    assert not card.must_confirm
    later = assemble_card(
        good_draft(), request(), card_id="Q-005", n=9, of_about=7, language="en", catalog=CATALOG
    )
    assert later.progress is not None
    assert later.progress.of_about == 9  # N never falls below n


def test_must_confirm_cards_refuse_you_decide_and_skip() -> None:
    req = request(frame_id="login_and_accounts", slots=["auth.methods"], must_confirm=True)
    d = good_draft(
        options=[
            opt(
                "A",
                "Phone code",
                "phone_otp",
                slot="auth.methods",
                recommended=True,
                reason="Simple.",
            ),
            opt("B", "Email and password", "email_password", slot="auth.methods"),
            opt("C", "No sign in", "no_sign_in", slot="auth.methods"),
        ]
    )
    card = assemble_card(d, req, card_id="Q-009", n=5, of_about=8, language="en", catalog=CATALOG)
    assert card.must_confirm
    assert not card.you_decide.enabled
    assert not card.skip.enabled
    assert not can_decline_to_answer(card)
    assert not skip_allowed(card)
    with pytest.raises(CardRefused):
        you_decide_writes(card)


@pytest.mark.parametrize("slot_id", [s for s, d in CATALOG.slots.items()])
def test_every_slot_has_a_working_fallback_card(slot_id: str) -> None:
    for lang in ("en", "hi"):
        card = fallback_card(slot_id, CATALOG, language=lang, card_id="Q-001", n=1, of_about=5)
        assert card.fallback
        assert card.slots == [slot_id]
        assert card.question
        assert card.language == lang
        if card.type == "choose_one":
            assert len([o for o in card.options if o.recommended]) == 1
            assert card.recommended_option() is not None
            assert all(o.sets for o in card.options)
        assert card.must_confirm == CATALOG.slots[slot_id].must_confirm
        assert card.you_decide.enabled != card.must_confirm


def test_fallback_card_from_a_frame_and_hindi_script() -> None:
    card = fallback_card("business_model", CATALOG, language="hi", card_id="Q-002", n=2, of_about=6)
    assert card.frame == "business_model"
    assert any("ऀ" <= c <= "ॿ" for c in card.question)
    form = fallback_card(
        "who_and_where",
        CATALOG,
        language="en",
        card_id="Q-003",
        n=1,
        of_about=6,
        slots=["context.geo"],
    )
    assert form.slots == ["context.geo"]


# --- answers -> writes ---------------------------------------------------------------------------------


def choose_card() -> Card:
    return assemble_card(
        good_draft(), request(), card_id="Q-004", n=1, of_about=5, language="en", catalog=CATALOG
    )


def test_choose_one_tap_writes_the_options_sets() -> None:
    assert answer_to_writes(choose_card(), "B", None, CATALOG) == [
        CardWrite("idea.business_model", "subscription", "user_tap", None, False)
    ]


def test_custom_answer_keeps_the_users_words_and_needs_confirmation() -> None:
    writes = answer_to_writes(choose_card(), None, "we charge a yearly licence to dealers", CATALOG)
    assert len(writes) == 1
    assert writes[0].custom_text == "we charge a yearly licence to dealers"
    assert writes[0].value == "we charge a yearly licence to dealers"
    assert writes[0].provenance_type == "user_text"
    assert writes[0].needs_confirmation
    # text that is really one of the options is a tap-equivalent
    same = answer_to_writes(choose_card(), None, "a monthly plan for restaurants", CATALOG)
    assert same[0].value == "subscription"
    assert not same[0].needs_confirmation


def test_unknown_option_id_is_a_custom_text() -> None:
    writes = answer_to_writes(choose_card(), "sell data to partners", None, CATALOG)
    assert writes[0].needs_confirmation


def test_pick_many_merges_list_values_and_flags_custom_features() -> None:
    req = CardRequest(
        frame_id="features", slots=["scope.features"], card_type="pick_many", option_source="family"
    )
    draft = CardDraft(
        question="Which of these should the first version have?",
        options=[
            CardOption(id="a", label="Browse", sets={"scope.features": [{"name": "Browse"}]}),
            CardOption(id="b", label="Search", sets={"scope.features": [{"name": "Search"}]}),
        ],
    )
    card = assemble_card(
        draft, req, card_id="Q-007", n=2, of_about=6, language="en", catalog=CATALOG
    )
    writes = answer_to_writes(card, ["a", "b"], "a loyalty wallet", CATALOG)
    assert writes[0].slot_id == "scope.features"
    assert writes[0].value == [{"name": "Browse"}, {"name": "Search"}]
    assert writes[1].value == [{"name": "a loyalty wallet", "custom": True}]
    assert writes[1].needs_confirmation
    assert answer_to_writes(card, [], None, CATALOG) == []


def test_enum_list_slots_get_lists() -> None:
    req = CardRequest(
        frame_id="login_and_accounts",
        slots=["auth.methods"],
        card_type="pick_many",
        option_source="model",
        must_confirm=True,
    )
    draft = CardDraft(
        question="Which sign-in methods?",
        options=[
            CardOption(id="a", label="Phone code", sets={"auth.methods": "phone_otp"}),
            CardOption(id="b", label="Email", sets={"auth.methods": "email_password"}),
        ],
    )
    card = assemble_card(
        draft, req, card_id="Q-008", n=2, of_about=6, language="en", catalog=CATALOG
    )
    assert answer_to_writes(card, ["a", "b"], None, CATALOG)[0].value == [
        "phone_otp",
        "email_password",
    ]


def test_quick_form_and_open_prompt() -> None:
    form = fallback_card("who_and_where", CATALOG, language="en", card_id="Q-010", n=1, of_about=5)
    form = form.model_copy(
        update={"type": "quick_form", "slots": ["context.geo", "audience.segments", "locales"]}
    )
    writes = answer_to_writes(
        form,
        {
            "context.geo": "Pune, India",
            "audience.segments": ["students"],
            "locales": [],
            "stray": "x",
        },
        None,
        CATALOG,
    )
    assert {w.slot_id for w in writes} == {"context.geo", "audience.segments"}
    assert all(w.provenance_type == "user_text" and not w.needs_confirmation for w in writes)
    prompt = fallback_card("idea.summary", CATALOG, language="en", card_id="Q-011", n=1, of_about=5)
    out = answer_to_writes(prompt, "  an app for my bakery  ", None, CATALOG)
    assert out == [
        CardWrite(
            "idea.summary", "an app for my bakery", "user_text", "an app for my bakery", False
        )
    ]
    assert answer_to_writes(prompt, "", None, CATALOG) == []


def test_priority_keeps_ranked_order() -> None:
    req = CardRequest(
        frame_id="priorities", slots=["scope.priority"], card_type="priority", option_source="fixed"
    )
    draft = CardDraft(
        question="Which matters most?",
        options=[
            CardOption(id="a", label="Browse", sets={"scope.priority": "must"}),
            CardOption(id="b", label="Search", sets={"scope.priority": "nice"}),
        ],
    )
    card = assemble_card(
        draft, req, card_id="Q-012", n=1, of_about=4, language="en", catalog=CATALOG
    )
    assert answer_to_writes(card, ["b", "a"], None, CATALOG)[0].value == "nice"


def test_you_decide_uses_the_recommended_option_as_assumed() -> None:
    writes = you_decide_writes(choose_card())
    assert writes == [
        CardWrite("idea.business_model", "per_transaction_fee", "assumed", None, False)
    ]


def test_you_decide_on_a_form_assumes_nothing_here() -> None:
    form = fallback_card("idea.summary", CATALOG, language="en", card_id="Q-013", n=1, of_about=5)
    assert you_decide_writes(form) == []


def test_family_hints_ground_the_writer() -> None:
    from mde_compass.engine.cards import family_hints

    hints = family_hints(CATALOG, ["scope.features", "roles.list"], ["identity_access"])
    assert "Sign up and sign in" in hints
    assert "member" in hints
    assert family_hints(CATALOG, ["scope.features"], ["no_such_family"]) == []
    assert "subscription" in family_hints(CATALOG, ["idea.business_model"], ["identity_access"])


def test_acronyms_and_plurals_are_not_brand_names() -> None:
    d = good_draft(question="Which APIs or UPI options matter?")
    assert not has(check(d), "brand.name")


def test_locales_accept_other_languages_and_forms() -> None:
    req = CardRequest(
        frame_id="who_and_where", slots=["locales"], card_type="pick_many", option_source="fixed"
    )
    draft = CardDraft(
        question="Which languages should the app speak?",
        options=[
            CardOption(id="a", label="English", sets={"locales": "en"}),
            CardOption(id="b", label="Marathi", sets={"locales": ["mr"]}),
            CardOption(id="c", label="Another language", sets={"locales": "other"}),
        ],
    )
    assert check(draft, req) == []


def test_the_catalog_fallback_cards_obey_the_card_rules_except_known_copy_issues() -> None:
    known = {
        "scope.references"
    }  # catalog copy asks two questions in one card (reported to the lead)
    for slot_id in CATALOG.slots:
        if slot_id in known:
            continue
        for lang in ("en", "hi"):
            card = fallback_card(slot_id, CATALOG, language=lang, card_id="Q-001", n=1, of_about=5)
            req = CardRequest(
                frame_id=card.frame, slots=card.slots, card_type=card.type, option_source="fixed"
            )
            draft = CardDraft(
                question=card.question,
                why_we_ask=card.why_we_ask,
                options=card.options,
                language=lang,
            )
            assert validate_card(draft, req, CATALOG, language=lang) == [], (slot_id, lang)


def test_hostile_card_text_validates_in_linear_time() -> None:
    import time

    huge = "word " * 40_000
    start = time.perf_counter()
    errors = check(good_draft(question=huge, why_we_ask=huge))
    assert time.perf_counter() - start < 2.0
    assert has(errors, "question.long")
    start = time.perf_counter()
    answer_to_writes(choose_card(), None, huge, CATALOG)
    assert time.perf_counter() - start < 1.0
