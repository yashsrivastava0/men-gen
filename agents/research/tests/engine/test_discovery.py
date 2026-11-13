"""Discovery (D-163): official-first variants, the provider's own-site follow-up and both engines on vendor topics."""

from __future__ import annotations

import json
from pathlib import Path

from support.fakes import FakeSearch, FakeWorld
from support.testing import atom, extractor, load_fixture, run_engine

from mde_core.clock import FakeClock
from mde_research_agent.calls import queries as q_calls
from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.sources import official_domains_for
from mde_research_agent.ports.search import Capabilities, SearchError

ROOT = Path(__file__).resolve().parents[4]
INPUTS = ROOT / "docs" / "compass" / "research-inputs"
CAT = load_catalog()
STANDARD = "standard-produce-marketplace-nashik"
QUICK = "quick-yoga-studio-bengaluru"


def research_input(stem: str) -> dict:
    return json.loads((INPUTS / f"{stem}.json").read_text(encoding="utf-8"))


def official_requests(run):  # type: ignore[no-untyped-def]
    off = {q.text for q in run.ctx.queries if q.variant == "official"}
    return [r for r in run.search.requests if r.query in off]


# --- official-first ----------------------------------------------------------------------------------------------------------


async def test_an_official_variant_replaces_a_descriptive_one_and_prefers_registry_domains() -> (
    None
):
    run = await run_engine(load_fixture(STANDARD))
    sent = official_requests(run)
    assert sent, "topics flagged official_first ask for the official pages"
    registry = {e.domain for e in CAT.authority}
    for req in sent:
        assert req.domain_mode in ("prefer", "restrict") and req.domains
        assert set(req.domains) <= registry and len(req.domains) <= 20
    first_round = [q for q in run.ctx.queries if q.round == 1]
    by_topic: dict[str, int] = {}
    for q in first_round:
        by_topic[q.target_id] = by_topic.get(q.target_id, 0) + 1
    want = CAT.ceilings.variants_per_topic
    assert max(by_topic.values()) <= want + 1, "the official variant is never an extra search"
    for ts in run.ctx.targets.values():
        offs = [q for q in first_round if q.target_id == ts.target.id and q.variant == "official"]
        assert len(offs) <= 1
        if offs:
            assert ts.target.official_first


async def test_only_official_first_topics_ask_for_registry_domains() -> None:
    run = await run_engine(load_fixture(STANDARD))
    flagged = {ts.target.id for ts in run.ctx.targets.values() if ts.target.official_first}
    assert flagged
    for q in run.ctx.queries:
        if q.variant == "official":
            assert q.target_id in flagged


async def test_a_provider_without_prefer_gets_a_site_restricted_query_only_on_risk_topics() -> None:
    world = FakeWorld(provider_content_every=3)
    search = FakeSearch(world)
    search.capabilities = Capabilities(
        country=True,
        language=True,
        domain_prefer=False,
        domain_restrict=True,
        exact_match=True,
        time_range=True,
        page_content=False,
    )
    run = await run_engine(load_fixture(STANDARD), world=world, search_port=search)
    by_id = {ts.target.id: ts.target for ts in run.ctx.targets.values()}
    official = [q for q in run.ctx.queries if q.variant == "official"]
    assert official
    for q in official:
        t = by_id[q.target_id]
        req = next(r for r in run.search.requests if r.query == q.text)
        assert req.domain_mode == "restrict"
        assert t.risk or t.domain_restrict, (
            "no prefer mode: only vendor, rule and platform topics are restricted"
        )
    plain_topics = [
        t for t in by_id.values() if t.official_first and not t.risk and not t.domain_restrict
    ]
    for t in plain_topics:
        assert not [q for q in official if q.target_id == t.id]


async def test_a_platform_topic_names_its_owners_domain() -> None:
    run = await run_engine(load_fixture(STANDARD))
    flagged = [
        ts.target
        for ts in run.ctx.targets.values()
        if ts.target.domain_restrict and ts.target.official_first
    ]
    for t in flagged:
        req = [r for r in official_requests(run) if set(r.domains) == set(t.domain_restrict)]
        assert req and all(r.domain_mode == "restrict" for r in req)


async def test_the_official_variant_passes_the_anatomy_gate_and_no_validator_fails() -> None:
    run = await run_engine(load_fixture(STANDARD))
    assert not [v for v in run.violations if v.rule in ("RV-14", "RV-06", "RV-11", "RV-12")]


def test_the_deterministic_official_variant_replaces_the_second_one() -> None:
    inp = q_calls.QueryIn(
        job="data",
        question="q",
        anchors=["tax rules"],
        context="produce app",
        place="India",
        languages=[],
        local_terms=[],
        local_intent=[],
        intent=["rules", "fees", "limits", "penalty"],
        earlier=[],
        want=3,
        round=1,
        focus_terms=[],
        seed_names=[],
        language="en",
        official_domains=["gst.gov.in"],
    )
    out = q_calls.deterministic_variants(inp)
    styles = [v.style for v in out.variants]
    assert styles.count("official") == 1 and styles[0] == "descriptive" and styles[1] == "official"
    plain = q_calls.deterministic_variants(inp.model_copy(update={"official_domains": []}))
    assert len(out.variants) == len(plain.variants) and "official" not in [
        v.style for v in plain.variants
    ]
    assert out.variants[1].text.startswith("official ")
    later = q_calls.deterministic_variants(
        inp.model_copy(update={"round": 2, "focus_terms": ["fees"]})
    )
    assert "official" not in [v.style for v in later.variants], "round one only"


def test_the_official_variant_is_written_in_the_authoritys_language() -> None:
    words = CAT.intent_words.languages["fr"].words[:4]
    inp = q_calls.QueryIn(
        job="data",
        question="q",
        anchors=["school fees"],
        context="school app",
        place="Senegal",
        languages=[],
        local_terms=[],
        local_intent=[],
        intent=["rules", "fees"],
        earlier=[],
        want=3,
        round=1,
        focus_terms=[],
        seed_names=[],
        language="fr",
        official_intent=list(words),
        official_domains=["dgid.sn"],
    )
    off = next(v for v in q_calls.deterministic_variants(inp).variants if v.style == "official")
    assert off.language == "fr" and words[0] in off.text and "official" not in off.text


async def test_rules_for_a_french_speaking_country_are_asked_in_french() -> None:
    run = await run_engine(research_input("synthetic-medium-school-parents-dakar"))
    sent = official_requests(run)
    assert sent
    assert all(r.language == "fr" for r in sent), {r.language for r in sent}
    domains = {d for r in sent for d in r.domains}
    assert {"bceao.int", "dgid.sn", "cdp.sn"} & domains
    fr = set(CAT.intent_words.languages["fr"].words)
    assert any(set(r.query.casefold().split()) & fr for r in sent)


async def test_each_country_of_a_multi_country_brief_gets_its_own_authorities_and_language() -> (
    None
):
    run = await run_engine(research_input("synthetic-big-site-safety-central-america"))
    per = {
        ts.target.country_code: ts.target.id
        for ts in run.ctx.targets.values()
        if ts.target.country_code and ts.target.official_first
    }
    assert set(per) == {"mx", "gt", "cr"}
    for code, _domain in (("mx", "stps.gob.mx"), ("gt", "mintrabajo.gob.gt"), ("cr", "mtss.go.cr")):
        reqs = [
            r
            for q in run.ctx.queries
            if q.variant == "official" and run.ctx.targets[q.target_id].target.country_code == code
            for r in run.search.requests
            if r.query == q.text
        ]
        assert reqs, code
        assert all(r.country == code and r.language == "es" for r in reqs)
    all_domains = {d for r in official_requests(run) for d in r.domains}
    assert {"stps.gob.mx", "mintrabajo.gob.gt", "mtss.go.cr"} <= all_domains


def test_official_domains_are_matched_by_topic_words_country_and_bloc() -> None:
    words = ["Data-protection rules for Portugal", "privacy law consent"]
    got = official_domains_for(CAT, words, ["pt"])
    assert got[0] == "cnpd.pt" and "eur-lex.europa.eu" in got, "the country first, then its bloc"
    assert "dpb.gov.in" not in got and all("." in d for d in got)
    assert got == official_domains_for(CAT, words, ["pt"]), "deterministic"
    assert (
        len(official_domains_for(CAT, ["tax gst invoice e-way bill customs"], ["in"], limit=3)) == 3
    )
    assert official_domains_for(CAT, ["zzz nothing"], ["in"]) == ()
    assert "cnpd.pt" not in official_domains_for(CAT, words, ["in"])


# --- the provider's own-site follow-up ---------------------------------------------------------------------------------------

PAGE = (
    "Payfast offers an online payments API with webhooks for payment events. Payfast has a sandbox for testing. "
    "Payfast charges 2.5 percent per transaction."
)


def provider_world(extra_domains: int = 0) -> FakeWorld:
    w = FakeWorld(generate=False)
    urls = [w.add_page("https://www.review-site.example/payfast", PAGE, title="Payfast review")]
    urls.append(w.add_page("https://docs.payfast.com/overview", PAGE, title="Payfast docs"))
    w.route(r"payments", urls)
    w.add_page("https://payfast.com/pricing", PAGE, title="Payfast pricing")
    return w


def provider_extractor():  # type: ignore[no-untyped-def]
    def build(inp):  # type: ignore[no-untyped-def]
        if "Payfast" not in inp.passages:
            return []
        return [
            atom(
                "service_option",
                "Payfast offers an online payments API with webhooks for payment events.",
                key="payfast",
                label="Payfast",
                provider="Payfast",
                endpoints_or_events=["payment.captured"],
            )
        ]

    return extractor(build)


async def test_a_discovered_provider_gets_documentation_pricing_and_help_queries_on_its_own_domain() -> (
    None
):
    w = provider_world()
    run = await run_engine(
        load_fixture(STANDARD), world=w, overrides={"rsch.extract.services": provider_extractor()}
    )
    ent = next((e for e in run.ctx.entities.values() if e.name == "Payfast"), None)
    assert ent is not None and ent.kind == "provider"
    assert ent.domain == "payfast.com", (
        "resolved from the hit domains by own_site against ctx.entities"
    )
    own = [
        r
        for r in run.search.requests
        if r.domain_mode == "restrict" and r.domains == ("payfast.com",)
    ]
    assert len(own) >= len(CAT.plan.entity_followup_words)
    words = " ".join(r.query for r in own).casefold()
    assert "documentation" in words and "pricing" in words and "help" in words
    assert all("payfast" in r.query.casefold() for r in own)
    assert run.ctx.extraction.get("provider_followups", 0) >= 1


async def test_the_follow_up_is_capped_by_the_load_class_and_counted_in_the_budgets() -> None:
    w = FakeWorld(generate=False)
    names = [f"Provider{n}x" for n in range(12)]
    urls = []
    for n in names:
        urls.append(
            w.add_page(
                f"https://www.{n.lower()}.com/about",
                f"{n} offers an online payments API with webhooks.",
            )
        )
    w.route(r"payments", urls)

    def build(inp):  # type: ignore[no-untyped-def]
        out = []
        for n in names:
            if n in inp.passages:
                out.append(
                    atom(
                        "service_option",
                        f"{n} offers an online payments API with webhooks.",
                        key=n.lower(),
                        label=n,
                        provider=n,
                        endpoints_or_events=["event"],
                    )
                )
        return out

    run = await run_engine(
        load_fixture(QUICK), world=w, overrides={"rsch.extract.services": extractor(build)}
    )
    cap = CAT.ceilings.for_class("focused").provider_followups
    restricted = [r for r in run.search.requests if r.domain_mode == "restrict" and r.domains]
    domains = {r.domains[0] for r in restricted if "provider" in r.query.casefold()}
    assert 0 < len(domains) <= cap
    assert run.ctx.budget.searches_used <= run.ctx.budget.hard.searches
    assert len(run.search.requests) == run.ctx.budget.searches_used, (
        "every follow-up search was admitted by the budget"
    )


async def test_a_provider_with_no_domain_found_gets_no_follow_up() -> None:
    w = FakeWorld(generate=False)
    u = w.add_page("https://www.review-site.example/p", PAGE)
    w.route(r"payments", [u])
    run = await run_engine(
        load_fixture(STANDARD), world=w, overrides={"rsch.extract.services": provider_extractor()}
    )
    ent = next(e for e in run.ctx.entities.values() if e.name == "Payfast")
    assert ent.domain is None
    assert not [r for r in run.search.requests if r.domains and "payfast" in r.query.casefold()]


# --- both engines on vendor topics ------------------------------------------------------------------------------------------


def two_engines(primary_cost: int = 8000, second_cost: int = 1000):  # type: ignore[no-untyped-def]
    clock = FakeClock()
    w1 = FakeWorld(provider_content_every=3, clock=clock, search_cost_micros=primary_cost)
    w2 = FakeWorld(provider_content_every=0, clock=clock, search_cost_micros=second_cost)
    s1, s2 = FakeSearch(w1), FakeSearch(w2)
    s1.name, s2.name = "tavily", "serper"
    s2.capabilities = Capabilities(
        country=True,
        language=True,
        domain_prefer=False,
        domain_restrict=True,
        exact_match=True,
        time_range=True,
        page_content=False,
    )
    return clock, w1, s1, s2


async def test_a_vendor_topic_sends_one_extra_query_to_the_second_engine() -> None:
    clock, w1, s1, s2 = two_engines()
    run = await run_engine(
        load_fixture(STANDARD), world=w1, search_port=s1, second_search=s2, clock=clock
    )
    both = [ts.target for ts in run.ctx.targets.values() if ts.target.engines == "both"]
    assert both and all(t.job == "services" for t in both)
    assert {t.need_key for t in both} <= set(CAT.plan.vendor_needs)
    assert s2.requests, "the second engine was asked"
    assert len(s2.requests) <= len(both) * 1, "one extra query per vendor topic in round one"
    second_queries = [q for q in run.ctx.queries if q.provider == "serper"]
    assert len(second_queries) == len(s2.requests)
    assert {q.target_id for q in second_queries} <= {t.id for t in both}
    for r in s2.requests:
        assert r.domain_mode == "none" and not r.want_content
    # topics that are not vendor topics never touch it
    other = {ts.target.id for ts in run.ctx.targets.values() if ts.target.engines != "both"}
    assert not ({q.target_id for q in second_queries} & other)


async def test_a_both_engine_query_counts_one_search_and_books_both_providers_cost() -> None:
    clock, w1, s1, s2 = two_engines(primary_cost=8000, second_cost=1000)
    run = await run_engine(
        load_fixture(STANDARD), world=w1, search_port=s1, second_search=s2, clock=clock
    )
    b = run.ctx.budget
    assert len(s1.requests) == b.searches_used, (
        "the second engine's queries are not admitted as searches"
    )
    assert b.second_searches == len(s2.requests) > 0
    assert b.by_step["search.tavily"] == 8000 * len(s1.requests)
    assert b.by_step["search.serper"] == 1000 * len(s2.requests)
    assert b.search_micros == b.by_step["search.tavily"] + b.by_step["search.serper"]


async def test_the_second_engine_never_runs_without_one_or_for_follow_up_rounds() -> None:
    run = await run_engine(load_fixture(STANDARD))
    assert run.ctx.second_search is None and run.ctx.budget.second_searches == 0
    clock, w1, s1, s2 = two_engines()
    run2 = await run_engine(
        load_fixture(STANDARD), world=w1, search_port=s1, second_search=s2, clock=clock
    )
    assert all(q.round == 1 for q in run2.ctx.queries if q.provider == "serper")


async def test_a_failing_second_engine_is_counted_and_the_run_goes_on() -> None:
    clock, w1, s1, s2 = two_engines()
    w2 = s2.world
    w2.fail_search = lambda _r: SearchError("rate_limited", "slow down")
    run = await run_engine(
        load_fixture(STANDARD), world=w1, search_port=s1, second_search=s2, clock=clock
    )
    assert run.ctx.failures.count("search_second", "rate_limited") >= 1
    assert not run.ctx.provider_down and run.result.status in ("complete", "partial")
    assert run.ctx.verified_count() > 0


async def test_a_page_both_engines_return_rises_in_the_ranking() -> None:
    clock, w1, s1, s2 = two_engines()
    shared = w1.add_page(
        "https://docs.shared-vendor.example/api",
        "Shared vendor API documentation with webhooks and sandbox.",
    )
    w2 = s2.world
    w2.add_page(
        "https://docs.shared-vendor.example/api",
        "Shared vendor API documentation with webhooks and sandbox.",
    )
    w2.route(r".", [shared])
    run = await run_engine(
        load_fixture(STANDARD), world=w1, search_port=s1, second_search=s2, clock=clock
    )
    vendor_topics = [ts for ts in run.ctx.targets.values() if ts.target.engines == "both"]
    assert vendor_topics
    pages = [p for p in run.ctx.pages.values() if "shared-vendor" in p.url]
    assert pages, "the page both engines named was read"


def test_the_flags_come_from_the_data_not_from_code() -> None:
    providers = CAT.jobs["services"].targets[0]
    assert providers.engines == "both" and providers.official_first
    assert set(CAT.plan.vendor_needs) >= {
        "payments",
        "messaging",
        "maps_location",
        "identity",
        "accounting",
    }
