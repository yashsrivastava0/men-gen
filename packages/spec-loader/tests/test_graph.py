from __future__ import annotations

import pytest

from mde_contracts.spec import AppSpec, NavNode, ScreenAction, ScreenBlock
from mde_spec_loader import Binding, build_ref_graph, parse_binding
from mde_spec_loader.graph import auth_tokens


def _with_screen(spec: AppSpec, screen_id: str, **changes: object) -> AppSpec:
    screens = [s.model_copy(update=changes) if s.id == screen_id else s for s in spec.screens]
    return spec.model_copy(update={"screens": screens})


def _screen(spec: AppSpec, screen_id: str) -> ScreenBlock:
    return next(s for s in spec.screens if s.id == screen_id)


def test_parse_binding() -> None:
    assert parse_binding("API-getProduct(id)") == Binding("api", "API-getProduct", None, ("id",))
    assert parse_binding("API-listProducts") == Binding("api", "API-listProducts", None, ())
    assert parse_binding("API-x(a, b)") == Binding("api", "API-x", None, ("a", "b"))
    assert parse_binding("ENT-Product.name") == Binding("entity", "ENT-Product", "name", ())
    assert parse_binding("ENT-Order.items.product") == Binding("entity", "ENT-Order", "items", ())
    assert parse_binding("ENT-Product") == Binding("entity", "ENT-Product", None, ())
    assert parse_binding("getProduct(id)") is None
    assert parse_binding("ENT-Product.") is None


def test_auth_tokens() -> None:
    assert auth_tokens("public") == ["public"]
    assert auth_tokens("ROLE-admin, ROLE-staff") == ["ROLE-admin", "ROLE-staff"]
    assert auth_tokens("") == []


def test_a_valid_spec_has_no_unresolved_edges(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    assert graph.unresolved == []
    assert graph.kind("SCR-001") == "screen" and graph.kind("ENT-Order") == "entity"
    assert graph.kind("API-getProduct") == "operation" and graph.kind("S5") == "step"
    assert graph.exists("ENUM:OrderStatus") and not graph.exists("ENUM:Nope")


def test_every_screen_is_reachable(spec12: AppSpec, spec50: AppSpec) -> None:
    for spec in (spec12, spec50):
        graph = build_ref_graph(spec)
        assert graph.reachable_screens() == [s.id for s in spec.screens]


def test_a_screen_only_reachable_through_an_action_counts(spec12: AppSpec) -> None:
    # remove SCR-004 (search) from the nav tree but keep the action that goes to it from home
    def prune(node: NavNode) -> NavNode:
        children = [prune(c) for c in node.children if c.screen != "SCR-004"]
        return node.model_copy(update={"children": children})

    nav = spec12.navigation.model_copy(update={"root": prune(spec12.navigation.root)})
    graph = build_ref_graph(spec12.model_copy(update={"navigation": nav}))
    assert "SCR-004" in graph.reachable_screens()


def test_unreachable_screens_are_left_out(spec12: AppSpec) -> None:
    home = _screen(spec12, "SCR-003")
    cut = home.model_copy(update={"actions": [a for a in home.actions if a.go != "SCR-004"]})

    def prune(node: NavNode) -> NavNode:
        children = [prune(c) for c in node.children if c.screen != "SCR-004"]
        return node.model_copy(update={"children": children})

    nav = spec12.navigation.model_copy(update={"root": prune(spec12.navigation.root)})
    spec = _with_screen(spec12, "SCR-003", actions=cut.actions).model_copy(
        update={"navigation": nav}
    )
    assert "SCR-004" not in build_ref_graph(spec).reachable_screens()


def test_neighbors_of_a_screen(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    product = graph.neighbors("SCR-006")
    assert "SCR-005" in product  # navigation parent (catalog)
    assert "SCR-007" in product  # next screen in the browse flow (cart)
    assert "SCR-006" not in product
    assert graph.neighbors("SCR-006") == sorted(product)


def test_neighbors_of_a_non_screen_are_the_screens_that_touch_it(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    touching = graph.neighbors("API-getProduct")
    assert "SCR-006" in touching and all(graph.kind(s) == "screen" for s in touching)


def test_refs_of_a_screen_are_deep_by_default(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    refs = graph.refs("SCR-006")
    assert {"API-getProduct", "API-listReviews", "API-addCartItem"} <= set(refs.operations)
    assert {"ENT-Product", "ENT-Review", "ENT-CartItem", "ENT-Cart"} <= set(refs.entities)
    assert "ENT-Category" in refs.entities  # Product.category is a ref field (one hop)
    assert "ProductStatus" in refs.enums
    shallow = graph.refs("SCR-006", deep=False)
    assert "ENT-Product" not in shallow.entities
    assert shallow.operations == refs.operations


def test_refs_collect_roles_integrations_and_research(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    assert graph.refs("SCR-011").research == ["RA-001"]
    gated = _with_screen(spec12, "SCR-010", auth="ROLE-admin")
    assert build_ref_graph(gated).refs("SCR-010").roles == ["ROLE-admin"]
    mentions = _with_screen(spec12, "SCR-008", notes="Pays through INT-payments (mock).")
    assert build_ref_graph(mentions).refs("SCR-008").integrations == ["INT-payments"]
    assert graph.refs("API-checkout").entities == ["ENT-Address", "ENT-Order", "ENT-User"]


def test_refs_of_flows_and_entities(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    assert graph.refs("FLW-001").screens[0] == "SCR-003"
    order = graph.refs("ENT-Order")
    assert {"ENT-User", "ENT-Address"} <= set(order.entities) and order.enums == ["OrderStatus"]


def test_referenced_by(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    users = graph.referenced_by("API-getProduct")
    assert "SCR-006" in users
    assert "NAV-009" in graph.referenced_by("SCR-006")


def test_unresolved_edges_are_reported(spec12: AppSpec) -> None:
    broken = _with_screen(
        spec12,
        "SCR-006",
        actions=[
            ScreenAction(name="x", calls="API-nope"),
            ScreenAction(name="y", go="SCR-099"),
            ScreenAction(name="z", go="/no/such/route"),
            ScreenAction(name="back_home", go="back"),
            ScreenAction(name="route_ok", go="/cart"),
        ],
        auth="admin",
        flow="FLW-099",
    )
    unresolved = {(e.relation, e.dst) for e in build_ref_graph(broken).unresolved}
    assert ("calls", "API-nope") in unresolved
    assert ("go", "SCR-099") in unresolved
    assert ("go", "/no/such/route") in unresolved
    assert ("auth_invalid", "admin") in unresolved
    assert ("screen_flow", "FLW-099") in unresolved
    assert not any(
        dst in ("back", "/cart") for _r, dst in unresolved
    )  # relative and route targets resolve


def test_a_route_go_resolves_to_the_screen(spec12: AppSpec) -> None:
    changed = _with_screen(spec12, "SCR-006", actions=[ScreenAction(name="r", go="/cart")])
    graph = build_ref_graph(changed)
    assert [e.dst for e in graph.out_edges("SCR-006", "go")] == ["SCR-007"]


def test_on_success_screen_id_counts_as_a_go(spec12: AppSpec) -> None:
    graph = build_ref_graph(spec12)
    targets = [(e.dst, e.detail) for e in graph.out_edges("SCR-008", "go")]
    assert ("SCR-009", "place_order.on_success") in targets


def test_invalid_binding_is_an_always_unresolved_edge(spec12: AppSpec) -> None:
    from mde_contracts.spec import DataBinding

    broken = _with_screen(spec12, "SCR-006", data=[DataBinding(bind="oops")])
    edges = [e for e in build_ref_graph(broken).unresolved if e.relation == "bind_invalid"]
    assert [e.dst for e in edges] == ["oops"]


@pytest.mark.parametrize("deep", [True, False])
def test_refs_never_include_navigation_links(spec12: AppSpec, deep: bool) -> None:
    plain = _with_screen(spec12, "SCR-005", actions=[])
    graph = build_ref_graph(plain)
    assert "SCR-006" in graph.neighbors("SCR-005")  # still a navigation neighbor
    assert "SCR-006" not in graph.refs("SCR-005", deep=deep).screens  # but not a reference
