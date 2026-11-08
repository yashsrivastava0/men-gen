from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
import yaml

from mde_content.packs import (
    SCREEN_CAP,
    LoadedPack,
    build_pack,
    core_features,
    default_features,
    load_pack,
    load_pack_file,
    load_packs,
    pack_ids,
    select_features,
    validate_pack,
)
from mde_content.recipes import recipe_ids
from mde_content.themes import theme_ids
from mde_contracts.content import DomainPack, PackNavNode, PackScreen
from mde_contracts.spec import ScreenAction

FEATURES = {
    "commerce": [
        "catalog_browse",
        "search",
        "cart_checkout",
        "payments",
        "order_tracking",
        "reviews",
        "favorites",
        "push_notifications",
        "loyalty",
        "support_chat",
    ],
    "food_delivery": [
        "restaurant_browse",
        "menu_item_options",
        "cart_checkout",
        "payments",
        "live_order_tracking",
        "ratings",
        "saved_addresses",
        "push_notifications",
        "promo_codes",
        "support",
    ],
    "booking_services": [
        "service_catalog",
        "provider_profiles",
        "slot_booking",
        "payments",
        "reminders",
        "reviews",
        "reschedule_cancel",
        "push_notifications",
        "memberships",
        "support",
    ],
    "internal_workforce": [
        "task_lists",
        "forms_checklists",
        "approvals",
        "timesheets",
        "directory",
        "announcements",
        "offline_sync",
        "push_notifications",
        "kiosk_mode",
        "reports",
    ],
    "custom": [
        "list_detail",
        "search",
        "forms",
        "auth_profile",
        "notifications",
        "settings",
        "media_gallery",
        "maps",
        "payments_mock",
    ],
}
PACKS = sorted(FEATURES)


def _scenarios(pack: DomainPack) -> Iterator[tuple[str, list[str]]]:
    """Feature selections that must all expand to a valid pack."""
    ids = [f.id for f in pack.features]
    core = set(core_features(pack))
    yield "core-only", []
    yield "defaults", default_features(pack)
    yield "everything", ids
    for fid in ids:
        if fid not in core:
            yield f"without-{fid}", [x for x in ids if x != fid]
            yield f"only-{fid}", [fid]


def _walk(node: PackNavNode) -> Iterator[PackNavNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


# --------------------------------------------------------------------------- the set of packs


def test_the_five_packs_are_packaged() -> None:
    assert pack_ids() == PACKS
    assert set(load_packs()) == set(PACKS)
    with pytest.raises(KeyError):
        load_pack("nope")


@pytest.mark.parametrize("pid", PACKS)
def test_feature_ids_are_exactly_the_agreed_ones(pid: str) -> None:
    pack = load_pack(pid)
    assert [f.id for f in pack.features] == FEATURES[pid]
    assert pack.id == pack.archetype == pid


@pytest.mark.parametrize("pid", PACKS)
def test_feature_tiers_and_translations(pid: str) -> None:
    pack = load_pack(pid)
    core = core_features(pack)
    assert 2 <= len(core) <= 4, "a few features are always on, the rest are optional"
    assert len(default_features(pack)) >= len(core) + 2
    for f in pack.features:
        assert f.label and f.description, f.id
        assert f.i18n.get("hi"), f"{pid}.{f.id} needs a Hindi label"
        assert f.tier in ("core", "standard", "extra")
        # "extra" means not ticked by default; core and standard are ticked.
        assert (f.tier == "extra") == (f.default_on is False), f"{pid}.{f.id}"


# What the question bank pre-ticks per archetype (docs/packages/content.md, questions half):
# the pack's default_on must match, so the checklist and the expansion agree.
PRE_TICKED = {
    "commerce": ["catalog_browse", "search", "cart_checkout", "payments", "order_tracking", "push_notifications"],
    "food_delivery": ["restaurant_browse", "menu_item_options", "cart_checkout", "payments", "live_order_tracking", "saved_addresses", "push_notifications"],
    "booking_services": ["service_catalog", "provider_profiles", "slot_booking", "payments", "reminders", "reschedule_cancel", "push_notifications"],
    "internal_workforce": ["task_lists", "forms_checklists", "approvals", "announcements", "push_notifications"],
    "custom": ["list_detail", "search", "auth_profile", "settings"],
}  # fmt: skip
LOOKS = {
    "commerce": ["clean-bright", "bold-dark", "warm-friendly"],
    "food_delivery": ["warm-friendly", "vibrant-playful", "clean-bright"],
    "booking_services": ["clean-bright", "warm-friendly", "minimal-mono"],
    "internal_workforce": ["corporate-calm", "clean-bright", "minimal-mono"],
    "custom": ["clean-bright", "minimal-mono", "vibrant-playful"],
}
APP_IDS = {"customer", "business", "courier", "admin", "employee"}  # scope.apps values
USER_ROLE_IDS = {"ROLE-user", "ROLE-staff", "ROLE-admin", "ROLE-employee", "ROLE-manager"}
FLAGS = {  # sensitive domain -> (flag id, must-confirm card id)
    "health": ("health_data_privacy", "Q-comp-01"),
    "finance": ("financial_data", "Q-comp-02"),
    "children": ("child_safety", "Q-comp-03"),
    "identity_documents": ("identity_documents", "Q-comp-04"),
    "precise_location": ("precise_location", "Q-comp-05"),
}


@pytest.mark.parametrize("pid", PACKS)
def test_defaults_match_the_question_bank_checklist(pid: str) -> None:
    assert default_features(load_pack(pid)) == PRE_TICKED[pid]


@pytest.mark.parametrize("pid", PACKS)
def test_look_options_match_the_question_bank(pid: str) -> None:
    pack = load_pack(pid)
    assert pack.theme_candidates == LOOKS[pid]
    assert pack.default_theme == LOOKS[pid][0]


@pytest.mark.parametrize("pid", PACKS)
def test_surfaces_are_app_ids_and_the_first_one_is_the_main_app(pid: str) -> None:
    pack = load_pack(pid)
    surfaces = {s.surface for s in pack.screens} | {r.surface for r in pack.roles}
    assert surfaces <= APP_IDS
    assert pack.screens[0].surface == pack.roles[0].surface, "the main app comes first"
    assert pack.roles[0].surface in ("customer", "employee")


def test_roles_cover_the_question_bank_choices() -> None:
    assert {"ROLE-employee", "ROLE-manager", "ROLE-admin"} <= {
        r.id for r in load_pack("internal_workforce").roles
    }
    assert {"ROLE-user", "ROLE-staff", "ROLE-admin"} <= {r.id for r in load_pack("custom").roles}
    assert USER_ROLE_IDS  # documented vocabulary, see the format doc


@pytest.mark.parametrize("pid", PACKS)
def test_compliance_triggers_use_the_agreed_flag_and_card_ids(pid: str) -> None:
    for t in load_pack(pid).compliance_triggers:
        flag, card = FLAGS[t.sensitive_domain]
        assert t.cards == [card], f"{pid}.{t.id}"
        assert t.conservative_defaults == {"compliance.flags": [flag]}, f"{pid}.{t.id}"


# --------------------------------------------------------------------------- validity


@pytest.mark.parametrize("pid", PACKS)
def test_full_pack_validates(pid: str) -> None:
    assert validate_pack(load_pack_file(pid)) == []


@pytest.mark.parametrize("pid", PACKS)
def test_every_feature_selection_expands_to_a_valid_pack(pid: str) -> None:
    loaded = load_pack_file(pid)
    for name, selection in _scenarios(loaded.pack):
        expanded = select_features(loaded, selection)
        # Removing a screen can break a flow's screen-to-screen chain; everything else must hold.
        assert validate_pack(expanded, check_flow_links=False) == [], f"{pid}: {name}"
        assert [f for f in expanded.flows if f.primary], f"{pid}: {name} lost its primary flow"


@pytest.mark.parametrize("pid", PACKS)
def test_screen_count_with_all_features_on(pid: str) -> None:
    pack = load_pack(pid)
    assert 25 <= len(pack.screens) <= 45
    assert len(pack.screens) <= SCREEN_CAP
    assert len(select_features(pid, [f.id for f in pack.features]).screens) == len(pack.screens)


@pytest.mark.parametrize("pid", PACKS)
def test_every_pack_screen_has_a_recipe_from_the_library(pid: str) -> None:
    known = recipe_ids()
    assert all(s.recipe in known for s in load_pack(pid).screens)


@pytest.mark.parametrize("pid", PACKS)
def test_each_pack_has_one_primary_flow_through_always_on_screens(pid: str) -> None:
    pack = load_pack(pid)
    primary = [f for f in pack.flows if f.primary]
    assert len(primary) == 1
    core = set(core_features(pack))
    by_key = {s.key: s for s in pack.screens}
    assert len(primary[0].screens) >= 4
    assert primary[0].purpose
    for key in primary[0].screens:
        assert by_key[key].feature is None or by_key[key].feature in core


@pytest.mark.parametrize("pid", PACKS)
def test_every_feature_has_screens(pid: str) -> None:
    pack = load_pack(pid)
    gated = {s.feature for s in pack.screens}
    assert {f.id for f in pack.features} <= gated | {None}
    assert {f.id for f in pack.features} - gated == set()


@pytest.mark.parametrize("pid", PACKS)
def test_flows_are_screen_to_screen_chains(pid: str) -> None:
    pack = load_pack(pid)
    by_key = {s.key: s for s in pack.screens}
    for flow in pack.flows:
        assert len(flow.screens) >= 2
        assert len(set(flow.screens)) == len(flow.screens)
        for a, b in zip(flow.screens, flow.screens[1:], strict=False):
            assert any(x.go == f"@{b}" for x in by_key[a].actions), f"{flow.key}: {a} -> {b}"


@pytest.mark.parametrize("pid", PACKS)
def test_navigation_tree_shape(pid: str) -> None:
    root = load_pack(pid).navigation
    nodes = list(_walk(root))
    assert root.kind == "auth_gate" and root.id == "NAV-001"
    assert all(re.fullmatch(r"NAV-\d{3}", n.id) for n in nodes)
    assert any(n.kind == "tabs" for n in nodes), "a pack has a tab bar"
    assert all(n.screen is None or re.fullmatch(r"@[a-z][a-z0-9_]*", n.screen) for n in nodes)
    tabs = [n for n in nodes if n.kind == "tabs"]
    assert all(3 <= len(t.children) <= 5 for t in tabs), "a tab bar holds three to five tabs"


@pytest.mark.parametrize("pid", PACKS)
def test_ids_follow_the_contract_patterns(pid: str) -> None:
    pack = load_pack(pid)
    assert all(re.fullmatch(r"ENT-[A-Za-z][A-Za-z0-9]*", e.id) for e in pack.entities)
    assert all(re.fullmatch(r"API-[A-Za-z][A-Za-z0-9]*", o.id) for o in pack.api_operations)
    assert all(re.fullmatch(r"ROLE-[a-z][a-z0-9_]*", r.id) for r in pack.roles)
    assert all(re.fullmatch(r"INT-[a-z][a-z0-9_]*", i.id) for i in pack.integrations)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", s.key) for s in pack.screens)


@pytest.mark.parametrize("pid", PACKS)
def test_screens_are_briefs_not_stubs(pid: str) -> None:
    for s in load_pack(pid).screens:
        assert len(s.purpose) >= 20, s.key
        assert 1 <= len(s.checks) <= 3, s.key
        if s.recipe not in ("welcome", "empty-state-only", "confirmation"):
            assert s.actions or s.data or s.sections, f"{s.key} does nothing"


@pytest.mark.parametrize("pid", PACKS)
def test_defaults_and_integrations_are_safe(pid: str) -> None:
    pack = load_pack(pid)
    assert pack.default_theme in pack.theme_candidates
    assert len(pack.theme_candidates) == 3 and set(pack.theme_candidates) <= set(theme_ids())
    assert pack.integrations, "default integrations are listed"
    assert all(i.mode == "mock" for i in pack.integrations), "mock by default"
    assert all(i.needs_dev_build is False for i in pack.integrations)
    assert {r.id for r in pack.roles} and len(pack.roles) >= 2
    assert len(pack.entities) >= 8 and len(pack.api_operations) >= 20


def test_precise_location_triggers_where_relevant() -> None:
    for pid in ("food_delivery", "booking_services"):
        triggers = load_pack(pid).compliance_triggers
        assert any(t.sensitive_domain == "precise_location" for t in triggers), pid
    for pid in PACKS:
        for t in load_pack(pid).compliance_triggers:
            assert t.keywords and t.research_hint, f"{pid}.{t.id}"


def test_payments_are_mock_and_money_is_never_a_float() -> None:
    for pid in PACKS:
        pack = load_pack(pid)
        assert all(i.mode == "mock" for i in pack.integrations if "payment" in i.id)
        for e in pack.entities:
            for f in e.fields:
                assert f.type != "number" or f.name in {
                    "rating",
                    "latitude",
                    "longitude",
                    "score",
                }, f"{pid}.{e.id}.{f.name}: money fields use type money"


# --------------------------------------------------------------------------- round trip


@pytest.mark.parametrize("pid", PACKS)
def test_yaml_round_trips_through_the_contract_models(pid: str) -> None:
    loaded = load_pack_file(pid)
    dumped = loaded.pack.model_dump()
    raw = yaml.safe_load(yaml.safe_dump(dumped))
    raw["feature_parts"] = yaml.safe_load(
        yaml.safe_dump({k: v.model_dump() for k, v in loaded.feature_parts.items()})
    )
    again = build_pack(raw)
    assert again == loaded


# --------------------------------------------------------------------------- selection semantics


def test_unknown_feature_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown feature"):
        select_features("commerce", ["loyalty", "teleport"])


def test_defaults_leave_out_the_extras() -> None:
    pack = select_features("commerce")
    assert "loyalty" not in {f.id for f in pack.features}
    assert {s.key for s in pack.screens}.isdisjoint({"loyalty", "loyalty_history", "support_chat"})
    assert "search" in {f.id for f in pack.features}


def test_core_features_are_always_on() -> None:
    pack = select_features("commerce", [])
    assert {f.id for f in pack.features} == {"catalog_browse", "cart_checkout"}
    keys = {s.key for s in pack.screens}
    assert {"home", "product_detail", "cart", "checkout", "order_confirmation"} <= keys


def test_turning_off_payments_removes_everything_it_owns_and_leaves_no_dangling_reference() -> None:
    full = load_pack_file("commerce")
    off = select_features(full, [f.id for f in full.pack.features if f.id != "payments"])
    keys = {s.key for s in off.screens}
    assert {"payment_methods", "add_payment_method", "payment_failed"}.isdisjoint(keys)
    assert not {o.id for o in off.api_operations} & {"API-createPayment", "API-addPaymentMethod"}
    assert {e.id for e in off.entities}.isdisjoint({"ENT-Payment", "ENT-PaymentMethod"})
    assert "INT-payments" not in {i.id for i in off.integrations}
    checkout = next(s for s in off.screens if s.key == "checkout")
    assert "pay_now" not in {a.name for a in checkout.actions}
    assert "payment_method" not in checkout.sections
    assert "place_order" in {a.name for a in checkout.actions}, "pay on delivery still works"
    assert "PaymentStatus" not in {e.name for e in off.enums}
    assert "PaymentMethodType" in {e.name for e in off.enums}, "orders still record the method"
    assert all(
        a.go not in ("@payment_methods", "@payment_failed") for s in off.screens for a in s.actions
    )


def test_turning_off_reviews_removes_the_section_on_an_always_on_screen() -> None:
    full = [f.id for f in load_pack("commerce").features]
    off = select_features("commerce", [f for f in full if f != "reviews"])
    detail = next(s for s in off.screens if s.key == "product_detail")
    assert "reviews_preview" not in detail.sections
    assert "see_all_reviews" not in {a.name for a in detail.actions}
    on = select_features("commerce", full)
    assert "reviews_preview" in next(s for s in on.screens if s.key == "product_detail").sections


def test_navigation_is_pruned_with_the_screens() -> None:
    off = select_features("commerce", [])
    nodes = list(_walk(off.navigation))
    labels = {n.label for n in nodes}
    assert "Search" not in labels and "Orders" not in labels
    assert {"Home", "Cart", "Account"} <= labels
    assert all(n.kind not in ("tabs", "stack") or n.screen or n.children for n in nodes)


def test_selection_does_not_mutate_the_loaded_pack() -> None:
    before = load_pack("commerce").model_dump()
    select_features("commerce", [])
    assert load_pack("commerce").model_dump() == before


# --------------------------------------------------------------------------- the validator catches mistakes


def _mutated(pid: str, **changes: object) -> DomainPack:
    return load_pack(pid).model_copy(update=changes)


def _replace_screen(pack: DomainPack, key: str, **changes: object) -> DomainPack:
    return pack.model_copy(
        update={
            "screens": [s.model_copy(update=changes) if s.key == key else s for s in pack.screens]
        }
    )


def test_validator_reports_unknown_recipe() -> None:
    bad = _replace_screen(load_pack("commerce"), "cart", recipe="no-such-recipe")
    assert any("unknown recipe" in p for p in validate_pack(bad))


def test_validator_reports_dangling_action_target_and_call() -> None:
    pack = load_pack("commerce")
    bad = _replace_screen(
        pack,
        "cart",
        actions=[ScreenAction(name="x", go="@nowhere"), ScreenAction(name="y", calls="API-nope")],
    )
    problems = validate_pack(bad)
    assert any("unknown screen @nowhere" in p for p in problems)
    assert any("unknown operation API-nope" in p for p in problems)


def test_validator_reports_unknown_role_entity_and_theme() -> None:
    pack = load_pack("commerce")
    ops = [
        pack.api_operations[0].model_copy(
            update={"roles": ["ROLE-ghost"], "response_entity": "ENT-Ghost"}
        ),
        *pack.api_operations[1:],
    ]
    problems = validate_pack(
        pack.model_copy(
            update={
                "api_operations": ops,
                "theme_candidates": ["neon", "clean-bright", "bold-dark"],
            }
        )
    )
    assert any("unknown role ROLE-ghost" in p for p in problems)
    assert any("entity ENT-Ghost does not exist" in p for p in problems)
    assert any("unknown theme candidate" in p for p in problems)


def test_validator_reports_flow_problems() -> None:
    pack = load_pack("commerce")
    flows = [pack.flows[0].model_copy(update={"screens": ["home", "ghost"]}), *pack.flows[1:]]
    problems = validate_pack(pack.model_copy(update={"flows": flows}))
    assert any("unknown screen ghost" in p for p in problems)
    unlinked = [pack.flows[0].model_copy(update={"screens": ["home", "checkout"]}), *pack.flows[1:]]
    assert any(
        "no action that goes to checkout" in p
        for p in validate_pack(pack.model_copy(update={"flows": unlinked}))
    )
    no_primary = [f.model_copy(update={"primary": False}) for f in pack.flows]
    assert any(
        "primary flow" in p for p in validate_pack(pack.model_copy(update={"flows": no_primary}))
    )


def test_validator_reports_duplicates_and_unreachable_screens() -> None:
    pack = load_pack("commerce")
    dup = pack.model_copy(update={"screens": [*pack.screens, pack.screens[0]]})
    assert any("duplicate screen key" in p for p in validate_pack(dup))
    orphan = PackScreen(
        key="orphan",
        route="/orphan",
        recipe="list",
        purpose="A screen nothing links to, used to prove reachability is checked",
        checks=["It is never shown"],
    )
    problems = validate_pack(pack.model_copy(update={"screens": [*pack.screens, orphan]}))
    assert any("screen orphan is not reachable" in p for p in problems)


def test_validator_reports_a_screen_audience_mismatch() -> None:
    pack = load_pack("commerce")
    bad = _replace_screen(
        pack, "home", data=[pack.screens[3].data[0].model_copy(update={"bind": "API-getCart"})]
    )
    assert any(
        "uses API-getCart (authenticated) from a public screen" in p for p in validate_pack(bad)
    )


def test_validator_reports_too_many_screens() -> None:
    pack = load_pack("commerce")
    extra = [
        pack.screens[0].model_copy(update={"key": f"extra_{i}", "route": f"/extra/{i}"})
        for i in range(SCREEN_CAP)
    ]
    problems = validate_pack(pack.model_copy(update={"screens": [*pack.screens, *extra]}))
    assert any("exceeds the cap" in p for p in problems)


def test_validator_checks_feature_parts() -> None:
    loaded = load_pack_file("commerce")
    bad_parts = {
        **loaded.feature_parts,
        "reviews": loaded.feature_parts["reviews"].model_copy(update={"operations": ["API-ghost"]}),
    }
    problems = validate_pack(LoadedPack(pack=loaded.pack, feature_parts=bad_parts))
    assert any("unknown operation API-ghost" in p for p in problems)


# --------------------------------------------------------------------------- more validator cases


def _first_entity(pack: DomainPack, **changes: object) -> DomainPack:
    return pack.model_copy(
        update={"entities": [pack.entities[0].model_copy(update=changes), *pack.entities[1:]]}
    )


def test_validator_reports_entity_problems() -> None:
    pack = load_pack("commerce")
    order = next(e for e in pack.entities if e.id == "ENT-Order")
    fields = [
        order.fields[0].model_copy(update={"ref": "ENT-Ghost"}),
        order.fields[1].model_copy(update={"enum": "NoSuchEnum"}),
        *order.fields[2:],
    ]
    sm = order.state_machine
    assert sm is not None
    bad_sm = sm.model_copy(update={"field": "no_such_field", "states": ["placed"]})
    bad_order = order.model_copy(update={"fields": fields, "state_machine": bad_sm})
    entities = [bad_order if e.id == order.id else e for e in pack.entities]
    problems = validate_pack(pack.model_copy(update={"entities": entities}))
    assert any("ref to unknown entity 'ENT-Ghost'" in p for p in problems)
    assert any("unknown enum 'NoSuchEnum'" in p for p in problems)
    assert any("state machine field 'no_such_field'" in p for p in problems)
    assert any("uses an unknown state" in p for p in problems)


def test_validator_reports_operation_problems() -> None:
    pack = load_pack("commerce")
    op = pack.api_operations[0].model_copy(
        update={"path": "no-slash", "request_entity": "ENT-Ghost", "auth": "ROLE-customer"}
    )
    problems = validate_pack(
        pack.model_copy(update={"api_operations": [op, *pack.api_operations[1:]]})
    )
    assert any("path must start with '/'" in p for p in problems)
    assert any("request entity ENT-Ghost does not exist" in p for p in problems)
    assert any("must be a defined role listed in roles" in p for p in problems)


def test_validator_reports_screen_problems() -> None:
    pack = load_pack("commerce")
    bad = _replace_screen(
        pack,
        "cart",
        checks=[],
        sections=["Not_Snake", "Not_Snake"],
        actions=[ScreenAction(name="a"), ScreenAction(name="a", go="cart")],
        data=[pack.screens[3].data[0].model_copy(update={"bind": "ENT-Cart.ghost"})],
        feature="ghost_feature",
        surface="nowhere",
        route="no-slash",
    )
    problems = validate_pack(bad)
    for needle in (
        "needs 1 to 3 checks",
        "is not snake_case",
        "duplicate section",
        "duplicate action",
        "neither calls nor goes anywhere",
        "is not an @key",
        "names an unknown entity field",
        "unknown feature 'ghost_feature'",
        "no role has surface 'nowhere'",
        "route must start with '/'",
    ):
        assert any(needle in p for p in problems), needle


def test_validator_reports_navigation_problems() -> None:
    pack = load_pack("commerce")
    nav = pack.navigation
    tabs = next(n for n in nav.children if n.kind == "tabs")
    ghost = PackNavNode.model_construct(
        id="NAV-900", route="/ghost", kind="screen", screen="@ghost", children=[], auth="public"
    )
    wrong_route = tabs.children[0].model_copy(update={"route": "/elsewhere"})
    bad_tabs = tabs.model_copy(update={"children": [wrong_route, ghost, *tabs.children[1:]]})
    new_root = nav.model_copy(
        update={"children": [bad_tabs if n is tabs else n for n in nav.children]}
    )
    problems = validate_pack(pack.model_copy(update={"navigation": new_root}))
    assert any("screen '@ghost' is not a pack screen @key" in p for p in problems)
    assert any("differs from screen route" in p for p in problems)


def test_validator_reports_unused_things_and_bad_parts() -> None:
    loaded = load_pack_file("commerce")
    extra_op = loaded.pack.api_operations[0].model_copy(
        update={"id": "API-neverUsed", "method": "GET", "path": "/never-used"}
    )
    extra_entity = loaded.pack.entities[0].model_copy(update={"id": "ENT-Lonely"})
    pack = loaded.pack.model_copy(
        update={
            "api_operations": [*loaded.pack.api_operations, extra_op],
            "entities": [*loaded.pack.entities, extra_entity],
        }
    )
    problems = validate_pack(pack)
    assert "unused API operation: API-neverUsed" in problems
    assert "unused entity: ENT-Lonely" in problems
    parts = {
        **loaded.feature_parts,
        "ghost": loaded.feature_parts["reviews"],
        "reviews": loaded.feature_parts["reviews"].model_copy(
            update={
                "screens": {
                    "ghost_screen": loaded.feature_parts["favorites"].screens["product_detail"],
                    "product_detail": loaded.feature_parts["favorites"]
                    .screens["product_detail"]
                    .model_copy(
                        update={
                            "sections": ["no_section"],
                            "actions": ["no_action"],
                            "data": ["no_bind"],
                        }
                    ),
                }
            }
        ),
    }
    problems = validate_pack(LoadedPack(pack=loaded.pack, feature_parts=parts))
    assert any("unknown feature 'ghost'" in p for p in problems)
    assert any("unknown screen ghost_screen" in p for p in problems)
    assert any("unknown section no_section" in p for p in problems)
    assert any("unknown action no_action" in p for p in problems)
    assert any("unknown binding no_bind" in p for p in problems)
    assert any("owned by both" in p for p in problems)


def test_validator_reports_integration_and_trigger_problems() -> None:
    pack = load_pack("commerce")
    pay = next(i for i in pack.integrations if i.id == "INT-payments")
    trigger = pack.compliance_triggers[0].model_copy(update={"sensitive_domain": "weather"})
    problems = validate_pack(
        pack.model_copy(
            update={
                "integrations": [
                    pay.model_copy(update={"mode": "real"}),
                    *[i for i in pack.integrations if i.id != "INT-payments"],
                ],
                "compliance_triggers": [trigger, *pack.compliance_triggers[1:]],
                "default_theme": "neon",
            }
        )
    )
    assert any("payments integrations must default to mock" in p for p in problems)
    assert any("unknown sensitive domain 'weather'" in p for p in problems)
    assert any("default theme 'neon'" in p for p in problems)


def test_select_features_accepts_a_loaded_pack_and_rejects_unknown_packs() -> None:
    loaded = load_pack_file("custom")
    assert select_features(loaded, ["search"]).id == "custom"
    with pytest.raises(KeyError):
        select_features("nope")
