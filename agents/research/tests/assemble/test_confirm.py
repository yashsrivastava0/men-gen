"""P6: the confirm list is ranked by code: money, legal, identity first, then by impact; each entry has a rank, a group, a class and
a top-20 flag. Display is the front end's job."""

from __future__ import annotations

import asyncio
import random
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from support.content import (
    combine,
    compliance_extractor,
    fact_extractor,
    make_item,
    platform_extractor,
)
from support.testing import load_fixture, rule_extractor, run_engine

from mde_research_agent.assemble.confirm import class_of, group_of, rank_confirm
from mde_research_agent.catalog import load_catalog
from mde_research_agent.catalog.loader import DATA_DIR
from mde_research_agent.domain.research import Confirm, Item

CAT = load_catalog()


def typed(amount: str, unit: str = "percent", currency: str | None = None) -> dict[str, Any]:
    return {"amount": amount, "currency": currency, "unit": unit, "period": None, "condition": None}


def fee(iid: str, **kw: Any) -> Item:
    return make_item(iid, "Payco", "fees", "2.9 percent", typed=typed("2.9"), **kw)


def rule(iid: str, claim: str, **kw: Any) -> Item:
    item = make_item(
        iid,
        "",
        "",
        "",
        kind="rule",
        data={
            "statement": claim,
            "subject": "a subject",
            "value": "5",
            "unit": None,
            "formula": None,
            "authority": None,
        },
        **kw,
    )
    return item.model_copy(update={"claim": claim, "label": claim[:30]})


def note(iid: str, **kw: Any) -> Item:
    return make_item(
        iid,
        "",
        "",
        "",
        kind="compliance_note",
        data={
            "topic": "retention",
            "authority": "The Regulator",
            "summary": "records are kept",
            "effective_date": None,
            "regime": "Data Act",
            "country": None,
            "applies_to": [],
            "penalty": None,
        },
        **kw,
    )


def entries(*items: Item) -> list[Confirm]:
    return [Confirm(item_id=i.id, why="why") for i in items]


# --- classes ---------------------------------------------------------------------------------------------------------------------


def test_the_classes_money_legal_identity_other() -> None:
    cfg = CAT.confirm
    assert class_of(fee("RI-001"), cfg) == "money"
    assert (
        class_of(
            make_item("RI-002", "Payco", "limits", "100", typed=typed("100", "request", "USD")), cfg
        )
        == "money"
    ), "a currency"
    assert class_of(rule("RI-003", "The platform fee is 5 percent of each order"), cfg) == "money"
    assert class_of(note("RI-004"), cfg) == "legal"
    assert class_of(rule("RI-005", "Records are kept", flags=["legal-adjacent"]), cfg) == "legal"
    assert (
        class_of(rule("RI-006", "Drivers need a verification step and a passport check"), cfg)
        == "identity"
    )
    assert class_of(rule("RI-007", "Orders ship within 3 days"), cfg) == "other"


def test_money_needs_a_figure_and_the_first_class_that_matches_wins() -> None:
    assert (
        class_of(
            rule("RI-001", "The fee schedule is published yearly").model_copy(
                update={"data": {"statement": "the fee schedule", "subject": "x"}}
            ),
            CAT.confirm,
        )
        == "other"
    )
    # a compliance note that states a fine with a figure is money (the first class), not legal
    fine = note("RI-002").model_copy(update={"claim": "A fine of 5000 applies", "label": "Fine"})
    assert class_of(fine, CAT.confirm) == "money"


def test_a_word_is_matched_as_a_word_not_as_a_substring() -> None:
    assert (
        class_of(rule("RI-001", "Users read feedback about the ride rated 5"), CAT.confirm)
        == "other"
    ), "feedback is not a fee"


# --- groups ----------------------------------------------------------------------------------------------------------------------


def test_the_group_is_what_the_item_is_about() -> None:
    assert group_of(fee("RI-001")) == "Payco"
    assert group_of(note("RI-002")) == "Data Act"
    assert group_of(rule("RI-003", "x")) == "a subject"
    pc = make_item(
        "RI-004",
        "",
        "",
        "",
        kind="platform_constraint",
        data={
            "capability": "deep links",
            "platform": None,
            "rule": "r",
            "effect_on_build": None,
            "status": None,
        },
    )
    assert group_of(pc) == "deep links"
    assert (
        group_of(make_item("RI-005", "", "", "", kind="trend", data={"what_changed": "x"}))
        == "services"
    )


# --- order -----------------------------------------------------------------------------------------------------------------------


def test_the_list_is_money_then_legal_then_identity_then_other_and_ranks_are_dense() -> None:
    items = [
        rule("RI-001", "Orders ship within 3 days"),
        note("RI-002"),
        rule("RI-003", "Drivers need a verification step"),
        fee("RI-004"),
        rule("RI-005", "Another plain rule"),
    ]
    out = rank_confirm(entries(*items), items, CAT.confirm)
    assert [c.item_id for c in out] == ["RI-004", "RI-002", "RI-003", "RI-001", "RI-005"]
    assert [c.class_ for c in out] == ["money", "legal", "identity", "other", "other"]
    assert [c.rank for c in out] == [1, 2, 3, 4, 5]
    assert all(c.top for c in out)


def test_inside_a_class_high_impact_conflicts_and_stronger_evidence_come_first() -> None:
    a = fee("RI-001", evidence="single_source").model_copy(update={"impact": "normal"})
    b = fee("RI-002", evidence="official").model_copy(update={"impact": "normal"})
    c = fee("RI-003", evidence="single_source").model_copy(update={"impact": "high"})
    d = fee("RI-004", evidence="single_source", flags=["conflict"]).model_copy(
        update={"impact": "normal"}
    )
    out = rank_confirm(entries(a, b, c, d), [a, b, c, d], CAT.confirm)
    assert [x.item_id for x in out] == ["RI-003", "RI-004", "RI-002", "RI-001"]


def test_only_the_first_twenty_are_flagged_top() -> None:
    items = [fee(f"RI-{n:03d}") for n in range(1, 26)]
    out = rank_confirm(entries(*items), items, CAT.confirm)
    assert [c.top for c in out] == [True] * 20 + [False] * 5
    assert CAT.confirm.top == 20


def test_the_order_does_not_depend_on_the_input_order_and_an_unknown_item_goes_last() -> None:
    items = [fee("RI-001"), note("RI-002"), rule("RI-003", "Orders ship in 3 days")]
    want = [
        c.model_dump()
        for c in rank_confirm(
            entries(*items, make_item("RI-009", "X", "status", "active")), [*items], CAT.confirm
        )
    ]
    assert (want[-1]["item_id"] == "RI-009" and want[-1]["class_"] == "other") or want[-1][
        "class"
    ] == "other"
    for seed in range(5):
        shuffled = entries(*items, make_item("RI-009", "X", "status", "active"))
        random.Random(seed).shuffle(shuffled)
        assert [c.model_dump() for c in rank_confirm(shuffled, [*items], CAT.confirm)] == want


def test_the_added_fields_are_additive_and_serialise_with_the_class_key() -> None:
    (c,) = rank_confirm(entries(fee("RI-001")), [fee("RI-001")], CAT.confirm)
    dumped = c.model_dump(mode="json", by_alias=True)
    assert dumped == {
        "item_id": "RI-001",
        "why": "why",
        "rank": 1,
        "group": "Payco",
        "class": "money",
        "top": True,
    }
    assert Confirm(item_id="RI-001", why="w").class_ == "other", "an older entry still loads"


# --- the data file and a whole run -------------------------------------------------------------------------------------------------


def test_the_class_order_is_closed(tmp_path: Path) -> None:
    root = tmp_path / "data"
    shutil.copytree(DATA_DIR, root)
    raw = yaml.safe_load((root / "confirm.yaml").read_text(encoding="utf-8"))
    raw["classes"] = {
        "legal": raw["classes"]["legal"],
        "money": raw["classes"]["money"],
        "identity": {},
        "other": {},
    }
    (root / "confirm.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception, match="money, legal, identity, other"):
        load_catalog(root)


def test_a_real_runs_confirm_list_is_ranked_and_money_comes_before_legal() -> None:
    run = asyncio.run(
        run_engine(
            load_fixture("standard-produce-marketplace-nashik"),
            overrides={
                "rsch.extract.services": fact_extractor(),
                "rsch.extract.platform": platform_extractor(),
                "rsch.extract.data": combine(rule_extractor(), compliance_extractor()),
            },
        )
    )
    confirm = run.research["confirm"]
    assert [c["rank"] for c in confirm] == list(range(1, len(confirm) + 1))
    classes = [c["class"] for c in confirm]
    order = ["money", "legal", "identity", "other"]
    assert classes == sorted(classes, key=order.index), "money, legal, identity, other"
    assert "money" in classes and "legal" in classes
    assert [c["top"] for c in confirm] == [n <= 20 for n in range(1, len(confirm) + 1)]
    assert all(c["group"] for c in confirm)
    assert run.violations == []
