"""The planner protects risk topics, keeps a job's best topic only when it has a required slot, ranks by Brief value and
expands per-country topics (D-163, plan finding 3 and 12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mde_research_agent.catalog import JOB_NAMES, load_catalog
from mde_research_agent.catalog.models import SlotSpec
from mde_research_agent.domain.plan import Target
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import _trim, build_plan

ROOT = Path(__file__).resolve().parents[4]
INPUTS = ROOT / "docs" / "compass" / "research-inputs"
CAT = load_catalog()
STEMS = sorted(p.stem for p in INPUTS.glob("*.json"))


def plan_of(stem: str) -> Any:
    inp = read_export(json.loads((INPUTS / f"{stem}.json").read_text(encoding="utf-8")), CAT)
    return inp, build_plan(inp, CAT)


def target(
    label: str,
    job: str = "features",
    priority: int = 50,
    risk: str | None = None,
    required: bool = False,
) -> Target:
    slot = SlotSpec(key="s", type="count", kind="k", required=required)
    return Target(
        id="",
        job=job,
        template_id=label,
        spec_need="n",
        lane="blueprint",
        label=label,
        question="q",
        decision_unlocked="d",
        anchors=(label,),
        intent=("i",),
        needs=("current",),
        priority=priority,
        slots=(slot,),
        risk=risk,
    )


# --- _trim ---------------------------------------------------------------------------------------------------------------


def test_risk_topics_stay_outside_the_cap_and_the_cap_applies_to_the_rest() -> None:
    risky = [target(f"law {i}", "data", 45, "regulation") for i in range(3)]
    plain = [target(f"plain {i}", "design", 90 - i) for i in range(5)]
    kept, rest = _trim([*risky, *plain], cap=2)
    assert {t.label for t in kept} == {"law 0", "law 1", "law 2", "plain 0", "plain 1"}
    assert [t.label for t in rest] == ["plain 2", "plain 3", "plain 4"]


def test_a_low_priority_risk_topic_is_not_evicted_for_a_higher_priority_plain_one() -> None:
    kept, _ = _trim(
        [target("dp", "trust", 45, "data_protection"), target("nav", "structure", 70)], cap=1
    )
    assert {t.label for t in kept} == {"dp", "nav"}


def test_job_keeps_one_only_when_its_best_topic_has_a_required_slot() -> None:
    top = [target(f"top {i}", "features", 90 - i, required=True) for i in range(3)]
    empty_job = target(
        "empty", "trust", 45, required=False
    )  # nothing required to find: does not push anyone out
    needed_job = target("needed", "data", 45, required=True)  # a required slot: keeps one topic
    kept, rest = _trim([*top, empty_job, needed_job], cap=3)
    labels = {t.label for t in kept}
    assert "needed" in labels and "empty" not in labels
    assert "empty" in {t.label for t in rest}
    assert (
        sum(t.job == "features" for t in kept) == 2
    )  # the donor came from the job with more than one


def test_the_risk_valve_sends_surplus_risk_topics_back_under_the_cap() -> None:
    risky = [target(f"law {i}", "data", 80 - i, "regulation") for i in range(4)]
    kept, rest = _trim(risky, cap=0, risk_cap=2)
    assert [t.label for t in kept] == ["law 0", "law 1"] and len(rest) == 2


# --- the real inputs ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stem", STEMS)
def test_every_input_runs_every_risk_topic_the_brief_asks_for_within_the_valve(stem: str) -> None:
    inp, plan = plan_of(stem)
    ceiling = CAT.ceilings.for_class(inp.load.load_class)
    risk_run = [t for t in plan.targets if t.risk]
    risk_unrun = [t for t in plan.unrun if t.risk]
    plain_run = [t for t in plan.targets if not t.risk]
    assert len(plain_run) <= ceiling.soft.targets
    assert len(plan.targets) <= ceiling.soft.targets + ceiling.risk_topics
    # no risk topic is left out for a plain one: any that did not run lost only to the safety valve
    assert not risk_unrun or len(risk_run) >= ceiling.risk_topics


@pytest.mark.parametrize("stem", STEMS)
def test_no_risk_topic_is_ranked_below_a_cut_plain_topic(stem: str) -> None:
    _, plan = plan_of(stem)
    plain_unrun = [t for t in plan.unrun if not t.risk]
    assert all(
        t.risk is None for t in plan.unrun if t.risk is None
    )  # sanity: the field exists on every target
    # everything the cap cut is ranked by priority (the unrun list is in rank order)
    assert [t.priority for t in plain_unrun] == sorted(
        (t.priority for t in plain_unrun), reverse=True
    )


def test_the_cap_sizes_are_the_ones_d163_sets() -> None:
    assert [CAT.ceilings.for_class(c).soft.targets for c in ("focused", "standard", "deep")] == [
        12,
        24,
        38,
    ]
    f, s, d = (CAT.ceilings.for_class(c) for c in ("focused", "standard", "deep"))
    assert (f.hard.searches, f.soft.searches) == (
        52,
        36,
    )  # 40/28 in D-163; raised after the first live Small run
    assert (s.hard.searches, s.soft.searches, s.hard.pages) == (
        130,
        90,
        180,
    )  # 96/64/130 in D-163; raised after the first live Medium run
    assert (d.hard.searches, d.soft.searches, d.hard.pages) == (
        220,
        160,
        300,
    )  # 170/120/230 in D-163; sized from the Medium run


def test_medium_and_big_run_the_provider_and_entity_topics_they_used_to_leave_out() -> None:
    _, nashik = plan_of("standard-produce-marketplace-nashik")
    templates = {f"{t.job}/{t.template_id}" for t in nashik.targets}
    assert "data/entity_shape" in templates  # "what a X record holds"
    needs = {t.need_key for t in nashik.targets if t.template_id == "providers"}
    assert {"tracking_telematics", "accounting"} <= needs  # the signals say both
    _, freight = plan_of("thorough-freight-fleet-india")
    assert sum(t.template_id == "entity_shape" for t in freight.targets) >= 4
    assert any(t.template_id == "formats" for t in freight.targets)


def test_entities_named_in_the_brief_outrank_navigation_and_roles() -> None:
    _, plan = plan_of("standard-produce-marketplace-nashik")
    entity = [t.priority for t in plan.targets if t.template_id == "entity_shape"]
    nav = [t.priority for t in plan.targets if t.template_id in ("navigation", "roles")]
    assert entity and nav and min(entity) > max(nav)


# --- per-country topics --------------------------------------------------------------------------------------------------


def test_a_three_country_brief_gets_one_regulation_and_provider_topic_per_country() -> None:
    _, plan = plan_of("synthetic-big-site-safety-central-america")
    per = [t for t in plan.targets if t.country_code]
    assert {t.country_code for t in per} == {"mx", "gt", "cr"}
    for template in ("providers", "rules", "data_protection_law"):
        mine = [t for t in per if t.template_id == template]
        assert mine, template
        assert {t.country_code for t in mine} == {"mx", "gt", "cr"}, template
    for t in per:
        assert t.place is not None and t.place.country_code == t.country_code
        assert t.place.phrases == (t.place.country_name,)
        assert t.label.endswith(f"({t.place.country_name})") or t.place.country_name in t.label
    # norms, design and screens stay shared
    shared = [t for t in plan.targets if t.job in ("design", "screens", "structure", "flows")]
    assert shared and all(t.country_code is None for t in shared)


def test_a_request_is_never_spread_over_the_countries() -> None:
    inp, plan = plan_of("synthetic-big-site-safety-central-america")
    for r in inp.requests:
        carriers = [t for t in (*plan.targets, *plan.unrun) if r.id in t.links.requests]
        assert len(carriers) <= 1, (r.id, [t.label for t in carriers])


def test_a_single_country_brief_is_not_expanded() -> None:
    _, plan = plan_of("thorough-freight-fleet-india")
    assert all(t.country_code is None for t in (*plan.targets, *plan.unrun))


def test_per_country_templates_carry_the_flags_in_the_data() -> None:
    providers = CAT.jobs["services"].targets[0]
    assert providers.per_country and providers.official_first and providers.engines == "both"
    rules = next(t for t in CAT.jobs["data"].targets if t.id == "rules")
    assert (
        rules.per_country and rules.place_scope == "city"
    )  # a single-country Brief keeps the city words
    assert rules.risk == "regulation" and rules.risk_from_candidate
    for job in JOB_NAMES:
        for tpl in CAT.jobs[job].targets:
            if tpl.scope == "country_each":
                assert tpl.place_scope == "country"


def test_a_per_country_topic_carries_its_countrys_language_first() -> None:
    _, plan = plan_of("synthetic-big-site-safety-central-america")
    per = [t for t in plan.targets if t.country_code and t.place is not None]
    assert per
    for t in per:
        assert t.place is not None and t.place.languages[:1] == ("es",), t.label
    _, porto = plan_of("synthetic-small-bike-repair-porto")
    assert all(t.country_code is None for t in porto.targets), "one country: nothing is expanded"
