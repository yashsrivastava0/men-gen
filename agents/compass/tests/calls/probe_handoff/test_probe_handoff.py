"""Tests of ``cmp.probe_handoff``: schema, goldens, real mock pipeline, code-decided verdicts, adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_compass.calls.base import CallRunner
from mde_compass.calls.probe_handoff import mock
from mde_compass.calls.probe_handoff.adapter import ProbeHandoffAdapter, build_digest
from mde_compass.calls.probe_handoff.fallback import fallback
from mde_compass.calls.probe_handoff.rules import evaluate, normalize_question, table_for
from mde_compass.calls.probe_handoff.schema import (
    BlockingQuestion,
    Digest,
    DigestFeature,
    Journey,
    PlanQuery,
    ProbeHandoffInput,
    ProbeHandoffOutput,
    Screen,
)
from mde_compass.calls.probe_handoff.spec import SPEC
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import (
    AppRecord,
    Brief,
    FeatureRecord,
    ReferenceRecord,
    RoleRecord,
)
from mde_compass.domain.common import BriefItem
from mde_compass.engine.ports import ProbeResult, TurnContext
from mde_core.errors import ProviderError
from mde_llm_gateway import to_strict_schema

EX_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/probe_handoff/examples"
EXAMPLES = sorted(EX_DIR.glob("*.json"))
SLOTS = frozenset(load_catalog().slots)


def _example(name: str) -> dict:
    return json.loads((EX_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _digest(**changes: object) -> Digest:
    base = ProbeHandoffInput.model_validate(_example("en_research_food_pune")["input"]).digest
    return base.model_copy(update=changes)


def _inp(kind: str = "research", language: str = "en", **changes: object) -> ProbeHandoffInput:
    return ProbeHandoffInput(kind=kind, language=language, digest=_digest(**changes))  # type: ignore[arg-type]


def _q(i: int = 0, **changes: object) -> PlanQuery:
    base = PlanQuery(
        query=f"food ordering {i}", region="Pune", entity="food ordering apps", terms=["food"]
    )
    return base.model_copy(update=changes)


def _out(**changes: object) -> ProbeHandoffOutput:
    base = ProbeHandoffOutput(
        can_start=True,
        plan_queries=[],
        journeys=[],
        screens=[],
        tone="",
        direction="",
        blocking_questions=[],
    )
    return base.model_copy(update=changes)


def _screens(n: int, first: bool = True) -> list[Screen]:
    return [Screen(name=f"Screen {i}", journey="J", first_release=first) for i in range(n)]


def _journey() -> Journey:
    return Journey(name="Order a meal", role="customer", steps=["open menu", "pick dish"])


def _ctx(language: str = "en") -> TurnContext:
    return TurnContext(session_id="ses_1", tenant_id="t1", language=language)


# --- schema and goldens ------------------------------------------------------------------------------


def test_examples_cover_the_three_kinds_and_languages() -> None:
    kinds = {_example(p.stem)["input"]["kind"] for p in EXAMPLES}
    langs = {_example(p.stem)["input"]["language"] for p in EXAMPLES}
    assert kinds == {"research", "blueprint", "design"} and {"hi", "hi-Latn"} <= langs


def test_schemas_are_strict_safe() -> None:
    to_strict_schema(ProbeHandoffInput, max_depth=5)
    to_strict_schema(ProbeHandoffOutput, max_depth=5)


@pytest.mark.parametrize("name", [p.stem for p in EXAMPLES])
def test_golden_examples(name: str) -> None:
    data = _example(name)
    inp = ProbeHandoffInput.model_validate(data["input"])
    out = ProbeHandoffOutput.model_validate(data["output"])
    assert mock.handle(inp) == out == fallback(inp)
    verdict = evaluate(inp, out, SLOTS)
    assert verdict.passed is data["expected_passed"]
    assert [g.slot_id for g in verdict.gaps] == data["expected_gap_slots"]
    assert ProbeHandoffOutput.model_validate_json(out.model_dump_json()) == out


@pytest.mark.parametrize("name", [p.stem for p in EXAMPLES])
async def test_real_mock_pipeline(name: str) -> None:
    data = _example(name)
    inp = ProbeHandoffInput.model_validate(data["input"])
    res = await CallRunner(make_client([SPEC])).run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output == ProbeHandoffOutput.model_validate(data["output"])


# --- pass or fail is computed by code ----------------------------------------------------------------


def test_a_model_claiming_it_passed_with_three_queries_is_overruled() -> None:
    out = _out(can_start=True, plan_queries=[_q(i) for i in range(3)])
    verdict = evaluate(_inp("research"), out, SLOTS)
    assert not verdict.passed and verdict.reasons
    assert any("3 complete queries" in r for r in verdict.reasons)
    assert any(g.slot_id is None and "3" in g.question for g in verdict.gaps)


def test_research_needs_eight_complete_queries() -> None:
    ok = _out(plan_queries=[_q(i) for i in range(8)])
    assert evaluate(_inp("research"), ok, SLOTS).passed
    assert not evaluate(
        _inp("research"), _out(plan_queries=[_q(i) for i in range(7)]), SLOTS
    ).passed
    for broken in (_q(9, region=" "), _q(9, entity=""), _q(9, terms=["", " "])):
        out = _out(plan_queries=[*(_q(i) for i in range(8)), broken])
        assert not evaluate(_inp("research"), out, SLOTS).passed


def test_research_fails_when_the_digest_has_no_geography_even_with_eight_queries() -> None:
    out = _out(plan_queries=[_q(i) for i in range(10)])  # the planner guessed a region
    verdict = evaluate(_inp("research", geography=""), out, SLOTS)
    assert not verdict.passed
    assert [g.slot_id for g in verdict.gaps] == ["context.geo"]


def test_blueprint_rules() -> None:
    ok = _out(journeys=[_journey()], screens=_screens(3))
    assert evaluate(_inp("blueprint"), ok, SLOTS).passed
    assert not evaluate(
        _inp("blueprint"), _out(journeys=[_journey()], screens=_screens(2)), SLOTS
    ).passed
    assert not evaluate(
        _inp("blueprint"), _out(journeys=[_journey()], screens=_screens(5, first=False)), SLOTS
    ).passed
    no_steps = Journey(name="x", role="r", steps=[" "])
    assert not evaluate(
        _inp("blueprint"), _out(journeys=[no_steps], screens=_screens(3)), SLOTS
    ).passed
    assert not evaluate(_inp("blueprint"), _out(journeys=[], screens=_screens(3)), SLOTS).passed


def test_blueprint_reports_missing_roles_and_intents() -> None:
    out = _out(can_start=True, journeys=[_journey()], screens=_screens(4))
    no_roles = evaluate(_inp("blueprint", roles=[]), out, SLOTS)
    assert not no_roles.passed and "roles.list" in [g.slot_id for g in no_roles.gaps]
    feature = DigestFeature(name="Coupons", intent="", priority="must", status="in")
    digest_features = [*_digest().features, feature]
    no_intent = evaluate(_inp("blueprint", features=digest_features), out, SLOTS)
    assert not no_intent.passed
    gap = next(g for g in no_intent.gaps if g.slot_id == "scope.features")
    assert "Coupons" in gap.question
    # a feature that is not "in" needs no intent
    later = DigestFeature(name="Gifting", intent="", priority="later", status="later")
    assert evaluate(_inp("blueprint", features=[*_digest().features, later]), out, SLOTS).passed
    no_features = evaluate(_inp("blueprint", features=[]), out, SLOTS)
    assert not no_features.passed


def test_design_rules() -> None:
    ok = _out(tone="warm", direction="A warm look for customers.")
    assert evaluate(_inp("design"), ok, SLOTS).passed
    assert not evaluate(_inp("design"), _out(tone="warm", direction=""), SLOTS).passed
    assert not evaluate(_inp("design"), _out(tone=" ", direction="x"), SLOTS).passed


def test_design_reports_missing_audience_locales_and_tone() -> None:
    ok = _out(can_start=True, tone="warm", direction="A warm look.")
    bare = evaluate(
        _inp("design", audience=[], locales=[], tone_words=[], brand_signals=[]), ok, SLOTS
    )
    assert not bare.passed
    assert {g.slot_id for g in bare.gaps} == {"audience.segments", "locales", "look.tone_words"}
    only_brand = evaluate(_inp("design", tone_words=[], brand_signals=["logo"]), ok, SLOTS)
    assert only_brand.passed  # a brand signal is enough


# --- the model's own blocking questions ----------------------------------------------------------------


def test_model_questions_become_gaps_mapped_to_slots() -> None:
    questions = [
        BlockingQuestion(
            question="Which city will it launch in?",
            slot_id=None,
            fix="ask",
            suggested_value_json=None,
        ),
        BlockingQuestion(
            question="Bad slot id", slot_id="nope.nothing", fix="ask", suggested_value_json=None
        ),
        BlockingQuestion(
            question="Default language?",
            slot_id="locales",
            fix="assume",
            suggested_value_json='["en"]',
        ),
        BlockingQuestion(
            question="Tone?",
            slot_id="look.tone_words",
            fix="assume",
            suggested_value_json="not json",
        ),
        BlockingQuestion(question="   ", slot_id=None, fix="ask", suggested_value_json=None),
    ]
    out = _out(plan_queries=[_q(i) for i in range(8)], blocking_questions=questions)
    verdict = evaluate(_inp("research"), out, SLOTS)
    by_q = {g.question: g for g in verdict.gaps}
    assert by_q["Which city will it launch in?"].slot_id == "context.geo"
    assert by_q["Bad slot id"].slot_id is None
    assert by_q["Default language?"].fix == "assume"
    assert json.loads(by_q["Default language?"].suggested_value_json or "") == ["en"]
    assert by_q["Tone?"].fix == "ask" and by_q["Tone?"].suggested_value_json is None
    assert len(verdict.gaps) == 4
    assert verdict.passed  # questions do not decide the verdict, the plan does


def test_gaps_are_deduplicated_per_slot() -> None:
    model = BlockingQuestion(
        question="Where?", slot_id="context.geo", fix="ask", suggested_value_json=None
    )
    verdict = evaluate(_inp("research", geography=""), _out(blocking_questions=[model]), SLOTS)
    assert [g.slot_id for g in verdict.gaps] == ["context.geo"]
    assert (
        verdict.gaps[0].question == "Where?"
    )  # the model's wording wins, the derived one is dropped


def test_normalize_question_handles_hostile_json_and_text() -> None:
    table = table_for("en")
    deep = BlockingQuestion(
        question="Where is it?", slot_id=None, fix="assume", suggested_value_json="[" * 50_000
    )
    got = normalize_question(deep, table, SLOTS)
    assert got is not None and got.fix == "ask" and got.suggested_value_json is None


# --- the fallback planners report gaps instead of guessing --------------------------------------------


def test_fallback_research_without_geography_plans_nothing_and_asks() -> None:
    out = fallback(_inp("research", geography=""))
    assert out.plan_queries == [] and not out.can_start
    assert [q.slot_id for q in out.blocking_questions] == ["context.geo"]


def test_fallback_research_queries_are_complete_and_use_only_digest_words() -> None:
    out = fallback(_inp("research"))
    assert len(out.plan_queries) >= 8
    for q in out.plan_queries:
        assert q.region == "Pune, Maharashtra, India" and q.entity and q.terms
    assert any("Zomato" in q.query for q in out.plan_queries)
    flat = json.dumps(fallback(_inp("research", references=[])).model_dump())
    assert "Zomato" not in flat


def test_fallback_blueprint_without_roles_reports_roles() -> None:
    out = fallback(_inp("blueprint", roles=[]))
    assert out.journeys == []
    assert "roles.list" in [q.slot_id for q in out.blocking_questions]


def test_fallback_design_without_tone_reports_it() -> None:
    inp = _inp("design", tone_words=[], brand_signals=[])
    out = fallback(inp)
    assert out.tone == "" and out.direction == ""
    verdict = evaluate(inp, out, SLOTS)
    assert not verdict.passed and verdict.gaps[0].slot_id == "look.tone_words"


# --- the adapter --------------------------------------------------------------------------------------


def _brief(*, geo: bool = True, roles: bool = True, tone: bool = True) -> Brief:
    brief = Brief()
    brief.idea["summary"] = BriefItem(value="order food from my restaurant", status="confirmed")
    brief.idea["category"] = BriefItem(value="food ordering", status="confirmed")
    if geo:
        brief.context["geo"] = BriefItem(
            value={"city": "Pune", "country": "India"}, status="confirmed"
        )
    brief.audience["segments"] = BriefItem(value="regular customers", status="confirmed")
    brief.apps.append(AppRecord(id="APP-001", name="Customer app", purpose="order food"))
    if roles:
        brief.roles.append(RoleRecord(id="ROL-001", name_in_user_words="customer", goal="order"))
    brief.scope += [
        FeatureRecord(id=f"FTR-00{i}", name=f"Feature {i}", intent=f"do thing {i}", priority="must")
        for i in range(1, 4)
    ]
    brief.references.append(ReferenceRecord(label="Zomato"))
    brief.meta.locales = ["en", "hi"]
    if tone:
        brief.experience["tone_words"] = BriefItem(value=["warm", "friendly"], status="confirmed")
    return brief


def test_build_digest() -> None:
    d = build_digest(_ctx(), _brief(), load_catalog())
    assert d.geography == "Pune, India" and d.category == "food ordering"
    assert d.audience == ["regular customers"] and d.tone_words == ["warm", "friendly"]
    assert [f.name for f in d.features] == ["Feature 1", "Feature 2", "Feature 3"]
    assert d.locales == ["en", "hi"] and d.references == ["Zomato"]


async def test_adapter_overrules_a_model_that_claims_to_pass() -> None:
    claim = _out(can_start=True, plan_queries=[_q(i) for i in range(3)])
    adapter = ProbeHandoffAdapter(CallRunner(ScriptedGateway(claim)), load_catalog())
    res = await adapter.probe(_ctx(), "research", _brief())
    assert isinstance(res, ProbeResult) and res.kind == "research"
    assert res.passed is False and res.gaps and len(res.plan) == 3


async def test_adapter_passes_a_real_plan_and_keeps_model_questions() -> None:
    q = BlockingQuestion(
        question="Which languages?", slot_id="locales", fix="assume", suggested_value_json='["hi"]'
    )
    plan = _out(plan_queries=[_q(i) for i in range(9)], blocking_questions=[q])
    adapter = ProbeHandoffAdapter(CallRunner(ScriptedGateway(plan)), load_catalog())
    res = await adapter.probe(_ctx(), "research", _brief())
    assert res.passed
    assert res.gaps[0].slot_id == "locales" and res.gaps[0].fix == "assume"
    assert res.gaps[0].suggested_value == ["hi"]


async def test_adapter_reports_gaps_when_the_brief_lacks_geography_roles_or_tone() -> None:
    catalog = load_catalog()
    full_plan = [
        _out(plan_queries=[_q(i) for i in range(9)]),
        _out(journeys=[_journey()], screens=_screens(4)),
        _out(tone="warm", direction="A warm look."),
    ]
    gateway = ScriptedGateway(*full_plan)
    adapter = ProbeHandoffAdapter(CallRunner(gateway), catalog)
    brief = _brief(geo=False, roles=False, tone=False)
    results = [await adapter.probe(_ctx(), k, brief) for k in ("research", "blueprint", "design")]
    assert [r.passed for r in results] == [False, False, False]
    assert [g.slot_id for g in results[0].gaps] == ["context.geo"]
    assert "roles.list" in [g.slot_id for g in results[1].gaps]
    assert "look.tone_words" in [g.slot_id for g in results[2].gaps]


async def test_adapter_falls_back_to_the_deterministic_planner_on_gateway_error() -> None:
    adapter = ProbeHandoffAdapter(
        CallRunner(ScriptedGateway(ProviderError("boom"))), load_catalog()
    )
    res = await adapter.probe(_ctx(), "research", _brief())
    assert res.passed and len(res.plan) >= 8
    adapter = ProbeHandoffAdapter(
        CallRunner(ScriptedGateway(ProviderError("boom"))), load_catalog()
    )
    res = await adapter.probe(_ctx(), "research", _brief(geo=False))
    assert not res.passed and res.plan == [] and res.gaps[0].slot_id == "context.geo"


async def test_adapter_end_to_end_with_the_real_mock_gateway() -> None:
    adapter = ProbeHandoffAdapter(CallRunner(make_client([SPEC])), load_catalog())
    results = {
        k: await adapter.probe(_ctx(), k, _brief()) for k in ("research", "blueprint", "design")
    }  # type: ignore[arg-type]
    assert all(r.passed for r in results.values()), {k: r.gaps for k, r in results.items()}
    assert results["design"].plan[0].startswith("Tone:")


async def test_adapter_hindi_session_reports_hindi_questions() -> None:
    adapter = ProbeHandoffAdapter(CallRunner(make_client([SPEC])), load_catalog())
    res = await adapter.probe(_ctx("hi"), "research", _brief(geo=False))
    assert not res.passed and "देश" in res.gaps[0].question


# --- linear time ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", ["A" * 50_000, "a'" * 25_000, "Ab " * 16_000, "{" * 50_000])
def test_linear_time_on_hostile_input(hostile: str) -> None:
    digest = _digest(
        idea=hostile,
        category=hostile,
        geography=hostile,
        audience=[hostile],
        references=[hostile],
        tone_words=[hostile],
        locales=[hostile],
    )
    started = time.monotonic()
    for kind in ("research", "blueprint", "design"):
        inp = ProbeHandoffInput(kind=kind, language="en", digest=digest)  # type: ignore[arg-type]
        out = fallback(inp)
        evaluate(inp, out, SLOTS)
        q = BlockingQuestion(
            question=hostile, slot_id=None, fix="ask", suggested_value_json=hostile
        )
        evaluate(inp, _out(blocking_questions=[q] * 20), SLOTS)
    assert time.monotonic() - started < 5.0


# --- property: the verdict is a function of the plan and the digest, never of the model's claim --------

_blank_or_text = st.sampled_from(["", " ", "x", "food ordering"])
_query = st.builds(
    PlanQuery,
    query=_blank_or_text,
    region=_blank_or_text,
    entity=_blank_or_text,
    terms=st.lists(_blank_or_text, max_size=2),
)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    queries=st.lists(_query, max_size=12),
    claim=st.booleans(),
    geography=st.sampled_from(["", "Pune"]),
)
def test_research_verdict_ignores_the_claim(
    queries: list[PlanQuery], claim: bool, geography: str
) -> None:
    inp = _inp("research", geography=geography)
    expected = (
        bool(geography)
        and len(queries) >= 8
        and all(
            q.query.strip()
            and q.region.strip()
            and q.entity.strip()
            and any(t.strip() for t in q.terms)
            for q in queries
        )
    )
    out = _out(can_start=claim, plan_queries=queries)
    assert evaluate(inp, out, SLOTS).passed is expected
    assert evaluate(inp, out.model_copy(update={"can_start": not claim}), SLOTS).passed is expected
