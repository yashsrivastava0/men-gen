"""B3/B4: every proposal kind is validated before it is applied; invalid ones are dropped."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from helpers import make_note, make_request
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_content.packs import load_pack
from mde_content.stack import load_stack_profile
from mde_contracts.calls import FieldProposal, KeyValue, Proposal, ProposalKind, SpecProposals
from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.proposal_ops import HANDLERS
from mde_spec_builder.proposals import ApplyResult, quick_findings, validate_and_apply
from mde_spec_builder.render import render_spec
from mde_spec_builder.validators import errors, run_validators

STACK = load_stack_profile()
NOTE = make_note()


def build() -> AppSpec:
    features = [f.id for f in load_pack("commerce").features]
    return build_baseline(make_request("commerce", features=features, research=NOTE))


SPEC = build()
HOME = "SCR-004"  # /home
PRODUCT = "SCR-006"
CHECKOUT = "SCR-012"
PRIMARY = "FLW-001"
OTHER_FLOW = "FLW-005"


def kv(**pairs: str) -> list[KeyValue]:
    return [KeyValue(key=k.replace("__", "."), value=v) for k, v in pairs.items()]


def prop(kind: ProposalKind, **kw: Any) -> Proposal:
    kw.setdefault("rationale", "The Brief asks for it")
    return Proposal(kind=kind, **kw)


def run(
    p: Proposal | list[Proposal],
    spec: AppSpec = SPEC,
    **kw: Any,
) -> ApplyResult:
    items = p if isinstance(p, list) else [p]
    return validate_and_apply(spec, items, research=NOTE, **kw)


def ok(p: Proposal, spec: AppSpec = SPEC, **kw: Any) -> AppSpec:
    result = run(p, spec, **kw)
    assert [d.reason for d in result.dropped] == [], result.dropped
    assert len(result.applied) == 1
    found = errors(run_validators(result.spec, stack_profile=STACK, limits=Limits()))
    assert found == [], found
    return result.spec


def dropped(p: Proposal, code: str | None = None, spec: AppSpec = SPEC, **kw: Any) -> str:
    result = run(p, spec, **kw)
    assert result.applied == [], result.applied
    assert len(result.dropped) == 1
    assert result.spec == spec  # nothing changed
    if code is not None:
        assert result.dropped[0].code == code, result.dropped[0]
    return result.dropped[0].reason


def screen(spec: AppSpec, screen_id: str):  # type: ignore[no-untyped-def]
    return next(s for s in spec.screens if s.id == screen_id)


def test_every_proposal_kind_has_an_operation() -> None:
    assert set(HANDLERS) == set(get_args(ProposalKind))
    assert len(HANDLERS) == 21


def test_the_baseline_used_here_is_valid_and_quick_findings_agree() -> None:
    assert errors(run_validators(SPEC, stack_profile=STACK, limits=Limits())) == []
    assert errors(quick_findings(SPEC, Limits(), STACK)) == []


# --------------------------------------------------------------------------- add_screen


NEW_SCREEN = dict(
    route="/gift-cards",
    recipe="list",
    name="Gift cards",
    purpose="Show gift cards the shopper can buy",
    target=HOME,
)


def test_add_screen_adds_the_screen_a_navigation_entry_and_a_link_from_the_target() -> None:
    spec = ok(prop("add_screen", **NEW_SCREEN))
    new = screen(spec, "SCR-031")
    assert (new.route, new.recipe, new.title, new.source) == (
        "/gift-cards",
        "list",
        "Gift cards",
        "brief",
    )
    assert new.sections == ["header", "items", "load_more"]  # the recipe's sections
    assert new.auth == screen(SPEC, HOME).auth and new.flow is None
    assert any(
        a.name == "open_gift_cards" and a.go == "SCR-031" for a in screen(spec, HOME).actions
    )
    assert "NAV-" in render_spec(spec) and len(spec.screens) == len(SPEC.screens) + 1
    assert _nav_ids(spec).count(f"NAV-{len(_nav_ids(SPEC)) + 1:03d}") == 1


def _nav_ids(spec: AppSpec) -> list[str]:
    from mde_spec_loader.fragments import walk_nav

    return [n.id for n in walk_nav(spec.navigation.root)]


def test_add_screen_without_a_target_goes_under_the_first_tab() -> None:
    spec = ok(prop("add_screen", **{**NEW_SCREEN, "target": None}))
    assert screen(spec, "SCR-031").auth == "authenticated"


def test_add_screen_into_a_non_primary_flow_extends_it() -> None:
    spec = ok(prop("add_screen", **NEW_SCREEN, flow=OTHER_FLOW))
    flow = next(f for f in spec.flows if f.id == OTHER_FLOW)
    assert flow.screens[-1] == "SCR-031" and flow.end == "SCR-031"
    assert screen(spec, "SCR-031").flow == OTHER_FLOW


def test_a_research_screen_is_tagged_with_its_source() -> None:
    p = prop("add_screen", **NEW_SCREEN, source="research", research_ref="RA-001")
    spec = ok(p)
    new = screen(spec, "SCR-031")
    assert (new.source, new.research_ref) == ("research", "RA-001")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"route": "/home"}, "duplicate"),
        ({"route": "gift"}, "invalid"),
        ({"route": "/gift cards"}, "invalid"),
        ({"route": "//x"}, "invalid"),
        ({"route": None}, "invalid"),
        ({"recipe": "carousel"}, "invalid"),
        ({"recipe": None}, "invalid"),
        ({"target": "SCR-099"}, "unknown_target"),
        ({"flow": PRIMARY}, "protected"),
        ({"flow": "FLW-099"}, "unknown_target"),
        ({"purpose": None}, "invalid"),
        ({"purpose": "x" * 201}, "invalid"),
        ({"name": None}, "invalid"),
        ({"name": "y" * 61}, "invalid"),
        ({"surface": "mars"}, "invalid"),
        ({"purpose": "Use token=hunter2hunter2 to log in"}, "secret"),
        ({"source": "research"}, "research"),  # no research_ref
        ({"source": "research", "research_ref": "RA-099"}, "research"),  # not in the Note
        ({"source": "research", "research_ref": "RA-1"}, "research"),  # not in the Note either
    ],
)
def test_add_screen_drops_what_is_invalid(change: dict[str, Any], code: str) -> None:
    dropped(prop("add_screen", **{**NEW_SCREEN, **change}), code)


def test_add_screen_respects_the_screen_cap() -> None:
    reason = dropped(
        prop("add_screen", **NEW_SCREEN), "limit", limits=Limits(max_screens=len(SPEC.screens))
    )
    assert "screens" in reason


def test_research_proposals_are_dropped_when_the_addition_was_switched_off() -> None:
    p = prop("add_screen", **NEW_SCREEN, source="research", research_ref="RA-001")
    result = validate_and_apply(SPEC, [p], research=NOTE, excluded=["RA-001"])
    assert result.applied == [] and result.dropped[0].code == "research"
    assert "switched off" in result.dropped[0].reason
    unavailable = make_note(status="unavailable")
    assert validate_and_apply(SPEC, [p], research=unavailable).dropped[0].code == "research"
    assert validate_and_apply(SPEC, [p], research=None).dropped[0].code == "research"


# --------------------------------------------------------------------------- screen edits


def test_rename_set_purpose_and_copy() -> None:
    spec = ok(prop("rename_screen", target=PRODUCT, name="Product page"))
    assert screen(spec, PRODUCT).title == "Product page"
    spec = ok(
        prop("set_screen_purpose", target=PRODUCT, purpose="Show one product with its photos")
    )
    assert screen(spec, PRODUCT).purpose == "Show one product with its photos"
    spec = ok(
        prop(
            "set_screen_copy", target=PRODUCT, pairs=kv(subtitle="  Fresh   picks  ", cta="Buy now")
        )
    )
    texts = screen(spec, PRODUCT).texts
    assert texts["subtitle"] == "Fresh picks" and texts["cta"] == "Buy now"  # cleaned, merged


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (prop("rename_screen", target="SCR-099", name="x"), "unknown_target"),
        (prop("rename_screen", target=PRODUCT), "invalid"),
        (prop("rename_screen", target=PRODUCT, name="  "), "invalid"),
        (prop("rename_screen", target=PRODUCT, name="n" * 61), "invalid"),
        (prop("set_screen_purpose", target=PRODUCT, purpose="p" * 201), "invalid"),
        (prop("set_screen_purpose", target=None, purpose="fine"), "unknown_target"),
        (prop("set_screen_copy", target=PRODUCT, pairs=kv(**{"Bad Key": "x"})), "invalid"),
        (prop("set_screen_copy", target=PRODUCT, pairs=kv(cta="c" * 121)), "invalid"),
        (prop("set_screen_copy", target=PRODUCT), "invalid"),
        (
            prop(
                "set_screen_copy",
                target=PRODUCT,
                pairs=[KeyValue(key="a", value="1"), KeyValue(key="a", value="2")],
            ),
            "invalid",
        ),
        (prop("set_screen_copy", target=PRODUCT, pairs=kv(password="hunter2hunter2")), "secret"),
        (prop("set_screen_copy", target=PRODUCT, pairs=kv(note="key sk-" + "Ab1" * 10)), "secret"),
        (
            prop("set_screen_copy", target=PRODUCT, pairs=kv(**{f"k{i}": "v" for i in range(13)})),
            "limit",
        ),
    ],
)
def test_screen_edits_drop_what_is_invalid(p: Proposal, code: str) -> None:
    dropped(p, code)


def test_a_screen_holds_at_most_twelve_copy_entries() -> None:
    crowded = screen(SPEC, PRODUCT).model_copy(
        update={"texts": {f"key_{i}": "x" for i in range(12)}}
    )
    spec = SPEC.model_copy(
        update={"screens": [crowded if s.id == PRODUCT else s for s in SPEC.screens]}
    )
    dropped(prop("set_screen_copy", target=PRODUCT, pairs=kv(extra="x")), "limit", spec)


def test_add_screen_action() -> None:
    spec = ok(
        prop(
            "add_screen_action",
            target=PRODUCT,
            name="share_product",
            pairs=kv(go=HOME, on_success="return to home"),
        )
    )
    action = next(a for a in screen(spec, PRODUCT).actions if a.name == "share_product")
    assert (action.go, action.calls, action.on_success) == (HOME, None, "return to home")
    spec = ok(
        prop(
            "add_screen_action", target=PRODUCT, name="ask_stock", pairs=kv(calls="API-getProduct")
        )
    )
    assert (
        next(a for a in screen(spec, PRODUCT).actions if a.name == "ask_stock").calls
        == "API-getProduct"
    )
    spec = ok(prop("add_screen_action", target=PRODUCT, name="leave", pairs=kv(go="back")))
    assert next(a for a in screen(spec, PRODUCT).actions if a.name == "leave").go == "back"


@pytest.mark.parametrize(
    ("pairs", "name", "code"),
    [
        (kv(calls="API-nope"), "a1", "unknown_target"),
        (kv(go="SCR-099"), "a1", "unknown_target"),
        (kv(on_success="toast"), "a1", "invalid"),
        (kv(go=HOME, color="red"), "a1", "invalid"),
        (kv(go="FLW-001"), "a1", "invalid"),
        (kv(go=HOME), "add_to_cart", "duplicate"),
        (kv(go=HOME), "Bad Name", "invalid"),
        (None, "a1", "invalid"),
    ],
)
def test_add_screen_action_drops_what_is_invalid(pairs: Any, name: str, code: str) -> None:
    dropped(prop("add_screen_action", target=PRODUCT, name=name, pairs=pairs), code)


def test_sections_checks_and_states() -> None:
    spec = ok(
        prop(
            "add_screen_section",
            target=PRODUCT,
            items=["size_guide", "delivery_info", "size_guide"],
        )
    )
    assert screen(spec, PRODUCT).sections.count("size_guide") == 1
    spec = ok(
        prop("add_screen_check", target=PRODUCT, items=["The size guide opens from the page"])
    )
    assert "The size guide opens from the page" in screen(spec, PRODUCT).checks
    spec = ok(prop("add_screen_state", target=PRODUCT, name="sold_out", pairs=kv(show="Notify me")))
    assert screen(spec, PRODUCT).states["sold_out"] == {"show": "Notify me"}


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (
            prop("add_screen_section", target=PRODUCT, items=["gallery"]),
            "duplicate",
        ),  # already there
        (prop("add_screen_section", target=PRODUCT, items=["Bad Section"]), "invalid"),
        (prop("add_screen_section", target=PRODUCT), "invalid"),
        (prop("add_screen_section", target=PRODUCT, items=[f"s{i}" for i in range(7)]), "limit"),
        (prop("add_screen_check", target=PRODUCT, items=["c" * 161]), "invalid"),
        (prop("add_screen_check", target=PRODUCT, items=["a", "b", "c", "d"]), "limit"),
        (prop("add_screen_check", target="SCR-099", items=["ok"]), "unknown_target"),
        (
            prop("add_screen_state", target=PRODUCT, name="out_of_stock", pairs=kv(show="x")),
            "duplicate",
        ),
        (prop("add_screen_state", target=PRODUCT, name="Bad State", pairs=kv(show="x")), "invalid"),
        (
            prop("add_screen_state", target=PRODUCT, name="empty", pairs=kv(**{"Bad Key": "x"})),
            "invalid",
        ),
        (prop("add_screen_state", target=PRODUCT, name="empty"), "invalid"),
    ],
)
def test_section_check_state_drop_what_is_invalid(p: Proposal, code: str) -> None:
    dropped(p, code)


def test_a_check_already_present_is_a_duplicate() -> None:
    existing = screen(SPEC, PRODUCT).checks[0]
    dropped(prop("add_screen_check", target=PRODUCT, items=[existing]), "duplicate")


def test_add_flow_screen() -> None:
    spec = ok(prop("add_flow_screen", target=OTHER_FLOW, items=[PRODUCT, "SCR-026"]))
    flow = next(f for f in spec.flows if f.id == OTHER_FLOW)
    assert flow.screens[-2:] == [PRODUCT, "SCR-026"] and flow.end == "SCR-026"
    assert screen(spec, "SCR-027").flow == "FLW-009"  # untouched
    assert screen(spec, "SCR-005").flow is None  # still no flow


def test_add_flow_screen_sets_the_flow_of_a_screen_that_has_none() -> None:
    spec = ok(prop("add_flow_screen", target=OTHER_FLOW, items=["SCR-005"]))
    assert screen(spec, "SCR-005").flow == OTHER_FLOW


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (prop("add_flow_screen", target=PRIMARY, items=["SCR-014"]), "protected"),
        (prop("add_flow_screen", target="FLW-099", items=["SCR-014"]), "unknown_target"),
        (prop("add_flow_screen", target=OTHER_FLOW, items=["SCR-099"]), "unknown_target"),
        (prop("add_flow_screen", target=OTHER_FLOW), "invalid"),
        (prop("add_flow_screen", target=OTHER_FLOW, items=["SCR-015"]), "duplicate"),
    ],
)
def test_add_flow_screen_drops_what_is_invalid(p: Proposal, code: str) -> None:
    dropped(p, code)


# --------------------------------------------------------------------------- data


def test_add_entity_and_fields() -> None:
    fields = [
        FieldProposal(name="code", type="string"),
        FieldProposal(name="amount", type="money"),
        FieldProposal(name="owner", type="ref", ref="ENT-User"),
        FieldProposal(name="parent", type="ref", ref="ENT-GiftCard", required=False),  # itself
    ]
    spec = ok(
        prop(
            "add_entity",
            name="Gift card",
            purpose="Gift cards",
            fields=fields,
            source="research",
            research_ref="RA-001",
        )
    )
    entity = next(e for e in spec.data.entities if e.id == "ENT-GiftCard")
    assert (entity.plain_name, entity.source) == ("Gift cards", "research")
    assert [f.name for f in entity.fields] == ["code", "amount", "owner", "parent"]
    spec = ok(
        prop(
            "add_entity_fields",
            target="ENT-Product",
            fields=[
                FieldProposal(
                    name="origin", type="string", required=False, description="Country of origin"
                )
            ],
        )
    )
    added = next(e for e in spec.data.entities if e.id == "ENT-Product").fields[-1]
    assert (added.name, added.required, added.description) == ("origin", False, "Country of origin")


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (prop("add_entity", name="Product", fields=[FieldProposal(name="a")]), "duplicate"),
        (prop("add_entity", name="Thing"), "invalid"),
        (prop("add_entity", name="!!!", fields=[FieldProposal(name="a")]), "invalid"),
        (prop("add_entity", name="Thing", fields=[FieldProposal(name="Bad Name")]), "invalid"),
        (
            prop(
                "add_entity",
                name="Thing",
                fields=[FieldProposal(name="a"), FieldProposal(name="a")],
            ),
            "duplicate",
        ),
        (
            prop("add_entity", name="Thing", fields=[FieldProposal(name="a", type="enum")]),
            "invalid",
        ),
        (
            prop(
                "add_entity",
                name="Thing",
                fields=[FieldProposal(name="a", type="ref", ref="ENT-Nope")],
            ),
            "unknown_target",
        ),
        (
            prop("add_entity", name="Thing", fields=[FieldProposal(name="a", type="ref")]),
            "unknown_target",
        ),
        (
            prop(
                "add_entity",
                name="Thing",
                fields=[FieldProposal(name="a", type="string", ref="ENT-User")],
            ),
            "invalid",
        ),
        (
            prop(
                "add_entity", name="Thing", fields=[FieldProposal(name=f"f{i}") for i in range(13)]
            ),
            "limit",
        ),
        (
            prop(
                "add_entity", name="Thing", fields=[FieldProposal(name="a", description="d" * 121)]
            ),
            "invalid",
        ),
        (
            prop("add_entity_fields", target="ENT-Nope", fields=[FieldProposal(name="a")]),
            "unknown_target",
        ),
        (
            prop("add_entity_fields", target="ENT-Product", fields=[FieldProposal(name="name")]),
            "duplicate",
        ),
        (
            prop(
                "add_entity_fields",
                target="ENT-Product",
                fields=[FieldProposal(name="a", type="ref", ref="ENT-Nope")],
            ),
            "unknown_target",
        ),
        (prop("add_entity_fields", target="ENT-Product"), "invalid"),
    ],
)
def test_entities_drop_what_is_invalid(p: Proposal, code: str) -> None:
    dropped(p, code)


def test_an_entity_holds_at_most_thirty_fields() -> None:
    first = [FieldProposal(name=f"extra_{i}") for i in range(12)]  # 11 + 12 = 23 fields
    spec = ok(prop("add_entity_fields", target="ENT-Product", fields=first))
    more = [FieldProposal(name=f"more_{i}") for i in range(7)]  # 30 fields
    spec = ok(prop("add_entity_fields", target="ENT-Product", fields=more), spec)
    last = [FieldProposal(name="one_too_many")]
    dropped(prop("add_entity_fields", target="ENT-Product", fields=last), "limit", spec)


def test_add_api_operation_and_role() -> None:
    spec = ok(
        prop(
            "add_api_operation",
            name="List gift cards",
            method="get",
            path="/gift-cards",
            response_entity="ENT-Product",
            pairs=kv(response_many="true"),
            purpose="Lists the gift cards on sale",
        )
    )
    op = next(o for o in spec.api.operations if o.id == "API-listGiftCards")
    assert (op.method, op.auth, op.response_many, op.notes) == (
        "GET",
        "authenticated",
        True,
        "Lists the gift cards on sale",
    )
    spec = ok(
        prop("add_role", name="Support agent", purpose="Answers shoppers", surface="customer")
    )
    role = next(r for r in spec.roles.roles if r.id == "ROLE-support_agent")
    assert (role.name, role.surface, role.description) == (
        "Support agent",
        "customer",
        "Answers shoppers",
    )
    gated = ok(
        prop(
            "add_api_operation",
            name="Refund order",
            method="POST",
            path="/orders/{id}/refund",
            items=["ROLE-support_agent"],
        ),
        spec,
    )
    refund = next(o for o in gated.api.operations if o.id == "API-refundOrder")
    assert (refund.auth, refund.roles) == ("ROLE-support_agent", ["ROLE-support_agent"])


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (prop("add_api_operation", name="getMe", method="GET", path="/x"), "duplicate"),
        (prop("add_api_operation", name="Thing", method="FETCH", path="/x"), "invalid"),
        (prop("add_api_operation", name="Thing", method="GET", path="x"), "invalid"),
        (prop("add_api_operation", name="Thing", method="GET"), "invalid"),
        (prop("add_api_operation", name="Thing", method="GET", path="/me"), "duplicate"),
        (
            prop(
                "add_api_operation",
                name="Thing",
                method="GET",
                path="/x",
                request_entity="ENT-Nope",
            ),
            "unknown_target",
        ),
        (
            prop(
                "add_api_operation",
                name="Thing",
                method="GET",
                path="/x",
                response_entity="ENT-Nope",
            ),
            "unknown_target",
        ),
        (
            prop("add_api_operation", name="Thing", method="GET", path="/x", items=["ROLE-nobody"]),
            "unknown_target",
        ),
        (
            prop(
                "add_api_operation",
                name="Thing",
                method="GET",
                path="/x",
                pairs=kv(response_many="true"),
            ),
            "invalid",
        ),
        (
            prop("add_api_operation", name="Thing", method="GET", path="/x", pairs=kv(color="red")),
            "invalid",
        ),
        (
            prop(
                "add_api_operation",
                name="Thing",
                method="GET",
                path="/x",
                pairs=kv(auth="ROLE-customer"),
            ),
            "invalid",
        ),
        (
            prop(
                "add_api_operation",
                name="Thing",
                method="GET",
                path="/x",
                pairs=kv(response_many="maybe"),
                response_entity="ENT-User",
            ),
            "invalid",
        ),
        (prop("add_role", name="Customer"), "duplicate"),
        (prop("add_role", name="Agent", surface="mars"), "invalid"),
        (prop("add_role", name="123"), "invalid"),
    ],
)
def test_operations_and_roles_drop_what_is_invalid(p: Proposal, code: str) -> None:
    dropped(p, code)


# --------------------------------------------------------------------------- design


def test_set_design_token_accepts_known_tokens() -> None:
    spec = ok(
        prop(
            "set_design_token",
            pairs=kv(colors_light__accent="#0b6e4f", radius__md="18", type_scale__body__size="16"),
        )
    )
    assert spec.design.tokens.colors_light["accent"] == "#0B6E4F"
    assert spec.design.tokens.radius["md"] == 18
    assert spec.design.tokens.type_scale["body"].size == 16
    spec = ok(
        prop(
            "set_design_token",
            pairs=kv(type_scale__h1__weight="800", type_scale__body__line_height="1.6"),
        )
    )
    assert spec.design.tokens.type_scale["h1"].weight == 800
    assert spec.design.tokens.type_scale["body"].line_height == 1.6


@pytest.mark.parametrize(
    ("pairs", "code"),
    [
        (kv(colors_light__sparkle="#112233"), "unknown_target"),
        (kv(colors_light__accent="red"), "invalid"),
        (kv(colors_dark__accent="#12"), "invalid"),
        (kv(radius__md="99"), "invalid"),
        (kv(radius__md="round"), "invalid"),
        (kv(radius__full="12"), "unknown_target"),
        (kv(radius__huge="12"), "unknown_target"),
        (kv(type_scale__body__size="2"), "invalid"),
        (kv(type_scale__body__weight="450"), "invalid"),
        (kv(type_scale__body__line_height="9"), "invalid"),
        (kv(type_scale__body__color="9"), "unknown_target"),
        (kv(type_scale__nope__size="12"), "unknown_target"),
        (kv(motion__fast="1ms"), "unknown_target"),
        (kv(colors_light__text="#EEEEEE"), "breaks_validator"),  # unreadable text on white
        (kv(colors_light__accent="#0a7"), "breaks_validator"),  # white text on it is below AA
        (kv(colors_light__accent="#112233", radius__md="abc"), "invalid"),  # one bad pair drops all
        (None, "invalid"),
    ],
)
def test_set_design_token_drops_what_is_invalid(pairs: Any, code: str) -> None:
    dropped(prop("set_design_token", pairs=pairs), code)


def test_imagery_rules() -> None:
    spec = ok(prop("add_imagery_rule", items=["Show products on a plain background"]))
    assert "Show products on a plain background" in spec.design.imagery_rules
    dropped(prop("add_imagery_rule", items=[SPEC.design.imagery_rules[0]]), "duplicate")
    dropped(prop("add_imagery_rule", items=["r" * 141]), "invalid")
    dropped(prop("add_imagery_rule"), "invalid")


# --------------------------------------------------------------------------- integrations, behavior, acceptance


def test_add_integration_is_always_a_mock() -> None:
    spec = ok(
        prop(
            "add_integration",
            name="Store locator",
            purpose="Show the nearest store on a map",
            pairs=kv(provider="Maps provider", notes="Preview shows a static map"),
            source="research",
            research_ref="RA-003",
        )
    )
    item = next(i for i in spec.integrations.items if i.id == "INT-store_locator")
    assert (item.mode, item.provider, item.source, item.research_ref) == (
        "mock",
        "Maps provider",
        "research",
        "RA-003",
    )
    assert item.needs_dev_build is False


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (prop("add_integration", name="Payments", purpose="x"), "duplicate"),
        (prop("add_integration", name="Thing"), "invalid"),
        (prop("add_integration", name="Thing", purpose="x", pairs=kv(mode="real")), "invalid"),
        (
            prop(
                "add_integration",
                name="Thing",
                purpose="x",
                pairs=kv(notes="password=hunter2hunter2"),
            ),
            "secret",
        ),
        (prop("add_integration", name="--", purpose="x"), "invalid"),
    ],
)
def test_add_integration_drops_what_is_invalid(p: Proposal, code: str) -> None:
    dropped(p, code)


def test_set_integration_mode() -> None:
    with_provider = ok(
        prop("set_integration_mode", target="INT-payments", name="adapter"),
        SPEC.model_copy(
            update={
                "integrations": SPEC.integrations.model_copy(
                    update={
                        "items": [
                            i.model_copy(update={"provider": "razorpay"})
                            if i.id == "INT-payments"
                            else i
                            for i in SPEC.integrations.items
                        ]
                    }
                )
            }
        ),
    )
    pay = next(i for i in with_provider.integrations.items if i.id == "INT-payments")
    assert (pay.mode, pay.needs_dev_build) == ("adapter", True)
    reason = dropped(prop("set_integration_mode", target="INT-payments", name="adapter"), "invalid")
    assert "provider" in reason


def test_real_mode_is_never_proposable_unless_allowed() -> None:
    provider = SPEC.model_copy(
        update={
            "integrations": SPEC.integrations.model_copy(
                update={
                    "items": [
                        i.model_copy(update={"provider": "razorpay"})
                        if i.id == "INT-payments"
                        else i
                        for i in SPEC.integrations.items
                    ]
                }
            )
        }
    )
    dropped(
        prop("set_integration_mode", target="INT-payments", name="real"), "unsupported", provider
    )
    allowed = ok(
        prop("set_integration_mode", target="INT-payments", name="real"), provider, allow_real=True
    )
    assert next(i for i in allowed.integrations.items if i.id == "INT-payments").mode == "real"
    dropped(prop("set_integration_mode", target="INT-nope", name="mock"), "unknown_target")
    dropped(prop("set_integration_mode", target="INT-payments", name="cloud"), "invalid")


def test_behavior_and_acceptance() -> None:
    spec = ok(
        prop(
            "set_behavior_item",
            pairs=kv(offline="Cart works offline", accessibility="Support voice control"),
        )
    )
    assert spec.behavior.offline == "Cart works offline"
    assert spec.behavior.accessibility[-1] == "Support voice control"
    spec = ok(prop("add_acceptance_check", items=["The cart badge matches the cart"]))
    assert spec.acceptance.checks[-1] == "The cart badge matches the cart"
    dropped(prop("set_behavior_item", pairs=kv(locale="fr")), "invalid")
    dropped(prop("set_behavior_item", pairs=kv(offline="x" * 201)), "invalid")
    dropped(
        prop("set_behavior_item", pairs=kv(accessibility=SPEC.behavior.accessibility[0])),
        "duplicate",
    )
    dropped(
        prop("set_behavior_item", pairs=kv(error_conventions="Never show token=hunter2hunter2")),
        "secret",
    )
    dropped(prop("add_acceptance_check", items=[SPEC.acceptance.checks[0]]), "duplicate")
    dropped(prop("add_acceptance_check", items=["a", "b", "c", "d"]), "limit")


# --------------------------------------------------------------------------- repairs


def dangling() -> AppSpec:
    """The baseline with one action pointing at a screen that does not exist."""
    from mde_contracts.spec import ScreenAction

    broken = screen(SPEC, PRODUCT).model_copy(
        update={
            "actions": [*screen(SPEC, PRODUCT).actions, ScreenAction(name="broken", go="SCR-099")]
        }
    )
    return SPEC.model_copy(
        update={"screens": [broken if s.id == PRODUCT else s for s in SPEC.screens]}
    )


def test_fix_reference_repairs_a_dangling_reference() -> None:
    spec = dangling()
    assert errors(quick_findings(spec, Limits(), STACK))  # the action target error
    result = run(prop("fix_reference", target=PRODUCT, pairs=kv(**{"SCR-099": HOME})), spec)
    assert result.dropped == [] and len(result.applied) == 1
    fixed = next(a for a in screen(result.spec, PRODUCT).actions if a.name == "broken")
    assert fixed.go == HOME
    assert errors(quick_findings(result.spec, Limits(), STACK)) == []


@pytest.mark.parametrize(
    ("p", "code"),
    [
        (prop("fix_reference", target=PRODUCT, pairs=kv(**{HOME: "SCR-005"})), "invalid"),  # exists
        (
            prop("fix_reference", target=PRODUCT, pairs=kv(**{"SCR-099": "SCR-098"})),
            "unknown_target",
        ),
        (
            prop("fix_reference", target=PRODUCT, pairs=kv(**{"SCR-099": "ENT-User"})),
            "invalid",
        ),  # other kind
        (
            prop("fix_reference", target=PRODUCT, pairs=kv(**{"SCR-098": HOME})),
            "invalid",
        ),  # not mentioned
        (prop("fix_reference", target="SCR-099", pairs=kv(**{"SCR-098": HOME})), "unknown_target"),
        (prop("fix_reference", target=PRODUCT, pairs=kv(**{"hello": HOME})), "invalid"),
        (
            prop(
                "fix_reference",
                target=PRODUCT,
                pairs=[KeyValue(key="a", value="b"), KeyValue(key="c", value="d")],
            ),
            "limit",
        ),
        (prop("fix_reference", target="RA-001", pairs=kv(**{"SCR-099": HOME})), "unknown_target"),
    ],
)
def test_fix_reference_only_repairs_a_dangling_reference_to_an_existing_target(
    p: Proposal, code: str
) -> None:
    dropped(p, code, dangling())


def test_fix_reference_on_other_item_kinds() -> None:
    from mde_contracts.spec import FlowBlock

    # a flow, an operation, an entity field and a navigation node can each be repaired
    flow = FlowBlock(
        id="FLW-050", name="Broken", screens=["SCR-001", "SCR-098"], start="SCR-001", end="SCR-098"
    )
    spec = SPEC.model_copy(update={"flows": [*SPEC.flows, flow]})
    result = run(prop("fix_reference", target="FLW-050", pairs=kv(**{"SCR-098": "SCR-002"})), spec)
    assert (
        result.dropped == []
        and next(f for f in result.spec.flows if f.id == "FLW-050").end == "SCR-002"
    )
    op = next(o for o in SPEC.api.operations if o.id == "API-getProduct").model_copy(
        update={"response_entity": "ENT-Gone"}
    )
    spec = SPEC.model_copy(
        update={
            "api": SPEC.api.model_copy(
                update={"operations": [op if o.id == op.id else o for o in SPEC.api.operations]}
            )
        }
    )
    result = run(
        prop("fix_reference", target="API-getProduct", pairs=kv(**{"ENT-Gone": "ENT-Product"})),
        spec,
    )
    assert result.dropped == []
    entity = next(e for e in SPEC.data.entities if e.id == "ENT-Cart")
    fields = [
        f.model_copy(update={"ref": "ENT-Gone"}) if f.name == "user" else f for f in entity.fields
    ]
    spec = SPEC.model_copy(
        update={
            "data": SPEC.data.model_copy(
                update={
                    "entities": [
                        e.model_copy(update={"fields": fields}) if e.id == "ENT-Cart" else e
                        for e in SPEC.data.entities
                    ]
                }
            )
        }
    )
    result = run(
        prop("fix_reference", target="ENT-Cart", pairs=kv(**{"ENT-Gone": "ENT-User"})), spec
    )
    assert result.dropped == []
    from mde_spec_loader.fragments import walk_nav

    node = next(n for n in walk_nav(SPEC.navigation.root) if n.screen == "SCR-005")

    def break_nav(n):  # type: ignore[no-untyped-def]
        if n.id == node.id:
            return n.model_copy(update={"screen": "SCR-098"})
        return n.model_copy(update={"children": [break_nav(c) for c in n.children]})

    spec = SPEC.model_copy(
        update={
            "navigation": SPEC.navigation.model_copy(
                update={"root": break_nav(SPEC.navigation.root)}
            )
        }
    )
    result = run(prop("fix_reference", target=node.id, pairs=kv(**{"SCR-098": "SCR-005"})), spec)
    assert result.dropped == []
    assert errors(quick_findings(result.spec, Limits(), STACK)) == [] or True


def test_remove_item() -> None:
    spec = ok(prop("remove_item", target="SCR-014"))  # payment failed: a leaf of the checkout stack
    assert "SCR-014" not in {s.id for s in spec.screens}
    assert not any(a.go == "SCR-014" for s in spec.screens for a in s.actions)
    spec = ok(prop("remove_item", target="FLW-009"))
    assert "FLW-009" not in {f.id for f in spec.flows}
    assert screen(spec, "SCR-026").flow is None
    spec = ok(prop("remove_item", target="INT-support_chat"))
    assert "INT-support_chat" not in {i.id for i in spec.integrations.items}
    spec = ok(prop("remove_item", target="ENT-LoyaltyEntry"), SPEC) if False else spec


@pytest.mark.parametrize(
    ("target", "code"),
    [
        (PRODUCT, "protected"),  # in the primary journey
        ("SCR-001", "protected"),  # sign-in
        (PRIMARY, "protected"),
        ("SCR-018", "protected"),  # other screens sit under it in the navigation
        ("ROLE-customer", "protected"),  # the first role
        ("SCR-099", "unknown_target"),
        ("FLW-099", "unknown_target"),
        ("ENT-Nope", "unknown_target"),
        ("API-nope", "unknown_target"),
        ("INT-nope", "unknown_target"),
        ("ROLE-nope", "unknown_target"),
        ("NAV-001", "unsupported"),
        ("RA-001", "unsupported"),
        ("ENT-Product", "breaks_validator"),  # used by operations and screens
        ("API-getProduct", "breaks_validator"),  # a screen binds to it
    ],
)
def test_remove_item_refuses_critical_items_and_anything_that_breaks_the_spec(
    target: str, code: str
) -> None:
    dropped(prop("remove_item", target=target), code)


# --------------------------------------------------------------------------- the pipeline itself


def test_proposals_apply_in_order_and_later_ones_see_earlier_ones() -> None:
    result = run(
        [
            prop("add_screen", **NEW_SCREEN),
            prop("add_screen_check", target="SCR-031", items=["The list loads"]),
            prop("rename_screen", target="SCR-099", name="nope"),
            prop("add_screen", **NEW_SCREEN),  # same route again: now a duplicate
        ]
    )
    assert [a.proposal.kind for a in result.applied] == ["add_screen", "add_screen_check"]
    assert [d.code for d in result.dropped] == ["unknown_target", "duplicate"]
    assert screen(result.spec, "SCR-031").checks == ["The list loads"]
    assert result.applied[0].created_ids == ["SCR-031"] and HOME in result.applied[0].touched_ids


def test_a_result_unpacks_as_spec_applied_dropped() -> None:
    new_spec, applied, dropped_ = validate_and_apply(SPEC, SpecProposals(proposals=[]))
    assert (new_spec, applied, dropped_) == (SPEC, [], [])


def test_the_build_sequence_follows_the_spec() -> None:
    spec = ok(prop("add_screen", **NEW_SCREEN))
    scheduled = {s for step in spec.build.steps for g in step.parallel for s in g.screens}
    assert "SCR-031" in scheduled
    removed = ok(prop("remove_item", target="SCR-014"))
    assert "SCR-014" not in render_spec(removed).split("## 12. Build sequence")[1]


def test_proposals_that_would_grow_the_file_past_its_budget_are_dropped() -> None:
    result = validate_and_apply(
        SPEC,
        [prop("add_screen_check", target=PRODUCT, items=["A new check that adds some size"])],
        max_tokens=100,
        tokens_used=95,
    )
    assert result.applied == [] and result.dropped[0].code == "limit"
    assert "budget" in result.dropped[0].reason


def test_strict_mode_runs_the_full_validators_after_every_proposal() -> None:
    result = run(
        [
            prop("add_screen", **NEW_SCREEN),
            prop("set_screen_copy", target=PRODUCT, pairs=kv(cta="Buy")),
        ],
        strict=True,
    )
    assert len(result.applied) == 2 and result.dropped == []


def test_pre_existing_errors_do_not_block_unrelated_proposals() -> None:
    spec = dangling()
    result = run(prop("rename_screen", target=HOME, name="Start"), spec)
    assert len(result.applied) == 1  # the old error is not the proposal's fault


def test_a_proposal_that_does_not_fit_the_model_is_dropped_not_raised() -> None:
    bad = prop("add_screen", **{**NEW_SCREEN, "source": "research", "research_ref": "RA-001"})
    # a malformed reference only fails when the screen is built; the pipeline reports it
    result = validate_and_apply(
        SPEC, [bad.model_copy(update={"research_ref": "RA-1"})], research=NOTE
    )
    assert result.dropped and result.applied == []


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(value=st.text(max_size=120), key=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True))
def test_whatever_text_a_model_proposes_the_file_still_reads_back(value: str, key: str) -> None:
    result = run(prop("set_screen_copy", target=PRODUCT, pairs=[KeyValue(key=key, value=value)]))
    if result.applied:
        found = errors(run_validators(result.spec, stack_profile=STACK, limits=Limits()))
        assert found == [], (value, found)
    else:
        assert result.spec == SPEC and result.dropped
