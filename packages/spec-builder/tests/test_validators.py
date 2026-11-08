"""One positive and at least one negative test per validator (spec 6.4)."""

from __future__ import annotations

import pytest

from mde_contracts.calls import ValidatorFinding
from mde_contracts.content import Capability, StackProfile
from mde_contracts.policy import Limits
from mde_contracts.spec import (
    ApiOperation,
    AppSpec,
    DataBinding,
    DesignTokens,
    Entity,
    EnumDef,
    FieldDef,
    FlowBlock,
    Integration,
    NavNode,
    Permission,
    Role,
    ScreenAction,
    ScreenBlock,
    StateMachine,
    Transition,
)
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec, make_sample_stack_profile
from mde_spec_builder.validators import (
    VALIDATOR_NAMES,
    action_targets,
    data_bindings,
    errors,
    flow_endpoints,
    has_errors,
    limits,
    navigation_targets,
    no_secrets,
    role_gates,
    run_validators,
    schema,
    stack_profile,
)


@pytest.fixture(scope="module")
def spec() -> AppSpec:
    return make_sample_spec(12)


@pytest.fixture(scope="module")
def profile() -> StackProfile:
    return make_sample_stack_profile()


def screen_of(spec: AppSpec, screen_id: str) -> ScreenBlock:
    return next(s for s in spec.screens if s.id == screen_id)


def edit_screen(spec: AppSpec, screen_id: str, **changes: object) -> AppSpec:
    screens = [s.model_copy(update=changes) if s.id == screen_id else s for s in spec.screens]
    return spec.model_copy(update={"screens": screens})


def add_screen(spec: AppSpec, **overrides: object) -> AppSpec:
    base = {
        "id": "SCR-090",
        "route": "/extra",
        "recipe": "list",
        "purpose": "An extra screen",
    }
    return spec.model_copy(
        update={"screens": [*spec.screens, ScreenBlock(**{**base, **overrides})]}
    )  # type: ignore[arg-type]


def messages(findings: list[ValidatorFinding]) -> str:
    return "\n".join(f.message for f in findings)


def by(findings: list[ValidatorFinding], severity: str) -> list[ValidatorFinding]:
    return [f for f in findings if f.severity == severity]


# --------------------------------------------------------------------------- the whole run


def test_run_validators_passes_on_the_sample(spec: AppSpec, profile: StackProfile) -> None:
    findings = run_validators(spec, stack_profile=profile, limits=Limits())
    assert findings == []
    assert not has_errors(findings)


def test_the_nine_validators_have_the_documented_names() -> None:
    assert VALIDATOR_NAMES == (
        "schema",
        "navigation_targets",
        "data_bindings",
        "action_targets",
        "role_gates",
        "flow_endpoints",
        "limits",
        "stack_profile",
        "no_secrets",
    )


def test_each_validator_names_itself_in_its_findings(spec: AppSpec, profile: StackProfile) -> None:
    broken = edit_screen(spec, "SCR-006", actions=[ScreenAction(name="x")], auth="admin")
    broken = broken.model_copy(
        update={"revision": broken.revision.model_copy(update={"version": 9})}
    )
    findings = run_validators(
        broken,
        stack_profile=profile.model_copy(update={"id": "other"}),
        limits=Limits(max_screens=3),
    )
    names = {f.validator for f in findings}
    assert {"schema", "action_targets", "role_gates", "limits", "stack_profile"} <= names
    assert names <= set(VALIDATOR_NAMES)
    assert errors(findings) and all(f.severity == "error" for f in errors(findings))


def test_findings_are_deterministic(spec: AppSpec, profile: StackProfile) -> None:
    broken = edit_screen(spec, "SCR-006", actions=[ScreenAction(name="x", go="SCR-099")])
    first = run_validators(broken, stack_profile=profile, limits=Limits())
    assert first == run_validators(broken, stack_profile=profile, limits=Limits())


# --------------------------------------------------------------------------- 1. schema


def test_schema_positive(spec: AppSpec) -> None:
    assert schema(spec) == []


def test_schema_duplicate_ids(spec: AppSpec) -> None:
    dup = spec.model_copy(update={"screens": [*spec.screens, spec.screens[0]]})
    found = schema(dup)
    assert any(
        "Duplicate screen id SCR-001" in f.message and f.target_ids == ["SCR-001"] for f in found
    )
    flows = spec.model_copy(update={"flows": [*spec.flows, spec.flows[0]]})
    assert any("Duplicate flow id" in f.message for f in schema(flows))


def test_schema_revision_must_match_the_header(spec: AppSpec) -> None:
    bad = spec.model_copy(update={"revision": spec.revision.model_copy(update={"version": 5})})
    found = schema(bad)
    assert any("revision block says version 5" in f.message for f in found)
    assert any(f.target_ids == ["REV", "HEADER"] for f in found)


def test_schema_unknown_schema_id(spec: AppSpec) -> None:
    bad = spec.model_copy(
        update={"header": spec.header.model_copy(update={"schema_id": "other/9"})}
    )
    assert any("Unknown spec schema" in f.message for f in schema(bad))


def test_schema_data_model_integrity(spec: AppSpec) -> None:
    entity = Entity(
        id="ENT-Broken",
        name="Broken",
        plain_name="Broken things",
        fields=[
            FieldDef(name="owner", type="ref"),
            FieldDef(name="kind", type="enum"),
            FieldDef(name="mood", type="string", enum="NoSuchEnum"),
            FieldDef(name="odd", type="string", ref="ENT-User"),
            FieldDef(name="owner"),
        ],
        state_machine=StateMachine(
            field="ghost",
            states=["a"],
            transitions=[Transition(source="a", target="zzz", on="go")],
        ),
    )
    data = spec.data.model_copy(
        update={
            "entities": [*spec.data.entities, entity],
            "enums": [
                *spec.data.enums,
                EnumDef(name="Empty", values=[]),
                EnumDef(name="Twice", values=["a", "a"]),
            ],
        }
    )
    found = schema(spec.model_copy(update={"data": data}))
    text = messages(found)
    assert "is a reference but names no entity" in text
    assert "is an enum but names no enum" in text
    assert "uses the enum NoSuchEnum, which is not defined" in text
    assert "names an entity but is not a reference" in text
    assert "two fields named 'owner'" in text
    assert "tracks field 'ghost'" in text
    assert "uses the state 'zzz'" in text
    assert "Enum Empty has no values" in text and "lists the value 'a' twice" in text
    assert {f.severity for f in found} == {"error", "warn"}


def test_schema_state_machine_states_should_be_enum_values(spec: AppSpec) -> None:
    orders = next(e for e in spec.data.entities if e.id == "ENT-Order")
    assert orders.state_machine is not None
    machine = orders.state_machine.model_copy(
        update={"states": [*orders.state_machine.states, "lost"]}
    )
    changed = orders.model_copy(update={"state_machine": machine})
    data = spec.data.model_copy(
        update={"entities": [changed if e.id == "ENT-Order" else e for e in spec.data.entities]}
    )
    found = schema(spec.model_copy(update={"data": data}))
    assert any(
        f.severity == "warn" and "not values of enum OrderStatus" in f.message for f in found
    )


def test_schema_design_colours(spec: AppSpec) -> None:
    tokens: DesignTokens = spec.design.tokens
    bad_tokens = tokens.model_copy(
        update={"colors_light": {**tokens.colors_light, "primary": "blue", "extra": "#123456"}}
    )
    design = spec.design.model_copy(update={"tokens": bad_tokens})
    found = schema(spec.model_copy(update={"design": design}))
    assert any("not a hex colour: 'blue'" in f.message for f in found)
    assert any(f.severity == "warn" and "only one of light and dark" in f.message for f in found)


def test_schema_dangling_research_and_build_references(spec: AppSpec) -> None:
    bad = edit_screen(spec, "SCR-011", research_ref="RA-099")
    assert any("research addition RA-099, which does not exist" in f.message for f in schema(bad))
    additions = [
        a.model_copy(update={"affects": ["SCR-099"]}) for a in spec.assumptions.research_additions
    ]
    assumptions = spec.assumptions.model_copy(update={"research_additions": additions})
    warned = schema(spec.model_copy(update={"assumptions": assumptions}))
    assert any(f.severity == "warn" and "affects SCR-099" in f.message for f in warned)
    steps = [
        s.model_copy(update={"screens": [*s.screens, "SCR-099"]}) if s.id == "S5" else s
        for s in spec.build.steps
    ]
    broken_build = spec.model_copy(update={"build": spec.build.model_copy(update={"steps": steps})})
    assert any("Build step S5 refers to SCR-099" in f.message for f in schema(broken_build))


def test_schema_round_trip_failures_are_reported(spec: AppSpec) -> None:
    corrupted = render_spec(spec).replace(
        "```yaml id=APP kind=app\n", "```yaml id=APP kind=app\nname: [oops\n"
    )
    assert any("cannot be read back" in f.message for f in schema(spec, rendered=corrupted))
    other = spec.model_copy(update={"app": spec.app.model_copy(update={"name": "Elsewhere"})})
    drift = schema(spec, rendered=render_spec(other))
    assert any(
        f.target_ids == ["APP"] and "does not read back as written" in f.message for f in drift
    )


def test_schema_an_empty_build_sequence_is_fine(spec: AppSpec) -> None:
    # validators run at B6, before the build sequence is generated at B9
    early = spec.model_copy(update={"build": spec.build.model_copy(update={"steps": []})})
    assert schema(early) == []


# --------------------------------------------------------------------------- 2. navigation


def test_navigation_positive(spec: AppSpec) -> None:
    assert navigation_targets(spec) == []


def test_navigation_target_must_exist(spec: AppSpec) -> None:
    root = spec.navigation.root
    ghost = NavNode(id="NAV-090", route="/ghost", kind="screen", screen="SCR-099")
    nav = spec.navigation.model_copy(
        update={"root": root.model_copy(update={"children": [*root.children, ghost]})}
    )
    found = navigation_targets(spec.model_copy(update={"navigation": nav}))
    assert any(
        "NAV-090 points at screen SCR-099, which does not exist" in f.message
        and f.target_ids == ["NAV-090", "SCR-099"]
        for f in found
    )


def test_a_screen_entry_without_a_screen_and_an_empty_group(spec: AppSpec) -> None:
    root = spec.navigation.root
    extra = [
        NavNode(id="NAV-091", route="/blank", kind="screen"),
        NavNode(id="NAV-092", route="/empty", kind="tabs"),
    ]
    nav = spec.navigation.model_copy(
        update={"root": root.model_copy(update={"children": [*root.children, *extra]})}
    )
    found = navigation_targets(spec.model_copy(update={"navigation": nav}))
    assert any(
        "NAV-091" in f.message and "names no screen" in f.message and f.severity == "error"
        for f in found
    )
    assert any("NAV-092" in f.message and f.severity == "warn" for f in found)


def test_every_screen_must_be_reachable(spec: AppSpec) -> None:
    orphan = add_screen(spec)
    found = navigation_targets(orphan)
    assert [f.target_ids for f in found] == [["SCR-090"]]
    assert "cannot be reached" in found[0].message and found[0].severity == "error"


def test_an_action_makes_a_screen_reachable(spec: AppSpec) -> None:
    linked = add_screen(spec)
    linked = edit_screen(
        linked,
        "SCR-003",
        actions=[*screen_of(spec, "SCR-003").actions, ScreenAction(name="more", go="SCR-090")],
    )
    assert navigation_targets(linked) == []


def test_a_screen_only_linked_from_an_unreachable_screen_is_unreachable(spec: AppSpec) -> None:
    two = add_screen(spec)
    two = add_screen(
        two, id="SCR-091", route="/extra2", actions=[ScreenAction(name="x", go="SCR-090")]
    )
    two = edit_screen(two, "SCR-090", actions=[ScreenAction(name="y", go="SCR-091")])
    assert {t for f in navigation_targets(two) for t in f.target_ids} == {"SCR-090", "SCR-091"}


def test_routes_must_be_unique(spec: AppSpec) -> None:
    dup = edit_screen(spec, "SCR-002", route=screen_of(spec, "SCR-001").route)
    assert any("share the route /login" in f.message for f in navigation_targets(dup))


# --------------------------------------------------------------------------- 3. data bindings


def test_data_bindings_positive(spec: AppSpec) -> None:
    assert data_bindings(spec) == []


def test_binding_names_a_missing_operation_entity_or_field(spec: AppSpec) -> None:
    bad = edit_screen(
        spec,
        "SCR-006",
        data=[
            DataBinding(bind="API-nope(id)"),
            DataBinding(bind="ENT-Ghost.name"),
            DataBinding(bind="ENT-Product.colour"),
            DataBinding(bind="ENT-Product.name"),
            DataBinding(bind="not a binding"),
        ],
    )
    text = messages(data_bindings(bad))
    assert "binds data to operation API-nope, which does not exist" in text
    assert "binds data to entity ENT-Ghost, which does not exist" in text
    assert "ENT-Product has no field 'colour'" in text
    assert "'not a binding', which is neither API-name(args) nor ENT-Name.field" in text
    assert "ENT-Product.name" not in text.replace("ENT-Product.colour", "")


def test_api_entities_must_exist(spec: AppSpec) -> None:
    op = ApiOperation(
        id="API-ghost",
        name="ghost",
        method="GET",
        path="/ghost",
        request_entity="ENT-A",
        response_entity="ENT-B",
    )
    api = spec.api.model_copy(update={"operations": [*spec.api.operations, op]})
    text = messages(data_bindings(spec.model_copy(update={"api": api})))
    assert "request entity ENT-A, which does not exist" in text
    assert "response entity ENT-B, which does not exist" in text


def test_entity_reference_fields_must_resolve(spec: AppSpec) -> None:
    entity = Entity(
        id="ENT-Loose",
        name="Loose",
        plain_name="Loose",
        fields=[FieldDef(name="owner", type="ref", ref="ENT-Nobody")],
    )
    data = spec.data.model_copy(update={"entities": [*spec.data.entities, entity]})
    text = messages(data_bindings(spec.model_copy(update={"data": data})))
    assert "Field owner of ENT-Loose refers to entity ENT-Nobody" in text


def test_shown_fields_that_do_not_exist_are_a_warning(spec: AppSpec) -> None:
    bad = edit_screen(
        spec, "SCR-006", data=[DataBinding(bind="API-getProduct(id)", show=["name", "sparkle"])]
    )
    found = data_bindings(bad)
    assert [f.severity for f in found] == ["warn"]
    assert "['sparkle']" in found[0].message
    free_text = edit_screen(
        spec, "SCR-006", data=[DataBinding(bind="API-getProduct(id)", show=["Price (formatted)"])]
    )
    assert data_bindings(free_text) == []  # only plain field names are checked


# --------------------------------------------------------------------------- 4. actions


def test_action_targets_positive(spec: AppSpec) -> None:
    assert action_targets(spec) == []


def test_an_action_needs_a_destination(spec: AppSpec) -> None:
    bad = edit_screen(spec, "SCR-006", actions=[ScreenAction(name="orphan_button")])
    found = action_targets(bad)
    assert len(found) == 1 and found[0].target_ids == ["SCR-006"]
    assert "'orphan_button' on SCR-006 has no destination" in found[0].message


def test_an_action_must_call_a_real_operation_and_go_to_a_real_target(spec: AppSpec) -> None:
    bad = edit_screen(
        spec,
        "SCR-006",
        actions=[
            ScreenAction(name="a", calls="API-nope"),
            ScreenAction(name="b", go="SCR-099"),
            ScreenAction(name="c", go="ENT-Order"),
            ScreenAction(name="d", go="/nowhere"),
            ScreenAction(name="e", go="back"),
            ScreenAction(name="f", go="/cart"),
            ScreenAction(name="g", go="NAV-006"),
        ],
    )
    text = messages(action_targets(bad))
    assert "calls API-nope, which is not an operation" in text
    assert "goes to SCR-099" in text and "goes to ENT-Order" in text and "goes to /nowhere" in text
    for ok in ("'e'", "'f'", "'g'"):
        assert ok not in text  # back, a route and a sheet/navigation node all resolve


# --------------------------------------------------------------------------- 5. role gates


def test_role_gates_positive(spec: AppSpec) -> None:
    assert role_gates(spec) == []


def test_gates_must_name_defined_roles(spec: AppSpec) -> None:
    bad = edit_screen(spec, "SCR-010", auth="ROLE-ghost")
    ops = [
        o.model_copy(update={"roles": ["ROLE-phantom"]}) if o.id == "API-getCart" else o
        for o in spec.api.operations
    ]
    bad = bad.model_copy(update={"api": spec.api.model_copy(update={"operations": ops})})
    root = spec.navigation.root
    nav_child = NavNode(
        id="NAV-090", route="/vip", kind="screen", screen="SCR-001", auth="ROLE-vip"
    )
    bad = bad.model_copy(
        update={
            "navigation": spec.navigation.model_copy(
                update={"root": root.model_copy(update={"children": [*root.children, nav_child]})}
            )
        }
    )
    text = messages(role_gates(bad))
    assert "Screen SCR-010 is gated to ROLE-ghost, which is not a defined role" in text
    assert "Operation API-getCart is gated to ROLE-phantom" in text
    assert "Navigation node NAV-090 is gated to ROLE-vip" in text


def test_auth_values_must_be_recognised(spec: AppSpec) -> None:
    found = role_gates(edit_screen(spec, "SCR-010", auth="admin"))
    assert any("auth value 'admin'" in f.message for f in found)
    assert role_gates(edit_screen(spec, "SCR-010", auth="ROLE-admin, ROLE-customer")) == []


def test_permissions_must_name_real_roles_screens_and_operations(spec: AppSpec) -> None:
    permissions = [
        *spec.roles.permissions,
        Permission(role="ROLE-ghost", screens=["SCR-001"], operations=["*"]),
        Permission(role="ROLE-admin", screens=["SCR-099"], operations=["API-nope"]),
    ]
    bad = spec.model_copy(
        update={"roles": spec.roles.model_copy(update={"permissions": permissions})}
    )
    text = messages(role_gates(bad))
    assert "Permissions are given to ROLE-ghost, which is not a defined role" in text
    assert "ROLE-admin is permitted SCR-099, which does not exist" in text
    assert "ROLE-admin is permitted API-nope, which does not exist" in text
    empty_perm = spec.model_copy(
        update={
            "roles": spec.roles.model_copy(
                update={"permissions": [Permission(role="ROLE-nobody", screens=[], operations=[])]}
            )
        }
    )
    assert any("ROLE-nobody" in f.message for f in role_gates(empty_perm))


def test_a_surface_without_a_role_is_a_warning(spec: AppSpec) -> None:
    found = role_gates(edit_screen(spec, "SCR-001", surface="staff"))
    assert [f.severity for f in found] == ["warn"] and "surface(s) ['staff']" in found[0].message


# --------------------------------------------------------------------------- 6. flows


def test_flow_endpoints_positive(spec: AppSpec) -> None:
    assert flow_endpoints(spec) == []


def test_a_flow_must_start_and_end_on_real_screens(spec: AppSpec) -> None:
    flow = spec.flows[0]
    broken = FlowBlock(
        id="FLW-090", name="Broken", screens=["SCR-001", "SCR-098"], start="SCR-097", end="SCR-099"
    )
    bad = spec.model_copy(update={"flows": [flow, broken]})
    text = messages(flow_endpoints(bad))
    assert "Flow FLW-090 lists screen SCR-098, which does not exist" in text
    assert "starts on SCR-097, which does not exist" in text
    assert "ends on SCR-099, which does not exist" in text


def test_start_and_end_must_belong_to_the_flow(spec: AppSpec) -> None:
    flow = FlowBlock(
        id="FLW-090", name="Odd", screens=["SCR-001", "SCR-002"], start="SCR-003", end="SCR-004"
    )
    text = messages(flow_endpoints(spec.model_copy(update={"flows": [spec.flows[0], flow]})))
    assert "starts on SCR-003, which is not one of its screens" in text
    assert "ends on SCR-004, which is not one of its screens" in text


def test_an_empty_flow_is_an_error_and_odd_order_a_warning(spec: AppSpec) -> None:
    empty = FlowBlock(id="FLW-090", name="Empty", screens=[], start="SCR-001", end="SCR-001")
    found = flow_endpoints(spec.model_copy(update={"flows": [spec.flows[0], empty]}))
    assert any(f.severity == "error" and "lists no screens" in f.message for f in found)
    shuffled = FlowBlock(
        id="FLW-091",
        name="Shuffled",
        screens=["SCR-001", "SCR-002", "SCR-001"],
        start="SCR-002",
        end="SCR-001",
    )
    warned = flow_endpoints(spec.model_copy(update={"flows": [*spec.flows, shuffled]}))
    assert [f.severity for f in warned] == ["warn", "warn"]
    assert "starts on SCR-002 but its first screen is SCR-001" in messages(warned)
    assert "lists SCR-001 more than once" in messages(warned)


def test_a_screen_must_belong_to_an_existing_flow(spec: AppSpec) -> None:
    found = flow_endpoints(edit_screen(spec, "SCR-001", flow="FLW-099"))
    assert any("belongs to flow FLW-099, which does not exist" in f.message for f in found)


def test_primary_flow_warnings(spec: AppSpec) -> None:
    none = spec.model_copy(
        update={"flows": [f.model_copy(update={"primary": False}) for f in spec.flows]}
    )
    assert any(
        "No flow is marked primary" in f.message and f.severity == "warn"
        for f in flow_endpoints(none)
    )
    two = spec.model_copy(
        update={"flows": [f.model_copy(update={"primary": True}) for f in spec.flows]}
    )
    assert any("are all marked primary" in f.message for f in flow_endpoints(two))
    unflowed = spec.model_copy(
        update={
            "flows": [],
            "screens": [s.model_copy(update={"flow": None}) for s in spec.screens],
        }
    )
    assert flow_endpoints(unflowed) == []  # no flows at all is allowed


# --------------------------------------------------------------------------- 7. limits


def test_limits_positive(spec: AppSpec) -> None:
    assert limits(spec, Limits(max_screens=12)) == []


def test_screen_count_is_capped(spec: AppSpec) -> None:
    found = limits(spec, Limits(max_screens=10))
    assert len(found) == 1 and found[0].severity == "error"
    assert "12 screens, over the cap of 10" in found[0].message
    assert found[0].target_ids == ["SCR-011", "SCR-012"]  # the screens beyond the cap


def test_file_size_has_a_budget_with_an_early_warning(spec: AppSpec) -> None:
    tokens = int(len(render_spec(spec)) / 4)
    over = limits(spec, Limits(), max_tokens=tokens // 2)
    assert any(f.severity == "error" and "over the budget" in f.message for f in over)
    close = limits(spec, Limits(), max_tokens=int(tokens / 0.9))
    assert [f.severity for f in close] == ["warn"] and "close to the budget" in close[0].message


def test_an_oversized_screen_block_is_a_warning(spec: AppSpec) -> None:
    huge = edit_screen(spec, "SCR-001", checks=[f"check number {i} " + "x" * 80 for i in range(80)])
    found = limits(huge, Limits(max_screens=50))
    assert any(f.target_ids == ["SCR-001"] and f.severity == "warn" for f in found)


# --------------------------------------------------------------------------- 8. stack profile


def test_stack_profile_positive(spec: AppSpec, profile: StackProfile) -> None:
    assert stack_profile(spec, profile) == []


def test_an_unsupported_capability_is_an_error(spec: AppSpec, profile: StackProfile) -> None:
    item = Integration(
        id="INT-beacons", name="Store beacons", purpose="Detect shoppers over Bluetooth"
    )
    integrations = spec.integrations.model_copy(update={"items": [*spec.integrations.items, item]})
    found = stack_profile(spec.model_copy(update={"integrations": integrations}), profile)
    assert [(f.severity, f.target_ids) for f in found] == [("error", ["INT-beacons"])]
    assert "Bluetooth is not supported by stack profile expo-managed" in found[0].message


def test_a_development_build_capability_is_flagged_as_a_warning(
    spec: AppSpec, profile: StackProfile
) -> None:
    behavior = spec.behavior.model_copy(
        update={"notifications": ["Push notification when an order ships"]}
    )
    found = stack_profile(spec.model_copy(update={"behavior": behavior}), profile)
    assert [(f.severity, f.target_ids) for f in found] == [("warn", ["BEHAVIOR"])]
    assert "needs a development build" in found[0].message and "web preview" in found[0].message


def test_screens_are_checked_by_recipe_sections_and_action_names(
    spec: AppSpec, profile: StackProfile
) -> None:
    found = stack_profile(edit_screen(spec, "SCR-002", sections=["fingerprint_prompt"]), profile)
    assert [f.target_ids for f in found] == [["SCR-002"]] and "Biometric" in found[0].message


def test_an_integration_flagged_as_needing_a_dev_build(
    spec: AppSpec, profile: StackProfile
) -> None:
    items = [
        i.model_copy(update={"needs_dev_build": True}) if i.id == "INT-email" else i
        for i in spec.integrations.items
    ]
    flagged = spec.model_copy(
        update={"integrations": spec.integrations.model_copy(update={"items": items})}
    )
    found = stack_profile(flagged, profile)
    assert [(f.severity, f.target_ids) for f in found] == [("warn", ["INT-email"])]


def test_the_spec_must_target_the_supplied_profile(spec: AppSpec, profile: StackProfile) -> None:
    found = stack_profile(spec, profile.model_copy(update={"id": "bare-rn"}))
    assert any(f.target_ids == ["HEADER"] and "bare-rn" in f.message for f in found)


def test_an_unsupported_app_type_is_flagged(spec: AppSpec, profile: StackProfile) -> None:
    app = spec.app.model_copy(update={"purpose": "A multiplayer game for shoppers"})
    found = stack_profile(spec.model_copy(update={"app": app}), profile)
    assert [(f.severity, f.target_ids) for f in found] == [("warn", ["APP"])]
    assert "closest option" in found[0].message
    gamified = spec.app.model_copy(update={"purpose": "A gamified loyalty app"})
    assert (
        stack_profile(spec.model_copy(update={"app": gamified}), profile) == []
    )  # whole words only


def test_matching_is_whole_word_and_case_insensitive(spec: AppSpec) -> None:
    custom = StackProfile(
        id="expo-managed",
        label="t",
        capabilities=[Capability(id="maps", label="Maps", supported=False, keywords=["map view"])],
        unsupported_types=[],
    )
    behavior = spec.behavior.model_copy(update={"offline": "MAP VIEW tiles cached"})
    assert stack_profile(spec.model_copy(update={"behavior": behavior}), custom)
    heatmap = spec.behavior.model_copy(update={"offline": "heatmap viewer"})
    assert stack_profile(spec.model_copy(update={"behavior": heatmap}), custom) == []


# --------------------------------------------------------------------------- 9. no secrets


def _fake_key() -> str:
    return "sk-" + "A1b2C3d4" * 4  # built at run time so no key-like text sits in the repo


def test_no_secrets_positive(spec: AppSpec) -> None:
    assert no_secrets(spec) == []


def test_a_secret_in_a_block_is_found_by_block_without_echoing_it(spec: AppSpec) -> None:
    key = _fake_key()
    bad = edit_screen(spec, "SCR-003", notes=f"Call the API with {key}")
    found = no_secrets(bad)
    assert len(found) == 1 and found[0].target_ids == ["SCR-003"] and found[0].severity == "error"
    assert "openai_key" in found[0].message and key not in found[0].message


def test_a_secret_in_the_summary_or_integrations_is_found(spec: AppSpec) -> None:
    bad = spec.model_copy(update={"summary": f"Use key {_fake_key()} for everything"})
    assert [f.target_ids for f in no_secrets(bad)] == [["SUMMARY"]]
    item = Integration(
        id="INT-vault", name="Vault", purpose="Stores data", notes="password: " + "hunter2hunter2"
    )
    integrations = spec.integrations.model_copy(update={"items": [*spec.integrations.items, item]})
    found = no_secrets(spec.model_copy(update={"integrations": integrations}))
    assert [f.target_ids for f in found] == [["INTEGRATIONS"]] and "assignment" in found[0].message


def test_connection_strings_and_private_keys_are_found(spec: AppSpec) -> None:
    note = "postgres" + "://user:" + "pw12345@db.internal/app"
    assert no_secrets(edit_screen(spec, "SCR-001", notes=note))
    dashes = "-----"
    pem = f"{dashes}BEGIN PRIVATE KEY{dashes}\nMIIBVQIBADANBg\n{dashes}END PRIVATE KEY{dashes}"
    assert no_secrets(edit_screen(spec, "SCR-001", notes=pem))


def test_no_secrets_accepts_already_rendered_text(spec: AppSpec) -> None:
    text = render_spec(spec).replace("Sell products", "Sell products " + _fake_key())
    assert no_secrets(spec, rendered=text)


# --------------------------------------------------------------------------- misc


def test_role_with_a_new_surface_is_accepted(spec: AppSpec) -> None:
    roles = [*spec.roles.roles, Role(id="ROLE-staff", name="Staff", surface="staff")]
    changed = edit_screen(spec, "SCR-001", surface="staff").model_copy(
        update={"roles": spec.roles.model_copy(update={"roles": roles})}
    )
    assert role_gates(changed) == []
