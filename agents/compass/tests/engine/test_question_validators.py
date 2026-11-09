from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.catalog.loader import Catalog, load_catalog
from mde_compass.config import CompassConfig, InterviewPolicy, QuestionPolicy
from mde_compass.domain.cards import Card, CardOption, CardType
from mde_compass.domain.interview import AssumedInstead, NeedStatus
from mde_compass.engine.needs import compute
from mde_compass.engine.question_validators import (
    Issue,
    LedgerEntry,
    ValidationContext,
    coverage_issues,
    cross_idea_overlap,
    cross_idea_overlap_pairs,
    is_universal,
    project_term_share,
    validate_batch,
    validate_question,
)
from mde_compass.engine.slots import SlotStore
from mde_compass.engine.terms import project_terms
from mde_core.clock import FakeClock

REAL = load_catalog()
# The tests set the universal flags themselves (only ``locales`` is universal) so they do not depend on the data.
_slots = {k: v.model_copy(update={"universal": k == "locales"}) for k, v in REAL.slots.items()}
CAT: Catalog = REAL.model_copy(update={"slots": _slots})
IDEA = "I want an app for my tiffin delivery service in Pune"
TERMS = project_terms(IDEA)


def opts(*labels: str, recommended: int | None = None) -> list[CardOption]:
    return [
        CardOption(
            id=f"o{i}",
            label=label,
            recommended=(i == recommended),
            reason="a common starting point" if i == recommended else "",
        )
        for i, label in enumerate(labels)
    ]


def card(
    cid: str = "Q-001",
    *,
    question: str = "Should customers pick a tiffin each day or subscribe weekly?",
    type: CardType = "choose_one",
    options: list[CardOption] | None = None,
    needs: list[str] | None = None,
    intent: str = "learn how customers order tiffins",
    **kw: object,
) -> Card:
    if options is None:
        options = opts("Pick each day", "Weekly plan", "Both ways") if type == "choose_one" else []
    fields: dict[str, object] = {
        "id": cid,
        "frame": "planner",
        "slots": [],
        "type": type,
        "question": question,
        "why_we_ask": "This decides how ordering works.",
        "options": options,
        "need_ids": needs if needs is not None else ["scope.features"],
        "intent": intent,
        "language": "en",
        "origin": "planner",
        "examples": ["two meals a day, paid monthly"]
        if type in ("open_prompt", "quick_form")
        else [],
    }
    fields.update(kw)
    return Card.model_validate(fields)


def ctx(**kw: object) -> ValidationContext:
    base: dict[str, object] = {"terms": TERMS, "catalog": CAT, "user_text": IDEA}
    base.update(kw)
    return ValidationContext(**base)  # type: ignore[arg-type]


def codes(c: Card, context: ValidationContext | None = None) -> list[str]:
    return [i.code for i in validate_question(c, context or ctx())]


# ------------------------------------------------------------------------------------------- the baseline
def test_a_good_question_has_no_issues() -> None:
    assert validate_question(card(), ctx()) == []


# ----------------------------------------------------------------------------------------- project term
def test_non_universal_question_needs_a_project_term() -> None:
    generic = card(
        question="Who will use your app?", type="open_prompt", needs=["audience.segments"]
    )
    assert "no_project_term" in codes(generic)
    specific = card(
        question="Who orders your tiffin: offices, families or students?",
        type="open_prompt",
        needs=["audience.segments"],
    )
    assert "no_project_term" not in codes(specific)


def test_universal_needs_and_confirmations_are_exempt() -> None:
    languages = card(
        question="Which languages should the app speak?", type="open_prompt", needs=["locales"]
    )
    assert is_universal(languages, CAT) and codes(languages) == []
    confirm = card(
        question="Is it right that you serve one city?",
        type="confirm",
        options=opts("Yes", "No"),
        needs=["context.geo"],
    )
    assert is_universal(confirm, CAT) and "no_project_term" not in codes(confirm)
    mixed = card(
        question="Which languages and areas?", type="open_prompt", needs=["locales", "context.geo"]
    )
    assert not is_universal(mixed, CAT) and "no_project_term" in codes(mixed)
    assert not is_universal(card(needs=["domain:pricing"]), CAT)
    assert not is_universal(card(needs=[]), CAT)


def test_the_real_registry_flags_are_honoured() -> None:
    universal = [sid for sid, slot in REAL.slots.items() if slot.universal]
    assert "locales" in universal
    asked = card(
        question="Which languages should the app speak?", type="open_prompt", needs=["locales"]
    )
    assert is_universal(asked, REAL)
    assert not is_universal(card(needs=["scope.features"]), REAL)


def test_project_term_in_options_only_does_not_count() -> None:
    c = card(
        question="How should people order?",
        options=opts("Pick each tiffin daily", "Weekly plan", "Both ways"),
    )
    assert "no_project_term" in codes(c)


# ------------------------------------------------------------------------------------------ duplicates
def test_same_intent_after_normalization_is_a_duplicate() -> None:
    earlier = LedgerEntry(
        "Q-000", intent="Learn how customers order tiffins!", need_ids=("audience.segments",)
    )
    issues = validate_question(card(), ctx(ledger=(earlier,)))
    dup = next(i for i in issues if i.code == "duplicate_intent")
    assert dup.ref == "Q-000"
    other = LedgerEntry(
        "Q-000", intent="learn the delivery radius", need_ids=("context.geo",), text="Where?"
    )
    assert validate_question(card(), ctx(ledger=(other,))) == []


def test_same_need_and_similar_text_is_a_duplicate() -> None:
    earlier = LedgerEntry(
        "Q-000",
        intent="something else",
        need_ids=("scope.features",),
        text="Should customers pick a tiffin each day or subscribe to weekly plans",
    )
    assert "duplicate_text" in codes(card(), ctx(ledger=(earlier,)))
    different_need = replace(earlier, need_ids=("context.geo",))
    assert "duplicate_text" not in codes(card(), ctx(ledger=(different_need,)))
    far = replace(earlier, text="Which neighbourhoods does your kitchen deliver to in the city")
    assert codes(card(), ctx(ledger=(far,))) == []


def test_identical_text_is_a_duplicate_whatever_the_need() -> None:
    earlier = LedgerEntry(
        "Q-000", intent="x", need_ids=("context.geo",), text=card().question.upper()
    )
    assert "duplicate_text" in codes(card(), ctx(ledger=(earlier,)))


def test_similarity_threshold_comes_from_the_policy() -> None:
    earlier = LedgerEntry(
        "Q-000",
        intent="x",
        need_ids=("scope.features",),
        text="Should customers pick a tiffin each day or subscribe weekly plans",
    )
    strict = ctx(ledger=(earlier,), policy=InterviewPolicy(duplicate_similarity_min=0.999))
    assert "duplicate_text" not in codes(card(), strict)
    assert "duplicate_text" in codes(card(), ctx(ledger=(earlier,)))


def test_a_follow_up_is_not_a_duplicate_of_its_parent() -> None:
    parent = LedgerEntry(
        "Q-000",
        intent="learn how customers order tiffins",
        need_ids=("scope.features",),
        text=card().question,
    )
    follow = card("Q-002", parent_card_id="Q-000", origin="followup", depth=1)
    assert validate_question(follow, ctx(ledger=(parent,))) == []
    assert "duplicate_intent" in codes(card("Q-002"), ctx(ledger=(parent,)))


def test_reusing_an_id_is_refused() -> None:
    earlier = LedgerEntry("Q-001", intent="other", need_ids=("context.geo",), text="Other?")
    assert "duplicate_id" in codes(card("Q-001"), ctx(ledger=(earlier,)))


def test_planned_questions_of_the_same_batch_count() -> None:
    planned = (LedgerEntry.from_card(card("Q-001")),)
    assert "duplicate_intent" in codes(card("Q-002"), ctx(planned=planned))


# ---------------------------------------------------------------------------------------- need mapping
def test_every_question_maps_to_a_need() -> None:
    assert "unmapped_need" in codes(card(needs=[]))
    assert "unmapped_need" in codes(card(needs=["made.up"]))
    assert "unmapped_need" in codes(card(needs=["domain:"]))
    assert "unmapped_need" in codes(card(needs=["scope.features", "domain:  "]))
    assert "unmapped_need" not in codes(card(needs=["domain:pricing"]))
    assert "unmapped_need" not in codes(card(needs=["scope.features", "domain:menu"]))


def test_empty_question_and_missing_intent() -> None:
    assert "empty_question" in codes(card(question="   "))
    assert "missing_intent" in codes(card(intent=" "))


# ------------------------------------------------------------------------------------------------ length
def test_question_and_option_word_limits() -> None:
    twenty = "Should customers pick a tiffin " + " ".join(["really"] * 13) + " or subscribe?"
    assert len(twenty.split()) == 20 and "question_too_long" not in codes(card(question=twenty))
    twenty_one = twenty.replace("Should", "Should now")
    assert "question_too_long" in codes(card(question=twenty_one))
    eight = " ".join(["word"] * 8)
    assert "option_too_long" not in codes(card(options=opts(eight, "Weekly plan", "Both ways")))
    nine = " ".join(["word"] * 9)
    assert "option_too_long" in codes(card(options=opts(nine, "Weekly plan", "Both ways")))


def test_hindi_gets_extra_words_like_card_validation() -> None:
    q = "पुणे में टिफिन " + " ".join(["शब्द"] * 19) + " क्या?"  # 23 words
    hindi = ctx(language="hi", user_text="पुणे में टिफिन सर्विस")
    c = card(
        question=q,
        language="hi",
        why_we_ask="इससे ऑर्डर का तरीका तय होता है।",
        options=opts("रोज़ चुनें", "साप्ताहिक प्लान", "दोनों तरीके"),
    )
    assert "question_too_long" not in codes(c, replace(hindi, terms=frozenset({"टिफिन"})))
    assert "question_too_long" in codes(card(question=q), ctx())


# ---------------------------------------------------------------------------------------------- options
def test_choose_one_needs_exactly_three_distinct_options() -> None:
    assert "choose_one_options" in codes(card(options=opts("A one", "B two")))
    assert "choose_one_options" in codes(card(options=opts("A", "B", "C", "D")))
    assert "options_not_distinct" in codes(card(options=opts("Same", "same", "Other")))
    assert codes(card(options=opts("Pick each day", "Weekly plan", "Both ways"))) == []


def test_open_prompt_and_quick_form_need_examples() -> None:
    q = "How do customers order a tiffin today?"
    assert codes(card(question=q, type="open_prompt")) == []
    assert "missing_examples" in codes(card(question=q, type="open_prompt", examples=[]))
    assert "missing_examples" in codes(card(question=q, type="quick_form", examples=["  "]))
    assert codes(card(question=q, type="quick_form")) == []


def test_other_card_types_option_counts() -> None:
    many = card(type="pick_many", options=opts("Daily", "Weekly", "Monthly"))
    assert codes(many) == []
    assert "options_count" in codes(card(type="pick_many", options=opts("Only one")))
    assert "options_count" in codes(card(type="priority", options=opts("Only one")))
    assert "options_count" in codes(card(type="look_signals", options=opts("A1", "B2")))
    assert "options_count" in codes(
        card(type="confirm", options=[], question="Is tiffin delivery right?")
    )


# ------------------------------------------------------------------------------------------ option basis
def test_typical_basis_needs_backing_data() -> None:
    typical = [
        CardOption(id="a", label="Pick each day", basis="typical"),
        CardOption(id="b", label="Weekly plan"),
        CardOption(id="c", label="Both ways"),
    ]
    other_need = [
        "idea.business_model"
    ]  # not a family-grounded slot (features, roles, integrations)
    assert "option_basis" in codes(card(options=typical, needs=other_need))
    backed = ctx(hints=("Pick each day",))
    assert "option_basis" not in codes(card(options=typical, needs=other_need), backed)
    # On a family-grounded need every option must be typical or flagged custom once hints exist.
    assert "option_basis" in codes(card(options=typical), backed)


def test_document_and_own_words_basis() -> None:
    doc = [
        CardOption(id="a", label="Pick each day", basis="your_document"),
        CardOption(id="b", label="Weekly plan"),
        CardOption(id="c", label="Both ways"),
    ]
    assert "option_basis" in codes(card(options=doc))
    assert "option_basis" not in codes(card(options=doc), ctx(evidence=("Menu changes daily",)))
    words = [replace_basis(o, "your_words") if i == 0 else o for i, o in enumerate(doc)]
    assert "option_basis" not in codes(card(options=words))  # the user typed the idea
    assert "option_basis" in codes(card(options=words), ctx(user_text="", terms=TERMS))


def replace_basis(option: CardOption, basis: str) -> CardOption:
    return option.model_copy(update={"basis": basis})


def test_option_sets_must_name_a_slot_of_the_question() -> None:
    bad = [
        CardOption(id="a", label="Pick each day", sets={"auth.methods": ["otp"]}),
        CardOption(id="b", label="Weekly plan"),
        CardOption(id="c", label="Both ways"),
    ]
    assert "option_sets" in codes(card(options=bad))
    good = [
        CardOption(id="a", label="Pick each day", sets={"scope.features": [{"name": "daily"}]}),
        CardOption(id="b", label="Weekly plan"),
        CardOption(id="c", label="Both ways"),
    ]
    assert "option_sets" not in codes(card(options=good))


def test_pre_ticked_suggestion_is_not_evidence() -> None:
    ticked = [
        CardOption(id="a", label="Daily", ticked=True),
        CardOption(id="b", label="Weekly"),
        CardOption(id="c", label="Monthly"),
    ]
    assert "option_basis" in codes(card(type="pick_many", options=ticked))


# --------------------------------------------------------------------------------------------- language
def test_language_declared_and_script() -> None:
    assert "wrong_language" in codes(card(language="hi"))  # declared hi, context en
    hindi_ctx = ctx(language="hi", user_text="")
    assert "wrong_language" in codes(
        card(language="hi"), hindi_ctx
    )  # Latin card, no Latin-speaking user
    hinglish_ctx = ctx(language="hi", user_text="mujhe Pune mein tiffin service chahiye")
    assert "wrong_language" not in codes(card(language="hi"), hinglish_ctx)  # Hinglish allowed
    devanagari = card(
        question="आप टिफिन रोज़ चुनेंगे या साप्ताहिक प्लान लेंगे?",
        language="hi",
        why_we_ask="इससे ऑर्डर का तरीका तय होता है।",
        options=opts("रोज़ चुनें", "साप्ताहिक प्लान", "दोनों तरीके"),
    )
    assert "wrong_language" not in codes(devanagari, replace(hindi_ctx, terms=frozenset({"टिफिन"})))
    assert "wrong_language" in codes(devanagari.model_copy(update={"language": "en"}), ctx())


# ------------------------------------------------------------------------------------------- technology
@pytest.mark.parametrize(
    "text",
    [
        "Which database should we use for your tiffin orders?",
        "Should the tiffin app be built in Flutter?",
        "Which tech stack do you want for the tiffin app?",
        "Do you want AWS or Azure hosting for your tiffin service?",
        "Which hex code should the tiffin brand colour use?",
        "Is the colour #FF8800 right for your tiffin brand?",
    ],
)
def test_technology_questions_are_refused(text: str) -> None:
    assert "technology_question" in codes(card(question=text, type="open_prompt"))


def test_business_questions_are_not_technology() -> None:
    ok = "Where do you keep your tiffin order records today?"
    assert "technology_question" not in codes(card(question=ok, type="open_prompt"))


def test_a_word_the_user_used_is_allowed() -> None:
    q = "How many server shifts does your restaurant run?"
    restaurant = ctx(
        terms=project_terms("an app for my restaurant server shifts"), user_text="restaurant"
    )
    assert "technology_question" not in codes(card(question=q, type="open_prompt"), restaurant)
    q2 = "Which database do you mean for your restaurant?"
    assert "technology_question" in codes(card(question=q2, type="open_prompt"), restaurant)


def test_technology_in_an_option_is_refused() -> None:
    c = card(options=opts("Pick each day", "Use Firebase", "Both ways"))
    assert "technology_question" in codes(c)


def test_catalog_no_ask_topics_decide() -> None:
    no_tech = CAT.model_copy(update={"no_ask": ["legal_wording"]})
    q = "Which database do you keep tiffin orders in?"
    assert "technology_question" not in codes(
        card(question=q, type="open_prompt"), ctx(catalog=no_tech)
    )
    legal = "Who writes the terms and conditions for your tiffin service?"
    assert "technology_question" in codes(
        card(question=legal, type="open_prompt"), ctx(catalog=no_tech)
    )
    unknown_topic = no_tech.model_copy(update={"no_ask": ["payment_settlement"]})
    assert "technology_question" in codes(
        card(question="How is the payment settlement done for tiffin?", type="open_prompt"),
        ctx(catalog=unknown_topic),
    )


# ---------------------------------------------------------------------------------------- declined option
def test_declined_categories_are_never_offered() -> None:
    c = card(options=opts("Pick each day", "A tiffin game with points", "Both ways"))
    assert "declined_option" in codes(c)
    vr = card(options=opts("Pick each day", "Virtual reality kitchen tour", "Both ways"))
    assert "declined_option" in codes(vr)
    assert "declined_option" not in codes(card())


def test_declined_category_in_option_sets_is_found() -> None:
    c = card(
        options=[
            CardOption(id="a", label="Pick each day"),
            CardOption(
                id="b", label="Weekly plan", sets={"scope.features": [{"name": "multiplayer game"}]}
            ),
            CardOption(id="c", label="Both ways"),
        ]
    )
    assert "declined_option" in codes(c)


# ------------------------------------------------------------------------------------------- wording
@pytest.mark.parametrize(
    "text",
    [
        "What is the best way for customers to order a tiffin?",
        "Don't you think customers should pick a tiffin weekly?",
        "Obviously customers pick a tiffin daily, right?",
        "You should let customers pick a tiffin daily, shouldn't they?",
    ],
)
def test_leading_wording_is_refused(text: str) -> None:
    assert "leading_wording" in codes(card(question=text, type="open_prompt"))


def test_invented_numbers_brand_names_and_two_questions() -> None:
    assert "invented_number" in codes(card(question="Do 80% of tiffin customers pay weekly?"))
    assert "brand_name" in codes(card(question="Do you want tiffin ordering like Swiggy offers?"))
    swiggy = ctx(user_text=IDEA + " like Swiggy")
    assert "brand_name" not in codes(
        card(question="Do you want tiffin ordering like Swiggy offers?"), swiggy
    )
    assert "multiple_questions" in codes(
        card(question="Do customers order a tiffin daily? Or weekly?")
    )


def test_place_names_from_the_facts_are_not_brands() -> None:
    terms = project_terms(IDEA, [], extra=["Kothrud"])
    q = "Do tiffin orders in Kothrud come daily or weekly?"
    assert "brand_name" not in codes(card(question=q), ctx(terms=terms, user_text=IDEA))
    assert "brand_name" in codes(card(question=q), ctx(terms=TERMS, user_text=IDEA))


def test_choice_questions_must_be_questions() -> None:
    assert "not_a_question" in codes(card(question="Customers pick a tiffin each day or weekly"))
    assert "not_a_question" not in codes(
        card(question="Describe a typical day for one tiffin customer", type="open_prompt")
    )


# ---------------------------------------------------------------------------------------- recommended
def test_at_most_one_recommended_option_with_a_reason_on_choice_cards_only() -> None:
    two = card(
        options=[
            o.model_copy(update={"recommended": i < 2, "reason": "r"})
            for i, o in enumerate(opts("A one", "B two", "C three"))
        ]
    )
    assert "too_many_recommended" in codes(two)
    no_reason = card(
        options=[
            o.model_copy(update={"recommended": i == 0})
            for i, o in enumerate(opts("Pick each day", "Weekly plan", "Both ways"))
        ]
    )
    assert "recommended_without_reason" in codes(no_reason)
    one = card(options=opts("Pick each day", "Weekly plan", "Both ways", recommended=2))
    assert codes(one) == []
    many = card(type="pick_many", options=opts("Daily", "Weekly", "Monthly", recommended=0))
    assert "recommended_not_allowed" in codes(many)
    confirm = card(
        type="confirm",
        question="Is tiffin delivery in Pune right?",
        options=opts("Yes", "No", recommended=0),
    )
    assert "recommended_not_allowed" not in codes(confirm)


# ------------------------------------------------------------------------------------------------ batch
def test_batch_keeps_order_and_rejects_with_reasons() -> None:
    cards = [
        card("Q-001"),
        card("Q-002", intent="learn how customers order tiffins"),  # same intent as Q-001
        card(
            "Q-003",
            question="Where do your tiffin riders start each morning?",
            type="open_prompt",
            intent="learn where riders start",
            needs=["domain:riders"],
        ),
        card(
            "Q-004",
            question="Who will use your app?",
            type="open_prompt",
            intent="who",
            needs=["audience.segments"],
        ),
    ]
    result = validate_batch(cards, ctx())
    assert [c.id for c in result.accepted] == ["Q-001", "Q-003"]
    assert [r.card_id for r in result.rejected] == ["Q-002", "Q-004"]
    assert all(r.source == "code" for r in result.rejected)
    assert result.rejected[0].reasons[0].startswith("duplicate_intent: ")
    assert "no_project_term" in result.rejected[1].reasons[0] or any(
        "no_project_term" in r for r in result.rejected[1].reasons
    )
    assert {i.code for i in result.issues["Q-002"]} == {"duplicate_intent"}
    assert result.accepted_share == pytest.approx(0.5)


def test_batch_catches_a_duplicate_against_an_earlier_question_in_the_same_batch() -> None:
    first = card("Q-001")
    twin = card(
        "Q-002",
        intent="a different intent",
        question="Should customers pick a tiffin each day or subscribe weekly",
    )
    result = validate_batch([first, twin], ctx())
    assert [c.id for c in result.accepted] == ["Q-001"]
    assert "duplicate_text" in {i.code for i in result.issues["Q-002"]}


def _distinct(n: int, group: str | None = None) -> list[Card]:
    topics = ["rider", "kitchen", "menu", "payment", "route", "refund", "festival", "weekend"]
    return [
        card(
            f"Q-{i + 1:03d}",
            question=f"How does the tiffin {topics[i]} work for you?",
            type="open_prompt",
            intent=f"learn the {topics[i]}",
            needs=[f"domain:{topics[i]}"],
            group_id=group,
        )
        for i in range(n)
    ]


def test_group_and_batch_size_limits() -> None:
    five_in_one_group = validate_batch(_distinct(5, "G-1"), ctx())
    assert [c.id for c in five_in_one_group.accepted] == ["Q-001", "Q-002", "Q-003", "Q-004"]
    assert {i.code for i in five_in_one_group.issues["Q-005"]} == {"over_group_size"}
    two_groups = _distinct(8)
    for i, c in enumerate(two_groups):
        two_groups[i] = c.model_copy(update={"group_id": f"G-{i // 4}"})
    assert len(validate_batch(two_groups, ctx()).accepted) == 8
    capped = validate_batch(two_groups, ctx(batch_limit=5))
    assert len(capped.accepted) == 5
    assert {i.code for i in capped.issues["Q-006"]} == {"over_batch_size"}
    assert validate_batch(two_groups, ctx(batch_limit=0)).accepted == []


def test_default_batch_limit_is_the_largest_policy_batch() -> None:
    extra = card(
        "Q-009",
        question="How does the tiffin holiday work for you?",
        type="open_prompt",
        intent="holiday",
        needs=["domain:holiday"],
    )
    nine = [*_distinct(8), extra]
    for i, c in enumerate(nine):
        nine[i] = c.model_copy(update={"group_id": f"G-{i // 3}"})
    assert len(validate_batch(nine, ctx()).accepted) == 8


def test_context_build_helper() -> None:
    earlier = card("Q-000")
    built = ValidationContext.build(
        CAT, TERMS, ledger_cards=[earlier], config=CompassConfig(), user_text=IDEA
    )
    assert built.ledger == (LedgerEntry.from_card(earlier),)
    assert "duplicate_intent" in [i.code for i in validate_question(card("Q-002"), built)]


# ---------------------------------------------------------------------------------------- batch measures
def test_project_term_share() -> None:
    cards = [
        card("Q-001"),
        card(
            "Q-002",
            question="Who will use your app?",
            type="open_prompt",
            needs=["audience.segments"],
        ),
        card(
            "Q-003", question="Which languages?", type="open_prompt", needs=["locales"]
        ),  # universal: not counted
    ]
    assert project_term_share(cards, TERMS, CAT) == pytest.approx(0.5)
    assert project_term_share([cards[2]], TERMS, CAT) == 1.0
    assert project_term_share([], TERMS, CAT) == 1.0


IDEAS = {
    "tiffin": [
        "Should customers pick a tiffin each day or subscribe weekly?",
        "How far from your kitchen in Pune will a rider deliver?",
        "Who packs the tiffin boxes and when?",
    ],
    "clinic": [
        "How long is one appointment with your family doctor?",
        "Can patients cancel a clinic booking the same day?",
        "Which specialists visit your Indore clinic?",
    ],
    "pharmacy": [
        "How does a pharmacy reorder stock when inventory runs low?",
        "Do you track medicine expiry dates per batch?",
        "Which distributors supply your B2B pharmacies?",
    ],
}
GENERIC = [
    "Who will use your app?",
    "What features do you want?",
    "How should users sign in?",
]


def _cards(texts: list[str], needs: str = "scope.features") -> list[Card]:
    return [
        card(f"Q-{i + 1:03d}", question=t, type="open_prompt", intent=f"i{i}", needs=[needs])
        for i, t in enumerate(texts)
    ]


def test_cross_idea_overlap_is_low_for_specific_and_one_for_generic() -> None:
    specific = {k: _cards(v) for k, v in IDEAS.items()}
    assert cross_idea_overlap(specific) < 0.2
    generic = {k: _cards(GENERIC) for k in IDEAS}
    assert cross_idea_overlap(generic) == pytest.approx(1.0)
    mixed = {**specific, "tiffin": _cards(GENERIC)}
    mixed["clinic"] = _cards(GENERIC)
    pairs = cross_idea_overlap_pairs(mixed)
    assert pairs[("clinic", "tiffin")] == pytest.approx(1.0)
    assert pairs[("clinic", "pharmacy")] < 0.2
    assert cross_idea_overlap(mixed) == pytest.approx(1.0)


def test_cross_idea_overlap_edge_cases() -> None:
    assert cross_idea_overlap({}) == 0.0
    assert cross_idea_overlap({"only": _cards(GENERIC)}) == 0.0
    assert cross_idea_overlap({"a": [], "b": _cards(GENERIC)}) == 0.0


def test_cross_idea_overlap_can_skip_universal_questions() -> None:
    languages = [
        card(
            "Q-001",
            question="Which languages should people read?",
            type="open_prompt",
            needs=["locales"],
        )
    ]
    both = {"a": languages, "b": languages}
    assert cross_idea_overlap(both) == pytest.approx(1.0)
    assert cross_idea_overlap(both, CAT) == 0.0


# --------------------------------------------------------------------------------------------- coverage
def _statuses() -> dict[str, NeedStatus]:
    return compute(CAT, [], SlotStore(CAT, clock=FakeClock()))


def test_coverage_issues_lists_uncovered_must_and_high_impact_needs() -> None:
    statuses = _statuses()
    policy = QuestionPolicy()
    issues = coverage_issues(statuses, [], [], [], CAT, policy)
    assert issues and all(i.startswith("uncovered_need: ") for i in issues)
    assert any("idea.summary" in i for i in issues) and any("scope.priority" in i for i in issues)
    assert not any("brand.name" in i for i in issues)  # should-level, low impact


def test_coverage_is_complete_when_needs_are_covered_assumed_or_satisfied() -> None:
    statuses = _statuses()
    from mde_compass.engine.needs import expert_gap_obligations

    owed = expert_gap_obligations(statuses, CAT, QuestionPolicy())
    half = len(owed) // 2
    covered, assumed = owed[:half], owed[half:]
    assert coverage_issues(statuses, covered, assumed, [], CAT, QuestionPolicy()) == []
    as_objects = [AssumedInstead(need_id=n, assumption="a guess") for n in assumed]
    assert coverage_issues(statuses, covered, as_objects, [], CAT, CompassConfig()) == []
    missing_one = coverage_issues(statuses, covered[:-1], assumed, [], CAT, QuestionPolicy())
    assert len(missing_one) == 1 and covered[-1] in missing_one[0]


def test_coverage_expert_gaps_and_unknown_needs() -> None:
    statuses = _statuses()
    from mde_compass.engine.needs import expert_gap_obligations

    owed = expert_gap_obligations(statuses, CAT, QuestionPolicy())
    assert coverage_issues(statuses, owed, [], ["brand.name"], CAT, QuestionPolicy()) == [
        "uncovered_need: brand.name (should, impact 2, missing) is not covered by a question, satisfied or listed in assumed_instead"
    ]
    assert (
        coverage_issues(statuses, [*owed, "brand.name"], [], ["brand.name"], CAT, QuestionPolicy())
        == []
    )
    free_text_gap = coverage_issues(
        statuses, owed, [], ["ask about delivery timing"], CAT, QuestionPolicy()
    )
    assert free_text_gap == []
    unknown = coverage_issues(
        statuses, [*owed, "made.up", "domain:menu"], [], [], CAT, QuestionPolicy()
    )
    assert unknown == ["unknown_need: made.up is not a catalog need or domain:<topic>"]


# --------------------------------------------------------------------------------------------- property
@settings(max_examples=60, deadline=None)
@given(
    intents=st.lists(
        st.sampled_from(["rider", "kitchen", "menu", "Rider!", "KITCHEN", "pay"]),
        min_size=1,
        max_size=10,
    )
)
def test_accepted_questions_never_repeat_an_intent(intents: list[str]) -> None:
    cards = [
        card(
            f"Q-{i + 1:03d}",
            question=f"How does the tiffin {w.lower().strip('!')} {i} work?",
            type="open_prompt",
            intent=w,
            needs=[f"domain:t{i}"],
            group_id=f"G-{i // 3}",
        )
        for i, w in enumerate(intents)
    ]
    result = validate_batch(cards, ctx(batch_limit=20))
    seen = [c.intent.lower().strip("!") for c in result.accepted]
    assert len(seen) == len(set(seen))
    assert {c.id for c in result.accepted} | {r.card_id for r in result.rejected} == {
        c.id for c in cards
    }
    assert not {c.id for c in result.accepted} & {r.card_id for r in result.rejected}
    again = validate_batch(result.accepted, ctx(batch_limit=20))
    assert [c.id for c in again.accepted] == [c.id for c in result.accepted]


def test_issue_string_form() -> None:
    assert str(Issue("no_project_term", "msg")) == "no_project_term: msg"
