from __future__ import annotations

from collections.abc import Sequence

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, load_catalog
from mde_compass.config import DEFAULT_CONFIG, QuestionPolicy
from mde_compass.domain.brief import AssumptionRecord
from mde_compass.domain.common import Provenance, Status
from mde_compass.domain.interview import AnswerSpec, Fact, NeedStatus
from mde_compass.engine.needs import (
    compute,
    expert_gap_obligations,
    high_impact_at,
    needs_table,
    split_aspect,
)
from mde_compass.engine.slots import SlotStore
from mde_core.clock import FakeClock

REAL = load_catalog()  # the real Needs Registry: every need has an answer_spec
# Same catalog with every answer_spec removed: the base for the synthetic specs below, so the semantics tests
# do not depend on the registry's data.
BASE = REAL.model_copy(
    update={"slots": {k: v.model_copy(update={"answer_spec": None}) for k, v in REAL.slots.items()}}
)
SEGMENTS = AnswerSpec(
    sufficient="At least one named segment: who they are, their goal, where they use the app.",
    aspects=["who", "goal", "where"],
    fact_types=["goal", "context"],
    min_facts=1,
)
ROLES = AnswerSpec(
    sufficient="Each kind of person who signs in and what they can change.",
    aspects=["role", "powers"],
    fact_types=["role"],
    min_facts=3,
)


def catalog() -> Catalog:
    slots = dict(BASE.slots)
    slots["audience.segments"] = slots["audience.segments"].model_copy(
        update={"answer_spec": SEGMENTS}
    )
    slots["roles.list"] = slots["roles.list"].model_copy(update={"answer_spec": ROLES})
    return BASE.model_copy(update={"slots": slots})


CAT = catalog()


def fact(n: int, *aspects: str, type: str = "context", status: str = "active") -> Fact:
    return Fact.model_validate(
        {
            "id": f"FCT-{n:03d}",
            "type": type,
            "statement": f"statement {n}",
            "quote": f"quote {n}",
            "aspects": list(aspects),
            "status": status,
        }
    )


def slots_with(**values: tuple[JsonValue, Status]) -> SlotStore:
    store = SlotStore(CAT, clock=FakeClock())
    for slot_id, (value, state) in values.items():
        sid = slot_id.replace("__", ".")
        store.write(
            sid,
            value,
            state=state,
            provenance=Provenance(type="user_text", ref="evt_1"),
        )
    return store


def one(
    slot_id: str,
    facts: list[Fact],
    store: SlotStore,
    *,
    assumptions: Sequence[AssumptionRecord] = (),
) -> NeedStatus:
    return compute(CAT, facts, store, assumptions=assumptions)[slot_id]


# ------------------------------------------------------------------------------------------ scope
def test_org_needs_only_in_enterprise() -> None:
    store = slots_with()
    guided = compute(CAT, [], store)
    enterprise = compute(CAT, [], store, mode="enterprise")
    assert not any(k.startswith("org.") for k in guided)
    assert set(enterprise) - set(guided) == {k for k in CAT.slots if k.startswith("org.")}
    assert list(guided) == [s for s in CAT.slot_ids_by_order() if s in guided]  # dependency order


def test_every_real_need_has_an_answer_spec() -> None:
    assert all(slot.answer_spec is not None for slot in REAL.slots.values())


def test_real_registry_confirmed_slot_without_covered_aspects_is_partial() -> None:
    store = SlotStore(REAL, clock=FakeClock())
    store.write(
        "idea.summary",
        "tiffin",
        state="volunteered",
        provenance=Provenance(type="user_text", ref="e"),
    )
    statuses = compute(REAL, [], store)
    summary = statuses["idea.summary"]
    spec = REAL.slots["idea.summary"].answer_spec
    assert spec is not None
    assert summary.state == "partial" and summary.missing_aspects == spec.aspects
    assert statuses["idea.category"].state == "missing"


def test_real_registry_is_satisfied_by_a_complete_fact_or_every_aspect() -> None:
    store = SlotStore(REAL, clock=FakeClock())
    for sid in ("idea.summary", "idea.category"):
        store.write(sid, "x", state="confirmed", provenance=Provenance(type="user_tap", ref="e"))
    spec = REAL.slots["idea.summary"].answer_spec
    assert spec is not None
    star = fact(1, "idea.summary:*")
    category_spec = REAL.slots["idea.category"].answer_spec
    assert category_spec is not None
    each = fact(2, *(f"idea.category:{a}" for a in category_spec.aspects))
    statuses = compute(REAL, [star, each], store)
    assert statuses["idea.summary"].state == "satisfied"
    assert statuses["idea.category"].state == "satisfied"
    assert statuses["idea.summary"].covered_aspects == spec.aspects


# --------------------------------------------------------------------------------- no aspects
def test_need_without_spec_is_satisfied_by_a_confirmed_or_volunteered_value() -> None:
    for state in ("volunteered", "confirmed"):
        s = one("idea.summary", [], slots_with(idea__summary=("a tiffin app", state)))
        assert s.state == "satisfied" and s.missing_aspects == [] and s.impact == 5
        assert s.level == "must"


def test_assumed_value_is_assumed_not_satisfied() -> None:
    s = one("idea.summary", [], slots_with(idea__summary=("guess", "assumed")))
    assert s.state == "assumed" and s.assumed_value == "guess"


def test_nothing_is_missing() -> None:
    s = one("idea.summary", [], slots_with())
    assert s.state == "missing" and s.assumed_value is None and s.fact_ids == []


def test_assumption_records_make_a_need_assumed() -> None:
    a = AssumptionRecord(id="ASM-001", claim="Pune only", slot_id="context.geo")
    s = one("context.geo", [], slots_with(), assumptions=[a])
    assert s.state == "assumed" and s.assumed_value == "Pune only"
    rejected = a.model_copy(update={"decision": "rejected"})
    assert one("context.geo", [], slots_with(), assumptions=[rejected]).state == "missing"


def test_a_user_value_beats_an_assumption_record() -> None:
    a = AssumptionRecord(id="ASM-001", claim="x", slot_id="idea.summary")
    s = one("idea.summary", [], slots_with(idea__summary=("mine", "volunteered")), assumptions=[a])
    assert s.state == "satisfied"


# ------------------------------------------------------------------------------------ aspects
def test_user_value_with_no_aspect_covered_is_partial_and_lists_what_is_missing() -> None:
    s = one(
        "audience.segments", [], slots_with(audience__segments=(["office workers"], "volunteered"))
    )
    assert s.state == "partial" and s.missing_aspects == ["who", "goal", "where"]
    assert s.covered_aspects == []


def test_some_aspects_covered_is_partial() -> None:
    facts = [fact(1, "audience.segments:who"), fact(2, "audience.segments:where")]
    s = one(
        "audience.segments",
        facts,
        slots_with(audience__segments=(["office workers"], "volunteered")),
    )
    assert s.state == "partial"
    assert s.covered_aspects == ["who", "where"] and s.missing_aspects == ["goal"]
    assert s.fact_ids == ["FCT-001", "FCT-002"]


def test_all_aspects_covered_is_satisfied() -> None:
    facts = [
        fact(1, "audience.segments:who", "audience.segments:goal"),
        fact(2, "audience.segments:where"),
    ]
    s = one("audience.segments", facts, slots_with(audience__segments=(["workers"], "confirmed")))
    assert s.state == "satisfied" and s.missing_aspects == []
    assert s.covered_aspects == ["who", "goal", "where"]  # spec order


def test_star_covers_everything_when_there_is_a_user_value() -> None:
    s = one(
        "audience.segments",
        [fact(1, "audience.segments:*")],
        slots_with(audience__segments=(["workers"], "confirmed")),
    )
    assert s.state == "satisfied" and s.covered_aspects == ["who", "goal", "where"]


def test_star_without_a_slot_value_is_only_partial() -> None:
    s = one("audience.segments", [fact(1, "audience.segments:*")], slots_with())
    assert s.state == "partial" and s.missing_aspects == []


def test_aspects_without_a_slot_value_are_partial() -> None:
    s = one("audience.segments", [fact(1, "audience.segments:who")], slots_with())
    assert s.state == "partial" and s.missing_aspects == ["goal", "where"]


def test_min_facts_of_the_right_types_satisfies() -> None:
    facts = [
        fact(1, "roles.list:role", type="role"),
        fact(2, "roles.list:role", type="role"),
        fact(3, "roles.list:role", type="role"),
    ]
    store = slots_with(roles__list=([{"name": "owner"}], "volunteered"))
    assert one("roles.list", facts, store).state == "satisfied"
    two_roles_one_other = [facts[0], facts[1], fact(3, "roles.list:role", type="context")]
    assert one("roles.list", two_roles_one_other, store).state == "partial"


def test_min_facts_of_one_does_not_make_aspects_moot() -> None:
    # SEGMENTS has min_facts=1 and a "goal" fact: one right-type fact must NOT satisfy a three-aspect need.
    s = one(
        "audience.segments",
        [fact(1, "audience.segments:goal", type="goal")],
        slots_with(audience__segments=(["x"], "volunteered")),
    )
    assert s.state == "partial"


def test_inactive_facts_and_unknown_or_malformed_aspects_are_ignored() -> None:
    facts = [
        fact(1, "audience.segments:who", status="superseded"),
        fact(2, "audience.segments:bogus"),
        fact(3, "garbage", "audience.segments:", ":who", "audience.segments:goal"),
        fact(4, "idea.summary:*", status="disputed"),
    ]
    s = one("audience.segments", facts, slots_with(audience__segments=(["x"], "volunteered")))
    assert s.covered_aspects == ["goal"] and s.missing_aspects == ["who", "where"]
    assert one("idea.summary", facts, slots_with()).state == "missing"


def test_disputed_value_is_partial_never_satisfied() -> None:
    store = SlotStore(CAT, clock=FakeClock())
    store.write(
        "idea.summary",
        "a",
        state="volunteered",
        provenance=Provenance(type="user_text", ref="e"),
        contributor_id="u1",
    )
    store.write(
        "idea.summary",
        "b",
        state="volunteered",
        provenance=Provenance(type="user_text", ref="e"),
        contributor_id="u2",
    )
    rec = store.get("idea.summary")
    assert rec is not None and rec.status == "disputed"
    assert compute(CAT, [], store)["idea.summary"].state == "partial"


def test_asked_and_followup_counters_are_carried() -> None:
    statuses = compute(
        CAT, [], slots_with(), asked={"idea.summary": 2}, followups={"idea.summary": 1}
    )
    assert (statuses["idea.summary"].asked, statuses["idea.summary"].followups_used) == (2, 1)
    assert statuses["idea.category"].asked == 0


def test_fact_ids_include_facts_with_effects_on_the_slot() -> None:
    f = Fact.model_validate(
        {
            "id": "FCT-007",
            "type": "goal",
            "statement": "s",
            "effects": [{"slot_id": "idea.summary", "value_json": '"x"'}],
        }
    )
    assert one("idea.summary", [f], slots_with()).fact_ids == ["FCT-007"]


def test_compute_does_not_mutate_its_inputs() -> None:
    facts = [fact(1, "audience.segments:who")]
    store = slots_with(audience__segments=(["x"], "volunteered"))
    facts_before = [f.model_copy(deep=True) for f in facts]
    slots_before = store.dump()
    a = [AssumptionRecord(id="ASM-001", claim="c", slot_id="context.geo")]
    a_before = [x.model_copy(deep=True) for x in a]
    compute(CAT, facts, store, assumptions=a, asked={"x": 1}, followups={"y": 1})
    assert facts == facts_before and store.dump() == slots_before and a == a_before


def test_accepts_a_plain_mapping_of_records() -> None:
    store = slots_with(idea__summary=("x", "volunteered"))
    statuses = compute(CAT, [], store.all())
    assert statuses["idea.summary"].state == "satisfied"


def test_split_aspect() -> None:
    assert split_aspect("audience.segments:goal") == ("audience.segments", "goal")
    assert split_aspect("domain:pricing:*") == ("domain:pricing", "*")
    assert (
        split_aspect("nocolon") is None
        and split_aspect("a:") is None
        and split_aspect(":b") is None
    )


# --------------------------------------------------------------------------------------------- views
def test_needs_table_shows_open_needs_compactly() -> None:
    facts = [fact(1, "audience.segments:who")]
    store = slots_with(
        idea__summary=("done", "volunteered"),
        audience__segments=(["x"], "volunteered"),
        context__geo=("guess", "assumed"),
    )
    statuses = compute(CAT, facts, store)
    table = needs_table(statuses, CAT)
    lines = table.splitlines()
    assert not any(line.startswith("idea.summary ") for line in lines)  # satisfied: hidden
    assert not any(
        line.startswith("unknowns.research_wanted") for line in lines
    )  # generated: never asked
    seg = next(line for line in lines if line.startswith("audience.segments |"))
    assert seg == (
        "audience.segments | must | impact 5 | partial | sufficient: "
        + SEGMENTS.sufficient
        + " | missing: goal, where"
    )
    geo = next(line for line in lines if line.startswith("context.geo |"))
    assert geo == 'context.geo | must | impact 5 | assumed | assumed: "guess"'
    assert needs_table(statuses, CAT) == table  # deterministic
    all_lines = needs_table(statuses, CAT, only_open=False).splitlines()
    assert any(line.startswith("idea.summary |") for line in all_lines)
    assert len(all_lines) == len(lines) + 1


def test_needs_table_truncates_long_assumed_values() -> None:
    store = slots_with(context__geo=("x" * 300, "assumed"))
    line = next(
        line
        for line in needs_table(compute(CAT, [], store), CAT).splitlines()
        if line.startswith("context.geo")
    )
    assert len(line.split("assumed: ")[1]) <= 80


def test_expert_gap_obligations() -> None:
    store = slots_with(
        idea__summary=("done", "volunteered"),
        context__geo=("guess", "assumed"),
    )
    statuses = compute(CAT, [], store)
    owed = expert_gap_obligations(statuses, CAT, QuestionPolicy())
    assert "idea.summary" not in owed  # satisfied
    assert "context.geo" not in owed  # visibly assumed
    assert "audience.segments" in owed and "idea.category" in owed  # must, missing
    assert "scope.priority" in owed  # should-level but impact 4
    assert "brand.name" not in owed  # should-level, impact 2
    assert "unknowns.research_wanted" not in owed  # generated
    assert owed == [n for n in CAT.slot_ids_by_order() if n in owed]


def test_expert_gap_obligations_threshold_and_config_forms() -> None:
    statuses = compute(CAT, [], slots_with())
    strict = expert_gap_obligations(statuses, CAT, QuestionPolicy(high_impact_at=5))
    assert "scope.priority" not in strict and "idea.subcategory" in strict  # still must-level
    assert expert_gap_obligations(statuses, CAT, DEFAULT_CONFIG) == expert_gap_obligations(
        statuses, CAT, DEFAULT_CONFIG.questions
    )
    assert high_impact_at(DEFAULT_CONFIG) == high_impact_at(DEFAULT_CONFIG.questions) == 4


@settings(max_examples=80, deadline=None)
@given(
    entries=st.lists(
        st.tuples(
            st.sampled_from(["audience.segments", "roles.list", "idea.summary", "zzz"]),
            st.sampled_from(["who", "goal", "where", "role", "powers", "*", "nope"]),
            st.sampled_from(["active", "superseded"]),
            st.sampled_from(["role", "goal", "context"]),
        ),
        max_size=8,
    ),
    value_state=st.sampled_from([None, "assumed", "volunteered", "confirmed"]),
)
def test_status_invariants(
    entries: list[tuple[str, str, str, str]], value_state: Status | None
) -> None:
    facts = [
        fact(i + 1, f"{need}:{aspect}", type=ftype, status=status)
        for i, (need, aspect, status, ftype) in enumerate(entries)
    ]
    store = slots_with()
    if value_state is not None:
        slot_value: JsonValue = ["x"]
        store = slots_with(audience__segments=(slot_value, value_state))
    statuses = compute(CAT, facts, store)
    seg = statuses["audience.segments"]
    spec_aspects = SEGMENTS.aspects
    assert set(seg.covered_aspects) <= set(spec_aspects) and set(seg.missing_aspects) <= set(
        spec_aspects
    )
    assert not set(seg.covered_aspects) & set(seg.missing_aspects)
    if seg.state == "satisfied":
        assert value_state in ("volunteered", "confirmed") and not seg.missing_aspects
    if value_state in ("volunteered", "confirmed"):
        assert seg.state in ("satisfied", "partial")
    for status in statuses.values():
        assert status.state in ("satisfied", "partial", "missing", "assumed")
        assert status.fact_ids == sorted(status.fact_ids)
