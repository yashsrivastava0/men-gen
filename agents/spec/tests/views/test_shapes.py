"""Every view returns exactly the keys and types of docs/api/spec-agent.md ('Response shapes the Bench front end reads'),
on all three real exports; each shape has one check helper below."""

from __future__ import annotations

import json
from typing import Any

from mde_spec_agent import views as V


def is_str_list(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def check_preview(p: dict[str, Any]) -> None:
    assert set(p["profile"]) >= {"id", "label", "why"}
    assert all(isinstance(p["profile"][k], str) and p["profile"][k] for k in ("id", "label", "why"))
    assert isinstance(p["inventory_count"], int) and p["inventory_count"] > 0
    assert p["phases"] and all(
        {"name", "screens", "note"} <= set(ph) and isinstance(ph["screens"], int)
        for ph in p["phases"]
    )
    assert isinstance(p["estimate"]["low_micros"], int) and isinstance(
        p["estimate"]["high_micros"], int
    )
    assert 0 <= p["estimate"]["low_micros"] <= p["estimate"]["high_micros"]
    assert p["calls_by_step"] and all(
        isinstance(v, int) and v > 0 for v in p["calls_by_step"].values()
    )
    assert isinstance(p["refused"], bool)


def check_g15(v: dict[str, Any]) -> None:
    assert v["app_map"] and all(
        isinstance(a["name"], str) and isinstance(a["screens"], int) and is_str_list(a["modules"])
        for a in v["app_map"]
    )
    assert v["roles"] and all(
        isinstance(r["name"], str) and is_str_list(r["access"]) and isinstance(r["home"], str)
        for r in v["roles"]
    )
    assert v["entities"] and all(
        isinstance(e["name"], str) and isinstance(e["plain"], str) and is_str_list(e["fields"])
        for e in v["entities"]
    )
    assert v["inventory"] and all(
        {"id", "title", "module", "phase"} <= set(i) and all(isinstance(i[k], str) for k in i)
        for i in v["inventory"]
    )
    assert len(v["phases"]) == 2
    for ph in v["phases"]:
        assert (
            isinstance(ph["name"], str)
            and isinstance(ph["why"], str)
            and is_str_list(ph["pulled_in"])
        )
        assert (
            isinstance(ph["count"], int)
            and is_str_list(ph["items"])
            and len(ph["items"]) == ph["count"]
        )
    assert isinstance(v["pull_in_rule"], str) and v["pull_in_rule"]
    json.dumps(v)


def check_screen(v: dict[str, Any]) -> None:
    for k in ("id", "title", "purpose"):
        assert isinstance(v[k], str)
    for r in v["regions"]:
        assert set(r) >= {"name", "importance", "text", "items"}
        assert r["importance"] in {"must", "usually", "could"} and is_str_list(r["items"])
    for a in v["actions"]:
        assert {"label", "intent", "effect"} <= set(a) and all(
            isinstance(a[k], str) for k in ("label", "intent", "effect")
        )
    for s in v["states"]:
        assert isinstance(s["name"], str) and isinstance(s["text"], str)
    for c in v["copy"]:
        assert isinstance(c["key"], str) and isinstance(c["text"], str)
    assert is_str_list(v["hero_records"])


def check_g2(v: dict[str, Any]) -> None:
    assert isinstance(v["summary"]["text"], str) and v["summary"]["text"]
    assert isinstance(v["content_hash"], str) and len(v["content_hash"]) == 64
    assert v["app_map"] and v["content_maps"]
    for f in v["flows"]:
        assert isinstance(f["id"], str) and isinstance(f["title"], str)
        assert is_str_list(f["roles"]) and is_str_list(f["steps"])
    for m in v["content_maps"]:
        check_screen(m)
    for d in v["data"]:
        assert isinstance(d["entity"], str) and isinstance(d["plain"], str)
    for k in (
        "differences_from_brief",
        "assumptions",
        "degraded_nodes",
        "blocking_needs",
        "checked_by",
    ):
        assert is_str_list(v[k]), k
    assert len(v["confirm_list"]) <= 20
    for c in v["confirm_list"]:
        assert c["category"] in {"money", "legal", "identity", "other"}
        assert isinstance(c["text"], str) and isinstance(c["why"], str)
    for t in v["capability_tiers"]:
        assert t["tier"] in {"A", "B", "C"} and isinstance(t["capability"], str)
    for r in v["store_rules"]:
        assert isinstance(r["store"], str) and isinstance(r["plain"], str)
    for s in v["suggestions"]:
        assert isinstance(s["id"], str) and isinstance(s["text"], str) and isinstance(s["why"], str)
    for k in ("code_fixes", "flags"):
        assert isinstance(v[k], list)
    for r in v["risks"]:
        assert isinstance(r["text"], str) and r["severity"] in {"blocker", "warning", "info"}
    json.dumps(v)


def check_journey(v: dict[str, Any]) -> None:
    assert isinstance(v["title"], str) and isinstance(v["role"], str)
    assert is_str_list(v["stories"]) and is_str_list(v["moments_that_matter"])
    for e in v["emotion_line"]:
        assert (
            isinstance(e["label"], str)
            and isinstance(e["level"], int)
            and isinstance(e["feeling"], str)
        )
    for u in v["unhappy_paths"]:
        assert isinstance(u["what"], str) and isinstance(u["recovery"], str)
    for s in v["steps"]:
        assert (
            isinstance(s["screen_id"], str)
            and isinstance(s["title"], str)
            and isinstance(s["says"], str)
        )
        assert is_str_list(s["entries"])
        assert all({"label", "to"} <= set(x) for x in s["exits"])


def check_graph(v: dict[str, Any]) -> None:
    assert is_str_list(v["roles"]) and is_str_list(v["entries"])
    ids = {n["id"] for n in v["nodes"]}
    assert all(n["in"] is True or n["in"] == "later" for n in v["nodes"])
    assert all(
        e["from"] in ids and e["to"] in ids and isinstance(e["label"], str) for e in v["edges"]
    )
    assert set(v["unreachable"]) <= ids and set(v["dead_ends"]) <= ids


def check_data(v: dict[str, Any]) -> None:
    assert v["entities"] and all(
        isinstance(e["name"], str)
        and isinstance(e["plain"], str)
        and is_str_list(e["lifecycle"])
        and is_str_list(e["fields"])
        for e in v["entities"]
    )
    for k in ("operations", "fixtures", "mock_behaviours", "integrations"):
        assert isinstance(v[k], list) and all(isinstance(r, dict) for r in v[k]), k
    assert v["fixtures"] and v["mock_behaviours"]
    for i in v["integrations"]:
        assert {"name", "if_interrupted", "pending_timeout"} <= set(i)


def check_needs(v: dict[str, Any]) -> None:
    assert set(v) >= {"needs", "blocks", "assets", "motion_intents", "stories", "opportunities"}
    assert all(isinstance(v[k], list) and all(isinstance(r, dict) for r in v[k]) for k in v)
    assert v["needs"] and v["stories"] is not None


def check_pack(tree: dict, tasks: dict, budget: dict, checks: dict) -> None:
    assert tree["files"] and all(
        isinstance(f["path"], str) and isinstance(f["tokens"], int) for f in tree["files"]
    )
    assert all({"id", "title", "read_tokens", "budget"} <= set(t) for t in tree["tasks"])
    assert isinstance(tree["task_read_budget"], int)
    assert tasks["waves"] and all(
        isinstance(w["wave"], str) and all({"id", "title", "tokens"} <= set(t) for t in w["tasks"])
        for w in tasks["waves"]
    )
    assert isinstance(budget["total_tokens"], int) and isinstance(budget["tokens_by_kind"], dict)
    assert isinstance(budget["tokens_by_format"], dict) and budget["task_totals"]
    assert all({"code", "severity", "where", "why", "fix"} <= set(r) for r in checks["results"])
    assert checks["builder_needs"] and all(
        {"id", "status"} <= set(n) for n in checks["builder_needs"]
    )
    assert set(checks["coverage"]) >= {"rows", "columns", "cells"}


def test_every_view_has_its_documented_shape(built, writer, config):
    g, d = built.graph, built.derived
    check_preview(V.preview(d, d.decision, writer, config, 5_000_000))
    check_g15(V.g15(g, d))
    check_g2(V.g2(g, built.pack, built.check, d, rcode_rows=built.rcode_rows, mock=built.mocks))
    sids = sorted(g.screens)
    for sid in sids[:: max(1, len(sids) // 6)]:
        for prof in ("empty", "typical", "busy", "extreme", "broken", "offline"):
            check_screen(V.screen_view(g, sid, prof, mock=built.mocks))
    for fid in g.flows:
        check_journey(V.journey_view(g, fid))
    check_graph(V.nav_graph(g, derived=d))
    check_data(V.data_view(g, mock=built.mocks))
    check_needs(V.needs_view(built.pack, g))
    check_pack(
        V.pack_tree(built.pack),
        V.tasks_view(built.pack),
        V.budget_view(built.pack),
        V.checks_view(built.check),
    )


def test_check_report_and_its_json_give_the_same_views(built):
    from_dict = json.loads(built.check.to_json())
    assert V.checks_view(built.check) == V.checks_view(from_dict)
    g, d = built.graph, built.derived
    a = V.g2(g, built.pack, built.check, d, rcode_rows=built.rcode_rows, mock=built.mocks)
    b = V.g2(g, built.pack, from_dict, d, mock=built.mocks)
    assert a["content_hash"] == b["content_hash"] and a["blocking_needs"] == b["blocking_needs"]
    assert a["confirm_list"] == b["confirm_list"] and a["risks"] == b["risks"]


def test_views_work_without_a_derived_plan(built, writer, config):
    g = built.graph
    check_g15(V.g15(g, None))
    check_g2(V.g2(g, built.pack, built.check, None, mock=built.mocks))
    check_preview(V.preview(g, None, writer, config, None))
    check_graph(V.nav_graph(g))


def test_g15_phase_split_matches_the_release_plan(built):
    v = V.g15(built.graph, built.derived)
    plan = built.derived.plan
    assert v["phases"][0]["count"] == len(plan.release_screens)
    assert v["phases"][1]["count"] == len(plan.deferred_screens)
    assert len(v["phases"][0]["pulled_in"]) == len(plan.pulled_in)
    assert len(v["inventory"]) == len(built.derived.inventory.screens)
    assert v["consent_required"] == plan.consent_required


def test_thorough_g15_shows_the_pull_in_and_the_hard_gate(thorough):
    v = V.g15(thorough.graph, thorough.derived)
    assert v["consent_required"] and v["needs_consent"]
    assert v["phases"][0]["pulled_in"], "the Dispatch module must be pulled into Phase 1"
    assert (
        v["compass_core"]["added_by_spec"] >= 0 and "Compass marked" in v["compass_core"]["plain"]
    )
    assert v["everything_in_one_release"]["available"] is True
