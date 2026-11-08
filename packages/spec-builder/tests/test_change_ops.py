"""Screen notes and screen-level change requests reach the spec (eval BUG-3).

Unit tests of ``change_ops`` over the commerce baseline, then whole builds: a patch with a note
re-runs only C10 for that screen, a removal outlives later builds, a full rebuild carries the
person's earlier requests along, and what cannot be done is a plain line in the Plan view.
"""

from __future__ import annotations

from typing import Any

import pytest
from helpers import DirectMockGateway, make_context, make_note, make_request, no_c11

from mde_content.stack import load_stack_profile
from mde_contracts.calls import C10Input, ChangeOp, Proposal
from mde_contracts.engines import BuildRequest, BuildResult
from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec
from mde_prompts.calls.c10_screen_briefs.detail import USER_DETAIL_MARK
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.change_ops import apply_requests, match_screen
from mde_spec_builder.engine import SpecBuilderImpl
from mde_spec_builder.incremental import plan_build, removed_routes, structure_matches
from mde_spec_builder.proposals import ApplyResult, validate_and_apply
from mde_spec_builder.rules import load_rules
from mde_spec_builder.validators import errors, run_validators
from mde_spec_loader import parse_spec

REMOVED = load_rules().change_ops.removed_reason
SECRET = "password=hunter2hunter2"  # secret-scan: allow


def validate(spec: AppSpec, items: Any) -> ApplyResult:
    return validate_and_apply(spec, items, limits=Limits())


def baseline(archetype: str = "commerce", **kw: Any) -> AppSpec:
    return build_baseline(make_request(archetype, **kw))


def valid(spec: AppSpec) -> list[Any]:
    """The validator errors of ``spec`` (none expected)."""
    return errors(run_validators(spec, stack_profile=load_stack_profile(), limits=Limits()))


def route_of(spec: AppSpec, route: str) -> Any:
    return next(s for s in spec.screens if s.route == route)


def op(kind: str, **kw: Any) -> ChangeOp:
    return ChangeOp.model_validate({"kind": kind, **kw})


def apply(spec: AppSpec, *ops: ChangeOp, notes: dict[str, str] | None = None) -> Any:
    return apply_requests(spec, list(ops), notes or {}, validate)


# --------------------------------------------------------------------------- finding a screen


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("SCR-009", "/cart"),
        ("scr-009", "/cart"),
        ("/cart", "/cart"),
        ("on the cart screen I want a gift message field", "/cart"),
        ("the cart", "/cart"),
        ("checkout", "/checkout"),
        ("the orders page", "/orders"),  # /orders, not /orders/[id] or the tracking screen
        ("order confirmation", "/order-confirmation"),
        ("change the sign in screen", "/login"),
        ("favorites", "/favorites"),
        ("the product screen", "/product/[id]"),
    ],
)
def test_a_screen_is_found_by_id_route_or_words(text: str, route: str) -> None:
    found = match_screen(baseline(research=make_note()), text)
    assert found is not None and found.route == route


@pytest.mark.parametrize(
    "text", [None, "", "   ", "make the buttons rounder", "SCR-777", "/nowhere"]
)
def test_no_screen_is_named_when_nothing_matches(text: str | None) -> None:
    assert match_screen(baseline(), text) is None


# --------------------------------------------------------------------------- notes


def test_a_patch_note_is_stored_on_the_screen_and_marks_it_custom() -> None:
    spec = baseline()
    out = apply(
        spec, op("patch_screen", instruction="on the cart screen I want a gift message field")
    )
    cart = route_of(out.spec, "/cart")
    assert cart.notes == "on the cart screen I want a gift message field"
    assert cart.source == "custom"
    assert out.briefs == {cart.id} and out.changed and out.lines == []
    assert [s for s in out.spec.screens if s.id != cart.id] == [
        s for s in spec.screens if s.id != cart.id
    ]  # nothing else moved
    assert valid(out.spec) == []


def test_a_note_names_its_screen_by_id_or_route_before_the_words_are_read() -> None:
    spec = baseline()
    out = apply(
        spec,
        op("patch_screen", target_id="/checkout", instruction="show the cart total in bold"),
        op("set_copy", target_id="SCR-009", instruction="the title should say Your basket"),
        op("other", instruction="on the help screen add a phone number"),
    )
    assert route_of(out.spec, "/checkout").notes == "show the cart total in bold"
    assert route_of(out.spec, "/cart").notes == "the title should say Your basket"
    assert route_of(out.spec, "/help").notes == "on the help screen add a phone number"
    assert out.lines == []


def test_notes_add_up_and_the_same_note_twice_is_one_note() -> None:
    spec = baseline()
    once = apply(spec, op("patch_screen", target_id="/cart", instruction="gift message field"))
    again = apply(
        once.spec,
        op("patch_screen", target_id="/cart", instruction="gift message field"),
        op("patch_screen", target_id="/cart", instruction="a coupon box"),
    )
    assert route_of(again.spec, "/cart").notes == "gift message field; a coupon box"
    unchanged = apply(
        once.spec, op("patch_screen", target_id="/cart", instruction="gift message field")
    )
    assert unchanged.spec == once.spec and not unchanged.changed and not unchanged.briefs


def test_long_notes_are_cut_and_the_oldest_go_first() -> None:
    spec = baseline()
    out = apply(spec, op("patch_screen", target_id="/cart", instruction="word " * 200))
    note = route_of(out.spec, "/cart").notes or ""
    assert 0 < len(note) <= 301 and note.endswith("…")
    many = apply(
        spec,
        *[
            op("patch_screen", target_id="/cart", instruction=f"detail number {i} " + "x" * 200)
            for i in range(5)
        ],
    )
    kept = route_of(many.spec, "/cart").notes or ""
    assert len(kept) <= 600 and "detail number 4" in kept and "detail number 0" not in kept


def test_a_secret_in_a_note_is_left_out_with_a_plain_line() -> None:
    spec = baseline()
    out = apply(spec, op("patch_screen", target_id="/cart", instruction=f"use {SECRET} here"))
    assert out.spec == spec and not out.changed
    assert out.lines == [
        "I left out a detail for Your cart because it looks like a password or a key."
    ]


def test_hidden_characters_never_reach_a_note() -> None:
    out = apply(baseline(), op("patch_screen", target_id="/cart", instruction="gift​ message"))
    assert route_of(out.spec, "/cart").notes == "gift message"


def test_a_request_about_no_known_screen_is_a_plain_line_not_an_error() -> None:
    spec = baseline()
    out = apply(spec, op("patch_screen", instruction="make the buttons rounder"))
    assert out.spec == spec and not out.changed
    assert out.lines == [
        'I could not tell which screen "make the buttons rounder" is about, so it is not in the plan.'
    ]
    none_at_all = apply(spec, op("patch_screen"), op("other", instruction=None))
    assert none_at_all.lines == []  # nothing was asked
    nothing_said = apply(spec, op("patch_screen", target_id="/cart"))
    assert nothing_said.lines == [
        "You did not say what to change on Your cart, so it is as before."
    ]


def test_requests_that_the_inputs_already_carry_are_not_read() -> None:
    spec = baseline()
    out = apply(
        spec,
        op("set_slot", slot_id="design.direction", value_text="bold-dark"),
        op("clear_slot", slot_id="design.accent"),
        op("toggle_feature", target_id="reviews", value_bool=True),
        op("toggle_research_item", target_id="RA-001", value_bool=False),
        op("other", slot_id="a11y", value_bool=True, instruction="toggle_assumption"),
        op("other"),
    )
    assert out.spec == spec and not out.changed and out.lines == [] and out.applied == []


def test_screen_notes_from_discover_find_their_screen_by_route_id_or_words() -> None:
    spec = baseline()
    out = apply(
        spec,
        notes={
            "/cart": "a gift message field",
            "SCR-010": "show delivery dates",
            "the help screen": "add a phone number",
            "on the orders screen I want a reorder button": "on the orders screen I want a reorder button",
        },
    )
    assert route_of(out.spec, "/cart").notes == "a gift message field"
    assert route_of(out.spec, "/checkout").notes == "show delivery dates"
    assert route_of(out.spec, "/help").notes == "add a phone number"
    assert route_of(out.spec, "/orders").notes == "on the orders screen I want a reorder button"
    assert all(route_of(out.spec, r).source == "custom" for r in ("/cart", "/checkout", "/help"))
    assert out.lines == [] and len(out.briefs) == 4


def test_a_screen_note_for_an_unknown_screen_is_ignored_with_a_note() -> None:
    spec = baseline()
    out = apply(spec, notes={"the spaceship screen": "needs a laser"})
    assert out.spec == spec
    assert out.lines == [
        'I could not tell which screen "the spaceship screen" is about, so it is not in the plan.'
    ]
    # the key does not name a screen but the text does
    found = apply(spec, notes={"Gift": "on the cart screen I want a gift message field"})
    assert route_of(found.spec, "/cart").notes == "on the cart screen I want a gift message field"


# --------------------------------------------------------------------------- add and remove


def test_add_screen_makes_a_valid_custom_screen_from_a_few_words() -> None:
    spec = baseline()
    out = apply(spec, op("add_screen", instruction="photo gallery"))
    added = route_of(out.spec, "/photo-gallery")
    assert (added.title, added.recipe, added.source) == ("Photo gallery", "media-gallery", "custom")
    assert added.purpose == "Photo gallery: added at your request"
    assert added.id == "SCR-025" and added.id in out.briefs
    assert out.applied[0].proposal.kind == "add_screen" and out.dropped == []
    assert any(n.screen == added.id for n in _nav(out.spec))
    assert valid(out.spec) == []


def _nav(spec: AppSpec) -> list[Any]:
    from mde_spec_loader.fragments import walk_nav

    return list(walk_nav(spec.navigation.root))


@pytest.mark.parametrize(
    ("words", "recipe"),
    [
        ("a wishlist", "list"),
        ("contact form", "form"),
        ("store map", "map-view"),
        ("something else entirely", "detail"),  # the default recipe
        ("chat with support page", "chat"),
    ],
)
def test_the_recipe_of_an_added_screen_follows_its_words(words: str, recipe: str) -> None:
    out = apply(baseline(), op("add_screen", instruction=words))
    assert out.spec.screens[-1].recipe == recipe


def test_add_screen_with_a_name_in_another_script_still_gets_a_route() -> None:
    out = apply(baseline(), op("add_screen", instruction="पसंदीदा"))
    assert out.spec.screens[-1].route == "/screen-25" and out.spec.screens[-1].title == "पसंदीदा"


def test_adding_a_screen_that_is_already_there_is_dropped_with_a_plain_line() -> None:
    spec = baseline()
    out = apply(spec, op("add_screen", instruction="cart"), op("add_screen", instruction=""))
    assert out.spec == spec and not out.changed
    assert out.lines == [
        'I could not add the screen "Cart": a screen already uses the route /cart.',
        "I could not add a screen without a name.",
    ]
    assert [d.code for d in out.dropped] == ["duplicate"]


def test_adding_past_the_screen_cap_is_dropped_with_a_plain_line() -> None:
    spec = baseline()
    full = len(spec.screens)
    out = apply_requests(
        spec,
        [op("add_screen", instruction="wishlist")],
        {},
        lambda s, items: validate_and_apply(s, items, limits=Limits(max_screens=full)),
    )
    assert out.spec == spec
    assert out.lines == [
        f'I could not add the screen "Wishlist": the app already has {full} screens.'
    ]


def test_remove_screen_takes_it_out_and_remembers_it_in_the_backlog() -> None:
    spec = baseline(research=make_note())
    out = apply(spec, op("remove_screen", target_id="SCR-022"))  # /favorites
    assert "/favorites" not in {s.route for s in out.spec.screens}
    assert len(out.spec.screens) == len(spec.screens) - 1
    record = [b for b in out.spec.backlog if b.reason == REMOVED]
    assert [(b.title, b.screens_hint) for b in record] == [("Favorites", ["/favorites"])]
    assert removed_routes(out.spec) == {"/favorites"}
    assert valid(out.spec) == []
    # the same request again finds nothing to remove and says so
    again = apply(out.spec, op("remove_screen", target_id="/favorites"))
    assert again.spec == out.spec and again.lines[0].startswith("I could not tell which screen")


def test_remove_screen_by_words_and_the_removed_screen_is_not_left_for_phase_two() -> None:
    from mde_spec_builder.summary import prd_lines

    spec = baseline()
    out = apply(spec, op("remove_screen", instruction="remove the notifications screen"))
    assert "/notifications" not in {s.route for s in out.spec.screens}
    assert not any("Left for phase two" in line for line in prd_lines(out.spec))
    assert not any("Notifications" in line for line in prd_lines(out.spec) if "phase two" in line)


def test_the_main_journey_and_sign_in_cannot_be_removed() -> None:
    spec = baseline()
    out = apply(
        spec, op("remove_screen", target_id="/checkout"), op("remove_screen", target_id="/login")
    )
    assert out.spec == spec
    assert len(out.lines) == 2 and all(line.startswith("I could not remove ") for line in out.lines)
    assert "main journey or sign-in" in out.lines[0]
    assert [d.code for d in out.dropped] == ["protected", "protected"]


# --------------------------------------------------------------------------- carried over a rebuild


def test_carry_over_replays_removals_notes_and_added_screens_on_a_fresh_baseline() -> None:
    first = baseline(research=make_note())
    edited = apply(
        first,
        op("remove_screen", target_id="/favorites"),
        op("patch_screen", target_id="/cart", instruction="a gift message field"),
        op("add_screen", instruction="store map"),
    ).spec
    fresh = baseline(research=make_note())  # the next build starts from the baseline again
    out = apply_requests(fresh, [], {}, validate, previous=edited)
    assert "/favorites" not in {s.route for s in out.spec.screens}
    assert route_of(out.spec, "/cart").notes == "a gift message field"
    assert route_of(out.spec, "/cart").source == "custom"
    again = route_of(out.spec, "/store-map")
    assert (again.recipe, again.source, again.title) == ("map-view", "custom", "Store map")
    assert removed_routes(out.spec) == {"/favorites"}
    assert out.lines == []


# --------------------------------------------------------------------------- the plan decision


def with_ops(prev: BuildResult, sections: list[str], *ops: ChangeOp, **kw: Any) -> BuildRequest:
    req = make_request("commerce", spec_version=2, **kw)
    return req.model_copy(
        update={
            "previous_spec": prev.spec,
            "affected_sections": sections,
            "change_ops": list(ops),
        }
    )


async def build(req: BuildRequest, gateway: DirectMockGateway | None = None) -> BuildResult:
    engine = SpecBuilderImpl(gateway or DirectMockGateway(), summary_loader=no_c11)
    return await engine.build(req, make_context())


async def first(**kw: Any) -> BuildResult:
    res = await build(make_request("commerce", **kw))
    assert res.status == "ok" and res.spec is not None
    return res


async def test_screen_level_requests_keep_a_build_a_patch() -> None:
    prev = await first()
    ask = op("patch_screen", target_id="/cart", instruction="gift message")
    assert plan_build(with_ops(prev, ["screens"], ask)).mode == "patch"
    assert (
        plan_build(
            with_ops(prev, ["screens", "navigation"], op("add_screen", instruction="map"))
        ).mode
        == "patch"
    )
    assert plan_build(with_ops(prev, ["screens"])).mode == "full"  # no request explains it
    assert plan_build(with_ops(prev, ["screens"], op("set_slot", slot_id="a11y"))).mode == "full"
    assert plan_build(with_ops(prev, ["all"], ask)).mode == "full"
    assert plan_build(with_ops(prev, ["screens", "data"], ask)).mode == "full"
    assert plan_build(with_ops(prev, ["design", "screens"], ask)).sections == {"design"}


# --------------------------------------------------------------------------- whole builds


async def test_a_note_after_hand_off_rewrites_only_that_screens_brief() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    ask = op("patch_screen", instruction="on the cart screen I want a gift message field")
    res = await build(with_ops(prev, ["screens"], ask), gateway)
    assert res.status == "ok" and res.spec is not None and prev.spec is not None
    assert set(gateway.ids) == {"C10.screen_briefs", "C12.critic"}  # no writers, one batch of one
    c10 = next(i for c, i, _ in gateway.calls if c == "C10.screen_briefs")
    assert isinstance(c10, C10Input) and [s.route for s in c10.screens] == ["/cart"]
    assert (
        f"{USER_DETAIL_MARK} on the cart screen I want a gift message field"
        in c10.screens[0].purpose
    )
    cart = route_of(res.spec, "/cart")
    assert (
        cart.notes == "on the cart screen I want a gift message field" and cart.source == "custom"
    )
    assert "gift message" in (res.spec_text or "")
    assert any("gift message field" in c for c in cart.checks)  # the model-free handler used it
    assert res.spec.revision.changed_ids == [cart.id]
    assert parse_spec(res.spec_text or "") == res.spec
    others = [s for s in res.spec.screens if s.id != cart.id]
    assert others == [s for s in prev.spec.screens if s.id != cart.id]
    assert (
        res.plan is not None and res.plan.diff_since_gate1 is None
    )  # a first draft had no baseline


async def test_a_removed_screen_stays_out_through_later_patches_and_rebuilds() -> None:
    note = make_note()
    prev = await first(research=note)
    removed = await build(
        with_ops(
            prev,
            ["screens", "navigation"],
            op("remove_screen", target_id="/favorites"),
            research=note,
        ),
        DirectMockGateway(),
    )
    assert removed.spec is not None and "/favorites" not in {s.route for s in removed.spec.screens}
    assert [b.title for b in removed.spec.backlog if b.reason == REMOVED] == ["Favorites"]
    # a design change afterwards is still a patch: the baseline still wants /favorites, the person did not
    gateway = DirectMockGateway()
    nxt = removed.model_copy()
    req = make_request("commerce", spec_version=3, research=note, design__accent="#0b6e4f")
    req = req.model_copy(update={"previous_spec": nxt.spec, "affected_sections": ["design"]})
    assert structure_matches(removed.spec, build_baseline(req))
    patched = await build(req, gateway)
    assert gateway.ids == ["C9c.design_tokens"] and patched.spec is not None
    assert "/favorites" not in {s.route for s in patched.spec.screens}
    # and a full rebuild (another feature switched on) brings the removal along
    full = make_request(
        "commerce",
        spec_version=3,
        research=note,
        features=["catalog_browse", "cart_checkout", "search", "order_tracking", "reviews"],
    ).model_copy(update={"previous_spec": removed.spec, "affected_sections": ["all"]})
    rebuilt = await build(full, DirectMockGateway())
    assert rebuilt.spec is not None and "/favorites" not in {s.route for s in rebuilt.spec.screens}
    assert [b.title for b in rebuilt.spec.backlog if b.reason == REMOVED] == ["Favorites"]


async def test_a_noted_screen_does_not_force_a_full_rebuild_later() -> None:
    prev = await first()
    noted = await build(
        with_ops(
            prev, ["screens"], op("patch_screen", target_id="/cart", instruction="gift message")
        ),
        DirectMockGateway(),
    )
    assert noted.spec is not None and route_of(noted.spec, "/cart").source == "custom"
    gateway = DirectMockGateway()
    req = make_request("commerce", spec_version=3, design__accent="#0b6e4f").model_copy(
        update={"previous_spec": noted.spec, "affected_sections": ["design"]}
    )
    res = await build(req, gateway)
    assert gateway.ids == [
        "C9c.design_tokens"
    ]  # source custom did not look like a changed structure
    assert res.spec is not None and route_of(res.spec, "/cart").notes == "gift message"


async def test_an_added_screen_gets_a_brief_and_survives_a_full_rebuild() -> None:
    prev = await first()
    gateway = DirectMockGateway()
    added = await build(
        with_ops(prev, ["screens", "navigation"], op("add_screen", instruction="photo gallery")),
        gateway,
    )
    assert added.spec is not None and prev.spec is not None
    screen = route_of(added.spec, "/photo-gallery")
    assert screen.source == "custom" and screen.id == "SCR-025"
    c10 = [i for c, i, _ in gateway.calls if c == "C10.screen_briefs"]
    assert (
        len(c10) == 1
        and isinstance(c10[0], C10Input)
        and [s.id for s in c10[0].screens] == [screen.id]
    )
    assert added.spec.revision.changed_ids  # the file says what moved
    assert errors(added.findings) == []
    # the person changes a feature later: the full rebuild keeps their screen
    full = make_request("commerce", spec_version=3, features=["catalog_browse", "cart_checkout"])
    full = full.model_copy(update={"previous_spec": added.spec, "affected_sections": ["all"]})
    rebuilt = await build(full, DirectMockGateway())
    assert rebuilt.spec is not None
    assert route_of(rebuilt.spec, "/photo-gallery").recipe == "media-gallery"
    assert len(rebuilt.spec.screens) < len(added.spec.screens)


async def test_the_first_build_reads_screen_notes_and_c10_sees_them() -> None:
    gateway = DirectMockGateway()
    req = make_request("commerce").model_copy(
        update={"screen_notes": {"cart": "on the cart screen I want a gift message field"}}
    )
    res = await build(req, gateway)
    assert res.status == "ok" and res.spec is not None
    cart = route_of(res.spec, "/cart")
    assert (
        cart.notes == "on the cart screen I want a gift message field" and cart.source == "custom"
    )
    seen = [
        s.purpose
        for c, i, _ in gateway.calls
        if c == "C10.screen_briefs"
        for s in i.screens  # type: ignore[attr-defined]
        if s.route == "/cart"
    ]
    assert len(seen) == 1 and USER_DETAIL_MARK in seen[0]
    assert "gift message" in (res.spec_text or "")


async def test_notes_given_again_on_every_build_do_not_pile_up() -> None:
    notes = {"cart": "a gift message field"}
    req = make_request("commerce").model_copy(update={"screen_notes": notes})
    one = await build(req)
    assert one.spec is not None
    again = make_request("commerce", spec_version=2).model_copy(
        update={
            "screen_notes": notes,
            "previous_spec": one.spec,
            "affected_sections": ["design"],
        }
    )
    two = await build(again, DirectMockGateway())
    assert two.spec is not None and route_of(two.spec, "/cart").notes == "a gift message field"
    full = again.model_copy(update={"affected_sections": ["all"]})
    three = await build(full, DirectMockGateway())
    assert three.spec is not None and route_of(three.spec, "/cart").notes == "a gift message field"


async def test_what_could_not_be_done_heads_the_plan_views_list_of_changes() -> None:
    prev = await first()
    asks = [
        op("patch_screen", instruction="make the buttons rounder"),
        op("remove_screen", target_id="/checkout"),
        op("patch_screen", target_id="/cart", instruction="a gift message field"),
    ]
    req = with_ops(prev, ["screens"], *asks).model_copy(
        update={"gate1_spec": prev.spec, "screen_notes": {"the spaceship screen": "a laser"}}
    )
    res = await build(req)
    assert res.plan is not None and res.plan.diff_since_gate1 is not None
    lines = res.plan.diff_since_gate1.lines
    assert lines[:3] == [
        'I could not tell which screen "the spaceship screen" is about, so it is not in the plan.',
        'I could not tell which screen "make the buttons rounder" is about, so it is not in the plan.',
        "I could not remove Review and place order: SCR-010 is part of the main journey or sign-in.",
    ]
    assert any(line.startswith("Changed screen: Your cart") for line in lines)
    assert res.proposals_dropped >= 1  # the removal at least; notes are not proposals


async def test_notes_without_a_baseline_still_reach_the_plan_view() -> None:
    req = make_request("commerce").model_copy(
        update={"screen_notes": {"the spaceship screen": "x"}}
    )
    res = await build(req)
    assert res.plan is not None and res.plan.diff_since_gate1 is not None
    assert res.plan.diff_since_gate1.lines == [
        'I could not tell which screen "the spaceship screen" is about, so it is not in the plan.'
    ]
    assert res.plan.diff_since_gate1.added_ids == []
    clean = await build(make_request("commerce"))
    assert clean.plan is not None and clean.plan.diff_since_gate1 is None


async def test_requests_never_fail_a_build_and_are_counted() -> None:
    prev = await first()
    asks = [op("add_screen", instruction="photo gallery"), op("remove_screen", target_id="/login")]
    res = await build(with_ops(prev, ["screens", "navigation"], *asks))
    assert res.status == "ok"
    assert res.proposals_dropped >= 1 and res.proposals_applied >= 1


def test_the_proposal_kinds_used_are_the_checked_ones() -> None:
    # the translation only ever produces proposals the validator knows how to refuse
    out = apply(
        baseline(), op("add_screen", instruction="wishlist"), op("remove_screen", target_id="/help")
    )
    assert [a.proposal.kind for a in out.applied] == ["add_screen", "remove_item"]
    assert all(isinstance(a.proposal, Proposal) for a in out.applied)


def test_c10_sees_a_screens_note_in_its_purpose_line_and_nothing_else_changes() -> None:
    from mde_spec_builder.context import screen_draft

    spec = baseline()
    plain = route_of(spec, "/cart")
    noted = route_of(
        apply(spec, op("patch_screen", target_id="/cart", instruction="a gift message field")).spec,
        "/cart",
    )
    assert screen_draft(plain).purpose == plain.purpose[:160]
    assert (
        screen_draft(noted).purpose
        == f"{plain.purpose[:160]} {USER_DETAIL_MARK} a gift message field"
    )
    assert screen_draft(noted).model_copy(update={"purpose": screen_draft(plain).purpose}) == (
        screen_draft(plain)
    )


def test_a_secret_is_never_echoed_in_a_plain_line() -> None:
    spec = baseline()
    out = apply(
        spec,
        op("patch_screen", instruction=f"on the spaceship screen use {SECRET}"),
        op("add_screen", instruction=SECRET),
        notes={SECRET: "x"},
    )
    assert out.spec == spec
    assert len(out.lines) == 3 and not any("hunter2" in line for line in out.lines)
    assert all(line.startswith("I could not tell which screen") for line in out.lines[:2])
    assert out.lines[0].startswith(
        'I could not tell which screen "something you wrote"'
    )  # the notes
    assert out.lines[2].startswith('I could not add the screen "something you wrote"')
    assert [d.code for d in out.dropped] == ["secret"]


async def test_a_copy_change_stays_a_patch_when_research_switched_a_feature_on() -> None:
    note = make_note()
    prev = await first(research=note)  # RA-001 put /favorites in the app (BUG-5)
    assert prev.spec is not None and route_of(prev.spec, "/favorites").source == "research"
    gateway = DirectMockGateway()
    ask = op("set_copy", target_id="/cart", instruction="the title should say Your basket")
    res = await build(with_ops(prev, ["screens"], ask, research=note), gateway)
    assert res.status == "ok" and res.spec is not None
    assert set(gateway.ids) == {"C10.screen_briefs", "C12.critic"}  # no writer re-ran
    c10 = [i for c, i, _ in gateway.calls if c == "C10.screen_briefs"]
    assert (
        len(c10) == 1
        and isinstance(c10[0], C10Input)
        and [s.route for s in c10[0].screens] == ["/cart"]
    )
    cart = route_of(res.spec, "/cart")
    assert [s for s in res.spec.screens if s.id != cart.id] == [
        s for s in prev.spec.screens if s.id != cart.id
    ]  # the research screens and every other screen are as they were
    assert res.spec.data == prev.spec.data and res.spec.design == prev.spec.design
    assert res.spec.api == prev.spec.api and res.spec.navigation == prev.spec.navigation
