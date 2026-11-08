"""QuestionSelector: order, levels, preconditions, caps, visual limit, assumptions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mde_content.catalogs import load_catalogs
from mde_content.expr import holds
from mde_content.policies import resolve_policy
from mde_content.questions import load_question_banks
from mde_contracts.common import Mode
from mde_contracts.engines import DiscoveryState
from mde_contracts.policy import Policy
from mde_contracts.slots import SlotData
from mde_discovery.selector import QuestionSelector, asked_base_ids, limit_reached
from mde_discovery.slots import SlotStore

NOW = datetime(2026, 11, 8, tzinfo=UTC)
BANKS = load_question_banks()
CATALOGS = load_catalogs()
SELECTOR = QuestionSelector(BANKS, CATALOGS)
GUIDED = resolve_policy("guided-default")
ENTERPRISE = resolve_policy("enterprise-it-services-firm")


def store(**slots: SlotData) -> SlotStore:
    s = SlotStore(None, lambda: NOW)
    for slot_id, value in slots.items():
        s.write(slot_id.replace("__", "."), value, state="confirmed", source="tap")
    return s


def pick(s: SlotStore, state: DiscoveryState | None = None, policy: Policy = GUIDED) -> str:
    sel = SELECTOR.next(state=state or DiscoveryState(), store=s, mode=policy.mode, policy=policy)
    return sel.card.id if sel.card else sel.kind


# ----------------------------------------------------------------------------- order


def test_first_the_idea_then_the_type_then_the_rest_in_catalog_order() -> None:
    s = SlotStore(None, lambda: NOW)
    assert pick(s) == "Q-idea-01"
    s.write("idea.summary", "a shop", state="volunteered", source="text")
    s.write("idea.archetype", "commerce", state="volunteered", source="text")
    assert pick(s) == "Q-type-01"  # a detected type is confirmed with a card
    s.write("idea.archetype", "commerce", state="confirmed", source="tap")
    order = []
    for _ in range(10):
        sel = SELECTOR.next(state=DiscoveryState(), store=s, mode="guided", policy=GUIDED)
        if sel.kind == "complete" or sel.card is None:
            break
        order.append(sel.slot)
        assert sel.slot is not None
        s.write(
            sel.slot, "online" if sel.slot == "pay.model" else "x", state="confirmed", source="tap"
        )
        if sel.slot == "scope.features":
            s.write("scope.features", ["payments"], state="confirmed", source="tap")
    assert order[:3] == ["scope.apps", "scope.features", "auth.method"]


def test_the_catalog_order_respects_depends_on() -> None:
    modes: list[tuple[Mode, Policy]] = [("guided", GUIDED), ("enterprise", ENTERPRISE)]
    for mode, policy in modes:
        ordered = [sd.slot_id for sd in SELECTOR.slots_in_order(mode, policy)]
        musts = [
            sd
            for sd in SELECTOR.slots_in_order(mode, policy)
            if SELECTOR.level(sd, mode, policy) == "must"
        ]
        for sd in musts:
            for dep in sd.depends_on:
                dep_def = SELECTOR.slot_def(dep)
                if SELECTOR.level(dep_def, mode, policy) == "must":
                    assert ordered.index(dep) < ordered.index(sd.slot_id), (mode, sd.slot_id, dep)


def test_a_slot_whose_precondition_fails_is_not_asked() -> None:
    s = store(idea__summary="a shop", idea__archetype="commerce", scope__apps=["customer"])
    s.write("scope.features", ["search"], state="confirmed", source="tap")  # no payments
    s.write("auth.method", "email_phone_code", state="confirmed", source="tap")
    s.write("design.direction", "clean-bright", state="confirmed", source="tap")
    s.write("brand.name", "Blue", state="confirmed", source="tap")
    assert pick(s) == "complete"  # pay.model is asked only when payments is ticked
    s.write("scope.features", ["search", "payments"], state="confirmed", source="tap")
    assert pick(s) == "Q-pay-01"


def test_a_volunteered_value_skips_its_card_but_a_checklist_is_still_shown() -> None:
    s = store(idea__summary="a shop", idea__archetype="commerce", scope__apps=["customer"])
    s.write("scope.features", ["search"], state="volunteered", source="text")
    s.write("design.direction", "bold-dark", state="volunteered", source="text")
    assert pick(s) == "Q-feat-01"  # tick the list, pre-filled from what the user said
    s.write("scope.features", ["search"], state="confirmed", source="tap")
    assert pick(s) == "Q-auth-01"
    s.write("auth.method", "none", state="confirmed", source="tap")
    assert pick(s) == "Q-brand-01"  # the look is skipped: it was volunteered


def test_assumed_slots_are_settled_and_never_asked_again() -> None:
    s = store(idea__summary="a shop", idea__archetype="commerce", scope__apps=["customer"])
    s.write("scope.features", ["search"], state="assumed", source="default")
    assert pick(s) == "Q-auth-01"


def test_cards_already_asked_are_not_asked_twice() -> None:
    s = store(idea__summary="a shop", idea__archetype="commerce")
    state = DiscoveryState(cards_asked=["Q-apps-01"])
    assert pick(s, state) == "Q-feat-01"
    assert asked_base_ids(DiscoveryState(cards_asked=["Q-apps-01", "Q-apps-01:5"])) == {"Q-apps-01"}


def test_a_contradiction_comes_before_everything_else() -> None:
    s = store(idea__summary="a shop", idea__archetype="commerce", scope__apps=["customer"])
    s.write("scope.features", ["favorites"], state="confirmed", source="tap")
    s.write("auth.method", "none", state="confirmed", source="tap")
    sel = SELECTOR.next(state=DiscoveryState(), store=s, mode="guided", policy=GUIDED)
    assert sel.resolution and sel.card is not None and sel.card.id == "Q-fix-01"


# ----------------------------------------------------------------------------- sensitive domains


def test_every_flagged_domain_gets_its_own_must_confirm_card_once() -> None:
    s = store(idea__summary="an app", idea__archetype="custom")
    state = DiscoveryState(sensitive_flags=["health", "finance"])
    assert pick(s, state) == "Q-comp-01"
    state = state.model_copy(
        update={"compliance_confirmed": ["health"], "cards_asked": ["Q-comp-01"]}
    )
    assert pick(s, state) == "Q-comp-02"
    state = state.model_copy(update={"compliance_confirmed": ["health", "finance"]})
    assert pick(s, state) == "Q-feat-05"  # the compliance cards are done, the rest follows


def test_compliance_cards_map_to_their_domain() -> None:
    assert SELECTOR.domain_of(BANKS.card("Q-comp-01")) == "health"
    assert SELECTOR.domain_of(BANKS.card("Q-comp-05")) == "precise_location"
    assert SELECTOR.domain_of(BANKS.card("Q-auth-01")) is None


# ----------------------------------------------------------------------------- levels and modes


def test_guided_asks_must_slots_only_enterprise_also_asks_should_slots() -> None:
    assert SELECTOR.asked_levels("guided") == ("must",)
    assert SELECTOR.asked_levels("enterprise") == ("must", "should")
    guided = {
        sd.slot_id
        for sd in SELECTOR.slots_in_order("guided", GUIDED)
        if SELECTOR.level(sd, "guided", GUIDED) == "must"
    }
    assert "auth.sso" not in guided and "approvers" not in guided
    ent = {
        sd.slot_id
        for sd in SELECTOR.slots_in_order("enterprise", ENTERPRISE)
        if SELECTOR.level(sd, "enterprise", ENTERPRISE) in ("must", "should")
    }
    assert {"auth.sso", "approvers", "data.classification", "regions", "release.channel"} <= ent


def test_a_policy_override_changes_the_level_of_a_slot() -> None:
    sd = SELECTOR.slot_def("design.direction")
    assert SELECTOR.level(sd, "enterprise", ENTERPRISE) == "should"  # the policy lowers it
    assert SELECTOR.level(sd, "enterprise", resolve_policy("platform-default")) == "must"
    sso = SELECTOR.slot_def("auth.sso")
    assert SELECTOR.level(sso, "guided", GUIDED) == "may"


def test_enterprise_asks_should_slots_grouped_after_the_must_slots() -> None:
    ordered = SELECTOR.slots_in_order("enterprise", ENTERPRISE)
    levels = [SELECTOR.level(sd, "enterprise", ENTERPRISE) for sd in ordered]
    first_should = levels.index("should")
    assert all(lv == "must" for lv in levels[:first_should])
    should_groups = [sd.group for sd, lv in zip(ordered, levels, strict=True) if lv == "should"]
    seen: list[str] = []
    for group in should_groups:  # a group never re-opens once left
        if not seen or seen[-1] != group:
            assert group not in seen, should_groups
            seen.append(group)


# ----------------------------------------------------------------------------- caps and the visual limit


def test_the_card_cap_and_the_turn_cap_apply_to_guided_only() -> None:
    state = DiscoveryState(card_count=10)
    assert limit_reached(state, "guided", GUIDED)
    assert not limit_reached(DiscoveryState(card_count=9), "guided", GUIDED)
    assert limit_reached(DiscoveryState(turn_count=30), "guided", GUIDED)
    assert not limit_reached(state, "enterprise", ENTERPRISE)  # as many as needed
    assert limit_reached(
        DiscoveryState(card_count=5),
        "guided",
        GUIDED.model_copy(update={"limits": GUIDED.limits.model_copy(update={"max_cards": 5})}),
    )


def test_at_most_two_visual_cards_in_guided() -> None:
    s = store(idea__summary="food", idea__archetype="food_delivery", scope__apps=["customer"])
    s.write("scope.features", ["restaurant_browse"], state="confirmed", source="tap")
    s.write("auth.method", "none", state="confirmed", source="tap")
    assert pick(s) == "Q-look-02"
    visual = DiscoveryState(cards_asked=["Q-look-01", "Q-look-03"])
    assert pick(s, visual) == "Q-brand-01"  # the look is defaulted instead of a third visual card


def test_a_slot_with_no_card_in_the_bank_is_written_by_c5() -> None:
    policy = GUIDED.model_copy(update={"slot_policy": {"a11y": "must"}})
    s = store(idea__summary="a shop", idea__archetype="commerce", scope__apps=["customer"])
    s.write("scope.features", ["search"], state="confirmed", source="tap")
    for slot, value in (
        ("auth.method", "none"),
        ("design.direction", "clean-bright"),
        ("brand.name", "Blue"),
    ):
        s.write(slot, value, state="confirmed", source="tap")
    sel = SELECTOR.next(state=DiscoveryState(), store=s, mode="guided", policy=policy)
    assert (sel.kind, sel.slot) == ("write_card", "a11y")


# ----------------------------------------------------------------------------- assumptions


@pytest.mark.parametrize(
    ("slot", "archetype", "expected"),
    [
        ("auth.method", "commerce", [("auth.method", "email_phone_code")]),
        ("design.direction", "food_delivery", [("design.direction", "warm-friendly")]),
        ("design.direction", "internal_workforce", [("design.direction", "corporate-calm")]),
        ("pay.model", "commerce", [("pay.model", "online")]),
        ("brand.name", "commerce", [("brand.name", "My App")]),
        ("a11y", "commerce", [("a11y", "standard")]),
        ("design.accent", "commerce", []),
    ],
)
def test_assumption_is_the_recommended_option_or_the_slot_default(
    slot: str, archetype: str, expected: list[tuple[str, object]]
) -> None:
    ctx = {"idea.archetype": archetype, "scope.features": ["payments"], "sensitive.domains": None}
    assert SELECTOR.assumption(slot, archetype, "guided", ctx) == expected


def test_assumption_for_a_checklist_is_the_pre_ticked_set() -> None:
    ctx = {"idea.archetype": "commerce", "sensitive.domains": None}
    [(slot, value)] = SELECTOR.assumption("scope.features", "commerce", "guided", ctx)
    assert slot == "scope.features" and isinstance(value, list)
    assert "payments" in value and "reviews" not in value


def test_a_compliance_assumption_is_the_careful_option_with_its_defaults() -> None:
    card = BANKS.card("Q-comp-01")
    writes = dict(SELECTOR.assumption("compliance.flags", None, "guided", {}, card=card))
    assert writes == {
        "compliance.flags": ["health_data_privacy"],
        "behavior.analytics": False,
        "behavior.offline": "none",
    }


def test_ask_if_uses_the_safe_evaluator_only() -> None:
    assert holds("idea.archetype in [commerce, food_delivery]", {"idea.archetype": "commerce"})
    with pytest.raises(ValueError):
        holds("__import__('os').system('x')", {})
