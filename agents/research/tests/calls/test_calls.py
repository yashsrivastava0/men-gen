import asyncio
from typing import Any

import pytest
from support.testing import OverrideGateway, make_client

from mde_core.errors import BudgetExceeded, CassetteMissing, ProviderError
from mde_llm_gateway import to_strict_schema
from mde_research_agent.calls import assemble, check, gaps, plan, queries
from mde_research_agent.calls.base import CallRunner
from mde_research_agent.calls.extract import (
    SPECS as EXTRACT,
)
from mde_research_agent.calls.extract import (
    ExtractIn,
    extract_out_model,
    mock_extract,
)
from mde_research_agent.calls.registry import all_specs
from mde_research_agent.core.quotes import verify_quote
from mde_research_agent.domain.atoms import KINDS
from mde_research_agent.ids import CounterIds

SPECS = all_specs()


def runner(gateway: Any) -> CallRunner:
    return CallRunner(gateway, run_id="rrn_test", ids=CounterIds())


def test_every_spec_is_registered_versioned_and_strict_schema_safe() -> None:
    ids = [s.id for s in SPECS]
    assert len(ids) == len(set(ids)) == 17 and all(i.startswith("rsch.") for i in ids)
    for s in SPECS:
        to_strict_schema(s.spec.output_model)  # pyright: ignore[reportArgumentType]
        assert s.spec.version >= 1 and s.spec.mock_handler and s.fallback
        assert not s.spec.tools, "calls that read web or Brief text have no tools"
        assert s.spec.untrusted_inputs, s.id


def test_the_checker_has_its_own_route_and_writers_do_not_use_it() -> None:
    assert [s.id for s in SPECS if s.spec.route == "standard"] == ["rsch.check"]
    assert check.SPEC.checker and not any(s.checker for s in SPECS if s.id != "rsch.check")


def _extract_in(job: str = "features") -> ExtractIn:
    return ExtractIn(
        job=job,
        question="Which yoga apps exist and what features do they offer?",
        anchor="yoga studio app",
        place="Bengaluru",
        language="en",
        focus="f",
        kinds=list(EXTRACT[job].spec.output_model.model_fields),  # pyright: ignore[reportOptionalMemberAccess]
        want_kinds=[],
        kind_guides=["competitor: x"],
        slots=["competitors"],
        page_title="Title",
        page_domain="example.com",
        page_published="2026-05",
        passages="Acme Yoga lets members book classes, join a waitlist and buy class packs by card. The app sends reminders before every class and supports several studios. Pricing starts low for members who commit to a monthly plan.",
        retry_keys=[],
    )


@pytest.mark.parametrize("job", sorted(EXTRACT))
async def test_every_extract_spec_renders_and_mock_runs_through_the_real_gateway(job: str) -> None:
    client = make_client()
    res = await runner(client).run(EXTRACT[job], _extract_in(job))
    assert not res.used_fallback, res.reason
    dumped = res.output.model_dump()
    assert set(dumped) == set(EXTRACT[job].spec.output_model.model_fields)  # pyright: ignore[reportOptionalMemberAccess]


def test_mock_extraction_quotes_are_verbatim_substrings_of_the_passages() -> None:
    inp = _extract_in("features")
    out = mock_extract(inp).model_dump()
    atoms = [a for arr in out.values() for a in arr]
    assert atoms
    for a in atoms:
        assert verify_quote(inp.passages, a["quote"]).ok, a["quote"]
        assert len(a["quote"].split()) <= 25 and len(a["claim"].split()) <= 40


def test_mock_extraction_is_deterministic_and_valid_for_every_job() -> None:
    for job in EXTRACT:
        inp = _extract_in(job)
        a, b = mock_extract(inp), mock_extract(inp)
        assert a == b
        extract_out_model(job).model_validate(a.model_dump())


async def test_the_planner_call_mock_returns_the_briefs_own_values() -> None:
    inp = plan.PlanIn(
        language="en",
        summary="s",
        category="yoga studio app",
        subcategory="",
        business_model="",
        country="India",
        country_code="in",
        region="",
        cities=["Bengaluru"],
        geo_uncertain=False,
        locales=["en"],
        features=[],
        roles=[],
        entities=[],
        modules=[],
        facts=[],
        requests=[],
        existing=[],
        declined=[],
    )
    res = await runner(make_client()).run(plan.SPEC, inp)
    assert (
        not res.used_fallback
        and res.output.place.country_code == "in"
        and res.output.extra_targets == []
    )


async def test_an_invalid_country_code_triggers_the_deterministic_fallback_and_counts_as_a_failure() -> (
    None
):
    inp = plan.PlanIn(
        language="en",
        summary="s",
        category="a b",
        subcategory="",
        business_model="",
        country="",
        country_code="",
        region="",
        cities=[],
        geo_uncertain=False,
        locales=[],
        features=[],
        roles=[],
        entities=[],
        modules=[],
        facts=[],
        requests=[],
        existing=[],
        declined=[],
    )
    bad = plan.PlanOut(
        place=plan.PlaceOut(country_code="zz", country_name="Nowhere", cities=[]),
        category_phrase="x",
        extra_targets=[],
        discovery_names=[],
    )
    gw = OverrideGateway(make_client(), {"rsch.plan": lambda i: bad})
    res = await runner(gw).run(plan.SPEC, inp)
    assert (
        res.used_fallback
        and res.failed
        and "country" in res.reason
        and res.output.place.country_code is None
    )


@pytest.mark.parametrize("exc", [ProviderError("boom"), TimeoutError("slow")])
async def test_model_side_failures_fall_back_and_count(exc: Exception) -> None:
    gw = OverrideGateway(make_client(), {"rsch.write_queries": lambda i: exc})
    inp = _query_in()
    res = await runner(gw).run(queries.SPEC, inp)
    assert res.used_fallback and res.failed and res.output.variants


@pytest.mark.parametrize("exc", [BudgetExceeded("cap"), CassetteMissing("none")])
async def test_budget_stops_and_missing_cassettes_are_not_hidden_by_fallbacks(
    exc: Exception,
) -> None:
    gw = OverrideGateway(make_client(), {"rsch.write_queries": lambda i: exc})
    with pytest.raises(type(exc)):
        await runner(gw).run(queries.SPEC, _query_in())


@pytest.mark.parametrize(
    ("mode", "tokens", "failed"),
    [("live", (0, 0), True), ("live", (5, 7), False), ("mock", (0, 0), False)],
)
async def test_a_zero_token_answer_counts_as_a_failure_only_when_billable(
    mode: str, tokens: tuple[int, int], failed: bool
) -> None:
    out = queries.deterministic_variants(_query_in())
    gw = OverrideGateway(
        make_client(), {"rsch.write_queries": lambda i: out}, mode=mode, tokens=tokens
    )
    res = await runner(gw).run(queries.SPEC, _query_in())
    assert res.failed is failed and (res.reason == "zero_token") is failed and not res.used_fallback


def _query_in() -> queries.QueryIn:
    return queries.QueryIn(
        job="features",
        question="q",
        anchors=["yoga studio app"],
        context="yoga studio app",
        place="Bengaluru",
        languages=["kn"],
        local_terms=["ಯೋಗ"],
        local_intent=["ಶುಲ್ಕ"],
        intent=["features", "pricing", "comparison"],
        entity=None,
        earlier=[],
        want=3,
        round=1,
        focus_terms=["waitlist"],
        seed_names=["Acme"],
    )


def test_deterministic_variants_never_repeat_and_follow_up_on_focus_terms() -> None:
    first = queries.deterministic_variants(_query_in())
    assert {v.style for v in first.variants} >= {"descriptive", "local"}
    assert sum(v.seed for v in first.variants) <= 1
    again = _query_in().model_copy(update={"earlier": [v.text for v in first.variants], "round": 2})
    second = queries.deterministic_variants(again)
    assert not {v.text for v in second.variants} & {v.text for v in first.variants}
    assert all("waitlist" in v.text for v in second.variants[:1])


def test_gap_followups_differ_from_earlier_queries_and_cap_at_two_per_topic() -> None:
    topic = gaps.GapTopic(
        target_id="T-01",
        job="services",
        anchors=["online payments"],
        context="yoga studio app",
        place="Bengaluru",
        intent=["api", "sandbox", "fees"],
        missing=["providers: need 2, found 1"],
        missing_terms=["api", "authentication", "sandbox", "webhook"],
        earlier=["online payments api Bengaluru"],
        entities=[gaps.EntityRef(index=0, name="Acme Pay")],
    )
    out = gaps.deterministic_followups(gaps.GapIn(topics=[topic]))
    assert 1 <= len(out.followups) <= 2
    assert out.followups[0].entity_index == 0
    assert all(f.text.casefold() != "online payments api bengaluru" for f in out.followups)
    stray = gaps.GapOut(
        followups=[gaps.Followup(target_id="T-99", text="x", language="en", exact=False)]
    )
    assert gaps.validate(gaps.GapIn(topics=[topic]), stray)


def test_assembly_orders_by_prevalence_and_impact_and_notes_come_from_data() -> None:
    items = [
        assemble.ItemIn(
            id="RI-1",
            kind="rule",
            label="a",
            claim="c",
            level="limited_evidence",
            impact="high",
            flags=[],
        ),
        assemble.ItemIn(
            id="RI-2",
            kind="competitor",
            label="b",
            claim="c",
            level="core",
            impact="normal",
            flags=[],
        ),
        assemble.ItemIn(
            id="RI-3",
            kind="competitor",
            label="c",
            claim="c",
            level="core",
            impact="high",
            flags=["unchecked"],
        ),
    ]
    out = assemble.deterministic_assembly(
        assemble.AssembleIn(
            language="en", lanes=[assemble.LaneIn(lane="blueprint", limit=2, items=items)]
        )
    )
    assert out.lanes[0].order == ["RI-3", "RI-2"] or out.lanes[0].order == ["RI-2", "RI-3"]
    assert len(out.lanes[0].order) == 2 and all(
        n.use_note and len(n.use_note.split()) <= 15 for n in out.lanes[0].notes
    )
    bad = assemble.AssembleOut(
        lanes=[assemble.LaneOut(lane="blueprint", order=["RI-99"], notes=[])]
    )
    assert assemble.validate(
        assemble.AssembleIn(
            language="en", lanes=[assemble.LaneIn(lane="blueprint", limit=2, items=items)]
        ),
        bad,
    )


def test_the_checker_heuristic_and_its_unchecked_fallback() -> None:
    inp = check.CheckIn(
        items=[
            check.CheckItem(
                id="a",
                claim="Settlement takes two business days",
                quote="Settlement takes two business days after capture",
            ),
            check.CheckItem(
                id="b",
                claim="Fees are two percent",
                quote="Our office is in the city centre near the park",
            ),
        ]
    )
    verdicts = {v.id: v.verdict for v in check.heuristic_check(inp).verdicts}
    assert verdicts["a"] == "supported" and verdicts["b"] == "not_supported"
    assert check.unchecked(inp).verdicts == []


def test_all_kinds_are_covered_by_some_job() -> None:
    from mde_research_agent.calls.extract import JOB_KINDS

    assert {k for ks in JOB_KINDS.values() for k in ks} == set(KINDS)
    _ = asyncio  # silence unused import in some configurations
