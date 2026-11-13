"""derive/release.py: the release planner (doc 15 4.5, 4.6): caps, pull-in, deferral reasons, consent, ordering."""

from __future__ import annotations

from mde_compass_intake.spec_input import Feature, Flow, FlowStep, Screen, ScreenUse, Trust
from mde_spec_agent.derive.facts import facts_from_input
from mde_spec_agent.derive.inventory import build_inventory
from mde_spec_agent.derive.profile import detail_profile
from mde_spec_agent.derive.release import plan_release
from mde_spec_core.config import default_config

from .conftest import tiny_input

CFG = default_config()


def plan_of(si, *, cfg=CFG, **kw):
    facts = facts_from_input(si)
    inv = build_inventory(si, None, facts, detail_profile(si, cfg, facts), cfg)
    return inv, plan_release(si, inv, cfg, **kw)


def capped(n: int):
    return CFG.model_copy(
        update={"scope": CFG.scope.model_copy(update={"scope_cap_per_app_phase": n})}
    )


# ---------------------------------------------------------------- the real exports


def test_quick_nothing_deferred_and_the_extended_screens_are_pulled_in(export) -> None:
    _inv, pl = plan_of(export("quick"))
    assert pl.later_by_reason() == {} and not pl.consent_required
    # sign-in, verify and language settings were `extended` in Compass; the release has them (system set, claimed by Compass)
    for sid in ("SCR-001", "SCR-002", "SCR-013"):
        assert sid in pl.release_screens
    assert pl.core_report.compass_core == 10 and pl.core_report.core_in_release == 10
    assert pl.per_app["APP-001"].release == len(pl.release_screens) <= pl.cap


def test_standard_live_tracking_is_a_user_deferral_with_an_outline_and_no_screens(export) -> None:
    si = export("standard")
    inv, pl = plan_of(si)
    d = pl.features["FTR-008"]
    assert (d.status, d.reason) == ("later", "user") and d.outline is not None
    assert not any("FTR-008" in p.feature_ids for p in inv.screens if p.id in pl.release_screens)
    assert pl.later_by_reason() == {
        "user": ["FTR-008"]
    }  # nothing deferred for capacity or the first release
    assert not any(c.kind == "capacity" for c in pl.needs_consent)
    # the duplicate feature is merged, not planned twice
    assert "FTR-006" not in pl.features


def test_thorough_dispatch_lands_in_phase_one(export) -> None:
    si = export("thorough")
    _inv, pl = plan_of(si)
    mod = next(m for m in pl.modules if m.name == "Dispatch")
    assert (mod.core, mod.extended) == (0, 19)  # Compass: nothing core
    assert mod.in_release > 0, (
        "a freight app whose first release has no dispatch cannot dispatch a truck"
    )
    assert pl.features["FTR-002"].status == "in" and pl.features["FTR-002"].pulled_in
    rules = {x.rule for x in pl.core_report.extended_pulled_in}
    assert "role_journey" in rules
    assert any(x.compass_id == "SCR-081" for x in pl.core_report.extended_pulled_in)
    # G1.5 is a hard gate for deep: the move is a consent
    assert any(c.kind == "pull_in" and "FTR-002" in c.feature_ids for c in pl.needs_consent)
    # every app stays far below the cap; the backlog has first-release features with outlines
    assert all(c.release < pl.cap for c in pl.per_app.values())
    later = pl.later_by_reason()
    assert later.get("first_release") and not later.get("capacity")
    assert all(pl.features[f].outline is not None for f in later["first_release"])


def test_core_report_accounts_for_every_core_screen(export) -> None:
    for key in ("quick", "standard", "thorough"):
        _inv, pl = plan_of(export(key))
        r = pl.core_report
        assert r.core_in_release + len(r.core_not_in_release) == r.compass_core
        assert not r.core_not_in_release  # no export defers a core screen


def test_a_user_evidenced_must_always_has_a_screen(export) -> None:
    for key in ("quick", "standard", "thorough"):
        _inv, pl = plan_of(export(key))
        for fid, d in pl.features.items():
            if d.user_evidenced_must:
                assert d.status == "in", fid


# ---------------------------------------------------------------- the rules on small inputs


def test_pull_in_never_pulls_a_feature_the_user_deferred() -> None:
    si = tiny_input()
    scr = si.structure.screens
    deferred = Feature(
        id="FTR-003",
        name="Gift slots",
        intent="later",
        scope_status="later",
        priority="later",
        user_evidenced=True,
        deferred_by_user=True,
        quote_ids=("FCT-9",),
        trust=Trust(grade="locked"),
    )
    gift = Screen(
        id="SCR-004",
        name="Gift",
        module="MOD-001",
        apps=("APP-001",),
        roles=("ROL-001",),
        serves=("FTR-003",),
        pattern="detail",
        phase="core",
    )  # Compass even calls it core
    flow = Flow(
        id="FLW-002",
        name="Gift flow",
        role="ROL-001",
        steps=(FlowStep(screen="SCR-001"), FlowStep(screen="SCR-004")),
        features=("FTR-001",),
    )
    si2 = si.model_copy(
        update={
            "features": (*si.features, deferred),
            "structure": si.structure.model_copy(
                update={"screens": (*scr, gift), "flows": (flow,)}
            ),
        }
    )
    _inv, pl = plan_of(si2)
    assert "SCR-004" not in pl.release_screens
    assert pl.deferred_screens["SCR-004"] == "user"
    assert pl.features["FTR-003"].reason == "user"  # never relabelled
    assert any("SCR-004" in t for t in pl.blocked_journeys)


def test_role_journey_pull_in_and_entity_producer_pull_in() -> None:
    si = tiny_input()
    scr = tuple(
        s.model_copy(update={"phase": "extended" if s.id != "SCR-001" else "core"})
        for s in si.structure.screens
    )
    si2 = si.model_copy(update={"structure": si.structure.model_copy(update={"screens": scr})})
    inv, pl = plan_of(si2)
    # SCR-001 shows the Slot entity; nothing core can create it, so the (merged) form is pulled in
    assert inv.screen_alias["SCR-003"] == "SCR-002"
    assert "SCR-002" in pl.release_screens
    assert [x.rule for x in pl.pulled_in if x.screen_id == "SCR-002"] == ["entity_producer"]
    assert pl.features["FTR-002"].status == "in" and pl.features["FTR-002"].pulled_in


def test_capacity_deferral_needs_consent_and_keeps_the_reason() -> None:
    si = tiny_input()
    extra = tuple(
        Screen(
            id=f"SCR-1{n:02d}",
            name=f"Extra {n}",
            module="MOD-001",
            apps=("APP-001",),
            roles=("ROL-001",),
            serves=("FTR-002",),
            entities=(ScreenUse(entity="ENT-001", use="view"),),
            pattern="detail",
            phase="core",
        )
        for n in range(8)
    )
    si2 = si.model_copy(
        update={
            "structure": si.structure.model_copy(
                update={"screens": (*si.structure.screens, *extra)}
            )
        }
    )
    _inv, pl = plan_of(si2, cfg=capped(14))
    assert pl.per_app["APP-001"].before_capacity > 14
    cap_kinds = {c.kind for c in pl.needs_consent}
    assert "capacity" in cap_kinds or "user_must" in cap_kinds or "over_cap" in cap_kinds
    assert pl.per_app["APP-001"].release <= 14 or "over_cap" in cap_kinds
    for _fid, d in pl.features.items():
        if d.reason == "capacity":
            assert not d.user_evidenced_must and d.outline is not None


def test_a_user_evidenced_must_is_never_deferred_for_capacity() -> None:
    si = tiny_input()
    # both features are musts the person evidenced; the cap cannot be met without deferring one
    feats = tuple(f.model_copy(update={"priority": "must"}) for f in si.features)
    si2 = si.model_copy(update={"features": feats})
    _inv, pl = plan_of(si2, cfg=capped(3))
    assert all(d.status == "in" for d in pl.features.values())
    kinds = [c.kind for c in pl.needs_consent]
    assert "user_must" in kinds and "over_cap" in kinds and pl.consent_required
    assert not [c for c in pl.needs_consent if c.kind == "capacity"]


def test_everything_in_one_release_is_allowed_and_recorded() -> None:
    _inv, pl = plan_of(tiny_input(), cfg=capped(3), everything_in_one_release=True)
    assert pl.everything_in_one_release and not pl.needs_consent
    assert pl.per_app["APP-001"].over_cap and any(
        "everything in one release" in n for n in pl.notes
    )
    assert all(d.status == "in" for d in pl.features.values())


def test_ordering_puts_user_evidenced_must_first_and_is_deterministic(export) -> None:
    _inv, pl = plan_of(export("standard"))
    first = pl.order[1]
    musts = [f for f in first if pl.features[f].user_evidenced_must]
    assert first[: len(musts)] == musts  # every must precedes every non-must
    assert pl.order[2] == ["FTR-008"]
    _inv2, pl2 = plan_of(export("standard"))
    assert pl.order == pl2.order and pl.release_screens == pl2.release_screens


def test_a_later_feature_keeps_dependencies_in_its_outline(export) -> None:
    _inv, pl = plan_of(export("thorough"))
    some = next(f for f in pl.order[2])
    assert pl.features[some].outline.text() != ""
