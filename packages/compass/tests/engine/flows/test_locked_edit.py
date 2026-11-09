"""Locked values: what counts as Locked, the impact preview, the confirm card."""

from __future__ import annotations

from mde_compass.catalog import load_catalog
from mde_compass.domain import Brief, PatchOp
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.engine.flows import locked_edit as le
from mde_compass.engine.flows import reopen
from mde_compass.engine.flows.locked_edit import value_text

CAT = load_catalog()


def _brief(
    *, status: str, tier: str, value: str = "fee on each delivery", via: str = "user_tap"
) -> Brief:
    prov = [Provenance(type=via, ref="evt_1", quote="")]  # type: ignore[arg-type]
    item = BriefItem(value=value, tier=tier, status=status, provenance=prov, confidence=0.9)  # type: ignore[arg-type]
    return Brief.model_validate({"idea": {"business_model": item.model_dump(mode="json")}})


def _set(path: str, value: object) -> list[PatchOp]:
    return [PatchOp(op="set", path=path, value=value)]  # type: ignore[arg-type]


def test_only_a_locked_and_confirmed_value_needs_the_confirm() -> None:
    path = "idea.business_model"
    assert le.is_locked(_brief(status="confirmed", tier="Locked"), path)
    assert not le.is_locked(
        _brief(status="volunteered", tier="Locked"), path
    )  # the user's own words
    assert not le.is_locked(_brief(status="assumed", tier="Default"), path)
    assert not le.is_locked(Brief(), path)


def test_a_changed_locked_slot_is_reported_with_its_new_value() -> None:
    brief = _brief(status="confirmed", tier="Locked")
    changes = le.changed_locked(brief, CAT, _set("idea.business_model", "a monthly plan"))
    assert [(s.id, v) for s, v in changes] == [("idea.business_model", "a monthly plan")]


def test_the_same_value_or_an_open_value_or_other_paths_apply_at_once() -> None:
    brief = _brief(status="confirmed", tier="Locked")
    assert le.changed_locked(brief, CAT, _set("idea.business_model", "fee on each delivery")) == []
    assert (
        le.changed_locked(
            _brief(status="assumed", tier="Default"), CAT, _set("idea.business_model", "x")
        )
        == []
    )
    assert (
        le.changed_locked(brief, CAT, _set("scope.FTR-001.priority", "must")) == []
    )  # a record field
    assert le.changed_locked(brief, CAT, [PatchOp(op="clear", path="idea.business_model")]) == []
    assert le.changed_locked(brief, CAT, _set("idea.nonexistent", "x")) == []


def test_the_impact_preview_names_the_consumers_in_both_languages() -> None:
    slot = CAT.slots["idea.business_model"]
    assert le.impact_names(slot, "en") == "research, the blueprint"
    assert le.impact_names(slot, "hi") != le.impact_names(slot, "en")
    assert le.impact_names(CAT.slots["idea.summary"], "en") == "everything else"  # consumer "all"


def test_the_confirm_card_shows_current_new_and_impact() -> None:
    brief = _brief(status="confirmed", tier="Locked")
    changes = le.changed_locked(brief, CAT, _set("idea.business_model", "a monthly plan"))
    card = le.build_card(
        brief, changes, catalog=CAT, card_id="Q-010", language="en", frame="change_variants"
    )
    assert "fee on each delivery" in card.question and "a monthly plan" in card.question
    assert "research, the blueprint" in card.why_we_ask
    apply_, keep = card.options
    assert apply_.sets == {"idea.business_model": "a monthly plan"} and not keep.sets
    assert card.type == "choose_one" and card.must_confirm


def test_value_text_flattens_lists_and_dicts() -> None:
    assert value_text(None) == "" and value_text(["a", {"k": 1}]) == "a, k: 1"


def test_the_back_edge_counts_its_own_questions_only() -> None:
    whys = [{"reason": "back_edge"}, {"reason": "clarify"}, {}, {"reason": "back_edge"}]
    assert reopen.loops_used(whys) == 2  # type: ignore[arg-type]


def test_the_back_edge_card_shows_the_surprise_and_a_keep_option() -> None:
    slot = CAT.slots["idea.business_model"]
    card = reopen.build_card(
        slot, catalog=CAT, card_id="Q-011", reason="restaurants pay a flat monthly fee", current="a fee",
        variants=[("A monthly plan", "subscription", "typical"), ("Free", "business_funded", ""), ("Fee", "per_transaction_fee", ""), ("Extra", "x", "")],
        language="en", frame="change_variants",
    )  # fmt: skip
    assert "flat monthly fee" in card.question and "a fee" in card.question
    assert [o.id for o in card.options] == ["A", "B", "C", "K"]  # at most three variants, then Keep
    assert card.options[0].sets == {"idea.business_model": "subscription"}
    assert not card.options[-1].sets
