"""Unit tests of the scenario runner itself (no service): vocabulary, slot paths, invariants."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mde_compass.scenarios import Scenario, SlotExpect, Step, load_dir
from mde_compass.scenarios.runner import check_slot, locked_without_evidence

DATA = Path(__file__).parent / "data"

BRIEF = {
    "idea": {
        "category": {
            "value": "commerce",
            "tier": "Default",
            "status": "assumed",
            "provenance": [],
            "history": [],
        },
        "summary": {
            "value": "A food delivery app",
            "tier": "Locked",
            "status": "confirmed",
            "provenance": [{"type": "user_tap", "ref": "evt_1"}],
            "history": [{"value": "x"}],
        },
    },
    "scope": [
        {"name": "Order food", "tier": "Default"},
        {"name": "Track order", "tier": "Default"},
    ],
    "experience": {},
}


def test_every_data_file_is_a_valid_scenario() -> None:
    found = load_dir(DATA, "SB-*.yaml")
    assert len(found) >= 30
    ids = [s.id for _, s in found]
    assert len(ids) == len(set(ids))
    for path, scn in found:
        assert path.stem == scn.id
        assert scn.steps, scn.id
        if scn.known_issue:
            assert scn.known_issue.startswith(("BUG-", "GAP-")) and scn.known_issue_note, scn.id


def test_step_has_exactly_one_action() -> None:
    assert Step.from_raw({"say": "hi", "expect": {"state": "INTERVIEWING"}}).action == "say"
    with pytest.raises(ValueError):
        Step.from_raw({"say": "hi", "tap": "A"})
    with pytest.raises(ValueError):
        Step.from_raw({"expect": {}})
    with pytest.raises(ValueError):
        Step.from_raw({"dance": True})


def test_unknown_expect_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        Step.from_raw({"say": "hi", "expect": {"stat": "X"}})


def test_scenario_id_and_mode_are_validated() -> None:
    with pytest.raises(ValueError):
        Scenario.model_validate({"id": "X-1", "steps": [{"say": "hi"}]})
    with pytest.raises(ValueError):
        Scenario.model_validate({"id": "SB-X", "mode": "chaos", "steps": [{"say": "hi"}]})


def test_slot_checks() -> None:
    ok = check_slot(
        BRIEF,
        "idea.summary",
        SlotExpect(contains="food", tier="Locked", history_min=1, provenance="user_tap"),
    )
    assert ok == []
    bad = check_slot(BRIEF, "idea.category", SlotExpect(contains="travel", tier="Locked"))
    assert len(bad) == 2
    assert (
        check_slot(BRIEF, "experience.locales", SlotExpect(filled=False, not_tier="Locked")) == []
    )
    assert check_slot(BRIEF, "experience.locales", SlotExpect(filled=True))  # nothing there yet
    assert check_slot(BRIEF, "scope.*.name", SlotExpect(match="order", match_max=2)) == []
    assert check_slot(
        BRIEF, "scope.*.name", SlotExpect(match="order", match_max=1)
    )  # two entries contain 'order'
    assert check_slot(BRIEF, "scope", SlotExpect(count_min=2, count_max=2)) == []


def test_locked_without_evidence_is_found() -> None:
    assert locked_without_evidence(BRIEF) == []
    leaky = {"idea": {"x": {"tier": "Locked", "provenance": [{"type": "assumed", "ref": "evt_1"}]}}}
    assert locked_without_evidence(leaky) == ["idea.x"]
    no_ref = {"a": [{"tier": "Locked", "provenance": [{"type": "user_text", "ref": " "}]}]}
    assert locked_without_evidence(no_ref) == ["a[0]"]


def test_deferred_file_lists_reasons() -> None:
    deferred = yaml.safe_load((DATA / "DEFERRED.yaml").read_text())["deferred"]
    assert deferred and all(d["id"].startswith("IN-") and d["reason"] for d in deferred)


# --------------------------------------------------------------------------- the planner path translations
def test_a_planned_card_belongs_to_a_frame_through_the_needs_it_serves() -> None:
    from mde_compass.domain import Card
    from mde_compass.scenarios.runner import frame_slots, in_frame

    planned = Card(
        id="Q-002", frame="planned", slots=[], type="open_prompt", question="Who orders?",
        need_ids=["idea.business_model"],
    )  # fmt: skip
    assert "idea.business_model" in frame_slots(["business_model"])
    assert in_frame(planned, ["business_model"], "planner")
    assert not in_frame(planned, ["business_model"], "legacy")  # the legacy engine names its frame
    assert not in_frame(planned, ["who_and_where"], "planner")
    legacy = planned.model_copy(update={"frame": "business_model", "need_ids": []})
    assert in_frame(legacy, ["business_model"], "legacy")
    assert frame_slots(["no_such_frame"]) == frozenset()


def test_the_engine_config_selects_the_question_engine_and_nothing_else() -> None:
    from mde_compass.config import DEFAULT_CONFIG
    from mde_compass.scenarios.runner import engine_config

    assert engine_config("planner").interview.engine == "planner"
    assert engine_config("legacy").interview.engine == "legacy"
    assert engine_config("planner").questions == DEFAULT_CONFIG.questions


def test_planner_only_slot_values_are_accepted_on_the_planner_path_only() -> None:
    exp = SlotExpect(contains_any=["ads"], planner_contains_any=["advertising"])
    brief = {"idea": {"business_model": {"value": "advertising", "tier": "Default"}}}
    assert check_slot(brief, "idea.business_model", exp, "planner") == []
    assert check_slot(brief, "idea.business_model", exp, "legacy") != []


def test_a_scenario_can_be_limited_to_one_engine_and_must_name_a_known_one() -> None:
    scn = Scenario.model_validate(
        {"id": "SB-X-1", "engines": ["planner"], "steps": [{"say": "hi"}]}
    )
    assert scn.engines == ["planner"]
    with pytest.raises(ValueError):
        Scenario.model_validate({"id": "SB-X-1", "engines": ["robot"], "steps": [{"say": "hi"}]})


# ---------------------------------------------------------------------------------- the structure expectation


async def _structured_conv(*, enabled: bool):  # type: ignore[no-untyped-def]
    from mde_compass.domain.events import Command
    from mde_compass.scenarios.runner import new_service
    from mde_compass.testing import Conversation

    svc = new_service("planner", policy={"structure_enabled": enabled})
    conv = await Conversation.start(svc)
    await conv.send(Command(event_id=conv._id(), name="budget_quick"))  # type: ignore[arg-type]
    await conv.say("I run a tiffin service in Pune and want an app for it")
    await conv.drive_to_review(max_steps=40)
    return conv


async def test_the_structure_expectation_passes_for_what_is_there_and_names_what_is_not() -> None:
    from mde_compass.scenarios.models import StructureExpect
    from mde_compass.scenarios.runner import check_structure

    conv = await _structured_conv(enabled=True)
    good = StructureExpect(
        absent=False,
        band="small",
        source="tier",
        modules_min=1,
        screens_min=1,
        no_error_issues=True,
        none_locked=True,
    )
    assert await check_structure(good, conv) == []
    wrong = StructureExpect(absent=True, band="large", screens_min=500, screens_max=1)
    errs = await check_structure(wrong, conv)
    assert len(errs) == 4 and any("band 'large' expected" in e for e in errs)
    assert any("none expected" in e for e in errs) and any(
        "at least 500 screens" in e for e in errs
    )


async def test_the_structure_expectation_sees_a_session_without_a_draft() -> None:
    from mde_compass.scenarios.models import StructureExpect
    from mde_compass.scenarios.runner import check_structure

    conv = await _structured_conv(enabled=False)
    assert await check_structure(StructureExpect(absent=True), conv) == []
    errs = await check_structure(StructureExpect(absent=False, modules_min=1), conv)
    assert any("a draft was expected" in e for e in errs)


def test_a_scenarios_policy_reaches_a_service_made_without_a_provider_fixture() -> None:
    from mde_compass.scenarios.runner import new_service

    assert new_service("planner").config.interview.structure_enabled is False
    on = new_service("planner", policy={"structure_enabled": True})
    assert on.config.interview.structure_enabled is True
