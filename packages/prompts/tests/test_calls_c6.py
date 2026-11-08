"""C6.plan_research: jobs and generic queries (up to max_queries_per_job each), trimmed to cap and budget."""

from __future__ import annotations

from typing import Any, cast

import pytest

from mde_contracts.calls import ALL_JOBS, BriefContext, C6Input, C6Output, OptionLite
from mde_contracts.registry import CALL_IO
from mde_contracts.research import ResearchJobId
from mde_llm_gateway.render import PromptRenderer
from mde_llm_gateway.strict_schema import to_strict_schema
from mde_prompts.calls.c6_plan_research.mock import handle
from mde_prompts.calls.c6_plan_research.plan import (
    brand_host,
    candidate_queries,
    deterministic_plan,
    feature_names,
    integration_topics,
    queries_per_job,
    topic_phrase,
    trim,
    web_jobs,
)
from mde_prompts.calls.c6_plan_research.spec import SPEC
from mde_prompts.registry import load_spec

BUDGET = 300_000


def brief(**overrides: object) -> BriefContext:
    base: dict[str, object] = {
        "app_summary": "A shop for handmade goods",
        "archetype": "commerce",
        "features": [OptionLite(id="catalog_browse", label="Browse")],
    }
    base.update(overrides)
    return BriefContext.model_validate(base)


def c6(b: BriefContext | None = None, **overrides: object) -> C6Input:
    data: dict[str, object] = {
        "brief": b or brief(),
        "depth": "light",
        "max_search_calls": 6,
        "remaining_budget_micros": BUDGET,
    }
    data.update(overrides)
    return C6Input.model_validate(data)


def test_spec_shape() -> None:
    assert SPEC.id == "C6.plan_research"
    assert SPEC.version == 2
    assert SPEC.output_model is C6Output
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "low"
    assert SPEC.tools == ()
    assert SPEC.untrusted_inputs == ()
    assert SPEC.fallback == "c6_deterministic_plan"
    assert SPEC.max_output_tokens >= 3000  # twenty queries and the reasoning before them
    assert CALL_IO["C6.plan_research"] == (C6Input, C6Output)
    assert load_spec("C6.plan_research") is SPEC


def test_output_model_converts_to_strict_schema() -> None:
    schema = to_strict_schema(C6Output)
    assert schema["additionalProperties"] is False


def test_templates_render_with_strict_undefined() -> None:
    inp = c6(
        brief(
            references=["Zed"],
            brand_url="https://zed.example",
            compliance_flags=["financial_data"],
            regions=["in"],
            roles=["customer"],
            look="clean-bright",
            login="email_phone_code",
            assumptions=["Currency: INR"],
        )
    )
    rendered = PromptRenderer().render(cast("Any", SPEC), inp)
    assert "Named reference apps: Zed" in rendered.user
    assert "Brand website: https://zed.example" in rendered.user
    assert "Compliance flags: financial_data" in rendered.user
    assert "max_search_calls: 6" in rendered.user
    assert "allow_company_name_in_queries: true" in rendered.user
    assert "At most 2 queries per job" in rendered.system
    assert "max_queries_per_job: 2" in rendered.user
    assert "untrusted_content" not in rendered.system  # no untrusted inputs in this call


def test_template_renders_for_a_bare_brief() -> None:
    rendered = PromptRenderer().render(cast("Any", SPEC), c6(brief(features=[])))
    assert "none listed" in rendered.user


def test_mock_output_validates_against_the_output_model() -> None:
    out = handle(c6(brief(compliance_flags=["financial_data"], references=["Zed"])))
    assert C6Output.model_validate(out.model_dump()) == out


def test_r1_is_always_planned_with_generic_queries() -> None:
    plan = handle(c6())
    jobs = {j.job: j for j in plan.jobs}
    assert set(jobs) == {"R1"}
    assert len(jobs["R1"].queries) == 2
    for query in jobs["R1"].queries:
        assert "online shopping" in query
        assert "handmade" not in query  # the summary text never leaks into a query


def test_r2_only_with_references() -> None:
    assert "R2" not in {j.job for j in handle(c6()).jobs}
    plan = handle(c6(brief(references=["Zed", "Quark", "Third"])))
    r2 = next(j for j in plan.jobs if j.job == "R2")
    assert r2.queries == ["Zed mobile app features overview", "Quark mobile app features overview"]


def test_r3_only_with_a_brand_url() -> None:
    assert "R3" not in {j.job for j in handle(c6()).jobs}
    plan = handle(c6(brief(brand_url="https://www.zed.example/shop")))
    r3 = next(j for j in plan.jobs if j.job == "R3")
    assert r3.queries == [
        "zed.example brand colors fonts logo tone of voice",
        "zed.example about us mission values",
    ]


def test_r4_only_with_compliance_flags() -> None:
    assert "R4" not in {j.job for j in handle(c6()).jobs}
    plan = handle(c6(brief(compliance_flags=["health_data_privacy", "needs_expert_review"])))
    r4 = next(j for j in plan.jobs if j.job == "R4")
    assert len(r4.queries) == 2  # one topic (needs_expert_review has none): two angles on it
    assert all("health data privacy and consent" in q for q in r4.queries)
    assert not any("expert" in q for q in r4.queries)


def test_r5_when_a_provider_is_implied_by_features_or_login() -> None:
    plain = brief(features=[OptionLite(id="catalog_browse", label="Browse")])
    assert "R5" not in {j.job for j in handle(c6(plain)).jobs}
    pay = brief(features=[OptionLite(id="payments", label="Pay")])
    plan = handle(c6(pay))
    r5 = next(j for j in plan.jobs if j.job == "R5")
    assert r5.queries[0] == "payment provider SDK React Native Expo compatibility"
    assert len(r5.queries) == 2
    sso = brief(login="sso")
    assert integration_topics(sso) == ["single sign-on"]


def test_integration_topics_put_sign_in_and_payments_first() -> None:
    b = brief(
        features=[
            OptionLite(id="maps", label="Maps"),
            OptionLite(id="payments", label="Pay"),
            OptionLite(id="push_notifications", label="Push"),
        ],
        login="sso",
    )
    assert integration_topics(b)[:2] == ["single sign-on", "payment provider"]


def test_at_most_two_queries_per_job_and_never_more_than_max_search_calls() -> None:
    b = brief(
        references=["A", "B", "C"],
        brand_url="https://a.example",
        compliance_flags=["health_data_privacy", "financial_data", "child_safety"],
        features=[OptionLite(id=f, label=f) for f in ("payments", "maps", "push_notifications")],
    )
    for cap in (0, 1, 3, 5, 6, 10, 50):
        plan = handle(c6(b, max_search_calls=cap, jobs_available=list(ALL_JOBS[:5])))
        assert all(len(j.queries) <= 2 for j in plan.jobs)
        assert sum(len(j.queries) for j in plan.jobs) <= cap


def test_trimming_keeps_every_job_before_any_second_query() -> None:
    b = brief(
        references=["A", "B"],
        brand_url="https://a.example",
        compliance_flags=["health_data_privacy", "financial_data"],
        features=[OptionLite(id=f, label=f) for f in ("payments", "maps")],
    )
    plan = handle(c6(b, max_search_calls=5, jobs_available=list(ALL_JOBS[:5])))
    assert {j.job for j in plan.jobs} == {"R1", "R2", "R3", "R4", "R5"}
    assert all(len(j.queries) == 1 for j in plan.jobs)


def test_the_budget_trims_the_plan() -> None:
    b = brief(compliance_flags=["financial_data"], features=[OptionLite(id="payments", label="P")])
    full = sum(len(j.queries) for j in handle(c6(b)).jobs)
    tight = sum(len(j.queries) for j in handle(c6(b, remaining_budget_micros=30_000)).jobs)
    none = handle(c6(b, remaining_budget_micros=5_000))
    assert tight == 2  # 30,000 micros / about 12,000 per search
    assert full > tight
    assert none.jobs == []


def test_jobs_available_is_respected() -> None:
    b = brief(references=["Zed"], compliance_flags=["financial_data"])
    plan = handle(c6(b, jobs_available=["R1"]))
    assert [j.job for j in plan.jobs] == ["R1"]


def test_r6_is_planned_only_when_available_and_never_uses_the_search_cap() -> None:
    plan = handle(c6(jobs_available=["R1", "R6"], max_search_calls=2))
    jobs = {j.job: j.queries for j in plan.jobs}
    assert len(jobs["R1"]) == 2
    assert len(jobs["R6"]) == 1


def test_policy_without_company_names_plans_neither_r2_nor_r3() -> None:
    b = brief(references=["Zed"], brand_url="https://zed.example")
    plan = handle(c6(b, policy={"allow_company_name_in_queries": False}))
    assert {j.job for j in plan.jobs}.isdisjoint({"R2", "R3"})
    for job in plan.jobs:
        for query in job.queries:
            assert "zed" not in query.lower()


def test_unknown_archetypes_use_their_id_as_a_generic_phrase() -> None:
    assert topic_phrase("pet_care") == "pet care"
    assert topic_phrase("custom") == "everyday app"
    out = candidate_queries(c6(brief(archetype="pet_care")))
    assert out["R1"][0].startswith("common features of pet care mobile apps")


def test_brand_host_strips_scheme_www_and_path() -> None:
    assert brand_host("https://www.Zed.example/a/b?x=1") == "zed.example"
    assert brand_host("zed.example") == "zed.example"
    assert brand_host(None) is None
    assert brand_host("") is None


def test_trim_leaves_r6_alone() -> None:
    out = trim({"R1": ["a b", "c d"], "R6": ["x y"]}, 1, ["R1", "R6"])
    assert out == {"R1": ["a b"], "R6": ["x y"]}


def test_every_query_is_plain_words() -> None:
    b = brief(
        references=["Zed"],
        brand_url="https://zed.example",
        compliance_flags=["financial_data"],
        features=[OptionLite(id="payments", label="P")],
        regions=["in"],
    )
    for job in deterministic_plan(c6(b, jobs_available=list(ALL_JOBS))).jobs:
        for query in job.queries:
            assert query == " ".join(query.split())
            assert len(query.split()) <= 12
            assert "{" not in query
            assert "@" not in query


@pytest.mark.parametrize(
    "archetype", ["commerce", "food_delivery", "booking_services", "internal_workforce", "custom"]
)
def test_every_archetype_gets_an_r1_plan(archetype: str) -> None:
    plan = handle(c6(brief(archetype=archetype)))
    assert plan.jobs[0].job == "R1"


async def test_runs_through_the_real_gateway_in_mock_mode() -> None:
    from mde_llm_gateway.testing_specs import build_test_gateway, make_ctx
    from mde_prompts.registry import PromptRegistry

    gateway = build_test_gateway(registry=PromptRegistry())
    result = await gateway.run(
        cast("Any", SPEC), c6(brief(compliance_flags=["financial_data"])), make_ctx()
    )
    assert result.mode == "mock"
    assert isinstance(result.output, C6Output)
    assert next(j.job for j in result.output.jobs) == "R1"


# --------------------------------------------------------------------------- the per-job rule (D-078)

CAPS = {"light": 6, "standard": 12, "deep": 20}
WEB = ["R1", "R2", "R3", "R4", "R5"]
NO_NAMES = ["R1", "R4", "R5"]  # a policy that bars company names removes R2 and R3


@pytest.mark.parametrize(
    ("depth_cap", "jobs", "expected"),
    [
        (6, 5, 2), (6, 4, 2), (6, 3, 2), (6, 2, 2), (6, 1, 2),
        (12, 5, 3), (12, 4, 3), (12, 3, 4), (12, 2, 6), (12, 1, 8),
        (20, 5, 4), (20, 4, 5), (20, 3, 7), (20, 2, 8), (20, 1, 8),
        (0, 5, 2), (6, 0, 2), (-3, 3, 2), (7, 5, 2), (7, 1, 7), (13, 5, 3),
    ],
)  # fmt: skip
def test_queries_per_job_is_the_depth_cap_spread_over_the_web_jobs(
    depth_cap: int, jobs: int, expected: int
) -> None:
    assert queries_per_job(depth_cap, jobs) == expected


def test_queries_per_job_is_clamped_and_always_covers_the_cap_when_it_can() -> None:
    for depth_cap in range(0, 41):
        for jobs in range(0, 7):
            per_job = queries_per_job(depth_cap, jobs)
            assert 2 <= per_job <= 8
            if depth_cap > 6 and per_job < 8:  # above Light, unless clamped, the jobs carry the cap
                assert per_job * max(1, jobs) >= depth_cap


def test_queries_per_job_stays_inside_the_contract_bounds() -> None:
    bounds = C6Input.model_fields["max_queries_per_job"].metadata
    low = next(m.ge for m in bounds if hasattr(m, "ge"))
    high = next(m.le for m in bounds if hasattr(m, "le"))
    assert (low, high) == (1, 8)
    assert {queries_per_job(c, n) for c in range(41) for n in range(7)} <= set(range(low, high + 1))


def test_web_jobs_leave_out_the_internal_job() -> None:
    assert web_jobs(["R1", "R6", "R4"]) == ["R1", "R4"]
    assert web_jobs([]) == []


def rich_brief(**overrides: object) -> BriefContext:
    base: dict[str, object] = {
        "references": ["Zed"],
        "brand_url": "https://zed.example",
        "compliance_flags": ["financial_data", "precise_location"],
        "features": [
            OptionLite(id="payments", label="Payments"),
            OptionLite(id="push_notifications", label="Push alerts"),
            OptionLite(id="catalog_browse", label="Browse products"),
        ],
        "regions": ["in"],
        "login": "email_phone_code",
    }
    base.update(overrides)
    return brief(**base)


def planned(
    depth: str,
    jobs: list[str],
    b: BriefContext | None = None,
    *,
    names: bool = True,
    cap: int | None = None,
) -> C6Output:
    """The deterministic plan the way the engine asks for it: per-job number from the shared rule."""
    depth_cap = CAPS[depth] if cap is None else cap
    return handle(
        c6(
            b or rich_brief(),
            depth=depth,
            max_search_calls=depth_cap,
            max_queries_per_job=queries_per_job(depth_cap, len(web_jobs(jobs))),
            jobs_available=jobs,
            policy={"allow_company_name_in_queries": names},
        )
    )


def total(plan: C6Output) -> int:
    return sum(len(j.queries) for j in plan.jobs if j.job != "R6")


@pytest.mark.parametrize(("depth", "expected"), [("standard", 12), ("deep", 20)])
def test_with_all_five_web_jobs_the_plan_reaches_the_ceiling(depth: str, expected: int) -> None:
    plan = planned(depth, WEB)
    assert total(plan) == expected
    per_job = queries_per_job(CAPS[depth], 5)
    assert all(len(j.queries) <= per_job for j in plan.jobs)
    assert {j.job for j in plan.jobs} == set(WEB)


@pytest.mark.parametrize(("depth", "expected"), [("standard", 12), ("deep", 20)])
def test_without_company_names_r1_r4_r5_alone_reach_the_ceiling(depth: str, expected: int) -> None:
    plan = planned(depth, NO_NAMES, names=False)
    assert total(plan) == expected
    assert {j.job for j in plan.jobs} == set(NO_NAMES)


@pytest.mark.parametrize("depth", ["standard", "deep"])
def test_one_flag_and_one_service_are_enough_to_reach_the_ceiling(depth: str) -> None:
    b = rich_brief(
        compliance_flags=["financial_data"],
        features=[OptionLite(id="payments", label="Payments")],
        login="",
        references=[],
        brand_url=None,
    )
    assert total(planned(depth, NO_NAMES, b, names=False)) == CAPS[depth]


@pytest.mark.parametrize("jobs", [WEB, NO_NAMES, ["R1"], ["R1", "R5"], ["R1", "R4"], [*WEB, "R6"]])
@pytest.mark.parametrize("names", [True, False])
def test_light_never_plans_more_than_six_searches(jobs: list[str], names: bool) -> None:
    plan = planned("light", jobs, names=names)
    assert 0 < total(plan) <= 6
    assert all(len(j.queries) <= 2 for j in plan.jobs)  # Light keeps two per job, however few jobs


@pytest.mark.parametrize("depth", ["light", "standard", "deep"])
def test_the_depth_cap_trims_the_plan_whatever_the_per_job_number(depth: str) -> None:
    for cap in range(0, CAPS[depth] + 1):
        assert total(planned(depth, WEB, cap=cap)) <= cap


def test_the_first_query_of_every_job_comes_before_any_second_one() -> None:
    for cap in range(len(WEB), 21):
        sizes = {j.job: len(j.queries) for j in planned("deep", WEB, cap=cap).jobs}
        assert set(sizes) == set(WEB), cap  # every job has its first query ...
        assert max(sizes.values()) - min(sizes.values()) <= 1, cap  # ... before any has a second
    five_firsts_then_the_two_best_ranked_jobs = planned("deep", WEB, cap=7)
    assert {j.job: len(j.queries) for j in five_firsts_then_the_two_best_ranked_jobs.jobs} == {
        "R1": 2,
        "R2": 1,
        "R3": 1,
        "R4": 2,
        "R5": 1,
    }  # job priority is R1, R4, R5, R2, R3


def test_trim_generalises_to_any_number_of_queries_per_job() -> None:
    pool: dict[ResearchJobId, list[str]] = {
        "R1": [f"R1 q{i} here" for i in range(5)],
        "R2": [f"R2 q{i} here" for i in range(5)],
        "R4": [f"R4 q{i} here" for i in range(5)],
    }
    order = ["R1", "R4", "R2"]
    out = trim(pool, 8, order, per_job=4)
    assert {j: len(q) for j, q in out.items()} == {"R1": 3, "R2": 2, "R4": 3}
    assert out["R1"] == pool["R1"][:3]  # each job keeps its own leading queries, in order
    assert trim(pool, 99, order, per_job=3) == {j: q[:3] for j, q in pool.items()}
    assert trim(pool, 99, order) == pool  # default: up to the contract bound of 8
    assert trim(pool, 99, order, per_job=0) == {}
    long_pool: dict[ResearchJobId, list[str]] = {"R1": [f"query number {i}" for i in range(12)]}
    assert len(trim(long_pool, 99, ["R1"])["R1"]) == 8


def test_every_generated_query_is_distinct_and_plain() -> None:
    for names in (True, False):
        plan = planned("deep", WEB if names else NO_NAMES, names=names)
        queries = [q for j in plan.jobs for q in j.queries]
        assert len(queries) == len({" ".join(q.casefold().split()) for q in queries})
        for query in queries:
            assert query == " ".join(query.split())
            assert 2 <= len(query.split()) <= 14
            assert len(query) <= 120
            assert not any(c in query for c in "{}[]<>@\\\"'`|")


def test_r1_asks_about_the_features_of_the_brief_and_about_wider_angles() -> None:
    b = rich_brief()
    cands = candidate_queries(c6(b, max_queries_per_job=8, jobs_available=["R1"]))["R1"]
    assert cands[0].startswith("common features of ")
    assert "UX patterns" in cands[1]
    assert any("how Browse products is designed" in q for q in cands)  # one per Brief feature
    assert any("monetization" in q for q in cands)  # and a wider angle
    assert len(cands) == 8
    # a smaller per-job number keeps the same leading queries (Light: the two original ones)
    assert cands[:2] == candidate_queries(c6(b, jobs_available=["R1"]))["R1"]


def test_feature_names_are_short_distinct_plain_phrases() -> None:
    b = brief(
        features=[
            OptionLite(id="a", label="Browse   products"),
            OptionLite(id="b", label="browse products"),
            OptionLite(id="c", label="  "),
            OptionLite(id="d", label="x" * 100),
        ]
    )
    names = feature_names(b)
    assert names[0] == "Browse products"
    assert len(names) == 2 and len(names[1]) == 40


def test_names_enter_only_r2_and_r3_queries_and_only_when_allowed() -> None:
    b = rich_brief(
        references=["Zedshop"],
        brand_url="https://zedbrand.example",
        features=[OptionLite(id="payments", label="Payments")],
    )
    for job in planned("deep", WEB, b).jobs:
        for query in job.queries:
            has_name = "zedshop" in query.casefold() or "zedbrand" in query.casefold()
            assert has_name == (job.job in {"R2", "R3"}), (job.job, query)
    barred = planned("deep", WEB, b, names=False)
    assert {j.job for j in barred.jobs} == {"R1", "R4", "R5"}
    assert not any("zed" in q.casefold() for j in barred.jobs for q in j.queries)


def test_references_are_covered_before_any_reference_gets_a_second_angle() -> None:
    b = brief(references=["Zed", "Quark", "Third"])
    r2 = candidate_queries(c6(b, max_queries_per_job=5, jobs_available=["R2"]))["R2"]
    assert [q.split()[0] for q in r2] == ["Zed", "Quark", "Third", "Zed", "Quark"]
    assert r2[0].endswith("mobile app features overview")
    assert r2[3] != r2[0]


def test_compliance_areas_and_services_are_covered_before_a_second_angle() -> None:
    out = candidate_queries(c6(rich_brief(), max_queries_per_job=4, jobs_available=["R4", "R5"]))
    areas = ["payment card data handling", "precise location consent and retention"]
    assert [q.startswith(areas[i % 2]) for i, q in enumerate(out["R4"])] == [True] * 4
    assert out["R4"][0] != out["R4"][2]
    assert [q.split(" SDK ")[0] for q in out["R5"][:2]] == ["SMS sign-in codes", "payment provider"]


def test_a_per_job_number_of_one_is_honoured() -> None:
    plan = handle(c6(rich_brief(), max_queries_per_job=1, jobs_available=WEB, max_search_calls=20))
    assert all(len(j.queries) == 1 for j in plan.jobs)


def test_the_system_prompt_states_the_per_job_number() -> None:
    rendered = PromptRenderer().render(cast("Any", SPEC), c6(max_queries_per_job=7))
    assert "At most 7 queries per job" in rendered.system
    assert "max_queries_per_job: 7" in rendered.user
    assert "At most 2 queries per job" not in rendered.system


def test_the_planning_arithmetic_of_the_ceilings_fits_the_research_cap() -> None:
    per_query, overhead, cap = 13_000, 5_500, 300_000  # config.py and budget.yaml (D-078)
    assert 20 * per_query + overhead == 265_500 <= cap
