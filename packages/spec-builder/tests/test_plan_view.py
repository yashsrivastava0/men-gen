from __future__ import annotations

import pytest

from mde_contracts.brief import AssumedItem, Brief, BriefRow
from mde_contracts.plan import ApproverStatus, PlanView
from mde_contracts.policy import GateRule, Policy, PolicyGates
from mde_contracts.research import (
    CompliancePointer,
    ResearchNote,
    ResearchSuggestion,
)
from mde_contracts.spec import (
    AppSpec,
    BacklogItem,
    Entity,
    FieldDef,
    Integration,
    ScreenBlock,
)
from mde_spec_builder.assemble import spec_ids
from mde_spec_builder.plan_view import (
    build_plan_view,
    humanize,
    screen_title,
)
from mde_spec_builder.render import render_spec, spec_hash
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex


@pytest.fixture(scope="module")
def spec() -> AppSpec:
    return make_sample_spec(14)


@pytest.fixture(scope="module")
def brief() -> Brief:
    return Brief(
        version=2,
        app_summary="A small shop app",
        rows=[
            BriefRow(
                key="building",
                title="What you are building",
                text="A shop",
                slot_ids=["idea.summary"],
            )
        ],
        assumptions=[
            AssumedItem(
                slot_id="auth.method",
                label="Login",
                value_text="Email and password",
                reason="You did not say",
            ),
            AssumedItem(
                slot_id="pay.provider",
                label="Payments",
                value_text="Mocked",
                reason="No provider chosen",
                included=False,
            ),
        ],
    )


def plan(spec: AppSpec, brief: Brief, **overrides: object) -> PlanView:
    params: dict[str, object] = {
        "summary": "A shop app where people browse, add to a cart and pay.",
        "brief": brief,
        "research_note": None,
        "policy": Policy(id="guided-default"),
        "gate1_ids": [],
    }
    params.update(overrides)
    return build_plan_view(spec, **params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- basics


def test_header_fields(spec: AppSpec, brief: Brief) -> None:
    view = plan(spec, brief)
    assert view.spec_version == 1 and view.language == "en"
    assert view.spec_hash == spec_hash(spec) == SpecIndex.from_text(render_spec(spec)).header.hash
    assert view.summary.startswith("A shop app") and view.screen_count == 14


def test_plan_view_is_pure_and_deterministic(spec: AppSpec, brief: Brief) -> None:
    assert plan(spec, brief) == plan(spec, brief)
    view = plan(spec, brief)
    assert (
        PlanView.model_validate_json(view.model_dump_json()) == view
    )  # a UI can store and read it


# --------------------------------------------------------------------------- screen map


def test_screen_map_groups_screens_by_flow_with_counts(spec: AppSpec, brief: Brief) -> None:
    view = plan(spec, brief)
    first = view.screen_map[0]
    assert first.flow_id == "FLW-001" and first.flow_name == "Browse and buy" and first.count == 6
    assert first.screen_ids == ["SCR-003", "SCR-005", "SCR-006", "SCR-007", "SCR-008", "SCR-009"]
    assert len(first.screen_titles) == first.count
    assert first.screen_titles[0] == "Today"  # the screen's own title copy
    ids = {i for g in view.screen_map for i in g.screen_ids}
    assert ids == {s.id for s in spec.screens}  # every screen appears somewhere


def test_screens_in_no_flow_land_in_other_screens(spec: AppSpec, brief: Brief) -> None:
    flows = [f for f in spec.flows if f.id != "FLW-001"]
    view = plan(spec.model_copy(update={"flows": flows}), brief)
    other = view.screen_map[-1]
    assert other.flow_id is None and other.flow_name == "Other screens"
    assert "SCR-005" in other.screen_ids and other.count == len(other.screen_ids)
    assert "SCR-003" not in other.screen_ids  # home is also in the sign-in flow


def test_a_shared_screen_shows_in_each_flow_it_belongs_to() -> None:
    spec = make_sample_spec(22)
    counts: dict[str, int] = {}
    brief = Brief(version=1, app_summary="x", rows=[])
    for group in plan(spec, brief).screen_map:
        for screen_id in group.screen_ids:
            counts[screen_id] = counts.get(screen_id, 0) + 1
    assert counts["SCR-006"] == 2  # the product page: browse flow and reviews flow


@pytest.mark.parametrize(
    ("route", "texts", "expected"),
    [
        ("/", {}, "Home"),
        ("/catalog", {}, "Catalog"),
        ("/product/[id]", {}, "Product detail"),
        ("/orders/[id]", {}, "Order detail"),
        ("/checkout/done", {}, "Checkout done"),
        ("/profile/addresses/new", {}, "New address"),
        ("/profile/edit", {}, "Edit profile"),
        ("/(tabs)/cart", {}, "Cart"),
        ("/shop", {"title": "Shop now"}, "Shop now"),
        ("/[id]", {}, "Show it"),
    ],
)
def test_screen_titles_are_plain(route: str, texts: dict[str, str], expected: str) -> None:
    screen = ScreenBlock(id="SCR-001", route=route, recipe="r", purpose="Show it", texts=texts)
    assert screen_title(screen) == expected


def test_humanize() -> None:
    assert humanize("unit_price") == "unit price"
    assert humanize("unitPrice") == "unit price"
    assert humanize("order-id") == "order id"


# --------------------------------------------------------------------------- look and data


def test_look_preview_comes_from_the_tokens(spec: AppSpec, brief: Brief) -> None:
    look = plan(spec, brief).look
    tokens = spec.design.tokens
    assert look.direction == "clean-bright" and look.radius == 12
    assert look.swatches_light == dict(sorted(tokens.colors_light.items()))
    assert look.swatches_dark["background"] == tokens.colors_dark["background"]
    assert look.font_sample == "Aa 16/400"


def test_look_without_a_body_style_or_radius(spec: AppSpec, brief: Brief) -> None:
    tokens = spec.design.tokens.model_copy(update={"type_scale": {}, "radius": {}})
    bare = spec.model_copy(update={"design": spec.design.model_copy(update={"tokens": tokens})})
    look = plan(bare, brief).look
    assert look.font_sample is None and look.radius == 0
    only = spec.design.tokens.model_copy(update={"radius": {"a": 4, "b": 8, "c": 16}})
    odd = spec.model_copy(update={"design": spec.design.model_copy(update={"tokens": only})})
    assert plan(odd, brief).look.radius == 8  # no "md": the middle value


def test_data_is_described_in_everyday_words(spec: AppSpec, brief: Brief) -> None:
    data = {e.id: e for e in plan(spec, brief).data}
    assert data["ENT-Order"].plain_name == "Orders"
    fields = data["ENT-Order"].fields_plain
    assert "status (one of: placed, paid, shipped, delivered, cancelled)" in fields
    assert "total (amount of money)" in fields and "placed at (date)" in fields
    assert "address (link to addresses)" in fields
    product = data["ENT-Product"].fields_plain
    assert "images (picture)" in product and "stock (number)" in product and "name" in product
    user = data["ENT-User"].fields_plain
    assert "phone (optional)" in user and "avatar (picture, optional)" in user
    assert "is default (yes or no)" in data["ENT-Address"].fields_plain


def test_plain_field_edge_cases(spec: AppSpec, brief: Brief) -> None:
    entity = Entity(
        id="ENT-Odd",
        name="Odd",
        plain_name="Odd things",
        fields=[
            FieldDef(name="owner", type="ref", ref="ENT-Ghost"),
            FieldDef(name="mood", type="enum", enum="Ghost"),
            FieldDef(name="payload", type="json"),
        ],
    )
    changed = spec.model_copy(update={"data": spec.data.model_copy(update={"entities": [entity]})})
    fields = plan(changed, brief).data[0].fields_plain
    assert fields == ["owner (link)", "mood (a choice)", "payload"]


# --------------------------------------------------------------------------- research


def test_skipped_research_says_so(spec: AppSpec, brief: Brief) -> None:
    skipped = spec.model_copy(
        update={
            "assumptions": spec.assumptions.model_copy(
                update={"research_status": "skipped", "research_additions": []}
            )
        }
    )
    view = plan(skipped, brief)
    assert view.research_status == "skipped" and view.research_note == "Research was skipped."
    assert view.research_added == []
    noted = spec.assumptions.model_copy(
        update={"research_status": "skipped", "research_note": "research: unavailable (budget)"}
    )
    text = plan(spec.model_copy(update={"assumptions": noted}), brief).research_note
    assert text == "Research was skipped: research: unavailable (budget)."


def test_unavailable_research_names_the_reason_in_plain_words(spec: AppSpec, brief: Brief) -> None:
    unavailable = spec.model_copy(
        update={
            "assumptions": spec.assumptions.model_copy(update={"research_status": "unavailable"})
        }
    )
    note = ResearchNote.unavailable("abc", "budget")
    view = plan(unavailable, brief, research_note=note)
    assert view.research_note is not None
    assert (
        "the research budget was used up" in view.research_note
        and "standard defaults" in view.research_note
    )
    custom = ResearchNote.unavailable("abc", "weird_reason")
    assert "weird_reason" in (plan(unavailable, brief, research_note=custom).research_note or "")
    assert "it could not run" in (plan(unavailable, brief).research_note or "")


def test_partial_research_and_compliance_pointers(spec: AppSpec, brief: Brief) -> None:
    partial = spec.model_copy(
        update={"assumptions": spec.assumptions.model_copy(update={"research_status": "partial"})}
    )
    note = ResearchNote(
        brief_hash="abc",
        compliance=[
            CompliancePointer(
                flag="gdpr", title="EU privacy basics", url="https://example.com/gdpr"
            )
        ],
    )
    view = plan(partial, brief, research_note=note)
    assert "only in part" in (view.research_note or "")
    # pointers travel in their own field, never mixed into the research note text
    assert [(p.flag, p.title, p.url) for p in view.compliance] == [
        ("gdpr", "EU privacy basics", "https://example.com/gdpr")
    ]
    assert "gdpr" not in (view.research_note or "") and "legal advice" not in (
        view.research_note or ""
    )
    ok = plan(spec, brief, research_note=note)
    assert ok.research_note is None  # status ok: nothing to say besides the pointers
    assert [p.title for p in ok.compliance] == ["EU privacy basics"]
    assert plan(spec, brief).compliance == []


def test_ok_research_without_pointers_has_no_note(spec: AppSpec, brief: Brief) -> None:
    assert plan(spec, brief).research_note is None


def test_research_additions_carry_toggle_sources_and_a_plain_reason(
    spec: AppSpec, brief: Brief
) -> None:
    view = plan(spec, brief)
    assert len(view.research_added) == 1
    item = view.research_added[0]
    assert item.id == "RA-001" and item.included and not item.verified
    assert item.why == "Seen in most similar apps"
    assert item.sources[0].url == "https://example.com/ecommerce-patterns"
    off = spec.assumptions.model_copy(
        update={
            "research_additions": [
                a.model_copy(update={"included": False, "kind": "compliance"})
                for a in spec.assumptions.research_additions
            ]
        }
    )
    toggled = plan(spec.model_copy(update={"assumptions": off}), brief).research_added[0]
    assert not toggled.included and toggled.why == "Worth checking for apps like yours"


def test_enterprise_research_shows_the_full_rationale(spec: AppSpec, brief: Brief) -> None:
    enterprise = spec.model_copy(
        update={"header": spec.header.model_copy(update={"mode": "enterprise"})}
    )
    note = ResearchNote(
        brief_hash="abc",
        suggestions=[
            ResearchSuggestion(
                id="RA-001",
                kind="feature",
                title="Order tracking",
                rationale="Nine of ten leading shops show a timeline.",
            )
        ],
    )
    assert (
        plan(enterprise, brief, research_note=note).research_added[0].why.startswith("Nine of ten")
    )
    assert (
        plan(spec, brief, research_note=note).research_added[0].why == "Seen in most similar apps"
    )


# --------------------------------------------------------------------------- integrations, roles


def test_real_versus_mocked_integrations(spec: AppSpec, brief: Brief) -> None:
    items = [
        *spec.integrations.items,
        Integration(
            id="INT-push", name="Push", purpose="Alerts", mode="real", needs_dev_build=True
        ),
        Integration(id="INT-crm", name="CRM", purpose="Contacts", mode="adapter"),
    ]
    changed = spec.model_copy(
        update={"integrations": spec.integrations.model_copy(update={"items": items})}
    )
    statuses = {i.id: (i.mode, i.needs_dev_build) for i in plan(changed, brief).integrations}
    assert statuses["INT-payments"] == ("mock", False) and statuses["INT-push"] == ("real", True)
    assert statuses["INT-crm"] == ("adapter", False)


def test_roles_see_the_screens_their_gates_allow(spec: AppSpec, brief: Brief) -> None:
    roles = {r.id: r for r in plan(spec, brief).roles}
    assert set(roles) == {"ROLE-customer", "ROLE-admin"}
    assert roles["ROLE-admin"].name == "Shop owner"
    assert len(roles["ROLE-admin"].sees) == 14  # permission "*"
    customer = roles["ROLE-customer"].sees
    assert "Today" in customer and "Your cart" in customer
    assert "Orders" in customer


def test_gate_based_visibility_without_permissions(spec: AppSpec, brief: Brief) -> None:
    from mde_contracts.spec import Role

    base = make_sample_spec(55)  # past the 51 customer screens: the admin dashboard exists
    stripped = base.model_copy(update={"roles": base.roles.model_copy(update={"permissions": []})})
    views = {r.id: r.sees for r in plan(stripped, brief).roles}
    assert "Dashboard" not in views["ROLE-customer"]  # gated to ROLE-admin
    assert "Dashboard" in views["ROLE-admin"]
    assert "Your cart" in views["ROLE-customer"] and "Your cart" in views["ROLE-admin"]  # public
    assert "Orders" in views["ROLE-customer"]  # authenticated, same surface
    assert "Orders" not in views["ROLE-admin"]  # authenticated, other surface
    extra = base.roles.model_copy(
        update={
            "roles": [*base.roles.roles, Role(id="ROLE-guest", name="Guest", surface="guest")],
            "permissions": [],
        }
    )
    guest = {r.id: r.sees for r in plan(base.model_copy(update={"roles": extra}), brief).roles}[
        "ROLE-guest"
    ]
    assert "Your cart" in guest and "Orders" not in guest  # public yes, customer-only no


# --------------------------------------------------------------------------- diff, assumptions, backlog


def test_no_baseline_means_no_diff(spec: AppSpec, brief: Brief) -> None:
    assert plan(spec, brief).diff_since_gate1 is None


def test_diff_since_gate1_lists_added_and_removed_ids_in_plain_lines(brief: Brief) -> None:
    older = make_sample_spec(12)
    newer = make_sample_spec(14)
    view = plan(newer, brief, gate1_ids=spec_ids(older))
    diff = view.diff_since_gate1
    assert diff is not None and diff.since == "gate1"
    assert [i for i in diff.added_ids if i.startswith("SCR")] == ["SCR-013", "SCR-014"]
    assert "FLW-004" in diff.added_ids and diff.removed_ids == []
    assert diff.lines[0].startswith("Added screen: ")
    assert diff.changed_ids == []
    shrunk = plan(older, brief, gate1_ids=spec_ids(newer)).diff_since_gate1
    assert shrunk is not None and shrunk.removed_ids == ["SCR-013", "SCR-014", "FLW-004"]
    assert shrunk.lines[:2] == ["Removed screen: SCR-013", "Removed screen: SCR-014"]
    assert shrunk.lines[2] == "Removed journey: FLW-004"


def test_diff_with_nothing_changed_says_so(spec: AppSpec, brief: Brief) -> None:
    diff = plan(spec, brief, gate1_ids=spec_ids(spec)).diff_since_gate1
    assert diff is not None and diff.lines == ["Nothing changed since the first draft."]


def test_diff_lines_are_capped(brief: Brief) -> None:
    diff = plan(make_sample_spec(60), brief, gate1_ids=["SCR-001"]).diff_since_gate1
    assert diff is not None and len(diff.lines) == 21
    assert diff.lines[-1].startswith("...and ") and diff.lines[-1].endswith("more changes")


def test_diff_labels_other_kinds_of_items(brief: Brief) -> None:
    spec = make_sample_spec(14)
    baseline = [
        i for i in spec_ids(spec) if i not in ("ENT-Review", "INT-email", "ROLE-admin", "FLW-003")
    ]
    diff = plan(spec, brief, gate1_ids=baseline).diff_since_gate1
    assert diff is not None
    assert "Added data: Reviews" in diff.lines and "Added integration: Email receipts" in diff.lines
    assert "Added role: Shop owner" in diff.lines and "Added journey: Track orders" in diff.lines


def test_assumptions_come_from_the_brief_with_their_toggles(spec: AppSpec, brief: Brief) -> None:
    view = plan(spec, brief)
    assert [(a.slot_id, a.included) for a in view.assumptions] == [
        ("auth.method", True),
        ("pay.provider", False),
    ]


def test_backlog_is_shown_only_when_present(spec: AppSpec, brief: Brief) -> None:
    assert plan(spec, brief).backlog == []
    backlog = [BacklogItem(title="Gift cards", reason="phase 2")]
    assert plan(spec.model_copy(update={"backlog": backlog}), brief).backlog == backlog


# --------------------------------------------------------------------------- enterprise


def test_guided_has_no_enterprise_extras(spec: AppSpec, brief: Brief) -> None:
    assert plan(spec, brief).enterprise is None


def test_enterprise_extras_list_the_required_approvers(spec: AppSpec, brief: Brief) -> None:
    policy = Policy(
        id="acme",
        mode="enterprise",
        gates=PolicyGates(
            plan=GateRule(approvers=["requester", "delivery_lead"], full_file_view=True)
        ),
    )
    extras = plan(spec, brief, policy=policy).enterprise
    assert extras is not None and extras.full_file_view
    assert [(a.role, a.decision) for a in extras.approvers] == [
        ("requester", None),
        ("delivery_lead", None),
    ]
    status = [ApproverStatus(role="requester", user_id="u1", decision="approve")]
    given = plan(spec, brief, policy=policy, approver_status=status).enterprise
    assert given is not None and given.approvers == status


def test_an_enterprise_spec_gets_extras_even_with_a_guided_policy(
    spec: AppSpec, brief: Brief
) -> None:
    enterprise = spec.model_copy(
        update={"header": spec.header.model_copy(update={"mode": "enterprise"})}
    )
    assert plan(enterprise, brief).enterprise is not None


# --------------------------------------------------------------------------- changed ids from the first draft


def test_changed_ids_compare_the_first_draft_with_the_current_one(
    spec: AppSpec, brief: Brief
) -> None:
    first = spec
    edited_screen = spec.screens[2].model_copy(update={"purpose": "A new purpose for this screen"})
    entity = spec.data.entities[0].model_copy(update={"plain_name": "People"})
    now = spec.model_copy(
        update={
            "screens": [
                edited_screen if s.id == edited_screen.id else s for s in spec.screens[:-1]
            ],
            "data": spec.data.model_copy(update={"entities": [entity, *spec.data.entities[1:]]}),
        }
    )
    view = plan(now, brief, gate1_ids=[], gate1_spec=first)
    diff = view.diff_since_gate1
    assert diff is not None and diff.since == "gate1"
    assert diff.removed_ids == [spec.screens[-1].id] and diff.added_ids == []
    assert diff.changed_ids == [edited_screen.id, entity.id]
    assert any(line.startswith("Changed screen:") for line in diff.lines)
    assert any(line.startswith("Changed data: People") for line in diff.lines)
    # ids alone (no first draft) still give added and removed, but cannot say what changed
    ids_only = plan(now, brief, gate1_ids=spec_ids(first))
    assert ids_only.diff_since_gate1 is not None and ids_only.diff_since_gate1.changed_ids == []
    same = plan(first, brief, gate1_ids=[], gate1_spec=first)
    assert same.diff_since_gate1 is not None
    assert same.diff_since_gate1.lines == ["Nothing changed since the first draft."]


# --------------------------------------------------------------------------- BUG-4: one granularity on both sides

# what the flow records of the first draft: the items and also the block ids
BLOCK_IDS = ["APP", "DESIGN", "NAV", "BEHAVIOR", "DATA", "API", "ROLES"]


def test_block_ids_among_the_first_drafts_ids_are_never_reported_as_removed(
    spec: AppSpec, brief: Brief
) -> None:
    diff = plan(spec, brief, gate1_ids=[*spec_ids(spec), *BLOCK_IDS]).diff_since_gate1
    assert diff is not None
    assert diff.removed_ids == [] and diff.added_ids == [] and diff.changed_ids == []
    assert diff.lines == ["Nothing changed since the first draft."]


def test_block_ids_do_not_hide_a_real_removal(spec: AppSpec, brief: Brief) -> None:
    older = make_sample_spec(16)
    diff = plan(spec, brief, gate1_ids=[*spec_ids(older), *BLOCK_IDS]).diff_since_gate1
    assert diff is not None
    assert diff.removed_ids == ["SCR-015", "SCR-016"] + [
        i for i in spec_ids(older) if i not in spec_ids(spec) and not i.startswith("SCR")
    ]
    assert not any("Removed item" in line for line in diff.lines)
    assert all(line.startswith("Removed ") for line in diff.lines)


def test_the_same_ids_are_compared_whether_the_first_draft_or_only_its_ids_are_given(
    spec: AppSpec, brief: Brief
) -> None:
    older = make_sample_spec(12)
    from_spec = plan(spec, brief, gate1_ids=[], gate1_spec=older).diff_since_gate1
    from_ids = plan(spec, brief, gate1_ids=[*spec_ids(older), *BLOCK_IDS]).diff_since_gate1
    assert from_spec is not None and from_ids is not None
    assert from_spec.added_ids == from_ids.added_ids
    assert from_spec.removed_ids == from_ids.removed_ids == []
    both = plan(spec, brief, gate1_ids=[*spec_ids(older), *BLOCK_IDS], gate1_spec=older)
    assert both.diff_since_gate1 is not None
    assert both.diff_since_gate1.added_ids == from_spec.added_ids


def test_a_changed_look_is_one_plain_line_and_the_block_is_not_reported_removed(
    spec: AppSpec, brief: Brief
) -> None:
    darker = spec.model_copy(
        update={"design": spec.design.model_copy(update={"direction": "bold-dark"})}
    )
    diff = plan(
        darker, brief, gate1_ids=[*spec_ids(spec), *BLOCK_IDS], gate1_spec=spec
    ).diff_since_gate1
    assert diff is not None
    assert diff.lines == ["Changed the look"]
    assert diff.changed_ids == ["DESIGN"] and diff.removed_ids == [] and diff.added_ids == []


def test_the_other_singleton_blocks_have_their_own_plain_line(spec: AppSpec, brief: Brief) -> None:
    renamed = spec.model_copy(update={"app": spec.app.model_copy(update={"name": "Another name"})})
    quiet = spec.model_copy(
        update={"behavior": spec.behavior.model_copy(update={"offline": "Online only"})}
    )
    both = quiet.model_copy(update={"app": renamed.app})
    diff = plan(both, brief, gate1_ids=[], gate1_spec=spec).diff_since_gate1
    assert diff is not None
    assert diff.lines == ["Changed the app's name or description", "Changed how the app behaves"]
    assert diff.changed_ids == ["APP", "BEHAVIOR"]


def test_items_and_singleton_blocks_changed_together_read_in_one_list(
    spec: AppSpec, brief: Brief
) -> None:
    edited = spec.screens[2].model_copy(update={"purpose": "A new purpose for this screen"})
    now = spec.model_copy(
        update={
            "screens": [edited if s.id == edited.id else s for s in spec.screens],
            "design": spec.design.model_copy(update={"direction": "bold-dark"}),
        }
    )
    diff = plan(now, brief, gate1_ids=[], gate1_spec=spec).diff_since_gate1
    assert diff is not None and diff.changed_ids == [edited.id, "DESIGN"]
    assert diff.lines[0].startswith("Changed screen: ") and diff.lines[-1] == "Changed the look"


def test_a_removed_item_is_named_when_the_first_draft_is_known(spec: AppSpec, brief: Brief) -> None:
    gone = spec.screens[-1]
    now = spec.model_copy(update={"screens": spec.screens[:-1]})
    diff = plan(now, brief, gate1_ids=[], gate1_spec=spec).diff_since_gate1
    assert diff is not None and diff.removed_ids == [gone.id]
    assert diff.lines[0] == f"Removed screen: {screen_title(gone)}"
    ids_only = plan(now, brief, gate1_ids=spec_ids(spec)).diff_since_gate1
    assert ids_only is not None and ids_only.lines[0] == f"Removed screen: {gone.id}"


def test_notes_head_the_list_and_are_never_cut(spec: AppSpec, brief: Brief) -> None:
    notes = ["I could not do the first thing.", "I could not do the second thing."]
    plain = plan(spec, brief, gate1_ids=spec_ids(spec), notes=notes).diff_since_gate1
    assert plain is not None and plain.lines == notes  # no "Nothing changed" beside a note
    big = plan(make_sample_spec(60), brief, gate1_ids=["SCR-001"], notes=notes).diff_since_gate1
    assert big is not None and big.lines[:2] == notes
    assert len(big.lines) == 2 + 21 and big.lines[-1].endswith("more changes")


def test_notes_alone_give_a_diff_when_there_is_no_baseline(spec: AppSpec, brief: Brief) -> None:
    view = plan(spec, brief, notes=["I could not do that."])
    assert view.diff_since_gate1 is not None
    assert view.diff_since_gate1.since == "gate1" and view.diff_since_gate1.lines == [
        "I could not do that."
    ]
    assert view.diff_since_gate1.added_ids == view.diff_since_gate1.removed_ids == []
