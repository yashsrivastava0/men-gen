"""SPC-DG-01..06: design and motion (doc 17 section 11.4): direction ambition, reduced motion, COULD tiers and caps."""

from __future__ import annotations

import json

import pytest
from edge_support.ea_support import cached_flow, codes, raw, validate
from edge_support.registry import case

from mde_spec_agent.lanes.screens.scrn.step import trim_could
from mde_spec_core.graph import Could
from mde_spec_core.synth import make_graph


def _std(tmp_path_factory):
    return cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))


def _could(i: int = 0, **kw):
    base = dict(
        id=f"COULD-{i}",
        kind="delight",
        when="the order is placed",
        effect="a small moment of confirmation",
        basis="FLW-001 step 2",
        basis_kind="journey_moment",
        assumed=True,
    )
    base.update(kw)
    return Could(**base)


def _small():
    return make_graph("small").model_copy(deep=True)


@case("spc-dg-01")
def test_a_dense_enterprise_app_leads_with_a_refined_direction_and_the_front_end_may_exceed(
    tmp_path_factory,
):
    thorough = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    first = sorted(thorough.graph.directions.values(), key=lambda d: d.id)[0]
    assert first.ambition == "refined"
    standard_first = sorted(_std(tmp_path_factory).graph.directions.values(), key=lambda d: d.id)[0]
    assert (
        standard_first.ambition != "refined"
    )  # the dense enterprise app is the one that is steered to restraint
    # ambition is Open guidance: nothing locks it, so the front end may exceed it
    assert all(d.tier == "open" for d in thorough.graph.directions.values())
    assert "refined" in thorough.pack_files["global/design-direction.md"]


@case("spc-dg-02")
def test_essential_motion_must_have_a_non_motion_alternative():
    g = _small()
    screen = next(iter(g.screens.values()))
    screen.could = [_could(0, motion="confirm_success")]  # essential, no alternative
    bad = [f for f in validate(g, only={"V-42"}) if f.severity == "blocker"]
    assert bad and "non-motion alternative" in bad[0].why
    screen.could = [
        _could(
            0, motion="confirm_success", reduced_motion_alt="The check mark appears without moving."
        )
    ]
    assert not [f for f in validate(g, only={"V-42"}) if f.severity == "blocker"]
    # a decorative intent needs none
    screen.could = [_could(0, motion="reward")]
    assert not [f for f in validate(g, only={"V-42"}) if f.severity == "blocker"]


@case("spc-dg-02")
def test_every_essential_motion_of_a_real_run_has_its_alternative(tmp_path_factory):
    from mde_spec_core import vocab

    f = _std(tmp_path_factory)
    essential = [
        c
        for s in f.graph.screens.values()
        for c in s.could
        if c.motion and vocab.is_essential_motion(c.motion)
    ]
    assert essential and all(c.reduced_motion_alt for c in essential)
    tokens = json.loads(f.pack_files["global/tokens.dtcg.json"])
    assert "motion.duration.reduced" in " ".join(next(iter(tokens.values()))["names"])


@case("spc-dg-03")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-DG-1: there is no `light` motion class: motion personalities are calm, springy, cinematic, playful, minimal; no device signal (low-end Android) is read and no Default floor of lighter motion is derived",
)
def test_low_end_devices_get_a_light_motion_class_as_a_default_floor(tmp_path_factory):
    f = _std(tmp_path_factory)
    personalities = {d.motion_personality for d in f.graph.directions.values()}
    assert "light" in personalities or "light" in f.pack_files["global/design-direction.md"].lower()


@case("spc-dg-04")
def test_a_could_that_needs_tier_b_is_flagged_with_its_tier_and_stays_optional(tmp_path_factory):
    f = _std(tmp_path_factory)
    assert f.flags and f.flags[0]["kind"] == "capability_risk" and f.flags[0]["tier"] == "B"
    assert "direction DIR-S003" in " ".join(f.flags[0]["implied_by"])
    g2 = f.g2()
    assert any(row["what"] == "capability_risk" for row in g2["flags"])  # shown to the owner at G2
    # COULD is outside the approved hash: toggling it changes nothing that G2 binds
    before = f.graph.content_hash()
    g = f.graph.model_copy(deep=True)
    for s in g.screens.values():
        s.could = [c.model_copy(update={"tier": "B"}) for c in s.could]
    assert g.content_hash() == before


@case("spc-dg-04")
def test_a_tier_c_could_is_flagged_not_dropped():
    g = _small()
    sid = next(iter(g.screens))
    patch = {
        "tables": {
            "screens": {
                sid: {
                    "app_id": g.screens[sid].app_id,
                    "could": [
                        _could(0, tier="C").model_dump(),
                        _could(1, tier="A").model_dump(),
                    ],
                }
            }
        }
    }
    trim_could([patch], g, {"compact": 5, "standard": 12, "deep": 30})
    kept = patch["tables"]["screens"][sid]["could"]
    assert {c["tier"] for c in kept} == {"A", "C"}


@case("spc-dg-05")
def test_more_could_entries_than_the_cap_is_a_v42_error_and_is_trimmed_by_criticality():
    g = _small()
    cap = 5  # compact, per app
    sid = next(iter(g.screens))
    g.screens[sid].could = [_could(i) for i in range(4)]  # cap per screen is 3
    errors = [f for f in validate(g, only={"V-42"}) if f.severity == "blocker"]
    assert errors and "COULD entries (cap 3)" in errors[0].why and "criticality" in errors[0].fix
    # the trimming itself: signature moments of the primary journey survive, the rest go
    flow = next(f for f in g.flows.values() if f.primary)
    moment = flow.moments_that_matter[0]
    entries = [_could(0, basis=f"{flow.id} step {moment}").model_dump()]
    entries += [_could(i, basis=f"{flow.id} step 99").model_dump() for i in range(1, 4)]
    entries += [
        _could(i, basis_kind="library_pattern", basis="a pattern").model_dump() for i in range(4, 9)
    ]
    patch = {"tables": {"screens": {sid: {"app_id": g.screens[sid].app_id, "could": entries}}}}
    dropped = trim_could([patch], g, {"compact": cap, "standard": 12, "deep": 30})
    kept = patch["tables"]["screens"][sid]["could"]
    assert dropped == 9 - cap and len(kept) == cap
    assert any(f"step {moment}" in c["basis"] for c in kept)  # the signature moment is kept first
    assert (
        sum(c["basis_kind"] == "library_pattern" for c in kept) == 1
    )  # least critical go first (4 of 5 gone)


@case("spc-dg-06")
def test_a_could_whose_basis_is_assumed_stays_assumed(tmp_path_factory):
    g = _small()
    flow = next(f for f in g.flows.values() if f.primary)
    flow.stages[0].emotion_assumed = True
    sid = next(iter(g.screens))
    g.screens[sid].could = [_could(0, basis=f"{flow.id} step {flow.stages[0].n}", assumed=False)]
    bad = [f for f in validate(g, only={"V-42"}) if f.severity == "blocker"]
    assert bad and "assumed" in bad[0].why  # an assumed basis is never presented as evidence
    g.screens[sid].could[0].assumed = True
    assert "V-42" not in codes(validate(g, only={"V-42"}), "blocker")
    f = _std(tmp_path_factory)
    could_files = {p: t for p, t in f.pack_files.items() if p.endswith(".could.md")}
    assert could_files and any("assumed: True" in t for t in could_files.values())
    assert "V-42" not in codes(validate(f.graph, only={"V-42"}), "blocker")
    assert all("tier: open" in t for t in could_files.values())
