"""``read_research``: a research.v5 dict into a small typed ``ResearchView``; missing, partial or failed research is a
thin view, never an exception (doc 15 section 3.5)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import ResearchView, pack, read_research

RESEARCH_OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "research-agent"
    / "outputs"
    / "claude-2026-11-13"
    / "medium-produce-marketplace-nashik"
    / "research.json"
)


@pytest.fixture(scope="module")
def research() -> dict[str, Any]:
    if not RESEARCH_OUTPUT.exists():
        pytest.skip("the recorded research output is not in this checkout")
    return json.loads(RESEARCH_OUTPUT.read_text(encoding="utf-8"))


def test_a_real_output_reads_into_a_complete_view(research: dict[str, Any]) -> None:
    view = read_research(research)
    assert not view.thin and view.thin_reasons == () and view.status == "complete"
    assert view.schema_name == "research.v5" and view.is_for(research["brief_hash"])
    assert (
        len(view.items),
        len(view.lane_decisions),
        len(view.dossiers),
        len(view.platform_constraints),
        len(view.compliance_register),
        len(view.confirm),
        len(view.conflicts),
        len(view.norms),
    ) == (361, 23, 19, 18, 4, 209, 4, 119)
    assert set(view.slices) == set(research["slices"]) and view.profile["load_class"] == "standard"
    assert view.profile["load_score"] == 27


def test_items_carry_evidence_applies_to_and_source_ids(research: dict[str, Any]) -> None:
    view = read_research(research)
    item = view.items[0]
    assert item.id == "RI-001" and item.kind == "competitor" and item.evidence
    assert item.applies_to[0].kind == "NEW" and item.applies_to[0].id is None
    assert item.source_ids and all(s.startswith("S-") for s in item.source_ids)
    assert {i.evidence for i in view.items} <= {
        "official",
        "corroborated",
        "single_source",
        "unverified",
    }


def test_dossier_fields_keep_the_explicit_null_reason(research: dict[str, Any]) -> None:
    dossier = read_research(research).dossiers[0]
    assert dossier.provider and dossier.fields
    unknown = [f for f in dossier.fields.values() if f.value is None]
    assert unknown and all(f.null_reason for f in unknown), (
        "a null is 'not stated', and it says why"
    )


def test_confirm_and_conflicts_and_lane_decisions_are_typed(research: dict[str, Any]) -> None:
    view = read_research(research)
    assert (
        view.confirm[0].item_id == "RI-153"
        and view.confirm[0].kind == "money"
        and view.confirm[0].top
    )
    assert view.conflicts[0].item_ids == ("RI-153", "RI-163", "RI-164")
    first = view.lane_decisions[0]
    assert first.id == "BP-FEATURES" and first.lane == "blueprint" and first.item_ids


def test_a_view_for_another_brief_is_not_for_this_one(research: dict[str, Any]) -> None:
    view = read_research(research)
    assert not view.is_for("0" * 64) and not ResearchView().is_for("")


@pytest.mark.parametrize("junk", [None, 5, "text", [], {}, [1, 2]], ids=repr)
def test_anything_that_is_not_a_research_file_is_a_thin_view(junk: Any) -> None:
    view = read_research(junk)
    assert view.thin and view.thin_reasons and view.items == () and view.dossiers == ()


@pytest.mark.parametrize(
    "status", ["partial", "failed", "skipped", ""], ids=lambda s: s or "no status"
)
def test_partial_or_failed_research_is_thin_but_keeps_what_it_has(
    research: dict[str, Any], status: str
) -> None:
    data = copy.deepcopy(research)
    data["status"] = status
    view = read_research(data)
    assert view.thin and "research status is" in view.thin_reasons[0]
    assert len(view.items) == 361, (
        "a partial result is still read; the caller treats its lanes as thin"
    )


def test_research_with_no_items_is_thin() -> None:
    view = read_research({"schema": "research.v5", "status": "complete", "items": []})
    assert view.thin and view.thin_reasons == ("research has no items",)


@pytest.mark.parametrize("wrong", [None, 5, "text", {}, [1, "a", None], {"x": 1}], ids=repr)
def test_no_section_of_the_file_can_crash_the_reader(research: dict[str, Any], wrong: Any) -> None:
    for key in list(research):
        data = {**research, key: copy.deepcopy(wrong)}
        view = read_research(data)
        assert isinstance(view, ResearchView), key


def test_a_stray_record_among_good_ones_is_skipped(research: dict[str, Any]) -> None:
    data = copy.deepcopy(research)
    data["items"] = ["stray", None, 7, {"no": "id"}, *data["items"]]
    data["dossiers"] = [None, *data["dossiers"]]
    view = read_research(data)
    assert len(view.items) == 361 and len(view.dossiers) == 19


def test_a_lane_with_no_items_or_a_decision_with_a_gap_is_a_thin_lane() -> None:
    data = {
        "status": "complete",
        "items": [{"id": "RI-001"}],
        "slices": {"blueprint": {"item_ids": ["RI-001"]}, "screens": {"item_ids": []}},
        "lane_decisions": [{"id": "D-1", "lane": "design", "gap": "no source found"}],
    }
    assert read_research(data).thin_lanes == ("design", "screens")


def test_pack_is_re_exported_and_works_on_the_raw_file(research: dict[str, Any]) -> None:
    out = pack(research, {"brief_ids": ["FTR-001"]})
    assert out["schema"] == "research.pack.v1" and out["brief_hash"] == research["brief_hash"]
