"""Tests of ``cmp.seed_research``: schema, goldens, real mock pipeline, adapter, must-not cases."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.seed_research import mock
from mde_compass.calls.seed_research.adapter import SeedResearchAdapter, build_input
from mde_compass.calls.seed_research.fallback import fallback
from mde_compass.calls.seed_research.rules import normalize, validate
from mde_compass.calls.seed_research.schema import (
    FeatureIn,
    GeoIn,
    GlossaryIn,
    SeedResearchInput,
    SeedResearchOutput,
    WireHints,
    WireRequest,
)
from mde_compass.calls.seed_research.spec import SPEC
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief, FeatureRecord, GlossaryRecord, ReferenceRecord
from mde_compass.domain.common import BriefItem
from mde_compass.domain.research import ResearchRequest
from mde_compass.engine.ports import SlotView, TurnContext
from mde_core.errors import ProviderError
from mde_llm_gateway import to_strict_schema

EXAMPLES = sorted(
    (Path(__file__).parents[3] / "src/mde_compass/calls/seed_research/examples").glob("*.json")
)


def _load(name: str) -> tuple[SeedResearchInput, SeedResearchOutput]:
    data = json.loads((EXAMPLES[0].parent / f"{name}.json").read_text(encoding="utf-8"))
    return SeedResearchInput.model_validate(data["input"]), SeedResearchOutput.model_validate(
        data["output"]
    )


def _inp(**changes: object) -> SeedResearchInput:
    return _load("en_food_pune")[0].model_copy(update=changes)


def _req(**changes: object) -> WireRequest:
    base = WireRequest(
        topic="Local food ordering apps in Pune",
        question="Which food ordering apps do regular customers in Pune use?",
        decision_unlocked="Which habits the app must fit",
        entity="food ordering apps",
        geography="Pune, India",
        source_languages=["en"],
        terms=["food ordering"],
        references=[],
        prefer_sources=["app store listings"],
        avoid=[],
        depth="standard",
        priority="high",
        shown_to_user_as="Who else serves Pune",
    )
    return base.model_copy(update=changes)


def _out(*requests: WireRequest, hints: WireHints | None = None) -> SeedResearchOutput:
    return SeedResearchOutput(
        requests=list(requests),
        hints=hints
        or WireHints(disambiguated_names=[], synonyms=[], local_competitors=[], languages=["en"]),
    )


def _ctx(language: str = "en") -> TurnContext:
    return TurnContext(session_id="ses_1", tenant_id="t1", language=language)


def test_three_examples() -> None:
    assert [p.stem for p in EXAMPLES] == [
        "en_food_pune",
        "hi_clinic_indore",
        "hinglish_kirana_jaipur",
    ]


def test_schemas_are_strict_safe() -> None:
    to_strict_schema(SeedResearchInput, max_depth=5)
    to_strict_schema(SeedResearchOutput, max_depth=5)


@pytest.mark.parametrize("name", [p.stem for p in EXAMPLES])
def test_golden_examples(name: str) -> None:
    inp, out = _load(name)
    assert mock.handle(inp) == out == fallback(inp)
    assert validate(inp, out) == []
    assert 3 <= len(out.requests) <= 8
    assert SeedResearchOutput.model_validate_json(out.model_dump_json()) == out
    # normalize leaves a valid fallback unchanged
    assert normalize(inp, out).requests == out.requests


@pytest.mark.parametrize("name", [p.stem for p in EXAMPLES])
async def test_real_mock_pipeline(name: str) -> None:
    inp, out = _load(name)
    res = await CallRunner(make_client([SPEC])).run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output == out


@pytest.mark.parametrize("name", [p.stem for p in EXAMPLES])
def test_fallback_requests_are_specific_and_carry_exclusions(name: str) -> None:
    inp, out = _load(name)
    for r in out.requests:
        assert r.geography.strip() and r.entity.strip() and [t for t in r.terms if t.strip()]
        assert all(e in r.avoid for e in inp.exclusions)
        ResearchRequest(id="RR-001", **dict(r.model_dump().items()))


def test_fallback_without_geography_uses_global_and_low_priority() -> None:
    inp = _inp(geography=GeoIn(country="", city="", region=""), locale_country="")
    out = fallback(inp)
    assert all(r.geography == "global" and r.priority == "low" for r in out.requests)


def test_fallback_uses_locale_country_when_the_brief_has_no_geography() -> None:
    inp = _inp(geography=GeoIn(country="", city="", region=""), locale_country="IN")
    out = fallback(inp)
    assert all(r.geography == "IN" for r in out.requests)
    assert any(r.priority == "high" for r in out.requests)


def test_fallback_minimum_and_maximum_counts() -> None:
    bare = SeedResearchInput(
        language="en",
        category="",
        subcategory="",
        geography=GeoIn(country="", city="", region=""),
        locale_country="",
        audience=[],
        business_model="",
        references=[],
        glossary=[],
        exclusions=[],
        unknowns=[],
        features=[],
        families=[],
        languages=[],
    )
    out = fallback(bare)
    assert len(out.requests) >= 3 and validate(bare, out) == []
    many = _inp(references=[f"App{i}" for i in range(20)])
    assert len(fallback(many).requests) == 8


def test_fallback_never_names_a_competitor_the_user_did_not_give() -> None:
    out = fallback(_inp(references=[]))
    assert out.hints.local_competitors == [] and all(r.references == [] for r in out.requests)
    out = fallback(_inp())
    assert out.hints.local_competitors == ["Zomato"]


# --- the "must not" cases ----------------------------------------------------------------------------


def test_requests_lacking_geography_entity_or_terms_are_repaired_or_dropped() -> None:
    inp = _inp()
    out = normalize(
        inp,
        _out(
            _req(geography=""),  # repaired from the Brief
            _req(
                topic="Rules", question="Which rules apply?", entity="", terms=["rules"]
            ),  # entity from category
            _req(topic="Payments", question="How do people pay?", terms=[]),  # terms from the Brief
            _req(topic="", question="No topic"),  # dropped
        ),
    )
    assert len(out.requests) == 3
    for r in out.requests:
        assert r.geography.strip() and r.entity.strip() and r.terms
    assert out.requests[0].geography == "Pune, Maharashtra, India"
    assert out.requests[1].entity == "restaurant ordering"


def test_unrepairable_request_is_dropped() -> None:
    bare = SeedResearchInput(
        language="en",
        category="",
        subcategory="",
        geography=GeoIn(country="", city="", region=""),
        locale_country="",
        audience=[],
        business_model="",
        references=[],
        glossary=[],
        exclusions=[],
        unknowns=[],
        features=[],
        families=[],
        languages=[],
    )
    out = normalize(bare, _out(_req(entity="", terms=[]), _req(geography="")))
    # the first has no category to repair entity or terms from; the second only needs a geography
    assert len(out.requests) == 1
    assert out.requests[0].geography == "global" and out.requests[0].priority == "low"


def test_invented_competitors_are_dropped_everywhere() -> None:
    inp = _inp()
    hints = WireHints(
        disambiguated_names=[],
        synonyms=[],
        local_competitors=["Zomato", "Swiggy", "zomato"],
        languages=["en"],
    )
    out = normalize(
        inp,
        _out(
            _req(entity="Swiggy", references=["Swiggy", "Zomato"]),
            _req(
                topic="Other",
                question="Another?",
                entity="food ordering apps",
                references=["Uber Eats"],
            ),
            hints=hints,
        ),
    )
    assert out.hints.local_competitors == ["Zomato"]
    assert (
        out.requests[0].entity == "restaurant ordering"
    )  # invented name replaced by the user's category
    assert out.requests[0].references == ["Zomato"]
    assert out.requests[1].references == []
    flat = json.dumps(out.model_dump(), ensure_ascii=False)
    assert "Swiggy" not in flat and "Uber" not in flat


def test_exclusions_are_always_in_avoid() -> None:
    out = normalize(
        _inp(), _out(_req(avoid=[]), _req(topic="B", question="Q?", avoid=["no gambling"]))
    )
    for r in out.requests:
        assert r.avoid[0] == "no alcohol"
    assert out.requests[1].avoid == ["no alcohol", "no gambling"]


def test_duplicates_are_dropped_and_count_is_capped() -> None:
    out = normalize(
        _inp(), _out(_req(), _req(), _req(topic="Same topic", question=_req().question))
    )
    assert len(out.requests) == 1
    many = [_req(topic=f"Topic {i}", question=f"Question {i}?") for i in range(30)]
    assert len(normalize(_inp(), _out(*many)).requests) == 12


def test_validator_wants_three_usable_requests() -> None:
    assert validate(_inp(), _out(_req())) != []
    assert validate(_inp(), _out(*[_req(topic=f"T{i}", question=f"Q{i}?") for i in range(3)])) == []


# --- the adapter -------------------------------------------------------------------------------------


def _brief() -> Brief:
    brief = Brief()
    brief.idea["category"] = BriefItem(value="food ordering", status="confirmed")
    brief.idea["business_model"] = BriefItem(value="a small fee on each order", status="assumed")
    brief.context["geo"] = BriefItem(
        value={"country": "India", "city": "Pune", "region": "Maharashtra"}, status="confirmed"
    )
    brief.audience["segments"] = BriefItem(
        value="regular customers, office workers", status="confirmed"
    )
    brief.references.append(ReferenceRecord(label="Zomato"))
    brief.glossary.append(GlossaryRecord(term="thali", synonyms=["meal plate"]))
    brief.scope.append(FeatureRecord(id="FTR-001", name="Order tracking", intent="see my order"))
    brief.meta.locales = ["en-IN", "hi"]
    return brief


def _ctx_with_exclusion() -> TurnContext:
    ctx = _ctx()
    ctx.slots.append(
        SlotView(slot_id="scope.must_not", value=["no alcohol", "no gambling"], state="confirmed")
    )
    return ctx


def test_build_input_from_brief_and_slots() -> None:
    inp = build_input(_ctx_with_exclusion(), _brief(), load_catalog())
    assert inp.category == "food ordering" and inp.geography.city == "Pune"
    assert inp.audience == ["regular customers", "office workers"]
    assert inp.references == ["Zomato"] and inp.exclusions == ["no alcohol", "no gambling"]
    assert inp.locale_country == "IN" and inp.languages[:2] == ["en", "hi"]
    assert inp.features == [FeatureIn(name="Order tracking", intent="see my order")]
    assert inp.glossary == [GlossaryIn(term="thali", synonyms=["meal plate"])]
    assert inp.unknowns  # slots research needs that the Brief does not have yet


def _model_answer() -> SeedResearchOutput:
    hints = WireHints(
        disambiguated_names=[],
        synonyms=["thali"],
        local_competitors=["Swiggy", "Zomato"],
        languages=["en"],
    )
    requests = [
        _req(topic=f"Topic {i}", question=f"Question {i}?", avoid=["no gambling"]) for i in range(4)
    ]
    return _out(*requests, hints=hints)


async def test_adapter_returns_validated_domain_requests() -> None:
    adapter = SeedResearchAdapter(CallRunner(ScriptedGateway(_model_answer())), load_catalog())
    res = await adapter.seed(_ctx_with_exclusion(), _brief())
    assert [r.id for r in res.requests] == ["RR-001", "RR-002", "RR-003", "RR-004"]
    assert all(isinstance(r, ResearchRequest) for r in res.requests)
    assert all(r.avoid[:2] == ["no alcohol", "no gambling"] for r in res.requests)
    assert res.hints.local_competitors == ["Zomato"]


async def test_adapter_falls_back_when_the_model_gives_too_few_valid_requests() -> None:
    bad = _out(_req(topic=""), _req(question=""))
    adapter = SeedResearchAdapter(CallRunner(ScriptedGateway(bad)), load_catalog())
    res = await adapter.seed(_ctx_with_exclusion(), _brief())
    assert 3 <= len(res.requests) <= 8
    assert all(r.geography and r.entity and r.terms for r in res.requests)
    assert res.hints.local_competitors == ["Zomato"]


async def test_adapter_falls_back_on_gateway_error() -> None:
    adapter = SeedResearchAdapter(
        CallRunner(ScriptedGateway(ProviderError("boom"))), load_catalog()
    )
    res = await adapter.seed(_ctx_with_exclusion(), _brief())
    assert len(res.requests) >= 3


async def test_adapter_end_to_end_with_the_real_mock_gateway() -> None:
    adapter = SeedResearchAdapter(CallRunner(make_client([SPEC])), load_catalog())
    res = await adapter.seed(_ctx_with_exclusion(), _brief())
    assert len(res.requests) >= 4
    assert any("Zomato" in r.references for r in res.requests)


async def test_adapter_hindi_session() -> None:
    brief = Brief()
    brief.idea["category"] = BriefItem(value="क्लिनिक बुकिंग", status="confirmed")
    brief.context["geo"] = BriefItem(value="इंदौर", status="confirmed")
    adapter = SeedResearchAdapter(CallRunner(make_client([SPEC])), load_catalog())
    res = await adapter.seed(_ctx("hi"), brief)
    assert res.requests[0].geography == "इंदौर" and "क्लिनिक बुकिंग" in res.requests[0].terms


# --- linear time ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile", ["A" * 50_000, "a'" * 25_000, "Ab " * 16_000, "x-" * 25_000, "1." * 25_000]
)
def test_linear_time_on_hostile_input(hostile: str) -> None:
    inp = _inp(
        category=hostile,
        audience=[hostile],
        exclusions=[hostile],
        glossary=[GlossaryIn(term=hostile, synonyms=[hostile])],
    )
    started = time.monotonic()
    out = fallback(inp)
    normalize(inp, out)
    normalize(inp, _out(_req(entity=hostile, topic=hostile, question=hostile, terms=[hostile])))
    assert time.monotonic() - started < 5.0
    assert len(out.requests) >= 3
