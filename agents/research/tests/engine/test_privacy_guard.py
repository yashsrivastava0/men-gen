"""The privacy guard inside a run: a query that names the brand, a phone, an e-mail or an address never reaches a provider.

The first line is the query gate in the worker (a model's variant that names a private term fails like any anatomy
failure and the deterministic variant takes its place); the second is ``GuardedSearch`` just before the provider.
Every private value here is invented.
"""

from __future__ import annotations

import pytest
from support.testing import load_fixture, run_engine

from mde_research_agent.adapters.search.guarded import GuardedSearch
from mde_research_agent.calls.queries import QueryIn, QueryOut, Variant, deterministic_variants
from mde_research_agent.core.privacy import find_private
from mde_research_agent.engine.worker import _check
from mde_research_agent.ports.search import SearchError, SearchRequest

QUICK = "quick-yoga-studio-bengaluru"  # its Brief names the brand "Sunrise Yoga"
NASHIK = "standard-produce-marketplace-nashik"  # no brand, no phone, no address
BRAND = "Sunrise Yoga"
PHONE = "+91 98765 43210"
EMAIL = "owner.name@example-clinic.test"
STREET = "221B Baker Street"


def leaking(*leaks: str):  # type: ignore[no-untyped-def]
    """A model that puts a private value into every variant it writes."""

    def handle(inp: QueryIn) -> QueryOut:
        base = deterministic_variants(inp).variants
        return QueryOut(
            variants=[
                Variant(
                    text=f"{leaks[i % len(leaks)]} {v.text}",
                    style="descriptive",
                    language="en",
                    exact=False,
                    seed=False,
                )
                for i, v in enumerate(base)
            ]
        )

    return handle


def with_private_facts() -> dict:
    data = load_fixture(QUICK)
    data["brief"]["domain_facts"].append(
        {
            "id": "FCT-901",
            "type": "context",
            "statement": f"We are at {STREET}, call {PHONE} or write to {EMAIL}.",
            "topics": ["business"],
            "entities": [],
        }
    )
    return data


async def test_a_model_variant_that_names_the_brand_never_reaches_the_provider() -> None:
    run = await run_engine(load_fixture(QUICK), overrides={"rsch.write_queries": leaking(BRAND)})
    sent = [r.query for r in run.search.requests]
    assert sent and not any("sunrise" in q.casefold() for q in sent)
    assert not any("sunrise" in q["text"].casefold() for q in run.evidence["queries"])
    assert run.ctx.failures.private_blocked >= 1
    assert (
        run.result.status == "complete" and run.research["items"]
    )  # the deterministic variants did the work
    assert not run.violations  # RV-11 included


async def test_phone_email_and_address_leaks_from_a_model_are_blocked_too() -> None:
    run = await run_engine(
        with_private_facts(), overrides={"rsch.write_queries": leaking(PHONE, EMAIL, STREET, BRAND)}
    )
    sent = " | ".join(r.query for r in run.search.requests).casefold()
    for value in ("98765", "example-clinic", "baker street", "sunrise"):
        assert value not in sent, value
    assert run.ctx.failures.private_blocked >= 4 and not run.violations


async def test_the_run_says_so_once_and_counts_the_blocked_queries_without_naming_them() -> None:
    run = await run_engine(load_fixture(QUICK), overrides={"rsch.write_queries": leaking(BRAND)})
    warnings = [
        e for e in run.events if e.type == "warning" and e.detail.get("code") == "private_term"
    ]
    assert len(warnings) == 1 and "private" in warnings[0].detail["message"]
    assert not any("sunrise" in e.model_dump_json().casefold() for e in run.events)
    rows = [f for f in run.evidence["failures"] if f["reason"] == "private_term"]
    assert rows == [
        {"channel": "query", "reason": "private_term", "count": run.ctx.failures.private_blocked}
    ]
    assert run.ctx.failures.total == sum(  # blocking is protection, not a failure of the run
        f["count"] for f in run.evidence["failures"] if f["reason"] != "private_term"
    )


async def test_the_gate_holds_for_any_caller_follow_ups_and_model_proposed_topics_included() -> (
    None
):
    run = await run_engine(load_fixture(QUICK))
    ctx = run.ctx
    target = ctx.plan.targets[0]
    place = target.place.phrases[0] if target.place else ""
    clean = f"{target.anchors[0]} {target.intent[0]} {place}".strip()
    before = ctx.failures.private_blocked
    assert _check(ctx, target, clean, require_place=False)
    assert ctx.failures.private_blocked == before
    for leak in (f"{BRAND} {clean}", f"{clean} {BRAND}", f'"{BRAND}" {target.intent[0]} {place}'):
        assert not _check(ctx, target, leak, require_place=False)
    assert ctx.failures.private_blocked == before + 3


async def test_the_second_line_refuses_a_request_built_without_the_gate() -> None:
    run = await run_engine(load_fixture(QUICK))
    assert isinstance(run.ctx.search, GuardedSearch)
    sent_before = len(run.search.requests)
    with pytest.raises(SearchError) as err:
        await run.ctx.search.search(SearchRequest(query=f"{BRAND} class booking pricing"))
    assert err.value.kind == "private_term" and len(run.search.requests) == sent_before


async def test_a_brief_with_nothing_private_is_not_wrapped_and_nothing_is_blocked() -> None:
    run = await run_engine(load_fixture(NASHIK))
    assert not run.ctx.input.private and run.ctx.search is run.search
    assert run.ctx.failures.private_blocked == 0


async def test_no_query_of_an_ordinary_run_names_a_private_term_and_rv11_is_clean() -> None:
    data = with_private_facts()
    run = await run_engine(data)
    terms = run.ctx.input.private
    assert terms and {t.kind for t in terms.terms} >= {"brand", "phone", "email", "address"}
    assert not [r.query for r in run.search.requests if find_private(r.query, terms)]
    assert not [v for v in run.violations if v.rule == "RV-11"]


def test_the_query_gate_is_the_first_thing_checked() -> None:
    """A private query fails before the anatomy rules are looked at: there is no code path that sends it anyway."""
    import inspect

    from mde_research_agent.engine import worker

    src = inspect.getsource(worker._check)
    assert src.index("find_private") < src.index("check_query")
