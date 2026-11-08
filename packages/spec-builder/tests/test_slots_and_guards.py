"""The slot view (what "not set" means) and the safety nets of the proposal pipeline."""

from __future__ import annotations

import pytest
from helpers import make_request, slot

from mde_contracts.calls import Proposal
from mde_contracts.engines import BuildRequest
from mde_contracts.spec import ScreenBlock
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.proposal_ops import HANDLERS, Change, OpsContext
from mde_spec_builder.proposals import validate_and_apply
from mde_spec_builder.slots import SlotView, effective_slots, render_value


def view(**values: object) -> SlotView:
    return SlotView(
        {k.replace("__", "."): slot(k.replace("__", "."), v) for k, v in values.items()}
    )


def test_empty_none_blank_and_empty_lists_are_not_set() -> None:
    v = SlotView(
        {
            "brand.name": slot("brand.name", ""),
            "scope.features": slot("scope.features", []),
            "locale": slot("locale", None),
            "idea.summary": slot("idea.summary", "An app", state="empty"),
        }
    )
    for slot_id in ("brand.name", "scope.features", "locale", "idea.summary", "regions"):
        assert not v.is_set(slot_id) and v.raw(slot_id) is None
    assert v.text("brand.name", "My App") == "My App" and v.items("scope.features") is None
    assert v.state("idea.summary") == "empty" and v.state("regions") == "empty"


def test_text_items_and_flags_read_every_value_shape() -> None:
    v = view(
        brand__name="  Mithai  ",
        scope__features=["a", " b ", ""],
        regions="in, eu ,",
        behavior__analytics=True,
        behavior__notifications="Yes",
        behavior__offline="off",
        a11y=1,
        locale=["hi", "en"],
        pay__model=False,
    )
    assert v.text("brand.name") == "Mithai" and v.text("locale") == "hi, en"
    assert v.text("pay.model") == "no" and v.text("behavior.analytics") == "yes"
    assert v.items("scope.features") == ["a", "b"] and v.items("regions") == ["in", "eu"]
    assert v.items("pay.model") is None  # a boolean is not a list
    assert v.flag("behavior.analytics") is True and v.flag("behavior.notifications") is True
    assert (
        v.flag("behavior.offline") is False
        and v.flag("a11y") is True
        and v.flag("pay.model") is False
    )
    assert v.flag("brand.name", True) is True and v.flag("regions") is None
    assert (
        render_value(None) == ""
        and render_value(True) == "yes"
        and render_value(["a", "b"]) == "a, b"
    )
    assert render_value(3) == "3"


def test_assumed_slots_come_in_the_fixed_slot_order() -> None:
    v = SlotView(
        {
            "locale": slot("locale", "hi", state="assumed"),
            "auth.method": slot("auth.method", "password", state="assumed"),
            "brand.name": slot("brand.name", "X", state="confirmed"),
            "regions": slot("regions", [], state="assumed"),
        }
    )
    assert [r.slot_id for r in v.assumed()] == ["auth.method", "locale"]


def test_answers_override_the_snapshot() -> None:
    req: BuildRequest = make_request("commerce", brand__name="Old").model_copy(
        update={"answer_slots": {"brand.name": slot("brand.name", "New")}}
    )
    assert effective_slots(req)["brand.name"].value == "New"
    assert SlotView.of(req).text("brand.name") == "New"


def test_a_value_that_does_not_fit_the_model_is_a_dropped_proposal_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = build_baseline(make_request("commerce"))

    def broken(spec_: object, p: Proposal, ctx: OpsContext) -> Change:
        ScreenBlock(
            id="not-an-id", route="/x", recipe="list", purpose="p"
        )  # raises ValidationError
        raise AssertionError("unreachable")

    monkeypatch.setitem(HANDLERS, "rename_screen", broken)
    result = validate_and_apply(
        spec, [Proposal(kind="rename_screen", target="SCR-001", name="x", rationale="r")]
    )
    assert result.applied == [] and result.dropped[0].code == "invalid"
    assert "does not fit the spec model" in result.dropped[0].reason
    assert result.spec == spec
