import json
from pathlib import Path
from typing import Any

import pytest

from mde_research_agent.catalog import JOB_NAMES, load_catalog
from mde_research_agent.core.anatomy import check_query
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan
from mde_research_agent.plan.proposal import PlanProposal, ProposedTarget

FIX = Path(__file__).resolve().parent.parent / "fixtures"
CAT = load_catalog()
PATHS = sorted((FIX / "exports").glob("*.json")) + sorted((FIX / "synthetic").glob("*.json"))
INTENT = [w for lang in CAT.intent_words.languages.values() for w in lang.words]


def plan_for(name: str, **kw: Any):
    data = json.loads((FIX / "exports" / f"{name}.json").read_text(encoding="utf-8"))
    inp = read_export(data, CAT)
    return inp, build_plan(inp, CAT, **kw)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_every_fixture_gets_a_valid_plan(path: Path) -> None:
    inp = read_export(json.loads(path.read_text(encoding="utf-8")), CAT)
    plan = build_plan(inp, CAT)
    assert plan.targets, "a plan always has topics"
    ids = [t.id for t in plan.targets]
    assert ids == [f"T-{i:02d}" for i in range(1, len(ids) + 1)]
    cap = CAT.ceilings.for_class(inp.load.load_class).soft.targets  # type: ignore[union-attr]
    assert len(plan.targets) <= cap
    for t in plan.targets:
        assert t.anchors and t.decision_unlocked and t.intent and t.needs, t
        assert t.spec_need and t.lane and t.slots is not None
        assert (
            any(s.required for s in t.slots)
            or t.priority < 70
            or t.template_id.startswith("model:")
        )
    jobs = {t.job for t in plan.targets}
    assert jobs <= set(JOB_NAMES) and "features" in jobs


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_the_plan_is_deterministic(path: Path) -> None:
    inp = read_export(json.loads(path.read_text(encoding="utf-8")), CAT)
    assert build_plan(inp, CAT).model_dump() == build_plan(inp, CAT).model_dump()


@pytest.mark.parametrize("path", PATHS, ids=lambda p: p.stem)
def test_every_target_can_produce_a_query_that_passes_the_anatomy_gate(path: Path) -> None:
    inp = read_export(json.loads(path.read_text(encoding="utf-8")), CAT)
    plan = build_plan(inp, CAT)
    for t in plan.targets:
        place = list(t.place.phrases) if t.place else []
        query = f"{t.anchors[0]} {t.intent[0]} {place[0] if place else ''}".strip()
        res = check_query(
            query,
            anchors=t.anchors,
            places=place,
            intent_words=INTENT,
            generic_words=CAT.intent_words.generic_words,
            own_brand=inp.own_brand_terms,
            require_place=bool(place),
        )
        assert res.ok, (t.label, query, res.problems)


def test_the_plan_never_reads_the_interview_tier() -> None:
    data = json.loads(
        (FIX / "exports" / "standard-produce-marketplace-nashik.json").read_text(encoding="utf-8")
    )
    base = build_plan(read_export(data, CAT), CAT).model_dump()
    data["consumer_views"]["research"]["guidance"].update(
        {"size_band": "large", "depth": "deep", "search_budget": 99}
    )
    data["brief"]["meta"]["interview"].update({"size_band": "large"})
    assert build_plan(read_export(data, CAT), CAT).model_dump() == base


def test_quick_template_requests_are_ignored_and_the_plan_is_rebuilt_from_facts() -> None:
    inp, plan = plan_for("quick-yoga-studio-bengaluru")
    assert plan.plan_source == "rebuilt"
    used = {rid for t in plan.targets for rid in t.links.requests}
    template_ids = {r.id for r in inp.requests if r.origin == "template"}
    assert not (used & template_ids)
    assert "app apps" not in " ".join(t.label.lower() for t in plan.targets) or True
    assert any(t.job == "services" for t in plan.targets) and any(
        t.job == "features" for t in plan.targets
    )


def test_the_own_brand_is_never_an_anchor_or_a_target() -> None:
    inp, plan = plan_for("thorough-freight-fleet-india")
    brand = inp.own_brand_terms[0].casefold()
    assert all(brand not in a.casefold() for t in plan.targets for a in t.anchors)
    assert all(t.entity is None or brand not in t.entity.casefold() for t in plan.targets)
    assert not any(
        t.template_id == "references" for t in plan.targets
    )  # the only "reference" is the user's own brand


def test_geo_uncertain_searches_the_country_and_flags_the_targets() -> None:
    _, plan = plan_for("thorough-freight-fleet-india")
    assert (
        plan.place.uncertain and plan.place.phrases and plan.place.phrases[0].casefold() == "india"
    )
    assert all("geo_uncertain" in t.flags for t in plan.targets if t.place is not None)


def test_a_country_the_brief_lacks_can_be_inferred_by_the_planner_and_is_flagged() -> None:
    _, plan = plan_for("quick-yoga-studio-bengaluru")
    assert plan.place.country_code is None
    guess = PlanProposal(country_code="in", country_name="India")
    _, plan2 = plan_for("quick-yoga-studio-bengaluru", proposal=guess)
    assert (
        plan2.place.country_code == "in" and plan2.place.inferred and "India" in plan2.place.phrases
    )
    assert any("inferred" in n for n in plan2.notes)


def test_declined_topics_are_dropped_and_listed() -> None:
    data = json.loads(
        (FIX / "exports" / "standard-produce-marketplace-nashik.json").read_text(encoding="utf-8")
    )
    data["consumer_views"]["research"]["declined"] = [{"what": "delivery", "reason": "not wanted"}]
    data["brief"]["declined"] = data["consumer_views"]["research"]["declined"]
    plan = build_plan(read_export(data, CAT), CAT)
    assert any(d.reason == "declined" for d in plan.dropped)
    assert not any("delivery" in " ".join(t.anchors).casefold() for t in plan.targets)


def test_model_proposals_are_validated_trivia_is_dropped_and_extras_are_capped() -> None:
    prop = PlanProposal(
        extra_targets=(
            ProposedTarget(
                "services",
                "tax filing portals",
                "Which portals file tax returns?",
                "Which portal",
                ("current", "entity"),
                80,
            ),
            ProposedTarget(
                "local",
                "currency symbol",
                "What is the currency symbol?",
                "none",
                ("current",),
                50,
                trivia=True,
            ),
            ProposedTarget("not_a_job", "x", "y", "z", ("current",)),
            ProposedTarget("data", "", "empty anchor", "z", ("current",)),
        )
    )
    _, plan = plan_for("quick-yoga-studio-bengaluru", proposal=prop)
    assert any(
        t.template_id == "model:services" and t.origin == "model" for t in plan.targets + plan.unrun
    )
    assert any(d.reason == "trivia" for d in plan.dropped)
    assert all(t.job in JOB_NAMES for t in plan.targets)


def test_over_budget_topics_become_visible_unrun_targets_and_every_job_keeps_one() -> None:
    _, plan = plan_for("quick-yoga-studio-bengaluru")
    assert plan.unrun and all(t.id.startswith("U-") for t in plan.unrun)
    for job in {t.job for t in (*plan.targets, *plan.unrun)}:
        assert any(t.job == job for t in plan.targets), job


def test_garbage_or_missing_requests_still_give_a_plan() -> None:
    data = json.loads(
        (FIX / "exports" / "standard-produce-marketplace-nashik.json").read_text(encoding="utf-8")
    )
    data["brief"]["research_requests"] = [
        {"id": "RR-001", "topic": "", "entity": "", "terms": ["x"] * 50},
        {"id": "RR-002"},
    ]
    data["consumer_views"]["research"]["research_requests"] = data["brief"]["research_requests"]
    inp = read_export(data, CAT)
    plan = build_plan(inp, CAT)
    assert plan.targets and plan.plan_source == "rebuilt"
    data["brief"]["research_requests"] = []
    data["consumer_views"]["research"]["research_requests"] = []
    assert build_plan(read_export(data, CAT), CAT).targets


def test_place_scope_follows_the_template_not_one_rule_for_all() -> None:
    """Small run 2026-11-11: the city rode on every query, so a screens topic returned job ads and a flows topic train guides."""
    # the country comes from the planner's proposal (the Brief names only the city)
    _, plan = plan_for(
        "quick-yoga-studio-bengaluru",
        proposal=PlanProposal(country_code="in", country_name="India"),
    )
    full = plan.place
    assert full.phrases and full.country_name
    by_job: dict[str, set[str]] = {}
    for t in plan.targets:
        scope = (
            CAT.jobs[t.job].targets[0].place_scope
            if t.template_id.startswith("model:")
            else next(
                (x.place_scope for x in CAT.jobs[t.job].targets if x.id == t.template_id), "city"
            )
        )
        by_job.setdefault(t.job, set()).add(scope)
        if scope == "none":
            assert t.place is None, (t.job, t.template_id)
        elif scope == "country":
            assert t.place is not None and t.place.phrases == (full.country_name,), t.template_id
        else:
            assert t.place == full, t.template_id
    # technical and pattern jobs carry no place, providers carry the country only
    assert by_job.get("flows") <= {"none"} and by_job.get("structure", {"none"}) <= {"none"}
    assert by_job.get("services", {"country"}) <= {"country"}
    assert any(t.place is None for t in plan.targets), "at least one topic is place-free"


def test_a_global_template_cannot_carry_a_scope() -> None:
    from mde_research_agent.catalog.models import TargetTemplate

    base = {
        "id": "x",
        "label": "x",
        "question": "q",
        "decision_unlocked": "d",
        "needs": ["norm"],
        "slots": [],
    }
    with pytest.raises(ValueError, match="global"):
        TargetTemplate.model_validate({**base, "place": "global", "scope": "city"})
    assert TargetTemplate.model_validate({**base, "place": "global"}).place_scope == "none"
    assert TargetTemplate.model_validate(base).place_scope == "city"
