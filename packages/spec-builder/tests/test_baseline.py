"""B2: the baseline is valid by construction for every archetype and every feature selection."""

from __future__ import annotations

from typing import Any

import pytest
from helpers import make_brief, make_note, make_policy, make_request
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_content.packs import default_features, load_pack, pack_ids
from mde_content.stack import load_stack_profile
from mde_contracts.engines import BuildRequest
from mde_contracts.spec import AppSpec
from mde_spec_builder.baseline import (
    build_baseline,
    resolve_apps,
    resolve_pack_id,
)
from mde_spec_builder.render import render_spec, with_rendered_hash
from mde_spec_builder.rules import load_rules
from mde_spec_builder.slots import SlotView
from mde_spec_builder.validators import errors, run_validators
from mde_spec_loader import SpecIndex, parse_spec

STACK = load_stack_profile()
ARCHETYPES = pack_ids()
AUTH_METHODS = sorted(load_rules().auth_methods)


def check(req: BuildRequest) -> AppSpec:
    """Build the baseline and assert that no validator reports an error."""
    spec = build_baseline(req)
    found = run_validators(spec, stack_profile=STACK, limits=req.policy.limits)
    assert errors(found) == [], errors(found)
    return spec


def test_there_are_five_archetypes_and_each_has_a_pack() -> None:
    assert set(ARCHETYPES) == {
        "commerce",
        "food_delivery",
        "booking_services",
        "internal_workforce",
        "custom",
    }


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_default_baseline_is_valid_and_round_trips(archetype: str) -> None:
    spec = check(make_request(archetype))
    assert len(spec.screens) >= 10 and len(spec.flows) >= 3
    assert sum(f.primary for f in spec.flows) == 1
    assert [s.id for s in spec.screens] == sorted(s.id for s in spec.screens)
    stamped = with_rendered_hash(spec)
    assert parse_spec(render_spec(stamped)) == stamped
    assert SpecIndex.from_text(render_spec(stamped)).verify(stamped.header.hash)


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_baseline_is_deterministic(archetype: str) -> None:
    a = render_spec(build_baseline(make_request(archetype)))
    b = render_spec(build_baseline(make_request(archetype)))
    assert a == b


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_every_feature_on_is_valid_and_within_the_cap(archetype: str) -> None:
    pack = load_pack(archetype)
    spec = check(make_request(archetype, features=[f.id for f in pack.features]))
    assert len(spec.screens) <= 50


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_core_only_is_valid(archetype: str) -> None:
    check(make_request(archetype, features=[]))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_any_feature_subset_is_valid(data: st.DataObject) -> None:
    archetype = data.draw(st.sampled_from(ARCHETYPES))
    ids = [f.id for f in load_pack(archetype).features]
    chosen = data.draw(st.lists(st.sampled_from(ids), unique=True, max_size=len(ids)))
    method = data.draw(st.sampled_from([None, *AUTH_METHODS]))
    extra: dict[str, Any] = {"auth__method": method} if method else {}
    check(make_request(archetype, features=chosen, **extra))


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_any_surface_role_payment_combination_is_valid(data: st.DataObject) -> None:
    archetype = data.draw(st.sampled_from(ARCHETYPES))
    pack = load_pack(archetype)
    surfaces = list(dict.fromkeys(r.surface for r in pack.roles))
    apps = data.draw(
        st.lists(st.sampled_from(["main", "admin", *surfaces]), min_size=1, max_size=3, unique=True)
    )
    roles = data.draw(
        st.sampled_from(
            [["user"], ["employee"], ["user", "staff"], ["employee", "manager", "admin"]]
        )
    )
    pay = data.draw(st.sampled_from(["online", "on_delivery", "both", "none"]))
    check(make_request(archetype, scope__apps=apps, users__roles=roles, pay__model=pay))


@pytest.mark.parametrize("archetype", ARCHETYPES)
@pytest.mark.parametrize("cap", [5, 9, 14, 20])
def test_scope_guard_keeps_phase_one_and_moves_the_rest_to_the_backlog(
    archetype: str, cap: int
) -> None:
    pack = load_pack(archetype)
    features = [f.id for f in pack.features]
    full = build_baseline(make_request(archetype, features=features))
    spec = check(make_request(archetype, features=features, policy=make_policy(max_screens=cap)))
    assert len(spec.screens) <= cap < len(full.screens)
    assert spec.backlog and all(b.reason.startswith("phase 2") for b in spec.backlog)
    primary_full = next(f for f in full.flows if f.primary)
    primary = next((f for f in spec.flows if f.primary), None)
    assert (
        primary is not None and primary.name == primary_full.name
    )  # phase one keeps the main journey


def test_a_roomy_cap_leaves_no_backlog() -> None:
    assert build_baseline(make_request("commerce")).backlog == []


# --------------------------------------------------------------------------- surfaces and apps


def test_resolve_apps_main_is_the_first_surface_and_admin_aliases_business() -> None:
    surfaces = ["customer", "business"]
    assert resolve_apps(["main"], surfaces) == (["customer"], [])
    assert resolve_apps(["admin"], surfaces) == (["business"], [])
    assert resolve_apps(["customer", "admin", "mars"], surfaces) == (
        ["customer", "business"],
        ["mars"],
    )
    assert resolve_apps(["mars"], surfaces) == (["customer"], ["mars"])
    assert resolve_apps(["admin"], ["customer"]) == (["customer"], ["admin"])


def test_other_apps_become_backlog_items_never_screens() -> None:
    one = check(make_request("commerce", scope__apps=["customer"]))
    two = check(make_request("commerce", scope__apps=["customer", "business"]))
    assert len(one.screens) == len(two.screens) and one.backlog == []
    assert [b.title for b in two.backlog] == ["Store manager app"]
    assert "separate app" in two.backlog[0].reason and two.backlog[0].screens_hint
    assert all(s.surface == "customer" for s in two.screens)
    assert {r.id for r in two.roles.roles} == {"ROLE-customer"}


def test_the_admin_surface_is_an_alias_of_business() -> None:
    admin = check(make_request("commerce", scope__apps=["admin"]))
    business = check(make_request("commerce", scope__apps=["business"]))
    assert render_spec(admin) == render_spec(business)
    assert {r.id for r in admin.roles.roles} == {"ROLE-store_manager"}
    # the shared sign-in screens come along; the shop screens do not
    assert {s.surface for s in admin.screens} == {"business"}
    assert any(s.recipe.startswith("login") for s in admin.screens)
    assert any(f.primary for f in admin.flows)  # a primary journey is always promoted


def test_unknown_apps_are_a_backlog_note_and_the_main_app_is_built() -> None:
    spec = check(make_request("custom", scope__apps=["mars"]))
    assert [b.title for b in spec.backlog] == ["Mars app"]
    assert len(spec.screens) >= 10


def test_a_multi_surface_pack_without_other_apps_hides_staff_screens() -> None:
    spec = check(make_request("food_delivery"))
    assert all(s.surface == "customer" for s in spec.screens)
    assert not any(r.id in ("ROLE-courier", "ROLE-restaurant_owner") for r in spec.roles.roles)
    assert not any(o.roles for o in spec.api.operations)


# --------------------------------------------------------------------------- roles


def test_users_roles_narrow_the_roles_of_the_app() -> None:
    all_roles = check(make_request("internal_workforce"))
    only = check(make_request("internal_workforce", users__roles=["employee"]))
    assert {r.id for r in all_roles.roles.roles} == {
        "ROLE-employee",
        "ROLE-manager",
        "ROLE-admin",
    }
    # the manager role stays because the Approvals feature (selected by default) needs it
    assert "ROLE-manager" in {r.id for r in only.roles.roles}
    assert "ROLE-admin" not in {r.id for r in only.roles.roles}
    assert any(a.label == "Role added: Manager" for a in only.assumptions.assumptions)
    no_approvals = check(
        make_request("internal_workforce", users__roles=["employee"], features=["task_lists"])
    )
    assert {r.id for r in no_approvals.roles.roles} == {"ROLE-employee"}


def test_no_sign_in_means_no_roles_beyond_the_first_and_nothing_is_gated() -> None:
    spec = check(make_request("custom", auth__method="none"))
    assert {r.id for r in spec.roles.roles} == {"ROLE-user"}
    assert {s.auth for s in spec.screens} == {"public"}
    assert not any(
        s.recipe in ("welcome", "signup") or s.recipe.startswith("login") for s in spec.screens
    )
    assert {o.auth for o in spec.api.operations} == {"public"}
    assert "INT-otp_delivery" not in {i.id for i in spec.integrations.items}


# --------------------------------------------------------------------------- sign-in variants


@pytest.mark.parametrize("archetype", ARCHETYPES)
@pytest.mark.parametrize("method", AUTH_METHODS)
def test_every_sign_in_method_is_valid_for_every_archetype(archetype: str, method: str) -> None:
    spec = check(make_request(archetype, auth__method=method))
    logins = [s for s in spec.screens if s.recipe.startswith("login")]
    if method == "none":
        assert logins == []
        return
    assert len(logins) == 1
    if method == "password":
        assert logins[0].recipe == "login-password"
        assert "API-signInPassword" in {o.id for o in spec.api.operations}
        assert "API-requestOtp" not in {o.id for o in spec.api.operations}
    elif method in ("social", "sso"):
        assert logins[0].recipe == "login-sso"
        assert not any(s.recipe == "signup" for s in spec.screens)
    # the login screen still leads somewhere
    assert any(a.go for a in logins[0].actions) or method == "guest_first"


def test_sso_required_in_the_policy_slot_wins_over_the_chosen_method() -> None:
    spec = check(make_request("commerce", auth__method="password", auth__sso="required"))
    assert any(s.recipe == "login-sso" for s in spec.screens)
    assert "INT-sso" in {i.id for i in spec.integrations.items}


# --------------------------------------------------------------------------- payments


def test_payments_are_mock_by_default_and_never_real() -> None:
    spec = check(make_request("commerce"))
    pay = next(i for i in spec.integrations.items if i.id == "INT-payments")
    assert pay.mode == "mock" and pay.provider is None and not pay.needs_dev_build


def test_a_named_provider_is_an_adapter_flagged_for_a_development_build() -> None:
    spec = check(make_request("commerce", pay__provider="razorpay"))
    pay = next(i for i in spec.integrations.items if i.id == "INT-payments")
    assert (pay.mode, pay.provider, pay.needs_dev_build) == ("adapter", "razorpay", True)


def test_real_payments_need_the_slot_and_an_enterprise_policy() -> None:
    guided = check(make_request("commerce", pay__provider="real:razorpay"))
    assert next(i for i in guided.integrations.items if i.id == "INT-payments").mode == "adapter"
    enterprise = check(
        make_request(
            "commerce",
            pay__provider="real:razorpay",
            policy=make_policy("enterprise-it-services-firm"),
        )
    )
    pay = next(i for i in enterprise.integrations.items if i.id == "INT-payments")
    assert (pay.mode, pay.provider) == ("real", "razorpay")


@pytest.mark.parametrize("model", ["on_delivery", "none"])
def test_no_online_payment_drops_the_payment_feature(model: str) -> None:
    with_pay = check(make_request("commerce"))
    without = check(make_request("commerce", pay__model=model))
    assert len(without.screens) < len(with_pay.screens)
    assert "INT-payments" not in {i.id for i in without.integrations.items}
    assert not any("payment" in s.route for s in without.screens)


def test_both_models_keep_payments_and_note_cash_on_delivery() -> None:
    spec = check(make_request("commerce", pay__model="both"))
    pay = next(i for i in spec.integrations.items if i.id == "INT-payments")
    assert "Cash on delivery" in (pay.notes or "")


# --------------------------------------------------------------------------- design


def test_tokens_come_from_the_chosen_theme() -> None:
    spec = check(make_request("commerce", design__direction="bold-dark"))
    assert spec.design.direction == "bold-dark"
    assert spec.design.tokens.colors_light["primary"] == "#6D28D9"


def test_the_pack_default_theme_is_used_when_no_direction_is_chosen() -> None:
    spec = check(make_request("food_delivery"))
    assert spec.design.direction == "warm-friendly"


def test_accent_nav_style_and_mode_are_applied_with_contrast_kept() -> None:
    from mde_content.themes import contrast_ratio

    spec = check(
        make_request(
            "commerce",
            design__accent="#f4d03f",  # a very light yellow: must be darkened to stay readable
            design__nav_style="drawer",
            design__mode="dark",
        )
    )
    light, dark = spec.design.tokens.colors_light, spec.design.tokens.colors_dark
    assert contrast_ratio(light["primary"], light["background"]) >= 4.5
    assert contrast_ratio(light["text_on_primary"], light["primary"]) >= 4.5
    assert contrast_ratio(dark["primary"], dark["background"]) >= 4.5
    assert spec.design.nav_style == "drawer"
    assert spec.design.component_variants["appearance"] == ["dark"]


def test_a_named_accent_colour_is_understood_and_a_nonsense_one_ignored() -> None:
    teal = check(make_request("commerce", design__accent="teal"))
    assert teal.design.tokens.colors_light["primary"].startswith("#")
    assert teal.design.tokens.colors_light["primary"] != "#1B5FD9"
    plain = check(make_request("commerce", design__accent="loud and proud"))
    assert plain.design.tokens.colors_light["primary"] == "#1B5FD9"


def test_the_brand_finding_of_the_research_note_is_applied_unless_excluded() -> None:
    note = make_note()
    applied = check(make_request("commerce", research=note))
    assert applied.design.direction == "warm-friendly"  # the brand's nearest direction
    assert applied.design.component_variants["font_family"] == ["Inter"]
    assert applied.app.tone == "warm, friendly"
    skipped = check(make_request("commerce", research=note, excluded=["RA-004"]))
    assert skipped.design.direction == "clean-bright"
    assert "font_family" not in skipped.design.component_variants
    explicit = check(make_request("commerce", research=note, design__direction="minimal-mono"))
    assert explicit.design.direction == "minimal-mono"  # the user's choice wins


# --------------------------------------------------------------------------- app, behavior, compliance


def test_app_block_uses_brand_name_summary_and_locale() -> None:
    spec = check(
        make_request(
            "commerce",
            brand__name="Mithai Mart",
            idea__summary="An app for my sweet shop in Pune",
            locale="hi",
        )
    )
    assert spec.app.name == "Mithai Mart" and spec.app.language == "hi"
    assert spec.app.purpose == "An app for my sweet shop in Pune"
    assert spec.header.language == "hi" and spec.behavior.locale == "hi"
    assert check(make_request("commerce")).app.name == "My App"


def test_behavior_follows_the_behavior_slots() -> None:
    spec = check(
        make_request(
            "commerce",
            behavior__offline="none",
            behavior__notifications=False,
            behavior__analytics=True,
            a11y="enhanced",
        )
    )
    assert spec.behavior.offline.startswith("Online only")
    assert len(spec.behavior.notifications) == 1 and "off" in spec.behavior.notifications[0]
    assert "screen_viewed" in spec.behavior.analytics_events
    assert len(spec.behavior.accessibility) > 4
    quiet = check(make_request("commerce", behavior__analytics=False))
    assert quiet.behavior.analytics_events == []
    assert any(link.startswith("/product") for link in quiet.behavior.deep_links)


def test_compliance_flags_add_flagged_behavior_and_notes_and_force_analytics_off() -> None:
    spec = check(
        make_request(
            "booking_services",
            compliance__flags=["health_data_privacy", "precise_location"],
            behavior__analytics=True,
        )
    )
    assert spec.behavior.analytics_events == []
    assert any("health details" in n for n in spec.behavior.notifications)
    assert any("Exact location" in p for p in spec.behavior.permission_prompts)
    labels = [a.label for a in spec.assumptions.assumptions]
    assert "Flagged: Health information" in labels and "Flagged: Exact location" in labels
    assert any("health details" in c.lower() for c in spec.acceptance.checks)


def test_unknown_compliance_flags_are_ignored() -> None:
    check(make_request("commerce", compliance__flags=["telepathy"]))


# --------------------------------------------------------------------------- assumptions


def test_assumptions_list_every_assumed_slot_with_an_impact() -> None:
    spec = check(
        make_request(
            "commerce",
            auth__method=("password", "assumed"),
            behavior__offline=("basic", "assumed"),
            locale=("en", "confirmed"),
            brief=make_brief(assumed=(("auth.method", "Login", "Email and password"),)),
        )
    )
    slots = [a.slot_id for a in spec.assumptions.assumptions if a.slot_id]
    assert slots == ["auth.method", "behavior.offline"]
    login = spec.assumptions.assumptions[0]
    assert login.label == "Login" and login.value_text == "password" and login.impact


def test_research_additions_are_recorded_with_toggles_and_sources() -> None:
    note = make_note()
    spec = check(make_request("commerce", research=note, excluded=["RA-002"]))
    additions = {a.id: a for a in spec.assumptions.research_additions}
    assert set(additions) == {"RA-001", "RA-002", "RA-003", "RA-004"}
    assert additions["RA-001"].included and additions["RA-001"].verified
    assert [s.url for s in additions["RA-001"].sources] == [
        "https://example.com/similar-apps",
        "https://example.org/guides",
    ]
    assert additions["RA-002"].included is False
    assert spec.assumptions.research_status == "ok"
    assert spec.header.research_note_hash == note.hash


def test_research_status_when_skipped_or_unavailable() -> None:
    skipped = check(make_request("commerce"))
    assert skipped.assumptions.research_status == "skipped"
    assert skipped.assumptions.research_note == "research: skipped"
    assert (
        skipped.assumptions.research_additions == [] and skipped.header.research_note_hash is None
    )
    gone = ResearchNoteFactory.unavailable("budget")
    spec = check(make_request("commerce", research=gone))
    assert spec.assumptions.research_status == "unavailable"
    assert spec.assumptions.research_note == "research: unavailable (budget)"
    assert spec.assumptions.research_additions == []


class ResearchNoteFactory:
    @staticmethod
    def unavailable(reason: str):  # type: ignore[no-untyped-def]
        from mde_contracts.research import ResearchNote

        return ResearchNote.unavailable("c" * 64, reason)


# --------------------------------------------------------------------------- enterprise copy-in


def test_enterprise_policy_values_are_copied_into_the_file() -> None:
    policy = make_policy("regulated-bank")
    spec = check(
        make_request(
            "internal_workforce",
            policy=policy,
            data__classification="confidential",
            regions=["eu-west", "ap-south"],
        )
    )
    assert spec.header.mode == "enterprise"
    assert (
        spec.header.inherits[0] == "regulated-bank" and "platform-default@1" in spec.header.inherits
    )
    text = render_spec(spec)
    assert "react-native-code-push" in text  # banned library copied in
    assert any("react-native-code-push" in c for c in spec.acceptance.checks)
    assert any("eu-west" in c for c in spec.acceptance.checks)
    assert any("confidential" in c for c in spec.behavior.error_conventions)
    labels = {a.label for a in spec.assumptions.assumptions}
    assert {
        "Company rule: banned libraries",
        "Company rule: allowed data regions",
        "Company rule: data classification",
    } <= labels


def test_guided_files_do_not_inherit_and_the_chain_is_self_contained() -> None:
    assert check(make_request("commerce")).header.inherits == []
    spec = check(make_request("custom", policy=make_policy("enterprise-it-services-firm")))
    assert spec.header.inherits == ["enterprise-it-services-firm", "platform-default@1"]


def test_named_enterprise_systems_become_adapter_integrations() -> None:
    spec = check(
        make_request(
            "internal_workforce",
            policy=make_policy("enterprise-it-services-firm"),
            integrations__list=["SAP", "Okta", "SAP", "!!"],
        )
    )
    named = [i for i in spec.integrations.items if i.source == "brief"]
    assert [(i.id, i.mode) for i in named] == [("INT-sap", "adapter"), ("INT-okta", "adapter")]


def test_the_policy_screen_cap_is_the_scope_guard_cap() -> None:
    spec = check(
        make_request(
            "commerce",
            features=[f.id for f in load_pack("commerce").features],
            policy=make_policy("enterprise-it-services-firm", max_screens=12),
        )
    )
    assert len(spec.screens) <= 12 and spec.backlog


# --------------------------------------------------------------------------- misc


def test_unknown_archetype_falls_back_to_custom_and_custom_is_generic() -> None:
    assert resolve_pack_id(SlotView({})) == "custom"
    spec = check(make_request("space_opera"))
    assert spec.app.name == "My App"
    assert render_spec(spec) == render_spec(check(make_request("custom")))


def test_unknown_feature_ids_are_ignored() -> None:
    spec = check(make_request("commerce", features=["catalog_browse", "teleport"]))
    assert len(spec.screens) >= 10


def test_answer_slots_override_the_snapshot() -> None:
    from helpers import slot

    req = make_request("commerce", brand__name="Old name")
    req = req.model_copy(update={"answer_slots": {"brand.name": slot("brand.name", "New name")}})
    assert check(req).app.name == "New name"


def test_defaults_match_the_pack_defaults() -> None:
    pack = load_pack("commerce")
    explicit = check(make_request("commerce", features=default_features(pack)))
    implicit = check(make_request("commerce"))
    assert render_spec(explicit) == render_spec(implicit)


def test_revision_and_header_carry_the_request() -> None:
    req = make_request("commerce", spec_version=4)
    spec = check(req)
    assert spec.header.spec_version == 4 == spec.revision.version
    assert spec.revision.previous_version is None and spec.revision.changed_ids == []
    assert spec.header.brief_hash == req.brief.hash and spec.header.app_id == req.app_id
    assert spec.header.stack_profile == STACK.id


def test_build_sequence_is_present_and_complete() -> None:
    spec = check(make_request("commerce"))
    assert [s.id for s in spec.build.steps] == [f"S{i}" for i in range(11)]
    scheduled = {sid for step in spec.build.steps for sid in step.screens}
    scheduled |= {sid for step in spec.build.steps for g in step.parallel for sid in g.screens}
    assert scheduled == {s.id for s in spec.screens}
