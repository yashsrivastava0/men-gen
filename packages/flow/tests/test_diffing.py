"""Diffs: spec ids (computed locally) and the plain-language Brief diff."""

from __future__ import annotations

from mde_contracts.brief import AssumedItem
from mde_contracts.spec import ResearchAddition
from mde_flow.diffing import IdDiff, brief_diff, diff_ids, plan_diff, spec_ids
from mde_flow.testing.builders import make_brief, make_spec


def test_identical_specs_have_an_empty_diff() -> None:
    a = make_spec()
    assert diff_ids(a, make_spec()).empty


def test_diff_ids_finds_added_removed_and_changed() -> None:
    old = make_spec(features=("workouts", "progress"))
    new = make_spec(features=("workouts", "progress", "chat"), direction="bold-dark")
    d = diff_ids(old, new)
    assert d.added == ["SCR-005"] and d.new_screens == 1
    assert d.removed == []
    assert {"DESIGN", "NAV", "FLW-001"} <= set(d.changed)
    back = diff_ids(new, old)
    assert back.removed == ["SCR-005"] and back.added == []
    assert not d.empty and IdDiff().empty


def test_a_changed_screen_is_reported_by_id() -> None:
    old = make_spec(features=("a",))
    new = make_spec(features=("b",))
    d = diff_ids(old, new)
    assert "SCR-003" in d.changed  # the feature screen's route and purpose changed


def test_block_level_changes_mark_the_block_id() -> None:
    old = make_spec()
    new = make_spec(app_name="Other")
    assert "APP" in diff_ids(old, new).changed
    assert "SCR-001" not in diff_ids(old, new).changed


def test_spec_ids_lists_every_addressable_id() -> None:
    ids = spec_ids(make_spec(features=("a",)))
    assert {"SCR-001", "SCR-002", "SCR-003", "FLW-001", "ENT-Item", "ROLE-user", "DESIGN"} <= ids


def test_plan_diff_writes_plain_lines() -> None:
    old = make_spec(features=("a",))
    new = make_spec(
        features=("a", "b"),
        research_additions=[ResearchAddition(id="RA-001", title="t", kind="feature")],
    )
    diff = plan_diff(old, new, since="gate1")
    assert diff.since == "gate1" and diff.added_ids == ["SCR-004"]
    assert any(line.startswith("Added screen SCR-004") for line in diff.lines)
    assert any(line.startswith("Updated navigation") for line in diff.lines)
    removed = plan_diff(new, old)
    assert any(line.startswith("Removed screen SCR-004") for line in removed.lines)
    assert plan_diff(None, new).lines == []


def test_brief_diff_lists_changed_rows() -> None:
    old = make_brief(1, direction="clean-bright")
    new = make_brief(2, direction="bold-dark")
    assert brief_diff(old, new) == ["Look: bold-dark"]
    assert brief_diff(None, new) == []


def test_brief_diff_features_and_summary() -> None:
    old = make_brief(1, features=("workouts",))
    new = make_brief(2, features=("workouts", "chat"), summary="A bigger app")
    lines = brief_diff(old, new)
    assert "What you are building: A bigger app" in lines
    assert "Feature added: Chat" in lines
    assert "Main features: workouts, chat" in lines
    assert "Feature removed: Chat" in brief_diff(new, old)
    flipped = new.model_copy(
        update={
            "rows": [
                r.model_copy(
                    update={"features": [f.model_copy(update={"on": False}) for f in r.features]}
                )
                if r.key == "features"
                else r
                for r in new.rows
            ]
        }
    )
    assert "Feature turned off: Chat" in brief_diff(new, flipped)


def test_brief_diff_assumptions() -> None:
    base = make_brief(1)
    item = AssumedItem(slot_id="locale", label="Language", value_text="English", reason="default")
    with_item = base.model_copy(update={"assumptions": [item]})
    assert brief_diff(base, with_item) == ["We assumed: Language: English"]
    assert brief_diff(with_item, base) == ["No longer assumed: Language"]
    changed = with_item.model_copy(
        update={"assumptions": [item.model_copy(update={"value_text": "Hindi"})]}
    )
    assert brief_diff(with_item, changed) == ["We assumed: Language: now Hindi"]
    off = with_item.model_copy(
        update={"assumptions": [item.model_copy(update={"included": False})]}
    )
    assert brief_diff(with_item, off) == ["Assumption turned off: Language"]
    assert brief_diff(off, with_item) == ["Assumption back on: Language"]


def test_brief_diff_never_returns_nothing_for_a_new_version() -> None:
    a = make_brief(1)
    b = make_brief(2)
    assert brief_diff(a, b) == ["Nothing you can see changed, but this is a new version."]


def test_brief_diff_rows_added_and_removed() -> None:
    a = make_brief(1)
    fewer = a.model_copy(update={"rows": [r for r in a.rows if r.key != "login"]})
    assert brief_diff(a, fewer) == ["Removed: Login"]
    assert brief_diff(fewer, a)[0].startswith("New: Login")
