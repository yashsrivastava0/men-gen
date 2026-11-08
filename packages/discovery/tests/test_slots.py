"""SlotStore: the spec 3.3 rules (overwrite, negation, assumed vs confirmed, untouched slots)."""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_contracts.calls import SlotUpdate
from mde_contracts.slots import SlotRecord, SlotState
from mde_discovery.slots import RANK, SlotStore, detect_contradiction

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def store(initial: dict[str, SlotRecord] | None = None) -> SlotStore:
    return SlotStore(initial, lambda: NOW)


# ----------------------------------------------------------------------------- the four rules


def test_a_correction_overwrites_and_never_appends() -> None:
    s = store()
    s.write("scope.features", ["search", "reviews"], state="confirmed", source="tap")
    s.write("scope.features", ["favorites"], state="confirmed", source="text")
    assert s.value("scope.features") == ["favorites"]
    s.write("brand.name", "Blue", state="confirmed", source="text")
    s.write("brand.name", "Green", state="confirmed", source="text")
    assert s.value("brand.name") == "Green"


def test_compliance_flags_are_the_one_union() -> None:
    s = store()
    s.write("compliance.flags", ["health_data_privacy"], state="confirmed", source="tap")
    s.write("compliance.flags", ["financial_data"], state="confirmed", source="tap")
    assert s.value("compliance.flags") == ["health_data_privacy", "financial_data"]
    s.write("compliance.flags", [], state="confirmed", source="tap")  # "it does not apply"
    assert s.value("compliance.flags") == ["health_data_privacy", "financial_data"]


def test_a_domain_answered_with_none_still_marks_the_slot_confirmed() -> None:
    s = store()
    s.write("compliance.flags", [], state="confirmed", source="tap")
    assert s.state("compliance.flags") == "confirmed"
    assert not s.is_filled("compliance.flags")  # an empty list is "unfilled" for ask_if


def test_an_explicit_negation_clears_the_slot() -> None:
    s = store()
    s.write("idea.archetype", "commerce", state="confirmed", source="tap")
    update = SlotUpdate(slot_id="idea.archetype", value_text="commerce", negate=True)
    assert s.apply_update(update, "commerce", state="confirmed", source="text").applied
    assert s.state("idea.archetype") == "empty"
    assert "idea.archetype" in s.changed


def test_negating_one_item_trims_a_list_and_negating_all_clears_it() -> None:
    s = store()
    s.write("scope.features", ["search", "payments", "reviews"], state="confirmed", source="tap")
    drop = SlotUpdate(slot_id="scope.features", value_list=["payments"], negate=True)
    s.apply_update(drop, ["payments"], state="confirmed", source="text")
    assert s.value("scope.features") == ["search", "reviews"]
    rest = SlotUpdate(slot_id="scope.features", value_list=["search", "reviews"], negate=True)
    s.apply_update(rest, ["search", "reviews"], state="confirmed", source="text")
    assert s.state("scope.features") == "empty"


def test_negating_a_value_the_slot_does_not_hold_changes_nothing() -> None:
    s = store()
    s.write("idea.archetype", "commerce", state="confirmed", source="tap")
    update = SlotUpdate(slot_id="idea.archetype", value_text="booking_services", negate=True)
    assert not s.apply_update(update, "booking_services", state="confirmed", source="text").applied
    assert s.value("idea.archetype") == "commerce"
    nothing = SlotUpdate(slot_id="brand.name", negate=True)
    assert not s.apply_update(nothing, None, state="confirmed", source="text").applied


def test_assumed_never_replaces_confirmed_or_volunteered() -> None:
    s = store()
    s.write("auth.method", "none", state="confirmed", source="tap")
    res = s.write("auth.method", "email_phone_code", state="assumed", source="default")
    assert not res.applied and s.value("auth.method") == "none"
    s.write("brand.name", "Blue", state="volunteered", source="text")
    assert not s.write("brand.name", "My App", state="assumed", source="default").applied
    assert s.value("brand.name") == "Blue"


def test_assumed_fills_an_empty_slot_and_replaces_an_older_assumption() -> None:
    s = store()
    assert s.write("pay.model", "online", state="assumed", source="default").applied
    assert s.write("pay.model", "both", state="assumed", source="default").applied
    assert s.value("pay.model") == "both"


def test_a_user_correction_replaces_an_assumption_and_keeps_confirmed_state() -> None:
    s = store()
    s.write("pay.model", "online", state="assumed", source="default")
    s.write("pay.model", "on_delivery", state="confirmed", source="tap")
    assert (s.value("pay.model"), s.state("pay.model")) == ("on_delivery", "confirmed")
    s.write("pay.model", "both", state="volunteered", source="text")
    assert (s.value("pay.model"), s.state("pay.model")) == ("both", "confirmed")


def test_an_untouched_slot_is_never_reset() -> None:
    s = store()
    s.write("brand.name", "Blue", state="confirmed", source="tap")
    s.write("auth.method", "none", state="confirmed", source="tap")
    s.clear("auth.method")
    s.write("design.direction", "bold-dark", state="volunteered", source="text")
    assert s.value("brand.name") == "Blue" and s.state("brand.name") == "confirmed"


def test_confirming_the_same_value_upgrades_the_state_without_a_value_change() -> None:
    s = store()
    s.write("idea.archetype", "commerce", state="volunteered", source="text")
    s.changed.clear()
    res = s.write("idea.archetype", "commerce", state="confirmed", source="tap")
    assert res.applied and not res.value_changed
    assert s.state("idea.archetype") == "confirmed"
    assert s.changed == []  # a confirmation is not a change of the app


def test_repeating_the_same_write_is_a_no_op() -> None:
    s = store()
    s.write("brand.name", "Blue", state="confirmed", source="tap")
    assert not s.write("brand.name", "Blue", state="confirmed", source="tap").applied
    assert not s.write("brand.name", "Blue", state="volunteered", source="text").applied


def test_unknown_slot_empty_values_and_whitespace_are_rejected_or_cleaned() -> None:
    s = store()
    assert s.write("nope.slot", "x", state="confirmed", source="tap").reason == "unknown_slot"
    assert s.write("brand.name", "   ", state="confirmed", source="tap").reason == "empty"
    assert s.write("scope.features", [], state="confirmed", source="tap").reason == "empty"
    s.write("brand.name", "  Blue  ", state="confirmed", source="tap")
    assert s.value("brand.name") == "Blue"
    s.write("scope.features", ["a", "a", " b "], state="confirmed", source="tap")
    assert s.value("scope.features") == ["a", "b"]


def test_provenance_is_recorded() -> None:
    s = store()
    s.write("design.direction", "bold-dark", state="volunteered", source="text", evidence="dark")
    rec = s.get("design.direction")
    assert rec is not None
    assert (rec.source, rec.evidence, rec.updated_at) == ("text", "dark", NOW)


def test_context_for_ask_if_has_slot_values_and_sensitive_domains() -> None:
    s = store()
    s.write("idea.archetype", "commerce", state="confirmed", source="tap")
    s.write("compliance.flags", [], state="confirmed", source="tap")
    ctx = s.context(["health"])
    assert ctx["idea.archetype"] == "commerce"
    assert ctx["sensitive.domains"] == ["health"]
    assert "compliance.flags" not in ctx  # an empty list is unfilled
    assert s.context([])["sensitive.domains"] is None


def test_remove_items_and_mark_disputed() -> None:
    s = store()
    assert not s.remove_items("scope.features", ["x"])
    s.write("scope.features", ["a", "b"], state="confirmed", source="tap")
    assert not s.remove_items("scope.features", ["zzz"])
    s.mark_disputed("scope.features")
    assert s.state("scope.features") == "disputed"
    s.mark_disputed("brand.name")  # an empty slot cannot be disputed
    assert s.state("brand.name") == "empty"


def test_a_contradiction_is_found_through_the_bank_resolution_cards() -> None:
    from mde_content.questions import load_question_banks

    banks = load_question_banks()
    s = store()
    s.write("idea.archetype", "commerce", state="confirmed", source="tap")
    s.write("auth.method", "email_phone_code", state="confirmed", source="tap")
    s.write("scope.features", ["favorites"], state="confirmed", source="tap")
    assert detect_contradiction(banks, s, "commerce", "guided", []) is None
    s.write("auth.method", "none", state="confirmed", source="text")
    card = detect_contradiction(banks, s, "commerce", "guided", [])
    assert card is not None and card.kind == "resolution" and card.slot == "auth.method"


# ----------------------------------------------------------------------------- properties

USER_STATES: list[SlotState] = ["volunteered", "confirmed"]
values = st.sampled_from(["none", "email_phone_code", "social", "password"])
write_ops = st.tuples(st.sampled_from(["volunteered", "confirmed", "assumed"]), values)


@settings(max_examples=150, deadline=None)
@given(st.lists(write_ops, min_size=1, max_size=25))
def test_property_a_user_given_value_is_never_replaced_by_an_assumption(
    ops: list[tuple[SlotState, str]],
) -> None:
    s = store()
    model_value: str | None = None
    model_state = "empty"
    for state, value in ops:
        s.write(
            "auth.method", value, state=state, source="default" if state == "assumed" else "text"
        )
        if state == "assumed":
            if model_state in ("empty", "assumed"):
                model_value, model_state = value, "assumed"
        else:
            keep_confirmed = model_state == "confirmed" and state == "volunteered"
            model_value = value
            model_state = "confirmed" if keep_confirmed else state
        assert s.value("auth.method") == model_value
        assert s.state("auth.method") == model_state
        if model_state in ("volunteered", "confirmed"):
            assert RANK[s.state("auth.method")] >= RANK["volunteered"]


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["write", "clear", "negate", "assume"]),
            st.sampled_from(["auth.method", "pay.model", "design.direction"]),
            values,
        ),
        min_size=1,
        max_size=30,
    )
)
def test_property_an_untouched_slot_never_resets(ops: list[tuple[str, str, str]]) -> None:
    s = store()
    s.write("brand.name", "Blue Basket", state="confirmed", source="tap", evidence="typed")
    s.write("regions", ["in"], state="volunteered", source="text")
    snapshot = {k: v for k, v in s.snapshot().items() if k in ("brand.name", "regions")}
    for kind, slot, value in ops:
        if kind == "write":
            s.write(slot, value, state="confirmed", source="tap")
        elif kind == "assume":
            s.write(slot, value, state="assumed", source="default")
        elif kind == "clear":
            s.clear(slot)
        else:
            s.apply_update(
                SlotUpdate.model_validate({"slot_id": slot, "value_text": value, "negate": True}),
                value,
                state="confirmed",
                source="text",
            )
    for slot_id, rec in snapshot.items():
        assert s.get(slot_id) == rec


@settings(max_examples=100, deadline=None)
@given(
    st.lists(st.lists(st.sampled_from(["a", "b", "c", "d"]), max_size=4), min_size=1, max_size=8)
)
def test_property_a_list_slot_holds_the_last_written_list_not_the_union(
    lists: list[list[str]],
) -> None:
    s = store()
    last: list[str] | None = None
    for items in lists:
        res = s.write("scope.features", items, state="confirmed", source="tap")
        if res.reason != "empty":
            last = list(dict.fromkeys(items))
    if last is not None:
        assert s.value("scope.features") == last
