"""Small pure helpers: design tokens, text hygiene, pack reshaping, call context, incremental bits."""

from __future__ import annotations

import pytest
from helpers import make_brief, make_note, make_request

from mde_content.packs import load_pack, select_features
from mde_content.themes import contrast_ratio
from mde_contracts.calls import KeyValue
from mde_contracts.spec import ResearchAddition, ScreenBlock
from mde_spec_builder import context as ctx
from mde_spec_builder import design
from mde_spec_builder import sanitize as sx
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.incremental import (
    affected_screens,
    changed_integration_ids,
    created_by,
    toggles,
)
from mde_spec_builder.pack_expand import (
    apply_auth_method,
    prune_pack,
    scope_guard,
    surfaces_of,
)
from mde_spec_builder.rules import load_rules
from mde_spec_builder.sanitize import Reject

# --------------------------------------------------------------------------- design


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#abc", "#AABBCC"),
        ("aabbcc", "#AABBCC"),
        (" #A1b2C3 ", "#A1B2C3"),
        ("#12", None),
        ("blue", None),
        ("#GGGGGG", None),
    ],
)
def test_normalize_hex(raw: str, expected: str | None) -> None:
    assert design.normalize_hex(raw) == expected


@pytest.mark.parametrize("accent", ["#FFE600", "#000000", "#E4572E", "#0B6E4F", "#FFFFFF"])
def test_the_accent_is_nudged_only_as_far_as_contrast_needs(accent: str) -> None:
    theme = design.get_theme("clean-bright")
    tokens = design.apply_primary(theme.tokens, accent)
    light, dark = tokens.colors_light, tokens.colors_dark
    for bg in ("background", "surface"):
        assert contrast_ratio(light["primary"], light[bg]) >= 4.5
        assert contrast_ratio(dark["primary"], dark[bg]) >= 4.5
    assert contrast_ratio(light["text_on_primary"], light["primary"]) >= 4.5
    assert contrast_ratio(dark["text_on_primary"], dark["primary"]) >= 4.5
    if accent == "#0B6E4F":
        assert light["primary"] == accent  # already readable on light: unchanged


def test_contrast_failures_name_the_pair_and_the_mode() -> None:
    tokens = design.get_theme("clean-bright").tokens
    pairs = design.pairs_for("clean-bright")
    assert design.contrast_failures(tokens, pairs) == set()
    bad = tokens.model_copy(update={"colors_light": {**tokens.colors_light, "text": "#EEEEEE"}})
    assert ("light", "text", "background") in design.contrast_failures(bad, pairs)
    odd = tokens.model_copy(update={"colors_light": {**tokens.colors_light, "text": "red"}})
    assert (
        design.contrast_failures(odd, pairs) == set()
    )  # not a hex colour: the schema validator's job
    assert ("text", "background") in design.pairs_for("no-such-theme")
    assert "clean-bright" in design.theme_ids()


# --------------------------------------------------------------------------- sanitize


def test_clean_collapses_whitespace_and_enforces_caps() -> None:
    assert sx.clean("  a \n\t b  ", 10, "x") == "a b"
    assert sx.clean("a​b‮c", 10, "x") == "abc"  # hidden characters dropped
    assert sx.clean(None, 10, "x", required=False) == ""
    assert sx.clean("   ", 10, "x", required=False) == ""
    for bad, code in (
        (None, "invalid"),
        ("  ", "invalid"),
        ("x" * 11, "invalid"),
        ("token=hunter2hunter2", "secret"),
        ("a\ud800b", "invalid"),
    ):
        with pytest.raises(Reject) as info:
            sx.clean(bad, 10 if code != "secret" else 100, "x")
        assert info.value.code == code


def test_snake_route_pairs_and_strings() -> None:
    assert sx.snake("share_it", 20, "k") == "share_it"
    for bad in ("Share", "1a", "a b", "a-b"):
        with pytest.raises(Reject):
            sx.snake(bad, 20, "k")
    assert sx.route("/gift-cards/[id]") == "/gift-cards/[id]" and sx.route("/") == "/"
    for bad in ("gift", "/a b", "//a", "/a/", "/é"):
        with pytest.raises(Reject):
            sx.route(bad)
    assert sx.pairs([KeyValue(key="a", value="1")], "p") == {"a": "1"}
    with pytest.raises(Reject):
        sx.pairs([], "p")
    with pytest.raises(Reject):
        sx.pairs([KeyValue(key=f"k{i}", value="v") for i in range(3)], "p", limit=2)
    assert sx.strings(["a", " a ", "b"], 5, "s", max_items=3) == ["a", "b"]
    for bad in (None, [], ["a", "b", "c"]):
        with pytest.raises(Reject):
            sx.strings(bad, 5, "s", max_items=2)


def test_identifier_helpers() -> None:
    assert sx.pascal("gift card!") == "GiftCard" and sx.camel("List gift cards") == "listGiftCards"
    assert sx.snake_case("Support  Agent") == "support_agent" and sx.words("a-b c9") == [
        "a",
        "b",
        "c9",
    ]


# --------------------------------------------------------------------------- pack reshaping


def test_pruning_cascades_to_screens_only_reachable_through_a_removed_one() -> None:
    pack = select_features("commerce", None)
    before = {s.key for s in pack.screens}
    pruned = prune_pack(pack, {"cart"})  # checkout is only reachable through the cart's tab stack
    after = {s.key for s in pruned.screens}
    assert "cart" not in after and after < before
    assert all(set(f.screens) <= after for f in pruned.flows)
    for screen in pruned.screens:
        assert not any(a.go == "@cart" for a in screen.actions)
    assert sum(f.primary for f in pruned.flows) == 1  # a primary journey is kept or promoted


def test_the_scope_guard_is_a_no_op_under_the_cap_and_groups_the_overflow_by_feature() -> None:
    pack = select_features("commerce", [f.id for f in load_pack("commerce").features])
    same, backlog = scope_guard(pack, 100)
    assert same is pack and backlog == []
    trimmed, backlog = scope_guard(pack, 14)
    assert len(trimmed.screens) <= 14
    titles = [b.title for b in backlog]
    assert len(titles) == len(set(titles)) and all(b.screens_hint for b in backlog)
    assert "Loyalty points" in titles or "Favorites" in titles  # extras go first


def test_unknown_sign_in_methods_change_nothing_and_surfaces_are_listed_main_first() -> None:
    pack = select_features("commerce", None)
    assert apply_auth_method(pack, "teleport", load_rules(), "ENT-User") is pack
    assert surfaces_of(pack) == ["customer", "business"]


# --------------------------------------------------------------------------- call context


REQ = make_request(
    "commerce", research=make_note(), brief=make_brief(features=("search", "cart_checkout"))
)
SPEC = build_baseline(REQ)


def test_the_outline_is_compact_and_marks_the_primary_flow() -> None:
    outline = ctx.outline(SPEC)
    assert len(outline.screens) == len(SPEC.screens) and all(
        len(s.purpose) <= 80 for s in outline.screens
    )
    assert [f.name.endswith("(primary)") for f in outline.flows].count(True) == 1
    assert outline.design_direction == SPEC.design.direction
    assert {r.id for r in outline.roles} == {r.id for r in SPEC.roles.roles}


def test_brief_context_takes_features_roles_look_login_and_brand() -> None:
    brief = ctx.brief_context(REQ, SPEC)
    assert [f.id for f in brief.features] == ["search", "cart_checkout"]
    assert brief.archetype == "commerce" and brief.language == "en" and brief.roles == ["Customer"]
    assert brief.look and brief.brand_name is None and brief.brand_url is None
    other = make_request(
        "commerce",
        brand__assets="https://brand.example.com",
        scope__features=["search"],
        compliance__flags=["financial_data"],
        regions=["in"],
    )
    spec = build_baseline(other)
    brief2 = ctx.brief_context(other, spec)
    assert brief2.brand_url == "https://brand.example.com"
    assert [f.label for f in brief2.features] == ["Search and filters"]  # labels from the pack
    assert brief2.compliance_flags == ["financial_data"] and brief2.regions == ["in"]


def test_screen_batches_skip_locked_screens_and_hold_at_most_four() -> None:
    locked = SPEC.screens[3].model_copy(update={"locked": True})
    spec = SPEC.model_copy(
        update={"screens": [locked if s.id == locked.id else s for s in SPEC.screens]}
    )
    batches = ctx.screen_batches(spec)
    flat = [s.id for b in batches for s in b]
    assert locked.id not in flat and len(flat) == len(SPEC.screens) - 1
    assert all(len(b) == 4 for b in batches[:-1]) and 1 <= len(batches[-1]) <= 4


def test_c10_context_is_only_the_batchs_own() -> None:
    batch = [s for s in SPEC.screens if s.route in ("/product/[id]", "/cart")]
    inp = ctx.c10_input(ctx.brief_context(REQ, SPEC), SPEC, batch, [])
    assert {s.id for s in inp.screens} == {s.id for s in batch}
    assert {r.id for r in inp.recipes} == {s.recipe for s in batch}
    assert {"ENT-Product", "ENT-Cart"} <= {e.id for e in inp.entities}
    assert "API-getProduct" in {o.id for o in inp.operations} and len(inp.entities) < 12
    assert not ({n.id for n in inp.neighbors} & {s.id for s in batch})
    assert len(inp.neighbors) <= 6


def test_blocks_for_returns_yaml_of_what_a_finding_points_at() -> None:
    blocks = ctx.blocks_for(
        SPEC, ["SCR-004", "ENT-Product", "API-getProduct", "SCR-999", "SCR-004", "DATA"]
    )
    assert [b.id for b in blocks] == ["SCR-004", "ENT-Product", "API-getProduct", "DATA"]
    assert [b.kind for b in blocks][:3] == ["screen", "entity", "operation"]
    assert all(len(b.text) <= 1500 for b in blocks)
    many = ctx.blocks_for(SPEC, [s.id for s in SPEC.screens])
    assert len(many) == 6


def test_c13_context_limits_findings_and_carries_the_blocks() -> None:
    from mde_contracts.calls import ValidatorFinding

    findings = [
        ValidatorFinding(validator="schema", target_ids=["SCR-004"], message=f"m{i}")
        for i in range(30)
    ]
    inp = ctx.c13_input(ctx.brief_context(REQ, SPEC), SPEC, findings, round_number=2)
    assert len(inp.findings) == 20 and inp.round == 2 and [b.id for b in inp.blocks] == ["SCR-004"]


# --------------------------------------------------------------------------- incremental helpers


def test_created_by_lists_screens_first_then_operations_then_entities() -> None:
    base = build_baseline(REQ)
    addition = next(a for a in base.assumptions.research_additions if a.id == "RA-001")
    ids = created_by(base, addition)
    assert ids and ids[0].startswith("SCR-")
    kinds = [i.split("-")[0] for i in ids]
    assert kinds == sorted(kinds, key=["SCR", "FLW", "API", "ENT", "INT", "ROLE"].index)
    assert created_by(base, ResearchAddition(id="RA-009", title="t", kind="feature")) == []


def test_toggles_compare_the_previous_additions_with_the_exclusions() -> None:
    t = toggles(SPEC, ["RA-001"])
    assert [a.id for a in t.off] == ["RA-001"] and t.on == []
    off = SPEC.model_copy(
        update={
            "assumptions": SPEC.assumptions.model_copy(
                update={
                    "research_additions": [
                        a.model_copy(update={"included": a.id != "RA-002"})
                        for a in SPEC.assumptions.research_additions
                    ]
                }
            )
        }
    )
    t2 = toggles(off, [])
    assert [a.id for a in t2.on] == ["RA-002"] and t2.off == []


def test_affected_screens_and_changed_integrations() -> None:
    found = affected_screens(SPEC, ["INT-payments"])
    routes = {s.route for s in SPEC.screens if s.id in found}
    assert "/checkout" in routes and "/home" not in routes
    assert affected_screens(SPEC, []) == set()
    swapped = SPEC.model_copy(
        update={
            "integrations": SPEC.integrations.model_copy(
                update={
                    "items": [
                        i.model_copy(update={"mode": "adapter", "provider": "x"})
                        if i.id == "INT-payments"
                        else i
                        for i in SPEC.integrations.items
                    ]
                }
            )
        }
    )
    assert changed_integration_ids(SPEC, swapped) == ["INT-payments"]
    _ = ScreenBlock
