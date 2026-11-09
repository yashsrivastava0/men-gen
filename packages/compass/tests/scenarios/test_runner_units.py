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
            assert scn.known_issue.startswith("BUG-") and scn.known_issue_note, scn.id


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
