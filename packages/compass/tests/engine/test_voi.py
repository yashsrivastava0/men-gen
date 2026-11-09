"""Value-of-information planner: actions, budgets, dependency order, never re-asking, property tests."""

from __future__ import annotations

import sys
import types
from collections.abc import Mapping

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_compass.catalog.loader import load_catalog, parse_ask_if
from mde_compass.config import QuestionPolicy
from mde_compass.domain.situation import SituationProfile
from mde_compass.domain.slots import SlotRecord


def _install_expr_stub() -> None:
    """Agent E1a owns ``engine/expr.py``; until it is merged this local stub stands in (tests only)."""
    try:
        import mde_compass.engine.expr  # noqa: F401
    except ImportError:

        def _filled(r: SlotRecord | None) -> bool:
            return r is not None and r.status != "empty" and r.value not in (None, "", [], {})

        def _ev(n: tuple, s: Mapping[str, SlotRecord | None]) -> bool:  # type: ignore[type-arg]
            tag = n[0]
            if tag == "and":
                return _ev(n[1], s) and _ev(n[2], s)
            if tag == "or":
                return _ev(n[1], s) or _ev(n[2], s)
            if tag == "not":
                return not _ev(n[1], s)
            if tag == "filled":
                return _filled(s.get(n[1]))
            if tag == "empty":
                return not _filled(s.get(n[1]))
            rec = s.get(n[1])
            val = rec.value if _filled(rec) else None
            if n[2] == "==":
                return bool(val == n[3])
            if n[2] == "!=":
                return bool(val != n[3])
            return val in n[3]

        def holds(expr: str | None, slots: Mapping[str, SlotRecord | None]) -> bool:
            return True if not expr else _ev(parse_ask_if(expr), slots)

        module = types.ModuleType("mde_compass.engine.expr")
        module.holds = holds  # type: ignore[attr-defined]
        sys.modules["mde_compass.engine.expr"] = module


_install_expr_stub()

from mde_compass.engine.voi import (  # noqa: E402
    JUST_BUILD_SLOTS,
    QueueItem,
    default_inferability,
    estimate_total,
    next_item,
    plan_queue,
    slot_score,
)

CATALOG = load_catalog()
CFG = QuestionPolicy()


def rec(slot_id: str, status: str, value: object = "x", confidence: float = 0.9) -> SlotRecord:
    return SlotRecord(slot_id=slot_id, value=value, status=status, confidence=confidence)  # type: ignore[arg-type]


def asks(queue: list[QueueItem]) -> list[QueueItem]:
    return [i for i in queue if i.action != "assume"]


def plan(slots: dict[str, SlotRecord | None] | None = None, **kw: object) -> list[QueueItem]:
    kw.setdefault("profile", None)
    kw.setdefault("depth", "standard")
    kw.setdefault("asked_frames", [])
    return plan_queue(slots or {}, CATALOG, **kw)  # type: ignore[arg-type]


def test_empty_state_starts_with_the_idea_in_dependency_order() -> None:
    queue = plan()
    assert next_item(queue) is not None
    assert next_item(queue).frame_id == "idea_in_your_words"  # type: ignore[union-attr]
    orders = [CATALOG.frames[i.frame_id].order for i in queue]
    assert orders == sorted(orders)


def test_standard_budget_is_about_seven_and_never_over_the_hard_cap() -> None:
    total = len(asks(plan()))
    assert 6 <= total <= CFG.hard_cap
    assert estimate_total(plan(), []) == total


def test_express_asks_at_most_five() -> None:
    assert len(asks(plan(depth="express"))) <= CFG.express_max


def test_just_build_it_asks_only_what_for_whom_where_which() -> None:
    queue = plan(profile=SituationProfile(urgency="just_build"), depth="express")
    asked_slots = {s for i in asks(queue) for s in i.slots}
    assert asked_slots <= JUST_BUILD_SLOTS
    assert {"idea.summary", "context.geo", "audience.segments", "locales"} <= asked_slots


def test_confirmed_and_volunteered_slots_are_never_reasked() -> None:
    slots: dict[str, SlotRecord | None] = {
        "idea.summary": rec("idea.summary", "volunteered"),
        "idea.category": rec("idea.category", "confirmed", "commerce"),
        "context.geo": rec("context.geo", "confirmed"),
    }
    queue = plan(slots)
    planned = {s for i in queue for s in i.slots}
    assert not planned & {"idea.summary", "idea.category", "context.geo"}


def test_a_frame_already_asked_is_not_asked_again() -> None:
    queue = plan(asked_frames=["idea_in_your_words"])
    assert all(i.frame_id != "idea_in_your_words" or i.action == "assume" for i in queue)


def test_risky_slots_are_must_confirm_and_asked() -> None:
    slots: dict[str, SlotRecord | None] = {
        s: rec(s, "confirmed")
        for s in ("idea.summary", "idea.category", "audience.segments", "apps.list", "roles.list")
    }
    queue = plan(slots)
    login = [i for i in queue if i.frame_id == "login_and_accounts" and i.action != "assume"]
    assert login
    assert login[0].action == "ask"
    assert login[0].must_confirm


def test_high_impact_and_inferable_is_proposed_not_asked() -> None:
    slots: dict[str, SlotRecord | None] = {"idea.summary": rec("idea.summary", "volunteered")}
    slots["idea.category"] = rec("idea.category", "assumed", "commerce", confidence=0.9)
    slots["idea.subcategory"] = rec("idea.subcategory", "assumed", "bakery", confidence=0.9)
    queue = plan(slots)
    kind = [i for i in queue if i.frame_id == "confirm_kind_of_app" and "idea.category" in i.slots]
    assert kind[0].action == "propose"


def test_inferability_override_changes_ask_into_propose() -> None:
    base = plan()
    changed = plan(inferability={"idea.summary": 0.95})
    first = lambda q: next(i for i in q if i.frame_id == "idea_in_your_words")  # noqa: E731
    assert first(base).action == "ask"
    assert first(changed).action == "propose"


def test_low_impact_slots_are_assumed_and_listed() -> None:
    queue = plan()
    limits = [i for i in queue if i.frame_id == "limits"]
    assert limits
    assert all(i.action == "assume" for i in limits)


def test_slots_sharing_a_frame_are_batched_into_one_item() -> None:
    queue = plan()
    frames_with_cards = [i.frame_id for i in asks(queue)]
    assert len(frames_with_cards) == len(set(frames_with_cards))
    form = next(i for i in queue if i.frame_id == "who_and_where" and i.action != "assume")
    assert {"audience.segments", "context.geo", "locales"} <= set(form.slots)


def test_ask_if_preconditions_gate_slots() -> None:
    # idea.business_model needs a category that is not an internal tool
    slots: dict[str, SlotRecord | None] = {
        "idea.summary": rec("idea.summary", "confirmed"),
        "idea.category": rec("idea.category", "confirmed", "internal_tool"),
    }
    assert not any("idea.business_model" in i.slots for i in plan(slots))


def test_enterprise_slots_only_in_enterprise_mode() -> None:
    assert not any(s.startswith("org.") for i in plan() for s in i.slots)
    ent = plan(depth="large", mode="enterprise")
    assert any(s.startswith("org.") for i in ent for s in i.slots)


def test_large_depth_asks_per_module() -> None:
    large = asks(plan(depth="large"))
    assert len(large) > len(asks(plan(depth="standard")))


def test_next_item_skips_assumptions_and_empty_queue() -> None:
    assert next_item([]) is None
    only_assume = [QueueItem("limits", ["constraints.offline"], "assume", 1.0, False, "x")]
    assert next_item(only_assume) is None


def test_estimate_total_counts_asked_plus_pending_and_is_at_least_one() -> None:
    assert estimate_total([], []) == 1
    queue = plan(asked_frames=["idea_in_your_words"])
    pending = len([i for i in asks(queue) if i.frame_id != "idea_in_your_words"])
    assert estimate_total(queue, ["idea_in_your_words"]) == 1 + pending


def test_score_formula_uses_config_weights() -> None:
    slot = CATALOG.slots["idea.summary"]
    base = slot_score(slot, 0.0, CFG)
    assert slot_score(slot, 1.0, CFG) < base
    heavy = QuestionPolicy(w_impact=10.0)
    assert slot_score(slot, 0.0, heavy) > base
    assert default_inferability(CATALOG.slots["context.scale_launch"]) > default_inferability(slot)


def test_plan_is_pure() -> None:
    slots: dict[str, SlotRecord | None] = {"idea.summary": rec("idea.summary", "volunteered")}
    assert plan(slots) == plan(slots)
    assert list(slots) == ["idea.summary"]


# --- property tests --------------------------------------------------------------------------------------

SLOT_IDS = [s for s in CATALOG.slots if CATALOG.slots[s].level != "generated"]
STATUSES = ["empty", "assumed", "volunteered", "confirmed", "disputed"]


@st.composite
def slot_states(draw: st.DrawFn) -> dict[str, SlotRecord | None]:
    chosen = draw(st.lists(st.sampled_from(SLOT_IDS), max_size=30, unique=True))
    return {
        s: rec(s, draw(st.sampled_from(STATUSES)), "v", draw(st.floats(0.0, 1.0))) for s in chosen
    }


prop = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@prop
@given(slot_states(), st.sampled_from(["express", "standard", "large"]), st.integers(0, 6))
def test_queue_never_contains_a_settled_slot(
    slots: dict[str, SlotRecord | None], depth: str, n_asked: int
) -> None:
    asked = [f.id for f in sorted(CATALOG.frames.values(), key=lambda f: f.order)][:n_asked]
    queue = plan(slots, depth=depth, asked_frames=asked)
    settled = {s for s, r in slots.items() if r and r.status in ("confirmed", "volunteered")}
    planned = [s for i in queue for s in i.slots]
    assert not settled & set(planned)
    assert len(planned) == len(set(planned))  # a slot is in exactly one item


@prop
@given(slot_states(), st.sampled_from(["express", "standard"]))
def test_asks_never_exceed_the_cap(slots: dict[str, SlotRecord | None], depth: str) -> None:
    cap = CFG.express_max if depth == "express" else CFG.hard_cap
    assert len(asks(plan(slots, depth=depth))) <= cap


@prop
@given(slot_states(), st.sampled_from(["express", "standard", "large"]))
def test_order_is_dependency_order(slots: dict[str, SlotRecord | None], depth: str) -> None:
    orders = [CATALOG.frames[i.frame_id].order for i in plan(slots, depth=depth)]
    assert orders == sorted(orders)


@prop
@given(slot_states(), st.lists(st.sampled_from(SLOT_IDS), min_size=1, max_size=6, unique=True))
def test_an_answer_that_settles_slots_removes_their_items(
    slots: dict[str, SlotRecord | None], answered: list[str]
) -> None:
    after = dict(slots)
    for s in answered:
        after[s] = rec(s, "confirmed")
    planned_after = {s for i in plan(after) for s in i.slots}
    assert not planned_after & set(answered)


@prop
@given(slot_states())
def test_just_build_only_asks_the_four_questions(slots: dict[str, SlotRecord | None]) -> None:
    queue = plan(slots, profile=SituationProfile(urgency="just_build"), depth="express")
    assert {s for i in asks(queue) for s in i.slots} <= JUST_BUILD_SLOTS


@prop
@given(slot_states())
def test_risky_asks_are_must_confirm(slots: dict[str, SlotRecord | None]) -> None:
    for item in asks(plan(slots)):
        risky = any(
            CATALOG.slots[s].must_confirm or CATALOG.slots[s].risk == "high" for s in item.slots
        )
        assert item.must_confirm == risky


def test_every_askable_slot_has_a_frame() -> None:
    from mde_compass.engine.voi import _frames_by_slot

    frames = _frames_by_slot(CATALOG)
    missing = [s for s, d in CATALOG.slots.items() if d.level != "generated" and s not in frames]
    assert missing == []


@pytest.mark.parametrize("depth", ["express", "standard", "large"])
def test_plan_is_fast(depth: str) -> None:
    import time

    start = time.perf_counter()
    for _ in range(20):
        plan(depth=depth)
    assert time.perf_counter() - start < 2.0
