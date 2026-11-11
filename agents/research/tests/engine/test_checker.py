"""RC-2: every verified item is checked inside its topic, before the coverage audit (RS-38, RS-44, RS-45)."""

from __future__ import annotations

from datetime import date

from support.fakes import FakeWorld
from support.testing import extractor, load_fixture, rule_extractor, run_engine

from mde_core.errors import ProviderError
from mde_research_agent.calls.check import CheckIn, CheckOut, CheckVerdict
from mde_research_agent.engine.checking import (
    meets_minimum,
    null_paths,
    passage_window,
)
from mde_research_agent.catalog import load_catalog
from mde_research_agent.engine.state import PageRecord

STANDARD = "standard-produce-marketplace-nashik"
QUICK = "quick-yoga-studio-bengaluru"


def supported(inp: CheckIn, **extra: list[str]) -> CheckOut:
    return CheckOut(
        verdicts=[CheckVerdict(id=i.id, verdict="supported", **extra) for i in inp.items]
    )


async def test_every_verified_item_has_a_verdict_or_the_unchecked_flag_and_batches_are_small() -> (
    None
):
    run = await run_engine(load_fixture(QUICK))
    checks = [inp for sid, inp in run.gateway.calls if sid == "rsch.check"]
    assert checks and all(len(c.items) <= 15 for c in checks)  # type: ignore[attr-defined]
    items = {i["id"]: i for i in run.research["items"]}
    for a in run.evidence["atoms"]:
        if a["status"] == "verified" and a["item_id"]:
            assert a["checked"] or "unchecked" in items[a["item_id"]]["flags"]
    # the check ran before the audit: every fact.verified event carries the verdict as its level
    verified = [e for e in run.events if e.type == "fact.verified"]
    assert verified and all(
        e.detail["level"] in ("supported", "partial", "unchecked") for e in verified
    )


async def test_the_checker_sees_the_passage_and_the_data_not_only_claim_and_quote() -> None:
    run = await run_engine(
        load_fixture(STANDARD), overrides={"rsch.extract.data": rule_extractor()}
    )
    sent = [i for sid, inp in run.gateway.calls if sid == "rsch.check" for i in inp.items]  # type: ignore[attr-defined]
    assert sent and all(i.passage and i.page_domain and i.data.startswith("{") for i in sent)
    assert any(len(i.passage.split()) > len(i.quote.split()) for i in sent)


async def test_a_rejected_item_reopens_its_slot_and_a_follow_up_runs_rs38() -> None:
    state = {"first": True}

    def check(inp: CheckIn) -> CheckOut:
        if state["first"]:  # the first batch of the run is judged unsupported
            state["first"] = False
            return CheckOut(
                verdicts=[CheckVerdict(id=i.id, verdict="not_supported") for i in inp.items]
            )
        return supported(inp)

    run = await run_engine(load_fixture(QUICK), overrides={"rsch.check": check})
    assert run.research["dropped"]["not_supported"] >= 1
    rejected = [
        e
        for e in run.events
        if e.type == "fact.rejected" and e.detail.get("reason") == "not_supported"
    ]
    assert rejected and all(e.detail.get("label") for e in rejected)
    assert not run.violations


async def test_a_failed_checker_leaves_items_unchecked_flagged_and_never_dropped_rs45() -> None:
    def broken(_: CheckIn) -> Exception:
        return ProviderError("down", status=503, retryable=False)

    run = await run_engine(load_fixture(QUICK), overrides={"rsch.check": broken})
    assert run.research["items"], "nothing was dropped because the checker failed"
    assert all(
        "unchecked" in i["flags"] and i["evidence"] == "unverified" for i in run.research["items"]
    )
    assert run.research["flagged"]["unchecked"] == len(run.research["items"])
    assert run.research["dropped"]["not_supported"] == 0
    assert not run.violations


async def test_identical_claim_quote_data_are_checked_once_across_the_run() -> None:
    text = "Payment fees are 2 percent of each sale and settlement takes three business days."
    w = FakeWorld(generate=False)
    urls = [
        w.add_page("https://docs.alpha.example/pay", text, published=date(2026, 8, 1)),
        w.add_page("https://docs.beta.example/pay", text, published=date(2026, 8, 2)),
    ]
    w.route(r".", urls)
    run = await run_engine(
        load_fixture(STANDARD), world=w, overrides={"rsch.extract.data": rule_extractor()}
    )
    sent = [i for sid, inp in run.gateway.calls if sid == "rsch.check" for i in inp.items]  # type: ignore[attr-defined]
    keys = [(i.claim, i.quote, i.data) for i in sent]
    assert len(keys) == len(set(keys)), "an identical item was sent to the checker twice"
    fees = [i for i in run.research["items"] if i["kind"] == "rule" and "fee" in i["claim"].lower()]
    assert fees and all(i["independent_domains"] == 1 for i in fees), (
        "the same paragraph on two domains is one source"
    )
    assert all("duplicate_source" in i["flags"] for i in fees)


async def test_fields_the_page_does_not_state_are_nulled_and_an_item_below_its_minimum_is_dropped_rs44() -> (
    None
):
    def check(inp: CheckIn) -> CheckOut:
        out = []
        for i in inp.items:
            fields = ["value"] if '"statement"' in i.data and '"value"' in i.data else []
            out.append(CheckVerdict(id=i.id, verdict="supported", unsupported_fields=fields))
        return CheckOut(verdicts=out)

    run = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": rule_extractor(), "rsch.check": check},
    )
    rules = [i for i in run.research["items"] if i["kind"] == "rule"]
    assert rules and all(i["data"].get("value") in (None, "") for i in rules)
    assert not run.violations  # RV-21: no unsupported field remains

    def drop_all(inp: CheckIn) -> CheckOut:
        return CheckOut(
            verdicts=[
                CheckVerdict(
                    id=i.id, verdict="supported", unsupported_fields=["statement", "subject"]
                )
                for i in inp.items
            ]
        )

    run2 = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": rule_extractor(), "rsch.check": drop_all},
    )
    assert not [i for i in run2.research["items"] if i["kind"] == "rule"]
    assert run2.research["dropped"]["unsupported_field"] >= 1


def test_null_paths_handles_fields_list_elements_and_nested_attributes() -> None:
    data = {
        "name": "x",
        "fees": "2%",
        "steps": ["a", "b", "c"],
        "fields": [{"name": "id", "type": "string"}, {"name": "when", "type": "date"}],
    }
    out, applied = null_paths(
        data, ["fees", "steps[1]", "fields[1].type", "nope", "fields[9]", "steps[1]"]
    )
    assert out["fees"] is None and out["steps"] == ["a", "c"]
    assert (
        out["fields"][1] == {"name": "when", "type": None} and out["fields"][0]["type"] == "string"
    )
    assert set(applied) == {"fees", "steps[1]", "fields[1].type"}
    assert data["fees"] == "2%"  # the input is never changed


def test_meets_minimum_after_nulling() -> None:
    lanes = load_catalog().lanes
    assert meets_minimum("rule", {"statement": "s", "subject": "x"}, lanes)
    assert not meets_minimum("rule", {"statement": None, "subject": "x"}, lanes)
    assert not meets_minimum("workflow", {"name": "n", "steps": []}, lanes)
    assert meets_minimum("workflow", {"name": "n", "steps": ["a"]}, lanes)
    assert not meets_minimum("entity_pattern", {"entity": "e", "fields": [{"name": None}]}, lanes)


def test_the_passage_window_is_about_150_words_around_the_quote() -> None:
    body = " ".join(f"w{i}" for i in range(400))
    text = f"{body}. The fee is 2 percent per sale. {body}."
    page = PageRecord(
        id="pg_1",
        url="https://a.example/x",
        final_url="https://a.example/x",
        domain="a.example",
        source_class="official",
        title="t",
        text=text,
        content_hash="h",
        text_source="fetch",
        published=None,
        retrieved_on=date(2026, 11, 11),
    )
    win = passage_window(page, "The fee is 2 percent per sale.")
    assert "The fee is 2 percent per sale." in win and 100 <= len(win.split()) <= 175
    assert (
        passage_window(page, "words that are nowhere on the page at all")
        == "words that are nowhere on the page at all"
    )
    assert extractor  # imported for parity with the other scenario tests


async def test_a_full_checker_batch_reaches_the_model_whole() -> None:
    """The gateway cuts untrusted text at a limit; a batch of 15 items with passages must fit under Research's limit."""
    from mde_research_agent.config import UNTRUSTED_CHARS
    from mde_research_agent.calls.check import CheckItem

    item = CheckItem(id="C1", claim="c " * 100, quote="q " * 40, data="d" * 2500, passage="p " * 450, page_title="t", page_domain="a.example")
    batch = CheckIn(items=[item.model_copy(update={"id": f"C{i}"}) for i in range(15)])
    assert len(batch.text) < UNTRUSTED_CHARS
