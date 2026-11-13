"""Diff and provenance round trips."""

from __future__ import annotations

from mde_compass.api.schemas import DiffResponse, ProvenanceResponse
from mde_compass.domain.brief import FeatureRecord
from mde_compass.domain.common import HistoryEntry
from mde_compass.views.diff import diff_briefs
from mde_compass.views.provenance import provenance_for, turn_id_of
from mde_compass.views.testing import (
    HISTORY_QUOTE,
    QUOTE_1,
    QUOTE_2,
    TURN_1,
    TURN_2,
    TURN_3,
    item,
    rich_brief,
)


def _by_path(diff: DiffResponse) -> dict[str, tuple[str, object, object]]:
    return {e.path: (e.kind, e.before, e.after) for e in diff.entries}


def test_identical_briefs_have_an_empty_diff() -> None:
    d = diff_briefs(rich_brief(), rich_brief(), from_version=3, to_version=3)
    assert d.entries == [] and d.sections_changed == []
    assert (d.from_version, d.to_version) == (3, 3)


def test_diff_reports_added_removed_changed_per_path() -> None:
    a, b = rich_brief(version=3), rich_brief(version=4)
    b.scope[0].priority = "nice"  # changed field of FTR-001
    b.scope.append(FeatureRecord(id="FTR-007", name="Reviews", intent="Rate meals"))  # added record
    b.scope = [f for f in b.scope if f.id != "FTR-006"]  # removed record
    b.idea["summary"] = item("Lunch boxes for offices")  # changed dict item
    del b.access["account_model"]  # removed dict item
    b.constraints["offline"] = item("full_offline", ptype="user_tap")  # changed value and tier
    b.glossary[0].synonyms = ["dabba", "tiffin box"]
    d = diff_briefs(a, b, from_version=3, to_version=4)
    paths = _by_path(d)
    assert paths["scope.FTR-001.priority"] == ("changed", "must", "nice")
    assert paths["scope.FTR-007"][0] == "added" and paths["scope.FTR-007"][1] is None
    assert paths["scope.FTR-006"][0] == "removed" and paths["scope.FTR-006"][2] is None
    assert paths["idea.summary"] == (
        "changed",
        "Order lunch boxes from home cooks",
        "Lunch boxes for offices",
    )
    assert paths["access.account_model"][0] == "removed"
    assert paths["constraints.offline"] == ("changed", "online_only", "full_offline")
    assert paths["constraints.offline.tier"] == ("changed", "Default", "Locked")
    assert paths["glossary.tiffin.synonyms"] == ("changed", ["dabba"], ["dabba", "tiffin box"])
    assert set(d.sections_changed) == {"idea", "scope", "access", "constraints", "glossary"}
    assert [e.path for e in d.entries] == sorted(
        (e.path for e in d.entries), key=lambda p: [e.path for e in d.entries].index(p)
    )  # stable order, no duplicates
    assert len({e.path for e in d.entries}) == len(d.entries)
    assert DiffResponse.model_validate(d.model_dump(mode="json")) == d


def test_diff_round_trip_apply_entries_reaches_the_target_values() -> None:
    a, b = rich_brief(version=1), rich_brief(version=2)
    b.scope[1].name = "Order tracking"
    b.context["geo"] = item({"city": "Mumbai", "country": "India"})
    d = diff_briefs(a, b, from_version=1, to_version=2)
    back = diff_briefs(b, a, from_version=2, to_version=1)
    forward = _by_path(d)
    for path, (kind, before, after) in _by_path(back).items():
        assert kind == "changed" and forward[path][1] == after and forward[path][2] == before


def test_summary_and_pitch_changes_are_entries() -> None:
    a, b = rich_brief(), rich_brief()
    b.summary = "A new one-liner"
    entries = _by_path(diff_briefs(a, b, from_version=1, to_version=2))
    assert entries["summary"][0] == "changed"


def test_provenance_for_dict_item_with_history_and_turn_ids() -> None:
    brief = rich_brief()
    brief.idea["summary"] = item(
        "Lunch boxes",
        history=[
            HistoryEntry(
                value="restaurants only", status="confirmed", turn_id=TURN_3, reason=HISTORY_QUOTE
            )
        ],
    )
    resp = provenance_for(brief, "idea.summary")
    assert isinstance(resp, ProvenanceResponse)
    assert resp.value == "Lunch boxes" and resp.tier == "Locked" and resp.status == "confirmed"
    assert resp.provenance[0].quote == QUOTE_1 and resp.provenance[0].type == "user_text"
    assert resp.provenance[0].ref == TURN_1 and resp.provenance[0].date is not None
    assert (
        resp.value_history[0]["value"] == "restaurants only"
        and resp.value_history[0]["turn_id"] == TURN_3
    )
    assert resp.turn_ids == [TURN_1, TURN_3]


def test_provenance_for_records_and_fields() -> None:
    brief = rich_brief()
    whole = provenance_for(brief, "scope.FTR-001")
    assert (
        whole is not None
        and isinstance(whole.value, dict)
        and whole.value["name"] == "Browse menus"
    )
    assert "provenance" not in whole.value and "history" not in whole.value
    field = provenance_for(brief, "scope.FTR-001.priority")
    assert field is not None and field.value == "must" and field.tier == "Locked"
    asm = provenance_for(brief, "assumptions.ASM-002")
    assert asm is not None and asm.item_id == "assumptions.ASM-002" and asm.tier == "Open"
    segments = provenance_for(brief, "audience.segments")
    assert (
        segments is not None
        and segments.provenance[0].quote == QUOTE_2
        and segments.turn_ids == [TURN_2]
    )
    by_name = provenance_for(brief, "glossary.tiffin")
    assert by_name is not None and by_name.value["term"] == "tiffin"  # type: ignore[index]


def test_source_refs_are_not_turn_ids() -> None:
    brief = rich_brief()
    resp = provenance_for(brief, "data.existing_systems")
    assert resp is not None and resp.provenance[0].ref == "SRC-001#p1" and resp.turn_ids == []
    assert turn_id_of("SRC-001#p3") is None
    assert turn_id_of(f"{TURN_1}#msg") == TURN_1


def test_unknown_paths_return_none() -> None:
    brief = rich_brief()
    for path in (
        "scope.FTR-999",
        "idea.nope",
        "scope.FTR-001.nonexistent",
        "",
        "bogus.thing",
        "scope",
    ):
        assert provenance_for(brief, path) is None
    assert provenance_for(brief, "experience.brand.name") is not None  # key with a dot


def test_provenance_response_round_trips_json() -> None:
    resp = provenance_for(rich_brief(), "scope.FTR-001")
    assert resp is not None
    assert ProvenanceResponse.model_validate(resp.model_dump(mode="json")) == resp
