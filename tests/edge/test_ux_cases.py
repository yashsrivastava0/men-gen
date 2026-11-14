"""SPC-UX (doc 15 12.8): the gates. The real HTTP API over the real service drives the consent flow and the G2 page; the
views are pure functions of the graph and are also called directly where a graph needs a specific shape."""

from __future__ import annotations

import json
import re

import pytest
from edge_support import case
from edge_support.eb_run import Rig, api_client, load_input, wait_for, wrap_gateway

from mde_spec_agent.views import g2 as g2_view
from mde_spec_core.graph import Feature, Rule
from mde_spec_core.synth import make_graph

THOROUGH = "thorough-freight-fleet-india"
ID_WORD = re.compile(r"\b(?:ENT|SCR|FLW|ROL|US|RUL|FTR|OP|MOD|APP|INT|PER|DIR)-S?\d{3}\b")


@pytest.fixture(scope="module")
def thorough_run(tmp_path_factory):
    """One finished thorough run (deep: G1.5 then G2) read through the API: (run id, g2 view, g15 view, graph)."""
    mp = pytest.MonkeyPatch()
    try:
        with api_client(tmp_path_factory.mktemp("ux"), mp) as client:
            rid = client.post("/api/runs", json={"compass_id": THOROUGH, "offline": True}).json()[
                "run_id"
            ]
            wait_for(client, rid, ("paused_g15",))
            g15 = client.get(f"/api/runs/{rid}/views/g15").json()
            client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"})
            wait_for(client, rid, ("awaiting_g2",))
            view = client.get(f"/api/runs/{rid}/views/g2").json()
            yield rid, view, g15, client.service.load(rid).graph
    finally:
        mp.undo()


# --------------------------------------------------------------------------------------------------- UX-01
@case("spc-ux-01")
def test_ux_01_everything_in_one_release_is_recorded_with_its_consent_and_the_page_says_so(
    tmp_path, monkeypatch
):
    with api_client(tmp_path, monkeypatch) as client:
        rid = client.post("/api/runs", json={"compass_id": THOROUGH, "offline": True}).json()[
            "run_id"
        ]
        snap = wait_for(client, rid, ("paused_g15",))
        assert snap["gates"]["g15"] == "open"
        ok = client.post(
            f"/api/runs/{rid}/g15", json={"decision": "approve", "everything_in_one_release": True}
        )
        assert ok.status_code == 200
        done = wait_for(client, rid, ("awaiting_g2",))
        rec = done["gate_records"]["g15"]
        assert (
            rec["decision"] == "approved" and rec["everything_in_one_release"] is True
        )  # recorded
        assert rec["consents"]  # with what the person consented to
        graph = client.service.load(rid).graph
        assert not [
            s for s in graph.screens.values() if s.status == "later"
        ]  # everything in one release
        view = client.get(f"/api/runs/{rid}/views/g2").json()
        assert any(
            "your consent is recorded" in r["text"] for r in view["risks"]
        )  # with a plain warning


@case("spc-ux-01")
def test_ux_01_the_user_can_choose_which_screens_are_deferred(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        rid = client.post("/api/runs", json={"compass_id": THOROUGH, "offline": True}).json()[
            "run_id"
        ]
        wait_for(client, rid, ("paused_g15",))
        graph = client.service.load(rid).graph
        deferred_before = {f for f, v in graph.features.items() if v.later_reason == "user"}
        moved = sorted(s.id for s in graph.screens.values() if s.status == "in" and not s.system)[
            -1
        ]
        res = client.post(
            f"/api/runs/{rid}/g15",
            json={"decision": "change", "moves": [{"item": moved, "to": "Phase 2 (later)"}]},
        ).json()
        assert res["recorded"] is True and res["moves"] == 1  # recorded ...
        assert (
            res["applied"] is True
        )  # ... applied: the split is derived again, the gate opens once more
        wait_for(client, rid, ("paused_g15",))
        client.post(f"/api/runs/{rid}/g15", json={"decision": "approve"})
        wait_for(client, rid, ("awaiting_g2",))
        after = client.service.load(rid).graph
        # ... and applied: the screen is out of the first release (a later feature keeps an outline and no screen
        # node, V-19) and the feature behind it is the person's own deferral
        assert moved not in after.screens
        deferred_after = {f for f, v in after.features.items() if v.later_reason == "user"}
        assert deferred_after - deferred_before


# --------------------------------------------------------------------------------------------------- UX-02
@case("spc-ux-02")
def test_ux_02_g2_reads_summary_first_with_a_ranked_top_20_and_content_maps(thorough_run):
    _rid, view, _g15, graph = thorough_run
    keys = list(view)
    assert keys[0] == "summary" and view["summary"]["text"].strip()  # the summary comes first
    assert (
        keys.index("differences_from_brief")
        < keys.index("confirm_list")
        < keys.index("degraded_nodes")
    )
    assert view["differences_from_brief"]  # the diff against the Brief
    live = [s for s in graph.screens.values() if s.status == "in"]
    assert len(view["content_maps"]) == len(live)  # a content map for every screen
    ranks = [x["rank"] for x in view["confirm_list"]]
    assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) <= 20


@case("spc-ux-02")
def test_ux_02_the_confirm_list_is_ranked_money_then_legal_then_identity_and_capped_at_20():
    g = make_graph("small")
    texts = (
        [("price of the plan", "money")] * 8
        + [("consent to the terms", "legal")] * 8
        + [("login password", "identity")] * 8
        + [("the colour of a thing", "other")] * 8
    )
    for i, (t, _) in enumerate(
        reversed(texts), start=1
    ):  # inserted in the opposite order of their rank
        g.add(
            "rules", Rule(id=f"RUL-{900 + i}", subject=t, statement=f"{t} number {i}", confirm=True)
        )
    rows = g2_view(g, None, None, None)["confirm_list"]
    assert len(rows) == 20 and [r["rank"] for r in rows] == list(range(1, 21))
    cats = [r["category"] for r in rows]
    assert cats == sorted(
        cats, key=["money", "legal", "identity", "other"].index
    )  # money first, other last
    assert cats[0] == "money" and cats.count("money") >= 8


# --------------------------------------------------------------------------------------------------- UX-03
@case("spc-ux-03")
def test_ux_03_a_non_technical_reader_gets_plain_words_no_json_and_store_rules_explained(
    thorough_run,
):
    _rid, view, g15, _g = thorough_run
    prose: list[str] = [
        view["summary"]["text"],
        *view["differences_from_brief"],
        *view["assumptions"],
        *view["degraded_nodes"],
    ]
    prose += [r["text"] for r in view["risks"]]
    prose += [c["text"] + " " + c["why"] for c in view["confirm_list"]]
    prose += [m for f in view["flows"] for m in [str(f.get("name", ""))]]
    prose += list(g15["notes"])
    for line in prose:
        assert not ID_WORD.search(line), line  # ids are shown with names or not at all
        assert "{" not in line and "[{" not in line, line  # no JSON on the page
        assert not re.search(r"\bV-\d\d\b", line), line  # no validator codes in the sentences
    rules = view["store_rules"]
    assert rules and all(
        r["plain"].strip() and r["store"] and r["source"].startswith("http") for r in rules
    )
    assert all(
        "{" not in r["plain"] for r in rules
    )  # the store rule is explained, not quoted as data
    assert all(t["what_it_means"].strip() for t in view["capability_tiers"])


# --------------------------------------------------------------------------------------------------- UX-04
@case("spc-ux-04")
def test_ux_04_a_users_deferral_wins_over_a_model_proposal_and_the_choice_is_recorded(tmp_path):
    from mde_spec_agent.lanes.product.plan.schema import ReleaseRec

    si = load_input("standard")
    feats = list(si.features)
    k = next(i for i, f in enumerate(feats) if f.scope_status == "in" and not f.deferred_by_user)
    feats[k] = feats[k].model_copy(update={"deferred_by_user": True, "scope_status": "later"})
    fid = feats[k].id
    rig = Rig(tmp_path, "standard", spec_input=si.model_copy(update={"features": tuple(feats)}))
    gw = wrap_gateway(rig.ctx)

    def relabel(out):  # the model (a Research default) wants the feature back in
        out = out.model_copy(deep=True)
        out.release = [
            ReleaseRec(
                feature=fid,
                status="in",
                reason="none",
                why="research says buyers expect it",
                prov=["assumed:x"],
            )
            if r.feature == fid
            else r
            for r in out.release
        ]
        return out

    gw.rewrite = lambda spec, inp, n: relabel if spec == "spec.plan" else None
    out = rig.go("plan")
    assert out.status == "partial" and out.reason == "plan_invalid"  # refused: the user wins
    errors = [e for c in rig.calls("spec.plan") for e in c.errors]
    assert any("never relabel a user deferral" in e for e in errors) or gw.count("spec.plan") == 2
    rig2 = Rig(
        tmp_path, "standard", spec_input=si.model_copy(update={"features": tuple(feats)}), tag="ok"
    )
    assert rig2.go("plan").status == "awaiting_g2"
    assert (
        rig2.ctx.graph.features[fid].status == "later"
        and rig2.ctx.graph.features[fid].later_reason == "user"
    )


@case("spc-ux-04")
def test_ux_04_the_override_the_person_chooses_at_g2_is_recorded_with_the_approval(
    tmp_path, monkeypatch
):
    with api_client(tmp_path, monkeypatch) as client:
        rid = client.post("/api/runs", json={"compass_id": "synth:small", "offline": True}).json()[
            "run_id"
        ]
        snap = wait_for(client, rid, ("awaiting_g2",))
        ok = client.post(
            f"/api/runs/{rid}/g2",
            json={
                "decision": "approve",
                "content_hash": snap["content_hash"],
                "suggestion_choices": {"peer-1": True, "could-2": False},
            },
        )
        assert ok.status_code == 200
        rec = client.get(f"/api/runs/{rid}").json()["gate_records"]["g2"]
        assert rec["content_hash"] == snap["content_hash"] and rec["suggestion_choices"] == {
            "peer-1": True,
            "could-2": False,
        }
        assert json.dumps(rec)  # a plain record, bound to the approved hash


# --------------------------------------------------------------------------------------------------- UX-05
@case("spc-ux-05")
def test_ux_05_the_structure_view_appears_before_any_screen_call_is_made(tmp_path):
    from mde_spec_agent.run.orchestrator import Orchestrator
    from mde_spec_agent.run.pipeline import build_pipeline

    rig = Rig(tmp_path, "thorough")
    out = __import__("asyncio").run(Orchestrator(rig.store, build_pipeline()).run(rig.ctx))
    assert (
        out.status == "paused" and out.stage == "g15"
    )  # the gate opens as soon as skeleton and inventory exist
    asked = {c.spec_id for c in rig.provider.calls}
    assert asked and asked <= {
        "spec.plan",
        "spec.prod_module",
        "spec.prod_journeys",
        "spec.prod_integ",
        "spec.dsgn",
    }  # nothing of Screens, hero, behaviours, locales or the summary has been asked yet
    from mde_spec_agent.views import g15

    view = g15(rig.ctx.graph, rig.ctx.derived)
    assert view["inventory"] and view["phases"] and view["app_map"]
    assert not rig.ctx.graph.hero or all(not h.filled for h in rig.ctx.graph.hero.values())


@case("spc-ux-05")
def test_ux_05_the_zero_cost_preview_exists_the_moment_a_run_starts(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        rid = client.post("/api/runs", json={"compass_id": THOROUGH, "offline": True}).json()[
            "run_id"
        ]
        pv = client.get(
            f"/api/runs/{rid}/preview"
        )  # asked at once, before the run has finished anything
        assert pv.status_code == 200 and pv.json()["inventory_count"] > 0
        wait_for(client, rid, ("paused_g15",))


# --------------------------------------------------------------------------------------------------- UX-06
@case("spc-ux-06")
def test_ux_06_a_tier_c_feature_is_said_plainly_at_g2_and_never_hidden():
    g = make_graph("small")
    g.add(
        "features",
        Feature(
            id="FTR-802",
            name="Live engine",
            capability="real_time_game_engine",
            capability_tier="C",
            status="later",
            later_reason="user",
        ),
    )
    view = g2_view(g, None, None, None)
    assert any(
        "Live engine cannot be built as asked (tier C)" in c["text"] for c in view["confirm_list"]
    )
    g.features["FTR-802"].status = "in"  # in the release it is a blocker and the page says so
    view_in = g2_view(g, None, None, None)
    assert any(r["severity"] == "blocker" for r in view_in["risks"])
    assert any(t["tier"] == "C" for t in view_in["capability_tiers"])  # and the tier is on the page


@case("spc-ux-06")
def test_ux_06_a_requested_nfc_feature_is_flagged_at_g15_and_g2():
    from mde_spec_agent.derive import derive_graph
    from mde_spec_agent.views import g15

    si = load_input("standard")
    nfc = si.features[0].model_copy(
        update={
            "id": "FTR-777",
            "name": "Tap an NFC card to check in",
            "intent": "Buyers tap an NFC card at the gate",
            "priority": "must",
        }
    )
    d = derive_graph(si.model_copy(update={"features": (*si.features, nfc)}))
    feature = d.graph.features["FTR-777"]
    assert feature.capability == "nfc" and feature.capability_tier == "B"
    assert "NFC" in json.dumps(g15(d.graph, d)) or "NFC" in json.dumps(
        g2_view(d.graph, None, None, d)["capability_tiers"]
    )
