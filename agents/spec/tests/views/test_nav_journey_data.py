"""Navigation (same reachability as V-04), journeys, data and needs views, pack views and diff."""

from __future__ import annotations

from mde_spec_agent import views as V
from mde_spec_core.graph import Screen
from mde_spec_core.validators import run_all
from mde_spec_core.validators.context import make_context


def v04(graph):
    f = run_all(make_context(graph), gate=3, only={"V-04"})
    return (
        sorted(x.where for x in f if x.severity == "blocker"),
        sorted(x.where for x in f if x.severity == "warning"),
    )


def planted(built):
    g = built.graph.model_copy(deep=True)
    app = sorted(g.apps)[0]
    g.add("screens", Screen(id="SCR-X001", name="Orphan page", app_id=app, screen_type="list"))
    g.add("screens", Screen(id="SCR-X002", name="Dead end page", app_id=app, screen_type="list"))
    first = next(
        s for s in sorted(g.screens.values(), key=lambda x: x.id) if s.app_id == app and s.leads_to
    )
    first.leads_to = [*first.leads_to, "SCR-X002"]
    return g


def test_nav_graph_finds_a_planted_orphan_and_a_planted_dead_end(quick):
    g = planted(quick)
    v = V.nav_graph(g)
    assert "SCR-X001" in v["unreachable"] and "SCR-X002" not in v["unreachable"]
    assert "SCR-X002" in v["dead_ends"]
    assert {n["id"] for n in v["nodes"]} >= {"SCR-X001", "SCR-X002"}


def test_nav_graph_agrees_with_validator_v04_on_every_export(built):
    for g in (built.graph, planted(built)):
        v = V.nav_graph(g)
        blockers, warnings = v04(g)
        assert v["unreachable"] == blockers
        assert v["dead_ends"] == warnings


def test_nav_graph_role_filter_and_later_nodes(thorough):
    g, d = thorough.graph, thorough.derived
    full = V.nav_graph(g, derived=d)
    assert any(n["in"] == "later" for n in full["nodes"])
    role = sorted(full["roles"])[0]
    one = V.nav_graph(g, role, derived=d)
    assert one["role"] == role and len(one["nodes"]) < len(full["nodes"])
    rid = next(r for r, x in g.roles.items() if x.name == role)
    assert V.nav_graph(g, rid, derived=d)["nodes"] == one["nodes"]
    ids = {n["id"] for n in one["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in one["edges"])


def test_journey_steps_follow_declared_exits(built):
    g = built.graph
    for fid, f in g.flows.items():
        j = V.journey_view(g, fid)
        assert j["title"] == f.name and j["steps"]
        for s in j["steps"]:
            declared = {x["to"] for x in s["exits"]}
            screen = g.screens[s["screen_id"]]
            assert declared <= set(screen.leads_to) | {
                st.target for a in screen.actions for st in a.effect if st.target
            }
        titles = {s["title"] for s in j["steps"]}
        assert all(e in titles for s in j["steps"] for e in s["entries"])


def test_journey_role_by_name_or_id_and_emotion_is_marked_assumed(quick):
    g = quick.graph
    fid = next(iter(g.flows))
    rid = g.flows[fid].role_ids[0]
    by_id = V.journey_view(g, fid, rid)
    by_name = V.journey_view(g, fid, g.roles[rid].name.upper())
    assert by_id == by_name and by_id["role"] == g.roles[rid].name
    assert by_id["emotion_line"] and all(1 <= e["level"] <= 5 for e in by_id["emotion_line"])
    assert "the spec's reading" in by_id["emotion_line"][0]["feeling"] and by_id["emotion_note"]
    assert V.journey_view(g, fid, None, "busy")["mock_profile"] == "busy"


def test_journey_unhappy_paths_are_split_into_what_and_recovery(quick):
    g = quick.graph.model_copy(deep=True)
    f = next(iter(g.flows.values()))
    f.unhappy_paths = ["payment fails -> the failed outcome and another attempt", "leave early"]
    f.moments_that_matter = [1]
    v = V.journey_view(g, f.id)
    assert v["unhappy_paths"][0] == {
        "what": "payment fails",
        "recovery": "the failed outcome and another attempt",
    }
    assert v["unhappy_paths"][1]["recovery"] == "" and v["moments_that_matter"][0].startswith(
        "Step 1:"
    )


def test_data_view_has_lifecycles_fixtures_and_integration_rules(built):
    v = V.data_view(built.graph, mock=built.mocks)
    with_life = [e for e in v["entities"] if e["lifecycle"]]
    assert with_life and all(" to " in t for e in with_life for t in e["transitions"])
    integ = [i for i in v["integrations"] if i["leaves_the_app"]]
    assert integ and all(i["if_interrupted"] and i["pending_timeout"] for i in integ)
    assert len(v["operations"]) == len(built.graph.operations)


def test_needs_view_opportunities_are_the_could_entries(quick):
    from mde_spec_core.graph import Could

    g = quick.graph.model_copy(deep=True)
    s = g.screens[sorted(g.screens)[0]]
    s.could = [
        Could(
            id="CP-1",
            kind="delight",
            when="booked",
            effect="a calm moment",
            basis="a journey moment",
            basis_kind="journey_moment",
            motion="state_change",
            tier="B",
        )
    ]
    v = V.needs_view(quick.pack, g)
    assert len(v["opportunities"]) == 1 and v["opportunities"][0]["needs"] == "tier B"
    assert any(m["screen"] == s.name for m in v["motion_intents"])
    assert len(v["stories"]) == len(g.stories) and len(v["needs"]) == len(quick.pack.needs)


def test_pack_views_agree_with_the_packager(built):
    tree = V.pack_tree(built.pack)
    assert {f["path"] for f in tree["files"]} == set(built.pack.files)
    totals = {t["id"]: t["read_tokens"] for t in tree["tasks"]}
    assert totals == {t["id"]: t["read_tokens"]["total"] for t in built.pack.tasks}
    b = V.budget_view(built.pack)
    report = built.pack.budget_report
    assert (
        b["total_tokens"] == report["total_tokens"]
        and b["tokens_by_kind"] == report["tokens_by_kind"]
    )
    assert b["tasks"] == report["tasks"] and b["max_task_read"] == report["max_task_read"]
    assert b["tasks_over_budget"] == report["tasks_over_budget"]
    assert b["bytes_by_format"][".md"] == report["bytes_by_format"][".md"] and b["tokens_by_kind"]
    waves = V.tasks_view(built.pack)["waves"]
    assert sum(len(w["tasks"]) for w in waves) == len(built.pack.tasks)
    assert [w["wave"] for w in waves] == sorted(w["wave"] for w in waves)


def test_diff_packs_by_hash(quick):
    from mde_spec_core.pack import assemble

    a = quick.pack
    assert V.diff_packs(a, a)["identical"]
    g = quick.graph.model_copy(deep=True)
    g.glossary["x"] = "y"
    next(iter(g.screens.values())).purpose = "A different purpose."
    b = assemble(g)
    d = V.diff_packs(b, a)
    status = {f["path"]: f["status"] for f in d["files"]}
    assert not d["identical"] and "changed" in status.values()
    assert status["global/glossary.md"] == "changed"
    first = sorted(g.screens)[0]
    assert status[f"screens/{first}.md"] == "changed"
    assert status["MANIFEST.json"] == "changed"
    only = {"files": {"a.md": "x"}, "manifest": {}}
    other = type("P", (), {"files": {"b.md": "y"}, "manifest": {}})()
    only_pack = type("P", (), only)()
    assert {f["path"]: f["status"] for f in V.diff_packs(only_pack, other)["files"]} == {
        "a.md": "added",
        "b.md": "removed",
    }


def test_checks_view_coverage_matrix_marks_gaps(quick):
    v = V.checks_view(quick.check)
    assert v["coverage"]["rows"] and set(v["coverage"]["cells"]) == set(v["coverage"]["rows"])
    cells = [c for row in v["coverage"]["cells"].values() for c in row.values()]
    assert all(c is False or isinstance(c, str) for c in cells)
    assert v["builder_needs_summary"] and v["counts"]["by_severity"]
