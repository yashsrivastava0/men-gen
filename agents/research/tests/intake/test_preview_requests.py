"""The zero-cost preview says which topic carries each Brief request, what is not run and what was dropped, with a reason."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from mde_research_agent.catalog import load_catalog
from mde_research_agent.inputs.estimates import Estimates
from mde_research_agent.inputs.preview import build_preview, request_rows
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan

ROOT = Path(__file__).resolve().parents[4]
INPUTS = ROOT / "docs" / "compass" / "research-inputs"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAT = load_catalog()
EST = Estimates()


def preview_of(data: dict[str, Any]) -> dict[str, Any]:
    return build_preview(data, read_export(data, CAT), CAT, EST)


def raw(stem: str) -> dict[str, Any]:
    return json.loads((INPUTS / f"{stem}.json").read_text(encoding="utf-8"))


def by_id(preview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in preview["requests"]["items"]}


def test_each_request_names_its_topic_and_the_counts_keep_their_old_keys() -> None:
    pv = preview_of(raw("synthetic-medium-school-parents-dakar"))
    req = pv["requests"]
    assert {"trusted", "template", "thin"} <= set(req)  # the console already reads these three
    item = by_id(pv)["RR-001"]
    assert item["status"] == "mapped" and item["reason"] == "mapped" and item["text"]
    (topic,) = item["topics"]
    assert topic["job"] == "features" and topic["label"] and topic["id"].startswith("T-")
    assert req["mapped"] + req["not_run"] + req["dropped"] + req["unmapped"] == req["trusted"]


def test_a_request_whose_topic_is_planned_but_ranked_too_low_is_not_run_with_that_topic_named() -> (
    None
):
    # Since the planner keeps every risk topic outside the cap, a request that is not run has to be built by moving its topic
    # behind the cap by hand: the row logic (request_rows) is what this test is about.
    data = raw("synthetic-medium-school-parents-dakar")
    inp = read_export(data, CAT)
    plan = build_plan(inp, CAT)
    carried = next(t for t in plan.targets if t.links.requests)
    moved = plan.model_copy(
        update={
            "targets": tuple(t for t in plan.targets if t is not carried),
            "unrun": (carried.model_copy(update={"id": "U-01"}), *plan.unrun),
        }
    )
    rows = request_rows(inp, moved)
    waiting = [i for i in rows if i["status"] == "not_run"]
    assert waiting and all(i["topics"][0]["id"].startswith("U-") for i in waiting)
    assert all("ranked below" in i["text"] for i in waiting)


def test_a_request_with_no_topic_at_all_is_unmapped_with_a_plain_reason() -> None:
    pv = preview_of(raw("synthetic-medium-school-parents-dakar"))
    gone = [i for i in pv["requests"]["items"] if i["status"] == "unmapped"]
    assert gone and all(i["topics"] == [] and "No planned topic fits" in i["text"] for i in gone)


def test_filler_requests_are_ignored_with_the_reason_not_counted_as_unmapped() -> None:
    data = json.loads(
        (FIXTURES / "exports" / "quick-yoga-studio-bengaluru.json").read_text(encoding="utf-8")
    )
    pv = preview_of(data)
    ignored = [i for i in pv["requests"]["items"] if i["status"] == "ignored"]
    assert len(ignored) == pv["requests"]["template"] + pv["requests"]["thin"] >= 4
    assert all(i["reason"] in ("template", "thin") and i["text"] for i in ignored)
    assert pv["requests"]["unmapped"] == 0


def test_a_request_whose_topic_the_person_declined_is_dropped_with_the_reason() -> None:
    data = raw("synthetic-small-bike-repair-porto")
    data["brief"]["declined"] = [
        {"what": "online payments", "reason": "we know it", "alternative": ""}
    ]
    data["consumer_views"]["research"]["declined"] = data["brief"]["declined"]
    pv = preview_of(data)
    assert (
        by_id(pv)["RR-002"]["status"] == "dropped" and by_id(pv)["RR-002"]["reason"] == "declined"
    )
    assert "do not want this researched" in by_id(pv)["RR-002"]["text"]
    assert pv["requests"]["dropped"] == 1
    assert pv["dropped"] and all(d["text"] and d["reason"] and d["label"] for d in pv["dropped"])
    assert any(d["requests"] == ["RR-002"] for d in pv["dropped"])


def test_not_run_topics_list_a_rank_a_job_and_a_one_line_reason() -> None:
    pv = preview_of(raw("thorough-freight-fleet-india"))
    rows = pv["not_run"]
    assert rows and len(rows) == len(
        pv["topics"]["not_run"]
    )  # the old label list is kept next to it
    first, last = rows[0], rows[-1]
    total = pv["topics"]["total"] + len(rows)
    cap = pv["topics"]["cap"]
    assert first["text"].startswith(
        f"Ranked {pv['topics']['total'] + 1} of {total} planned topics; this size runs the top {cap}"
    )
    assert "never left out" in first["text"]  # the risk topics run outside the cap
    cost = CAT.plan.topic_cost
    assert all(
        r["est_searches"] == cost.searches and r["est_usd_micros"] == cost.usd_micros for r in rows
    )
    assert pv["not_run_cost"] == {
        "topics": len(rows),
        "est_searches": round(len(rows) * cost.searches, 1),
        "est_usd_micros": len(rows) * cost.usd_micros,
    }
    assert last["text"].startswith(f"Ranked {total} of {total}")
    assert all(
        r["id"].startswith("U-") and r["job"] and r["label"] and r["reason"] == "over_cap"
        for r in rows
    )
    assert all("\n" not in r["text"] for r in rows)


def test_an_unverified_seal_is_shown_as_a_warning_in_plain_words() -> None:
    data = raw("synthetic-small-bike-repair-porto")
    assert preview_of(data)["input_binding"] == "verified"
    assert not [w for w in preview_of(data)["warnings"] if w["code"].startswith("unverified")]
    for mutate, state, words in (
        (
            lambda d: d["export"].pop("approved_baseline"),
            "unverified_no_baseline",
            "no approval record",
        ),
        (lambda d: d["brief"].update(_hash="0" * 64), "unverified_hash_mismatch", "does not match"),
    ):
        changed = copy.deepcopy(data)
        mutate(changed)
        pv = preview_of(changed)
        assert pv["input_binding"] == state
        (warning,) = [w for w in pv["warnings"] if w["code"] == state]
        assert words in warning["text"] and "researched as it is" in warning["text"]


def test_the_places_list_every_country_the_brief_names() -> None:
    pv = preview_of(raw("synthetic-big-site-safety-central-america"))
    assert [c["name"] for c in pv["place"]["countries"]] == ["Mexico", "Guatemala", "Costa Rica"]
    assert pv["place"]["country"] == "Mexico"
    single = preview_of(raw("synthetic-small-bike-repair-porto"))
    assert single["place"]["countries"] == [{"name": "Portugal", "code": "pt"}]
