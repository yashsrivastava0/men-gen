"""Query privacy and domain policy at engine level: names stay out of every query; the policy's
allowed and blocked domains are honoured for search, sources and brand extraction."""

from __future__ import annotations

import asyncio
import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from research_helpers import (
    DirectMockGateway,
    ScriptedGateway,
    full_slots,
    make_brief,
    make_context,
    make_engine,
    make_extras_request,
    make_plan,
    make_policy,
    make_slots,
    s1_text,
    slot,
)

from mde_contracts.brief import Brief
from mde_contracts.calls import C6Input, C8Output, S1Input
from mde_research.privacy import compact

NAME = "Zedshopworks"
RIVAL = "Quarkmart"
ORG = "client-acme-holdings"


def private_request(**policy: object):
    brief = make_brief(summary=f"{NAME} is a shop app by Client Acme Holdings, like {RIVAL}")
    slots = full_slots(brand__name=NAME)
    return make_extras_request(
        references=[RIVAL],
        brand_url=f"https://www.{NAME.lower()}.example/",
        brief=brief,
        slots=slots,
        policy=make_policy(allow_company_name=False, org=ORG, **policy),  # type: ignore[arg-type]
    )


def all_queries(gw: DirectMockGateway | ScriptedGateway) -> list[str]:
    return [c.input.query for c in gw.calls if c.call_id == "S1.web_search"]  # type: ignore[attr-defined]


def leaks(text: str) -> bool:
    folded = compact(text)
    return any(compact(t) in folded for t in (NAME, RIVAL, ORG))


async def test_names_never_appear_in_any_query_when_the_policy_forbids_them() -> None:
    gw = DirectMockGateway()
    note = await make_engine(gw).run(private_request(), make_context()[0])
    queries = all_queries(gw)
    assert queries
    assert note.status == "ok"
    assert not any(leaks(q) for q in queries)
    jobs = {c.input.job for c in gw.calls if c.call_id == "S1.web_search"}  # type: ignore[attr-defined]
    assert jobs.isdisjoint({"R2", "R3"})  # reference apps and brand need the names


async def test_the_planner_never_sees_the_names_either() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(private_request(), make_context()[0])
    (c6_in,) = [c.input for c in gw.calls if c.call_id == "C6.plan_research"]
    assert isinstance(c6_in, C6Input)
    assert c6_in.brief.brand_name is None
    assert c6_in.brief.brand_url is None
    assert c6_in.brief.references == []
    assert not leaks(c6_in.brief.app_summary)
    assert "the company" in c6_in.brief.app_summary
    assert c6_in.policy.allow_company_name_in_queries is False
    assert "R2" not in c6_in.jobs_available and "R3" not in c6_in.jobs_available
    for call in gw.calls:
        if call.call_id in {"C8.synthesize_research", "C7.extract"}:
            assert not leaks(call.input.model_dump_json())


async def test_a_planner_that_puts_the_name_in_a_query_is_corrected() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script(
        "C6.plan_research",
        make_plan(
            {
                "R1": [f"{NAME} shop features compared", f"best apps for {RIVAL} like shops"],
                "R4": [
                    f"{ORG} payment card rules",
                    "payment card data handling for online shopping",
                ],
            }
        ),
    )
    await make_engine(gw).run(private_request(), make_context()[0])
    queries = all_queries(gw)
    assert queries
    assert not any(leaks(q) for q in queries)
    assert "shop features compared" in queries  # the name is cut out, the rest stays
    assert "payment card data handling for online shopping" in queries


async def test_a_plan_made_only_of_leaks_falls_back_to_generic_queries() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", make_plan({"R1": [NAME, f"{NAME} {RIVAL}"]}))
    report = await make_engine(gw).run_with_report(private_request(), make_context()[0])
    assert "c6_deterministic_plan" in report.fallbacks_used
    assert report.queries
    assert not any(leaks(q) for q in report.queries)


async def test_gap_queries_are_scrubbed_too() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script(
        "C8.synthesize_research",
        C8Output(suggestions=[], gaps=[f"how does {NAME} handle returns", f"{RIVAL} refund flow"]),
    )
    note = await make_engine(gw).run(
        private_request().model_copy(update={"depth": "deep"}), make_context()[0]
    )
    assert not any(leaks(q) for q in all_queries(gw))
    assert not any(leaks(g) for g in note.gaps)
    assert any("handle returns" in q for q in all_queries(gw))


async def test_names_are_used_when_the_policy_allows_them() -> None:
    gw = DirectMockGateway()
    req = make_extras_request(
        references=[RIVAL],
        brand_url=f"https://www.{NAME.lower()}.example/",
        slots=full_slots(),
        policy=make_policy(allow_company_name=True),
    )
    await make_engine(gw).run(req, make_context()[0])
    s1 = [c.input for c in gw.calls if c.call_id == "S1.web_search"]
    assert any(RIVAL in i.query for i in s1)  # type: ignore[attr-defined]
    r3 = next(i for i in s1 if i.job == "R3")  # type: ignore[attr-defined]
    assert NAME.lower() in r3.query  # type: ignore[attr-defined]
    assert r3.allowed_domains == [f"www.{NAME.lower()}.example"]  # type: ignore[attr-defined]


async def test_brand_extraction_only_for_a_site_the_user_gave() -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(make_extras_request(slots=full_slots()), make_context()[0])
    assert "R3" not in {c.input.job for c in gw.calls if c.call_id == "S1.web_search"}  # type: ignore[attr-defined]


async def test_a_link_typed_into_the_name_card_counts_as_the_users_site() -> None:
    gw = DirectMockGateway()
    slots = full_slots()
    slots["brand.name"] = slot("brand.name", "https://www.mybakery.example/menu")
    from research_helpers import make_request

    await make_engine(gw).run(make_request(slots=slots), make_context()[0])
    r3 = [c.input for c in gw.calls if c.call_id == "S1.web_search" and c.input.job == "R3"]  # type: ignore[attr-defined]
    assert len(r3) == 1
    assert r3[0].allowed_domains == ["www.mybakery.example"]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- domain policy


async def test_allowed_domains_are_passed_to_every_search() -> None:
    gw = DirectMockGateway()
    policy = make_policy(allowed_domains=["docs.expo.dev", "owasp.org"])
    note = await make_engine(gw).run(
        make_extras_request(slots=full_slots(), policy=policy), make_context()[0]
    )
    inputs = [c.input for c in gw.calls if c.call_id == "S1.web_search"]
    assert inputs
    assert all(i.allowed_domains == ["docs.expo.dev", "owasp.org"] for i in inputs)  # type: ignore[attr-defined]
    assert note.status == "ok"  # the mock re-points its sources at the allowed domains
    hosts = {s.domain for f in note.facts for s in f.sources}
    assert hosts <= {"docs.expo.dev", "owasp.org"}


async def test_sources_outside_the_allow_list_never_become_facts() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", make_plan({"R1": ["first query words", "second query words"]}))
    text = (
        "Shops offer search and filters for the catalog ([a](https://docs.expo.dev/a)). "
        "Reviews build trust for many shoppers ([b](https://example.com/b))."
    )
    gw.script("S1.web_search", s1_text(text, "https://docs.expo.dev/a", "https://example.com/b"))
    note = await make_engine(gw).run(
        make_extras_request(policy=make_policy(allowed_domains=["docs.expo.dev"])),
        make_context()[0],
    )
    (c7_in,) = gw.inputs("C7.extract")[:1]
    assert [s.url for s in c7_in.sources] == ["https://docs.expo.dev/a"]  # type: ignore[attr-defined]
    assert "example.com" not in c7_in.search_text  # type: ignore[attr-defined]
    urls = {s.url for f in note.facts for s in f.sources}
    assert all("docs.expo.dev" in u or u.endswith("owasp.org/") for u in urls) or not urls


async def test_blocked_domains_never_become_facts() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", make_plan({"R1": ["first query words", "second query words"]}))
    text = "Shops offer search ([a](https://sub.bad.example/a)). Reviews help ([b](https://example.org/b))."
    gw.script("S1.web_search", s1_text(text, "https://sub.bad.example/a", "https://example.org/b"))
    note = await make_engine(gw).run(
        make_extras_request(policy=make_policy(blocked_domains=["bad.example"])), make_context()[0]
    )
    for fact in note.facts:
        assert all("bad.example" not in s.url for s in fact.sources)
    c7_in = gw.inputs("C7.extract")[0]
    assert [s.url for s in c7_in.sources] == ["https://example.org/b"]  # type: ignore[attr-defined]


async def test_a_result_with_only_blocked_sources_is_dropped_without_asking_the_model() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", make_plan({"R1": ["first query words", "second query words"]}))
    gw.script(
        "S1.web_search",
        s1_text("Shops offer search for everyone here.", "https://bad.example/x"),
        s1_text("Shops offer search for everyone here.", "https://bad.example/y"),
    )
    note = await make_engine(gw).run(
        make_extras_request(policy=make_policy(blocked_domains=["bad.example"])), make_context()[0]
    )
    assert gw.counts["C7.extract"] == 0
    assert note.status == "unavailable"


async def test_brand_job_is_skipped_when_the_site_is_blocked_or_not_allowed() -> None:
    for policy in (
        make_policy(blocked_domains=["brand.example"]),
        make_policy(allowed_domains=["docs.expo.dev"]),
    ):
        gw = DirectMockGateway()
        req = make_extras_request(
            slots=full_slots(), brand_url="https://brand.example/", policy=policy
        )
        await make_engine(gw).run(req, make_context()[0])
        assert "R3" not in {c.input.job for c in gw.calls if c.call_id == "S1.web_search"}  # type: ignore[attr-defined]


async def test_brand_search_is_pinned_to_the_users_own_site() -> None:
    gw = DirectMockGateway()
    req = make_extras_request(slots=full_slots(), brand_url="https://brand.example/")
    note = await make_engine(gw).run(req, make_context()[0])
    r3 = next(c.input for c in gw.calls if c.call_id == "S1.web_search" and c.input.job == "R3")  # type: ignore[attr-defined]
    assert isinstance(r3, S1Input)
    assert r3.allowed_domains == ["brand.example"]
    assert note.brand is not None
    assert note.brand.source_url == "https://brand.example/"
    assert note.brand.colors == ["#1A4FD6", "#FFFFFF"]


# --------------------------------------------------------------------------- property tests


@settings(max_examples=40, deadline=None, derandomize=True)
@given(
    company=st.text(alphabet=string.ascii_lowercase, min_size=6, max_size=12),
    rival=st.text(alphabet=string.ascii_lowercase, min_size=6, max_size=12),
    org=st.text(alphabet=string.ascii_lowercase, min_size=6, max_size=10),
    summary_prefix=st.sampled_from(["An app for", "Mobile shop of", "Booking for", ""]),
    hostile=st.booleans(),
    depth=st.sampled_from(["light", "standard", "deep"]),
)
def test_the_company_name_is_in_no_query_for_any_name_or_brief(
    company: str, rival: str, org: str, summary_prefix: str, hostile: bool, depth: str
) -> None:
    async def scenario() -> list[str]:
        brief = Brief.model_validate(
            {
                **make_brief(
                    summary=f"{summary_prefix} {company} and {rival} by {org}"
                ).model_dump(),
            }
        ).with_hash()
        slots = make_slots("commerce", compliance__flags=["financial_data"], brand__name=company)
        req = make_extras_request(
            references=[rival],
            brand_url=f"https://www.{company}.example/",
            brief=brief,
            slots=slots,
            depth=depth,
            policy=make_policy(allow_company_name=False, org=f"{org}-holdings"),
        )
        gw = ScriptedGateway(fallback=DirectMockGateway())
        if hostile:  # a model that ignores the rule
            gw.script(
                "C6.plan_research",
                make_plan(
                    {
                        "R1": [f"{company} features of shops", f"{rival} app patterns"],
                        "R4": [f"{org} holdings payment card rules and consent"],
                    }
                ),
            )
            gw.script(
                "C8.synthesize_research",
                C8Output(suggestions=[], gaps=[f"what does {company} do about returns"]),
            )
        await make_engine(gw).run(req, make_context()[0])
        return all_queries(gw)

    queries = asyncio.run(scenario())
    assert queries
    for query in queries:
        folded = compact(query)
        assert compact(company) not in folded
        assert compact(rival) not in folded
        assert compact(f"{org}-holdings") not in folded
    assert company.isalpha()


@pytest.mark.parametrize("allow", [True, False])
async def test_the_policy_flag_is_passed_to_the_planner(allow: bool) -> None:
    gw = DirectMockGateway()
    await make_engine(gw).run(
        make_extras_request(slots=full_slots(), policy=make_policy(allow_company_name=allow)),
        make_context()[0],
    )
    (c6_in,) = [c.input for c in gw.calls if c.call_id == "C6.plan_research"]
    assert isinstance(c6_in, C6Input)
    assert c6_in.policy.allow_company_name_in_queries is allow
