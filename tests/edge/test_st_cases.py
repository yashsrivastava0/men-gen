"""SPC-ST-01..06: stories and journeys (doc 17 section 11.3). Real exports through full offline runs, plus graph mutations."""

from __future__ import annotations

import json

import pytest
from edge_support.ea_support import cached_flow, codes, derive, raw, tiny_si, validate
from edge_support.registry import case

from mde_spec_core.graph import Acceptance, Story


def _std(tmp_path_factory):
    return cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))


def _thorough(tmp_path_factory):
    return cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))


@case("spc-st-01")
def test_no_personas_in_the_brief_are_derived_from_roles_and_marked_assumed(tmp_path_factory):
    f = cached_flow(tmp_path_factory, "sc-quick", lambda: raw("quick"))
    personas = list(f.graph.personas.values())
    assert personas and all(p.assumed and p.tier != "locked" for p in personas)
    roles = {r.id for r in f.graph.roles.values()}
    assert {p.role_id for p in personas} <= roles  # derived from the roles
    assert all(p.goal for p in personas)


@case("spc-st-02")
def test_stories_for_later_features_never_make_screens(tmp_path_factory):
    f = _std(tmp_path_factory)
    later = [x for x in f.graph.features.values() if x.status == "later"]
    assert [x.name for x in later] == ["Live delivery tracking for buyers"]
    assert (
        not [s for s in f.graph.screens.values() if later[0].id in s.provenance_refs()]
        if hasattr(f.graph.screens, "provenance_refs")
        else True
    )
    from mde_spec_core.validators.release import screen_features

    assert not [s for s in f.graph.screens.values() if later[0].id in screen_features(f.graph, s)]
    assert "V-19" not in {
        x.code for x in validate(f.graph, only={"V-19"}) if x.where == later[0].id
    }
    ledger = {e["id"]: e for e in json.loads(f.pack_files["global/scope-ledger.json"])}
    assert ledger[later[0].id]["status"] == "later" and ledger[later[0].id]["outline"]


@case("spc-st-02")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-ST-1: the scope ledger keeps an outline for the deferred feature but no backlog story (as a role, I want, so that); no story exists for any `later` feature",
)
def test_a_later_feature_has_a_backlog_story_in_the_ledger(tmp_path_factory):
    f = _std(tmp_path_factory)
    later = next(x for x in f.graph.features.values() if x.status == "later")
    entry = next(
        e for e in json.loads(f.pack_files["global/scope-ledger.json"]) if e["id"] == later.id
    )
    assert "story" in json.dumps(entry).lower() or any(
        later.id in s.feature_ids for s in f.graph.stories.values()
    )


@case("spc-st-03")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-ST-2: two roles that want opposite things are never detected: no conflict flag or confirm item exists for contradictory stories (the graph has no field for it and G2 has no such list)",
)
def test_two_roles_with_opposite_wants_are_flagged_for_confirm():
    from mde_spec_core.synth import make_graph

    g = make_graph("small").model_copy(deep=True)
    roles = list(g.roles)[:2]
    fid = next(iter(g.features))
    for i, (r, want) in enumerate(
        zip(roles, ("see every price", "hide every price"), strict=False)
    ):
        g.stories[f"US-X{i}"] = Story(
            id=f"US-X{i}",
            role_id=r,
            want=want,
            so_that="the other role cannot undermine them",
            feature_ids=[fid],
            acceptance=[Acceptance(given="a", when="b", then="c")],
        )
    text = json.dumps([f.as_dict() for f in validate(g)]).lower()
    assert "conflict" in text or "opposite" in text


@case("spc-st-04")
def test_a_cross_app_journey_has_both_apps_screens_and_one_data_timeline(tmp_path_factory):
    f = _thorough(tmp_path_factory)
    g = f.graph
    cross = [fl for fl in g.flows.values() if fl.variant == "cross_app"]
    assert cross
    for fl in cross:
        stage_screens = [s for st in fl.stages for s in st.screen_ids]
        apps = {g.screens[s].app_id for s in stage_screens}
        assert len(apps) >= 2 and set(fl.app_ids) == apps  # screens of both apps are on the journey
        assert fl.mock_profile in ("typical", "busy")  # one data timeline (a profile), not per app
    # one timeline: the shared records of the program exist once, with ids that carry no app
    assert g.hero and not [h for h in g.hero if "APP" in h]
    assert "V-44" not in codes(validate(g, only={"V-44"}), "blocker")


@case("spc-st-05")
def test_first_run_and_returning_journeys_use_two_data_profiles(tmp_path_factory):
    t = _thorough(tmp_path_factory).graph
    first = [fl for fl in t.flows.values() if fl.variant == "first_run"]
    back = [fl for fl in t.flows.values() if fl.variant == "returning"]
    assert first and back
    assert {fl.mock_profile for fl in first} == {"empty"}
    assert {fl.mock_profile for fl in back} <= {"typical", "busy"}
    assert len(first) == len(back)  # one of each per journey
    # the data profiles themselves exist in the mock plan
    from mde_spec_agent.views.screen import mock_profiles

    assert {"empty", "typical"} <= set(mock_profiles(t))


@case("spc-st-06")
def test_a_story_with_no_in_feature_behind_it_is_a_v40_blocker(tmp_path_factory):
    g = _std(tmp_path_factory).graph.model_copy(deep=True)
    later = next(x.id for x in g.features.values() if x.status == "later")
    role = next(iter(g.roles))
    before = {x.where for x in validate(g, only={"V-40"}) if x.severity == "blocker"}
    g.stories["US-GHOST"] = Story(
        id="US-GHOST",
        role_id=role,
        want="track the delivery live",
        so_that="I know when it arrives",
        feature_ids=[later],
        acceptance=[Acceptance(given="a", when="b", then="c")],
    )
    found = [
        x for x in validate(g, only={"V-40"}) if x.severity == "blocker" and x.where == "US-GHOST"
    ]
    assert found and "no in feature" in found[0].why and "US-GHOST" not in before
    assert "backlog story" in found[0].fix  # it can live only there, or as a suggestion
    d = derive(tiny_si())  # and a story with no feature at all is the same blocker
    d.graph.stories["US-NONE"] = Story(
        id="US-NONE", role_id=next(iter(d.graph.roles)), want="x", so_that="y"
    )
    assert "US-NONE" in {
        x.where for x in validate(d.graph, only={"V-40"}) if x.severity == "blocker"
    }
