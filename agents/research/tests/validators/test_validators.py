"""Every validator RV-01..RV-23 has a passing and a failing test (RV-11 is reserved and does nothing)."""

import asyncio
import copy
from collections.abc import Callable
from typing import Any

import pytest
from support.testing import load_fixture, rule_extractor, run_engine

from mde_research_agent.catalog import load_catalog
from mde_research_agent.domain.events import Event
from mde_research_agent.validators import REGISTRY, ValidationContext, run_all
from mde_research_agent.validators.registry import must_refuse

CAT = load_catalog()


@pytest.fixture(scope="module")
def good():
    return asyncio.run(
        run_engine(
            load_fixture("standard-produce-marketplace-nashik"),
            overrides={"rsch.extract.data": rule_extractor()},
        )
    )


def vctx(
    good, mutate: Callable[[dict[str, Any], dict[str, Any], list[Event]], None] | None = None
) -> ValidationContext:
    out, ev, events = (
        copy.deepcopy(good.research),
        copy.deepcopy(good.evidence),
        [e.model_copy(deep=True) for e in good.events],
    )
    if mutate:
        mutate(out, ev, events)
    cs = CAT.ceilings.for_class(good.ctx.input.load.load_class)
    return ValidationContext(
        output=out,
        evidence=ev,
        inp=good.ctx.input,
        catalog=CAT,
        plan=good.ctx.plan,
        events=events,
        ceilings_hard=cs.hard.model_dump(),
        store_tokens=cs.store_tokens,
        chunk_items=cs.chunk_items,
        digest_tokens=cs.digest_tokens,
    )


def rules_of(violations) -> set[str]:
    return {v.rule for v in violations}


def test_a_good_run_passes_every_validator(good) -> None:
    assert run_all(vctx(good)) == []
    assert set(REGISTRY) == {f"RV-{i:02d}" for i in range(1, 24)}


def high_item(out: dict[str, Any]) -> dict[str, Any]:
    return next(i for i in out["items"] if i["impact"] == "high")


@pytest.mark.parametrize(
    ("rule", "mutate"),
    [
        ("RV-01", lambda o, e, ev: o["items"][0].pop("claim")),
        ("RV-01", lambda o, e, ev: o["items"][0].update(data={"nonsense": 1})),
        (
            "RV-02",
            lambda o, e, ev: next(a for a in e["atoms"] if a["status"] == "verified").update(
                quote="words that are nowhere on the stored page at all"
            ),
        ),
        (
            "RV-03",
            lambda o, e, ev: o["items"][0]["sources"][0].update(
                url="https://not-in-the-fetch-log.example/x"
            ),
        ),
        ("RV-03", lambda o, e, ev: o["items"][0]["sources"][0].update(retrieved_on="2001-01-01")),
        ("RV-04", lambda o, e, ev: o["items"][0].update(tier="locked")),
        ("RV-05", lambda o, e, ev: o["items"][0].update(use_note="Costs 9999 dollars at Fakecorp")),
        (
            "RV-05",
            lambda o, e, ev: o["items"][0].update(
                use_note="one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
            ),
        ),
        (
            "RV-06",
            lambda o, e, ev: e["queries"][0].update(text="yoga https://evil.example/x features"),
        ),
        (
            "RV-06",
            lambda o, e, ev: e["queries"][0].update(
                text="tvly-abcdefghijklmnop1234 yoga features Bengaluru"
            ),
        ),
        (
            "RV-07",
            lambda o, e, ev: high_item(o).update(
                independent_domains=1,
                flags=[f for f in high_item(o)["flags"] if f != "single-source"],
            ),
        ),
        (
            "RV-08",
            lambda o, e, ev: next(a for a in e["atoms"] if a.get("item_id")).update(
                verdict="not_supported"
            ),
        ),
        ("RV-09", lambda o, e, ev: o["slices"]["blueprint"].update(digest="x" * 200_000)),
        (
            "RV-10",
            lambda o, e, ev: e["targets"].append(
                {
                    "id": "T-99",
                    "label": "ghost",
                    "job": "data",
                    "status": "not_found",
                    "reason": None,
                    "slots_required": 1,
                    "slots_filled": 0,
                    "queries": [],
                    "followups": 0,
                }
            ),
        ),
        ("RV-12", lambda o, e, ev: o["budget"].update(searches_used=10_000)),
        ("RV-13", lambda o, e, ev: o.update(brief_hash="0" * 64)),
        ("RV-14", lambda o, e, ev: e["queries"][0].update(text="features")),
        (
            "RV-15",
            lambda o, e, ev: [
                e.update(
                    atoms=[
                        a
                        for a in e["atoms"]
                        if a["target_id"]
                        != next(t["id"] for t in e["targets"] if t["status"] == "covered")
                    ]
                )
            ],
        ),
        (
            "RV-16",
            lambda o, e, ev: o["items"][0]["applies_to"].append(
                {"kind": "FTR", "id": "FTR-999", "relation": "expects"}
            ),
        ),
        (
            "RV-18",
            lambda o, e, ev: next(a for a in e["atoms"] if a.get("item_id")).update(
                claim="Costs 987654 dollars every month",
                quote="a sentence with no figure in it at all",
            ),
        ),
        (
            "RV-19",
            lambda o, e, ev: next(i for i in o["items"] if i["independent_domains"] >= 1).update(
                independent_domains=77
            ),
        ),
        (
            "RV-20",
            lambda o, e, ev: (
                [
                    a.update(about_region="zz")
                    for a in e["atoms"]
                    if a.get("item_id") and e["targets"]
                ]
                and [t.update(place="in") for t in e["targets"]]
            ),
        ),
        (
            "RV-21",
            lambda o, e, ev: [
                next(a for a in e["atoms"] if a.get("item_id")).update(checked=False),
                [i.update(flags=[f for f in i["flags"] if f != "unchecked"]) for i in o["items"]],
            ],
        ),
        ("RV-22", lambda o, e, ev: o["profile"].update(store_tokens=10_000_000)),
        (
            "RV-22",
            lambda o, e, ev: o["items"].append(
                o["items"][0] | {"id": "RI-9999", "store": "appendix", "appendix_reason": None}
            ),
        ),
        (
            "RV-23",
            lambda o, e, ev: o["items"][0].update(
                evidence="official" if o["items"][0]["evidence"] != "official" else "unverified"
            ),
        ),
        (
            "RV-23",
            lambda o, e, ev: o["lane_quality"]["blueprint"].update(
                quality="strong"
                if o["lane_quality"]["blueprint"]["quality"] != "strong"
                else "none"
            ),
        ),
        ("RV-17", lambda o, e, ev: ev[0].detail.update(secret_field="x")),
        ("RV-17", lambda o, e, ev: ev[3].__setattr__("label", " ")),
    ],
)
def test_each_validator_catches_its_failure(good, rule: str, mutate) -> None:
    got = run_all(vctx(good, mutate), only=[rule])
    assert rule in rules_of(got), f"{rule} did not fire: {got}"


def test_rv17_catches_page_text_in_an_event(good) -> None:
    def mutate(o, e, ev) -> None:
        quote = next(a["quote"] for a in e["atoms"] if len(a["quote"]) > 40)
        ev[5].__setattr__("label", f"Found: {quote}")

    assert "RV-17" in rules_of(run_all(vctx(good, mutate), only=["RV-17"]))


def test_rv11_is_reserved_and_never_fires(good) -> None:
    assert run_all(vctx(good), only=["RV-11"]) == []


def test_only_binding_and_tier_failures_refuse_the_commit(good) -> None:
    refused = must_refuse(
        run_all(
            vctx(
                good,
                lambda o, e, ev: (  # pyright: ignore[reportArgumentType]
                    o.update(brief_hash="0" * 64),
                    o["items"][0].update(tier="locked"),
                ),
            )
        )
    )
    assert {v.rule for v in refused} == {"RV-04", "RV-13"}
    assert (
        must_refuse(run_all(vctx(good, lambda o, e, ev: e["queries"][0].update(text="features"))))
        == []
    )


def test_a_crashing_validator_is_reported_not_raised(good) -> None:
    bad = vctx(good, lambda o, e, ev: o.update(items="not a list"))
    assert isinstance(run_all(bad), list)


def test_rv21_does_not_call_a_removed_list_element_a_leftover() -> None:
    """The first live run with many checked items had 10 false RV-21 errors: re-applying "steps[0]" to a list whose first
    element was already removed removes the NEXT element, which looked like a leftover."""
    from types import SimpleNamespace

    from mde_research_agent.validators.registry import rv21_checked

    item = {"id": "RI-001", "flags": []}
    after_removal = {"steps": ["second", "third"], "purpose": None, "role": None}
    atom = {
        "status": "verified",
        "item_id": "RI-001",
        "kind": "workflow",
        "checked": True,
        "unsupported_fields": ["steps[0]", "purpose"],
        "data": after_removal,
    }
    ctx = SimpleNamespace(output={"items": [item]}, evidence={"atoms": [atom]})
    assert rv21_checked(ctx) == []  # type: ignore[arg-type]
    # a scalar the checker flagged but that is still set IS a leftover
    atom["data"] = {"steps": ["second"], "purpose": "still here", "role": None}
    assert [v.rule for v in rv21_checked(ctx)] == ["RV-21"]  # type: ignore[arg-type]
