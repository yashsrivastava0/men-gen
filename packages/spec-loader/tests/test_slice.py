from __future__ import annotations

import pytest

from mde_contracts.spec import AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_loader import (
    SpecIndex,
    UnknownIdError,
    estimate_tokens,
    format_spec_tag,
    parse_spec_tag,
)

BUDGET = 10_000


def test_a_screen_slice_has_the_four_tier_layout(index12: SpecIndex) -> None:
    sliced = index12.slice("SCR-006")
    assert sliced.id == "SCR-006" and sliced.spec_tag == "@spec SCR-006@v1"
    assert [t.tier for t in sliced.tiers] == [0, 1, 2]
    tier0, tier1, tier2 = sliced.tiers
    assert tier0.block_ids == ["HEADER"]
    assert tier1.block_ids == ["DESIGN", "NAV"]
    assert tier2.block_ids[0] == "SCR-006"
    assert sliced.block_ids == [*tier0.block_ids, *tier1.block_ids, *tier2.block_ids]
    assert sliced.token_estimate == estimate_tokens(sliced.text)
    assert sum(t.token_estimate for t in sliced.tiers) <= sliced.token_estimate
    assert "## Tier 0: always" in sliced.text and "## Tier 3" not in sliced.text


def test_tier_zero_carries_header_summary_tone_naming_and_rules(index12: SpecIndex) -> None:
    text = index12.slice("SCR-001").text
    assert "spec_version: 1" in text and "stack_profile: expo-managed" in text
    assert "Summary: " in text and "Tone: " in text
    assert 'Naming: app "' in text and "`@spec <ID>@v1`" in text
    assert "raise_decision(question, options, default)" in text
    assert "Stack rule" not in text
    custom = index12.slice("SCR-001", stack_rules=["Use expo-router", "No class components"])
    assert (
        "Stack rule: Use expo-router" in custom.text
        and "Stack rule: No class components" in custom.text
    )


def test_tier_one_is_the_exact_design_and_navigation_blocks(index12: SpecIndex) -> None:
    sliced = index12.slice("SCR-001")
    for block_id in ("DESIGN", "NAV"):
        assert index12.get([block_id])[block_id] in sliced.text


def test_tier_two_carves_only_what_the_screen_references(index12: SpecIndex) -> None:
    sliced = index12.slice("SCR-006")  # product detail
    ids = sliced.tiers[2].block_ids
    assert {"API-getProduct", "API-listReviews", "API-addCartItem"} <= set(ids)
    assert {"ENT-Product", "ENT-Review", "ENT-Cart", "ENT-Category"} <= set(ids)
    assert "ENUMS" in ids and "ProductStatus" in sliced.text
    assert "API-getOrder" not in sliced.text and "ENT-Notification" not in sliced.text
    assert "```yaml id=API kind=api" not in sliced.text  # never the whole API block
    assert "### Referenced by this task" in sliced.text


def test_slice_includes_roles_and_integrations_the_screen_uses(
    index12: SpecIndex, spec12: AppSpec
) -> None:
    gated = spec12.model_copy(
        update={
            "screens": [
                s.model_copy(update={"auth": "ROLE-admin", "notes": "Pays through INT-payments"})
                if s.id == "SCR-008"
                else s
                for s in spec12.screens
            ]
        }
    )
    index = SpecIndex.from_text(render_spec(gated))
    ids = index.slice("SCR-008").tiers[2].block_ids
    assert "ROLE-admin" in ids and "INT-payments" in ids


def test_kit_signatures_are_added_by_recipe(index12: SpecIndex) -> None:
    recipe = index12.spec.screens[5].recipe  # SCR-006
    sliced = index12.slice(
        "SCR-006", kit_signatures={recipe: ["Button(props: {label: string})"], "other": ["X()"]}
    )
    assert "### Kit components (signatures)" in sliced.text
    assert "- Button(props: {label: string})" in sliced.text and "X()" not in sliced.text
    assert "Kit components" not in index12.slice("SCR-006").text


def test_tier_three_adds_neighbors_on_demand(index12: SpecIndex) -> None:
    plain = index12.slice("SCR-006")
    more = index12.slice("SCR-006", tier3=True, max_neighbors=1)
    assert [t.tier for t in more.tiers] == [0, 1, 2, 3]
    assert len(more.tiers[3].block_ids) == 1
    assert more.tiers[3].block_ids[0] in index12.neighbors("SCR-006")
    assert more.token_estimate > plain.token_estimate and "## Tier 3" in more.text
    assert [t.tier for t in index12.slice("S3", tier3=True).tiers] == [0, 1, 2]  # screens only


def test_locked_screens_are_flagged(spec12: AppSpec) -> None:
    locked = spec12.model_copy(
        update={
            "screens": [
                s.model_copy(update={"locked": True}) if s.id == "SCR-001" else s
                for s in spec12.screens
            ]
        }
    )
    sliced = SpecIndex.from_text(render_spec(locked)).slice("SCR-001")
    assert sliced.locked and "LOCKED: SCR-001" in sliced.text


def test_step_slices_load_what_the_step_declares(index12: SpecIndex) -> None:
    s0 = index12.slice("S0")
    assert s0.tiers[2].block_ids == ["S0", "APP"]  # DESIGN is already in tier 1
    s3 = index12.slice("S3")
    assert s3.tiers[2].block_ids == ["S3", "ROLES"]
    s4 = index12.slice("S4")
    assert s4.tiers[2].block_ids == ["S4", "DATA", "API"]
    assert s4.spec_tag == "@spec S4@v1"
    s1 = index12.slice("S1")
    assert s1.tiers[2].block_ids == ["S1"]
    for step in ("S7", "S8", "S9", "S10"):
        assert index12.slice(step).tiers[2].block_ids[0] == step


def test_the_vertical_slice_step_includes_its_flow_and_screens(index12: SpecIndex) -> None:
    s5 = index12.slice("S5")
    ids = s5.tiers[2].block_ids
    assert ids[:2] == ["S5", "FLW-001"]
    assert {"SCR-003", "SCR-005", "SCR-006", "SCR-007", "SCR-008", "SCR-009"} <= set(ids)
    assert "API-checkout" in ids and "ENT-Order" in ids


def test_s6_describes_the_groups_without_loading_every_screen(index12: SpecIndex) -> None:
    s6 = index12.slice("S6")
    assert s6.tiers[2].block_ids == ["S6"]
    assert "owns:" in s6.text and "group:" in s6.text


def test_a_flow_slice(index12: SpecIndex) -> None:
    sliced = index12.slice("FLW-003")
    assert sliced.tiers[2].block_ids[0] == "FLW-003"
    assert {"SCR-010", "SCR-011"} <= set(sliced.tiers[2].block_ids)


def test_unknown_targets(index12: SpecIndex) -> None:
    for target in ("SCR-999", "S11", "ENT-Order", "nope"):
        with pytest.raises(UnknownIdError):
            index12.slice(target)


def test_slices_are_deterministic(index12: SpecIndex) -> None:
    assert index12.slice("SCR-006").text == index12.slice("SCR-006").text


def test_every_slice_of_a_50_screen_spec_is_well_under_the_budget(index50: SpecIndex) -> None:
    spec = index50.spec
    sizes = {s.id: index50.slice(s.id).token_estimate for s in spec.screens}
    assert len(sizes) == 50
    assert max(sizes.values()) < BUDGET // 2, "a screen task must stay well under 10k tokens"
    steps = {s.id: index50.slice(s.id).token_estimate for s in spec.build.steps}
    assert max(steps.values()) < BUDGET
    # the whole file is far larger than any slice: the engine never loads it whole
    assert index50.index().total_tokens > 3 * max(sizes.values())


def test_slice_size_does_not_grow_with_the_number_of_screens(
    index12: SpecIndex, index50: SpecIndex
) -> None:
    small = index12.slice("SCR-006").token_estimate
    large = index50.slice("SCR-006").token_estimate
    assert large < small * 2  # nav grows a little, the task's own context does not


def test_spec_tag_helpers() -> None:
    assert format_spec_tag("SCR-014", 7) == "@spec SCR-014@v7"
    assert parse_spec_tag("// @spec SCR-014@v7\nexport const x = 1") == ("SCR-014", 7)
    assert parse_spec_tag("no tag here") is None
    assert parse_spec_tag(format_spec_tag("S5", 12)) == ("S5", 12)
