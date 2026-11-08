"""Incremental builds (spec 3.6): only the affected writers re-run; everything else is carried over."""

from __future__ import annotations

from typing import Any

from helpers import DirectMockGateway, make_context, make_note, make_request, no_c11

from mde_contracts.engines import BuildRequest, BuildResult
from mde_contracts.spec import ScreenBlock
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.engine import SpecBuilderImpl
from mde_spec_builder.incremental import (
    graft_roles,
    is_baseline_screen,
    plan_build,
    sections_for_slots,
    structure_matches,
)
from mde_spec_loader import block_bodies, diff_specs, parse_spec


async def build(req: BuildRequest, gateway: DirectMockGateway | None = None) -> BuildResult:
    engine = SpecBuilderImpl(gateway or DirectMockGateway(), summary_loader=no_c11)
    return await engine.build(req, make_context())


async def first(archetype: str = "commerce", **kw: Any) -> BuildResult:
    res = await build(make_request(archetype, **kw))
    assert res.status == "ok" and res.spec is not None
    return res


def follow(previous: BuildResult, archetype: str, sections: list[str], **kw: Any) -> BuildRequest:
    req = make_request(archetype, spec_version=2, **kw)
    return req.model_copy(update={"previous_spec": previous.spec, "affected_sections": sections})


def untouched(prev: Any, new: Any, *changed: str) -> None:
    """Every block except the named ones, the header and the revision reads the same."""
    a, b = block_bodies(prev), block_bodies(new)
    same = {k for k in a if k in b and a[k] == b[k]}
    differing = {k for k in {*a, *b} if k not in same} - {"REV"}
    assert differing == set(changed), differing


# --------------------------------------------------------------------------- the dependency map


def test_the_slot_to_section_map_follows_spec_3_6() -> None:
    assert sections_for_slots(["design.direction", "brand.name"]) == ["design"]
    assert sections_for_slots(["auth.method"]) == ["roles"]
    assert sections_for_slots(["users.roles", "auth.sso"]) == ["roles"]
    assert sections_for_slots(["pay.model", "integrations.list"]) == ["integrations"]
    assert sections_for_slots(["behavior.offline", "locale", "a11y"]) == ["behavior"]
    assert sections_for_slots(["scope.features"]) == ["all"]
    assert sections_for_slots(["idea.archetype", "design.accent"]) == ["all"]
    assert sections_for_slots(["design.accent", "locale"]) == ["design", "behavior"]
    assert sections_for_slots(["scope.apps", "regions"]) == ["all"]
    assert sections_for_slots([]) == []


async def test_plan_build_picks_a_patch_only_for_patchable_sections() -> None:
    prev = await first()
    assert plan_build(make_request("commerce")).mode == "full"  # nothing to patch
    assert plan_build(follow(prev, "commerce", ["design"])).mode == "patch"
    assert plan_build(follow(prev, "commerce", ["design", "roles"])).sections == {"design", "roles"}
    assert plan_build(follow(prev, "commerce", ["all"])).mode == "full"
    assert plan_build(follow(prev, "commerce", ["design", "screens"])).mode == "full"
    assert plan_build(follow(prev, "commerce", [])).mode == "patch"  # a research toggle only


# --------------------------------------------------------------------------- patches


async def test_a_design_change_reruns_only_the_design_writer() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    req = follow(prev, "commerce", ["design"], design__accent="#0b6e4f", design__nav_style="drawer")
    res = await build(req, gateway)
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    assert gateway.ids == ["C9c.design_tokens"]  # no screens, no data, no critic, no summary
    untouched(prev.spec, res.spec, "DESIGN")
    assert (
        res.spec.design.tokens.colors_light["primary"]
        != prev.spec.design.tokens.colors_light["primary"]
    )
    assert res.spec.design.nav_style == "drawer"
    assert res.spec.summary == prev.spec.summary  # the plan summary is not rewritten
    assert res.spec.header.spec_version == 2 and res.spec.revision.previous_version == 1
    assert res.spec.revision.changed_ids == ["DESIGN"]
    assert res.spec_hash != prev.spec_hash and res.spec_text is not None
    assert parse_spec(res.spec_text) == res.spec
    # the rendered text of every untouched section is the same text
    old_lines = set(prev.spec_text.splitlines())  # type: ignore[union-attr]
    new_only = [ln for ln in res.spec_text.splitlines() if ln not in old_lines]
    assert all(
        ln.strip().startswith(
            (
                "hash:",
                "version",
                "previous_version",
                "changed_ids",
                "-",
                "primary",
                "text_on_primary",
                "nav_style",
                "|",
            )
        )
        or ":" in ln
        for ln in new_only
    )
    assert len(new_only) < 40


async def test_a_behavior_change_needs_no_calls_at_all() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    res = await build(follow(prev, "commerce", ["behavior"], behavior__offline="none"), gateway)
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    assert gateway.calls == []
    untouched(prev.spec, res.spec, "BEHAVIOR")
    assert res.spec.behavior.offline.startswith("Online only")


async def test_a_language_change_rewrites_every_screen_brief() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    res = await build(follow(prev, "commerce", ["behavior"], locale="hi"), gateway)
    assert res.spec is not None and prev.spec is not None
    assert res.spec.header.language == "hi" and res.spec.app.language == "hi"
    assert set(gateway.ids) == {"C10.screen_briefs"}
    briefed = {s.id for _, i, _ in gateway.calls for s in i.screens}  # type: ignore[attr-defined]
    assert briefed == {s.id for s in prev.spec.screens}


async def test_a_payment_change_touches_integrations_and_the_screens_that_mention_them() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    res = await build(follow(prev, "commerce", ["integrations"], pay__provider="razorpay"), gateway)
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    pay = next(i for i in res.spec.integrations.items if i.id == "INT-payments")
    assert (pay.mode, pay.provider, pay.needs_dev_build) == ("adapter", "razorpay", True)
    assert set(gateway.ids) == {"C10.screen_briefs"}  # only the screens that mention payments
    for _, inp, _ in gateway.calls:
        for screen in inp.screens:  # type: ignore[attr-defined]
            assert "payment" in " ".join([screen.route, *screen.sections, *screen.actions]).lower()
    diff = diff_specs(prev.spec, res.spec)
    assert set(diff.all_ids) <= {
        "INTEGRATIONS",
        "INT-payments",
        "SUMMARY",
    }  # SUMMARY: what is mocked


async def test_a_sign_in_change_swaps_the_sign_in_screens_and_keeps_the_rest() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    res = await build(follow(prev, "commerce", ["roles"], auth__method="password"), gateway)
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    logins = [s for s in res.spec.screens if s.recipe.startswith("login")]
    assert [s.recipe for s in logins] == ["login-password"]
    ops = {o.id for o in res.spec.api.operations}
    assert "API-signInPassword" in ops and "API-requestOtp" not in ops
    assert "INT-sms_otp" not in {i.id for i in res.spec.integrations.items}
    assert {"C9b.data_api_roles", "C10.screen_briefs"} <= set(gateway.ids)
    assert "C9a.screens_nav" not in gateway.ids and "C9c.design_tokens" not in gateway.ids
    briefed = {s.id for c, i, _ in gateway.calls if c == "C10.screen_briefs" for s in i.screens}  # type: ignore[attr-defined]
    assert briefed == {
        s.id
        for s in res.spec.screens
        if s.recipe in ("welcome", "signup") or s.recipe.startswith("login")
    }
    shop_before = {
        s.id: s
        for s in prev.spec.screens
        if not s.recipe.startswith(("login", "welcome", "signup"))
    }
    shop_after = {s.id: s for s in res.spec.screens if s.id in shop_before}
    assert shop_after == shop_before  # no other screen changed
    assert res.spec.design == prev.spec.design and res.spec.data == prev.spec.data


async def test_a_structural_change_falls_back_to_a_full_build() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    # the person turned a feature off while the dependency map said "design": the baseline moved
    req = follow(prev, "commerce", ["design"], features=["catalog_browse", "cart_checkout"])
    res = await build(req, gateway)
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    assert "C9a.screens_nav" in gateway.ids  # the writers all ran
    assert len(res.spec.screens) < len(prev.spec.screens)


async def test_a_scope_change_rebuilds_everything() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    res = await build(
        follow(prev, "commerce", ["all"], features=["catalog_browse", "cart_checkout", "search"]),
        gateway,
    )
    assert res.status == "ok" and res.spec is not None
    assert gateway.ids[:3] == ["C9a.screens_nav", "C9b.data_api_roles", "C9c.design_tokens"]
    assert res.spec.revision.previous_version == 1 and res.spec.revision.changed_ids


# --------------------------------------------------------------------------- research toggles


async def test_switching_a_research_screen_off_removes_exactly_what_it_created() -> None:
    note = make_note()
    prev = await first("food_delivery", research=note)  # RA-001 becomes a screen through C9a
    assert prev.spec is not None
    created = [s for s in prev.spec.screens if s.research_ref == "RA-001"]
    assert len(created) == 1
    gateway = DirectMockGateway()
    req = follow(prev, "food_delivery", [], research=note, excluded=["RA-001"])
    res = await build(req, gateway)
    assert res.status == "ok" and res.spec is not None
    assert not {
        "C9a.screens_nav",
        "C9b.data_api_roles",
        "C9c.design_tokens",
        "C10.screen_briefs",
    } & set(gateway.ids)
    gone = {s.id for s in prev.spec.screens} - {s.id for s in res.spec.screens}
    assert gone == {created[0].id}
    addition = next(a for a in res.spec.assumptions.research_additions if a.id == "RA-001")
    assert addition.included is False and addition.affects == []
    # the other additions are untouched, still listed and still included
    assert [a.included for a in res.spec.assumptions.research_additions if a.id != "RA-001"] == [
        True
    ] * 3
    # every other screen is as before, except the one that linked to the removed screen
    before = {s.id: s for s in prev.spec.screens}
    for screen in res.spec.screens:
        if screen.actions != before[screen.id].actions:
            assert [a.name for a in before[screen.id].actions if a not in screen.actions] == [
                f"open_{created[0].title.lower().replace(' ', '_')}"  # type: ignore[union-attr]
            ]
        else:
            assert screen == before[screen.id]


async def test_switching_it_back_on_brings_it_back_with_only_that_addition() -> None:
    note = make_note()
    prev = await first("food_delivery", research=note)
    off = await build(follow(prev, "food_delivery", [], research=note, excluded=["RA-001"]))
    assert off.spec is not None
    gateway = DirectMockGateway()
    res = await build(follow(off, "food_delivery", [], research=note, excluded=[]), gateway)
    assert res.status == "ok" and res.spec is not None
    back = [s for s in res.spec.screens if s.research_ref == "RA-001"]
    assert len(back) == 1 and back[0].source == "research"
    c9a = next(i for c, i, _ in gateway.calls if c == "C9a.screens_nav")
    assert [r.id for r in c9a.research] == ["RA-001"]  # type: ignore[attr-defined]
    assert next(a for a in res.spec.assumptions.research_additions if a.id == "RA-001").included


async def test_a_pack_feature_addition_is_a_full_rebuild_when_toggled() -> None:
    note = make_note()
    prev = await first("commerce", research=note)
    assert prev.spec is not None
    assert any(
        s.research_ref == "RA-001" for s in prev.spec.screens
    )  # favorites came from the pack
    req = follow(prev, "commerce", [], research=note, excluded=["RA-001"])
    assert plan_build(req).mode == "full"
    res = await build(req)
    assert res.status == "ok" and res.spec is not None
    assert not any(s.research_ref == "RA-001" for s in res.spec.screens)
    assert not any(r.startswith("/favorites") for r in (s.route for s in res.spec.screens))
    addition = next(a for a in res.spec.assumptions.research_additions if a.id == "RA-001")
    assert addition.included is False and addition.affects == []
    plain = await first("commerce")  # as if research never suggested it
    assert {s.route for s in res.spec.screens} == {s.route for s in plain.spec.screens}  # type: ignore[union-attr]


async def test_the_brand_addition_is_a_design_change() -> None:
    note = make_note()
    prev = await first("commerce", research=note)
    assert prev.spec is not None and prev.spec.design.direction == "warm-friendly"
    gateway = DirectMockGateway()
    res = await build(follow(prev, "commerce", [], research=note, excluded=["RA-004"]), gateway)
    assert res.spec is not None
    assert res.spec.design.direction == "clean-bright"  # the brand finding no longer applies
    assert "C9c.design_tokens" in gateway.ids


# --------------------------------------------------------------------------- BUG-5: research feature screens


async def test_a_design_change_stays_a_patch_when_research_switched_a_feature_on() -> None:
    note = make_note()
    prev = await first("commerce", research=note)  # RA-001 switches `favorites` on in B2
    assert prev.spec is not None
    research_screens = [s for s in prev.spec.screens if s.source == "research"]
    assert research_screens and all(s.research_ref == "RA-001" for s in research_screens)
    gateway = DirectMockGateway()
    req = follow(prev, "commerce", ["design"], research=note, design__accent="#0b6e4f")
    assert plan_build(req).mode == "patch"
    res = await build(req, gateway)
    assert res.status == "ok" and res.spec is not None
    assert gateway.ids == ["C9c.design_tokens"]  # no screens, no data, no critic, no summary
    untouched(
        prev.spec, res.spec, "DESIGN"
    )  # every other block, research screens too, byte for byte
    assert res.spec.revision.changed_ids == ["DESIGN"]
    addition = next(a for a in res.spec.assumptions.research_additions if a.id == "RA-001")
    assert addition.affects and addition.included


async def test_a_behavior_change_with_research_features_needs_no_calls() -> None:
    note = make_note()
    prev = await first("commerce", research=note)
    gateway = DirectMockGateway()
    res = await build(
        follow(prev, "commerce", ["behavior"], research=note, behavior__offline="none"), gateway
    )
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    assert gateway.calls == []
    untouched(prev.spec, res.spec, "BEHAVIOR")


async def test_a_sign_in_change_regates_the_research_added_screens_like_the_pack_ones() -> None:
    note = make_note()
    prev = await first("commerce", research=note)
    assert prev.spec is not None
    stale_screens = [
        s.model_copy(update={"auth": "public"}) if s.source == "research" else s
        for s in prev.spec.screens
    ]
    stale = prev.spec.model_copy(update={"screens": stale_screens})
    base = build_baseline(make_request("commerce", research=note, auth__method="password"))
    grafted, replaced = graft_roles(stale, base)
    favorites = next(s for s in grafted.screens if s.research_ref == "RA-001")
    assert favorites.auth == "authenticated"  # the gate of the new baseline, not the stale one
    assert (favorites.source, favorites.research_ref) == ("research", "RA-001")
    assert replaced == {
        s.id for s in prev.spec.screens if s.recipe in ("welcome", "login-otp", "signup")
    }


def test_which_screens_of_the_previous_version_the_baseline_owns() -> None:
    fresh = ScreenBlock(id="SCR-001", route="/a", recipe="list", purpose="x", research_ref="RA-001")
    cases = [
        ("default", None, fresh, True),
        ("custom", None, fresh, True),  # a pack screen the person annotated
        ("research", "RA-001", fresh, True),  # a pack feature that research switched on
        ("research", "RA-002", fresh, False),  # a screen another suggestion's proposal added
        ("brief", None, fresh, False),  # added by a proposal
        ("default", None, None, True),  # a pack screen the baseline no longer has: structure moved
        ("custom", None, None, False),  # a screen the person added
        ("research", "RA-001", None, False),
    ]
    for source, ref, base_screen, owned in cases:
        screen = ScreenBlock(
            id="SCR-001",
            route="/a",
            recipe="list",
            purpose="x",
            source=source,  # type: ignore[arg-type]
            research_ref=ref,
        )
        assert is_baseline_screen(screen, base_screen) is owned, (source, ref, base_screen)


async def test_screens_that_proposals_added_do_not_change_the_structure() -> None:
    note = make_note()
    prev = await first("food_delivery", research=note)  # RA-001 becomes a screen through C9a
    assert prev.spec is not None and any(s.research_ref == "RA-001" for s in prev.spec.screens)
    base = build_baseline(make_request("food_delivery", research=note))
    assert structure_matches(prev.spec, base)  # the proposal's screen is not a baseline screen
    moved = prev.spec.model_copy(
        update={"screens": [s for s in prev.spec.screens if s.source != "default"][:1]}
    )
    assert not structure_matches(moved, base)  # most of the pack's screens are gone
