from __future__ import annotations

import pytest

from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec, NavNode
from mde_spec_builder.assemble import (
    assign_flow_ids,
    assign_screen_ids,
    canonicalize,
    changed_ids,
    flow_id,
    nav_id,
    navigation_screen_order,
    neighbors,
    number_navigation,
    reference_graph,
    refs,
    renumber_screens,
    rewrite_screen_ids,
    screen_id,
    spec_ids,
)
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec, make_sample_stack_profile
from mde_spec_builder.validators import run_validators
from mde_spec_loader.graph import build_ref_graph


@pytest.fixture(scope="module")
def spec() -> AppSpec:
    return make_sample_spec(14)


def test_ids_are_zero_padded() -> None:
    assert screen_id(1) == "SCR-001" and screen_id(14) == "SCR-014" and screen_id(999) == "SCR-999"
    assert flow_id(3) == "FLW-003" and nav_id(12) == "NAV-012"
    for bad in (0, 1000, -1):
        with pytest.raises(ValueError, match="out of range"):
            screen_id(bad)


def test_assign_screen_ids_follows_navigation_order_then_extras() -> None:
    mapping = assign_screen_ids(["home", "cart", "home", "pay"], ["admin", "cart"])
    assert mapping == {"home": "SCR-001", "cart": "SCR-002", "pay": "SCR-003", "admin": "SCR-004"}
    assert assign_screen_ids(["b", "a"]) == {
        "b": "SCR-001",
        "a": "SCR-002",
    }  # given order, not sorted
    assert assign_screen_ids(["x", "y"]) == assign_screen_ids(["x", "y"])
    assert assign_flow_ids(["browse", "pay", "browse"]) == {"browse": "FLW-001", "pay": "FLW-002"}


def test_assign_rejects_more_than_999_screens() -> None:
    with pytest.raises(ValueError, match="out of range"):
        assign_screen_ids(f"k{i}" for i in range(1000))


def test_navigation_screen_order_is_depth_first_parents_first(spec: AppSpec) -> None:
    order = navigation_screen_order(spec.navigation.root)
    assert order == [s.id for s in spec.screens]  # the sample numbers screens in navigation order
    leaf = NavNode(id="NAV-001", route="/", kind="stack")
    assert navigation_screen_order(leaf) == []


def test_number_navigation_renumbers_depth_first(spec: AppSpec) -> None:
    root = spec.navigation.root
    scrambled = root.model_copy(update={"id": "NAV-777"})
    renumbered = number_navigation(scrambled)
    assert renumbered.id == "NAV-001"
    assert number_navigation(root) == root  # already numbered in pre-order
    assert number_navigation(root, start=10).id == "NAV-010"


def test_reference_graph_helpers_accept_a_spec_or_a_graph(spec: AppSpec) -> None:
    graph = reference_graph(spec)
    assert graph.unresolved == []
    assert neighbors(spec, "SCR-006") == neighbors(graph, "SCR-006")
    assert refs(spec, "SCR-006") == refs(graph, "SCR-006")
    assert "API-getProduct" in refs(graph, "SCR-006").operations
    assert refs(graph, "SCR-006", deep=False).entities == []
    assert reference_graph(spec).edges == build_ref_graph(spec).edges


def test_spec_ids_cover_every_addressable_item(spec: AppSpec) -> None:
    ids = spec_ids(spec)
    assert ids[0] == "SCR-001" and "FLW-001" in ids and "ENT-Order" in ids
    assert "API-getProduct" in ids and "ROLE-admin" in ids and "INT-payments" in ids
    assert len(ids) == len(set(ids))


def test_canonicalize_sorts_screens_and_flows(spec: AppSpec) -> None:
    shuffled = spec.model_copy(
        update={"screens": list(reversed(spec.screens)), "flows": list(reversed(spec.flows))}
    )
    assert canonicalize(shuffled) == spec
    assert render_spec(canonicalize(shuffled)) == render_spec(spec)


def _permute_screen_ids(spec: AppSpec) -> AppSpec:
    """Reverse the numbering of every screen id everywhere (a worst-case id scramble)."""
    total = len(spec.screens)
    mapping = {s.id: screen_id(total - i) for i, s in enumerate(spec.screens)}
    data = rewrite_screen_ids(spec.model_dump(mode="python"), mapping)
    assert isinstance(data, dict)
    return AppSpec.model_validate(data)


def test_renumber_screens_restores_navigation_order_and_every_reference(spec: AppSpec) -> None:
    scrambled = _permute_screen_ids(spec)
    assert scrambled != spec
    assert scrambled.screens[0].id == "SCR-014"
    fixed = renumber_screens(scrambled)
    assert fixed == spec
    findings = run_validators(fixed, stack_profile=make_sample_stack_profile(), limits=Limits())
    assert findings == []


def test_renumber_handles_screens_outside_the_navigation_tree(spec: AppSpec) -> None:
    from mde_contracts.spec import ScreenBlock

    extra = ScreenBlock(id="SCR-050", route="/extra", recipe="list", purpose="Extra")
    with_extra = spec.model_copy(update={"screens": [*spec.screens, extra]})
    fixed = renumber_screens(with_extra)
    assert [s.id for s in fixed.screens][-1] == "SCR-015"  # after the 14 in the tree
    assert fixed.screens[-1].route == "/extra"


def test_changed_ids_reports_what_differs(spec: AppSpec) -> None:
    other = make_sample_spec(13)
    assert changed_ids(spec, spec) == []
    assert "SCR-014" in changed_ids(spec, other)
    assert changed_ids(other, spec) == changed_ids(other, spec)
