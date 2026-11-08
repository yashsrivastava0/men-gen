from __future__ import annotations

from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_contracts.spec import AppSpec, FlowBlock, ScreenBlock
from mde_spec_builder.sequence import (
    STEP_GATES,
    STEP_NAMES,
    build_sequence,
    globs_overlap,
    owns_for_group,
    primary_flow,
    route_segments,
    screen_groups,
    vertical_slice_screens,
)
from mde_spec_builder.testing import make_sample_spec


def _matches(glob: str, path: str) -> bool:
    return path.startswith(glob[:-2]) if glob.endswith("/**") else path == glob


def _file_of(screen: ScreenBlock) -> str:
    return f"app/{'/'.join(route_segments(screen.route))}.tsx"


def _with_flows(spec: AppSpec, flows: list[FlowBlock]) -> AppSpec:
    return spec.model_copy(update={"flows": flows})


# --------------------------------------------------------------------------- the eleven steps


def test_s0_to_s10_are_generated_in_order_with_the_documented_loads() -> None:
    seq = build_sequence(make_sample_spec(14))
    assert [s.id for s in seq.steps] == [f"S{i}" for i in range(11)]
    loads = {s.id: s.load for s in seq.steps}
    assert (
        loads["S0"] == ["APP", "DESIGN"] and loads["S1"] == ["DESIGN"] and loads["S2"] == ["DESIGN"]
    )
    assert loads["S3"] == ["NAV", "ROLES"] and loads["S4"] == ["DATA", "API"]
    assert loads["S7"] == ["BEHAVIOR"] and loads["S8"] == ["INTEGRATIONS"]
    assert loads["S9"] == ["DESIGN", "BEHAVIOR"] and loads["S10"] == ["ACCEPT"]
    assert all(s.name == STEP_NAMES[s.id] and s.gate == STEP_GATES[s.id] for s in seq.steps)


def test_only_the_vertical_slice_stops_for_feedback() -> None:
    seq = build_sequence(make_sample_spec(14))
    assert [s.id for s in seq.steps if s.stop_for_feedback] == ["S5"]


def test_s5_is_the_primary_flow_end_to_end() -> None:
    spec = make_sample_spec(14)
    s5 = build_sequence(spec).steps[5]
    primary = next(f for f in spec.flows if f.primary)
    assert s5.flow == primary.id == "FLW-001"
    assert s5.screens == primary.screens and s5.load == [primary.id]
    assert s5.parallel == []


def test_the_build_sequence_is_deterministic() -> None:
    spec = make_sample_spec(30, seed=5)
    assert build_sequence(spec) == build_sequence(spec)
    assert build_sequence(spec) == spec.build  # the sample stores exactly this sequence


# --------------------------------------------------------------------------- primary flow


def test_primary_flow_prefers_the_marked_flow() -> None:
    spec = make_sample_spec(14)
    flows = [f.model_copy(update={"primary": f.id == "FLW-003"}) for f in spec.flows]
    assert primary_flow(_with_flows(spec, flows)) is not None
    assert primary_flow(_with_flows(spec, flows)).id == "FLW-003"  # type: ignore[union-attr]


def test_the_first_of_several_primaries_wins() -> None:
    spec = make_sample_spec(14)
    flows = [f.model_copy(update={"primary": True}) for f in spec.flows]
    assert primary_flow(_with_flows(spec, flows)).id == flows[0].id  # type: ignore[union-attr]


def test_without_a_marked_flow_the_biggest_one_is_used() -> None:
    spec = make_sample_spec(14)
    flows = [f.model_copy(update={"primary": False}) for f in spec.flows]
    biggest = max(flows, key=lambda f: len(f.screens))
    assert primary_flow(_with_flows(spec, flows)).id == biggest.id  # type: ignore[union-attr]


def test_ties_go_to_the_lowest_id_and_unknown_screens_do_not_count() -> None:
    spec = make_sample_spec(6)
    a = FlowBlock(
        id="FLW-002", name="B", screens=["SCR-001", "SCR-002"], start="SCR-001", end="SCR-002"
    )
    b = FlowBlock(
        id="FLW-001", name="A", screens=["SCR-003", "SCR-004"], start="SCR-003", end="SCR-004"
    )
    ghost = FlowBlock(
        id="FLW-000",
        name="G",
        screens=["SCR-090", "SCR-091", "SCR-092"],
        start="SCR-090",
        end="SCR-092",
    )
    chosen = primary_flow(_with_flows(spec, [a, b, ghost]))
    assert chosen is not None and chosen.id == "FLW-001"


def test_no_flow_means_no_primary_flow() -> None:
    assert primary_flow(_with_flows(make_sample_spec(6), [])) is None


def test_without_flows_the_slice_starts_at_the_first_navigation_screen() -> None:
    spec = _with_flows(make_sample_spec(10), [])
    unflowed = spec.model_copy(
        update={"screens": [s.model_copy(update={"flow": None}) for s in spec.screens]}
    )
    flow, ids = vertical_slice_screens(unflowed)
    assert flow is None
    assert ids[0] == "SCR-001" and 1 <= len(ids) <= 5 and len(set(ids)) == len(ids)
    seq = build_sequence(unflowed)
    assert seq.steps[5].flow is None and seq.steps[5].screens == ids and seq.steps[5].load == []
    built = set(ids) | {s for g in seq.steps[6].parallel for s in g.screens}
    assert built == {s.id for s in unflowed.screens}


def test_a_spec_with_no_screens_has_empty_s5_and_s6() -> None:
    spec = make_sample_spec(3).model_copy(update={"screens": [], "flows": []})
    seq = build_sequence(spec)
    assert seq.steps[5].screens == [] and seq.steps[6].parallel == []


# --------------------------------------------------------------------------- groups and ownership


def test_every_screen_is_in_exactly_one_s6_group_or_in_s5() -> None:
    spec = make_sample_spec(40)
    seq = build_sequence(spec)
    slice_ids = seq.steps[5].screens
    grouped = [s for g in seq.steps[6].parallel for s in g.screens]
    assert len(grouped) == len(set(grouped))
    assert not set(grouped) & set(slice_ids)
    assert set(grouped) | set(slice_ids) == {s.id for s in spec.screens}


def test_groups_follow_flows_then_surfaces() -> None:
    spec = make_sample_spec(30)
    groups = {g.group: g for g in build_sequence(spec).steps[6].parallel}
    assert "sign-in-or-sign-up" in groups and "track-orders" in groups
    assert set(groups["sign-in-or-sign-up"].screens) == {"SCR-001", "SCR-002"}
    surface_groups = [name for name in groups if name in ("customer", "admin")]
    assert surface_groups  # screens that belong to no flow fall back to their surface


def test_group_names_are_unique_even_when_flow_names_collide() -> None:
    spec = make_sample_spec(14)
    renamed = [
        f.model_copy(update={"name": "Same name", "primary": f.id == "FLW-001"}) for f in spec.flows
    ]
    names = [g.group for g in build_sequence(_with_flows(spec, renamed)).steps[6].parallel]
    assert len(names) == len(set(names))
    assert any(n.startswith("same-name-") for n in names)


def test_owns_examples() -> None:
    mine = [route_segments("/orders"), route_segments("/orders/[id]")]
    assert owns_for_group(mine, []) == ["app/orders.tsx", "app/orders/**"]
    # another group uses a path under /orders: fall back to exact files
    outside = [route_segments("/orders/[id]/invoice")]
    assert owns_for_group(mine[:1], outside) == ["app/orders.tsx"]
    pair = [route_segments("/login"), route_segments("/register")]
    assert owns_for_group(pair, []) == ["app/login.tsx", "app/register.tsx"]
    assert owns_for_group([route_segments("/")], []) == ["app/index.tsx"]
    assert owns_for_group(
        [route_segments("/(tabs)/browse/a"), route_segments("/(tabs)/browse/b")], []
    ) == ["app/(tabs)/browse/**"]


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("app/a.tsx", "app/a.tsx", True),
        ("app/a.tsx", "app/b.tsx", False),
        ("app/a/**", "app/a/b/**", True),
        ("app/a/b/**", "app/a/**", True),
        ("app/a/**", "app/b/**", False),
        ("app/a/**", "app/a/x.tsx", True),
        ("app/a/x.tsx", "app/a/**", True),
        ("app/a/**", "app/a.tsx", False),
        ("app/ab/**", "app/a/**", False),
        ("app/a/**", "app/ab/x.tsx", False),
    ],
)
def test_globs_overlap(a: str, b: str, overlap: bool) -> None:
    assert globs_overlap(a, b) is overlap


def test_route_segments() -> None:
    assert route_segments("/product/[id]") == ("product", "[id]")
    assert route_segments("/") == ("index",) and route_segments("") == ("index",)
    assert route_segments("/(tabs)/a/") == ("(tabs)", "a")


@settings(max_examples=25, deadline=None)
@given(st.integers(min_value=1, max_value=65), st.integers(min_value=0, max_value=100))
def test_file_ownership_never_overlaps_and_covers_each_screen_once(n: int, seed: int) -> None:
    spec = make_sample_spec(n, seed=seed)
    groups = build_sequence(spec).steps[6].parallel
    for left, right in combinations(groups, 2):
        for a in left.owns:
            for b in right.owns:
                assert not globs_overlap(a, b), (left.group, a, right.group, b)
    by_id = {s.id: s for s in spec.screens}
    for group in groups:
        for screen_id in group.screens:
            path = _file_of(by_id[screen_id])
            owners = [g.group for g in groups if any(_matches(o, path) for o in g.owns)]
            assert owners == [group.group], (screen_id, path, owners)
    in_slice = set(build_sequence(spec).steps[5].screens)
    for screen_id in in_slice:  # the vertical slice is built by one agent: no group owns its files
        path = _file_of(by_id[screen_id])
        assert not any(_matches(o, path) for g in groups for o in g.owns)


def test_screen_groups_directly() -> None:
    spec = make_sample_spec(20)
    groups = screen_groups(spec, [])  # nothing in the slice: every screen is grouped
    assert sorted(s for g in groups for s in g.screens) == [s.id for s in spec.screens]
    assert all(g.owns for g in groups)
