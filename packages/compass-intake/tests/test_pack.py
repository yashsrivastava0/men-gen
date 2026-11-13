"""The contract of ``pack(research, need_card)`` (copied from Research's ``tests/assemble/test_pack.py``, D-175; docs/research-agent/spec-contract.md):
select by structure, never miss an item that applies to a named Brief id, split and never truncate, respect the token budget,
be deterministic, and use no model. The synthetic cases are the same; the "runs" are a real Research output."""

from __future__ import annotations

import ast
import copy
import json
import random
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake.pack import MIN_TOKEN_BUDGET, compact_item, load_default_notes, pack

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src" / "mde_compass_intake"
RESEARCH_OUTPUT = (
    HERE.parents[2]
    / "docs"
    / "research-agent"
    / "outputs"
    / "claude-2026-11-13"
    / "medium-produce-marketplace-nashik"
    / "research.json"
)
NOTES = load_default_notes()


def make(i: int, applies: list[tuple[str, str | None]], **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"RI-{i:03d}",
        "job": "features",
        "kind": "competitor",
        "label": f"Label {i}",
        "claim": "A claim about it. " * 3,
        "impact": "normal",
        "evidence": "single_source",
        "tier": "default",
        "confidence": "medium",
        "use_note": "",
        "flags": [],
        "applies_to": [{"kind": k, "id": x, "relation": "expects"} for k, x in applies],
        "sources": [
            {
                "id": f"S-{i:03d}",
                "domain": "docs.example",
                "source_class": "official",
                "url": f"https://docs.example/{i}",
                "published_on": None,
            }
        ],
        "independent_domains": 1,
        "data": {"name": f"thing {i}"},
        "rank": i,
        "store": "main",
    }
    base.update(kw)
    return base


def small_research() -> dict[str, Any]:
    items = [
        make(1, [("FTR", "FTR-001"), ("ENT", "ENT-002")]),
        make(2, [("ENT", "ENT-002")], kind="entity_pattern"),
        make(3, [("FTR", "FTR-009")]),
        make(4, [("NEW", None)]),
        make(5, [("FTR", "FTR-001")], store="appendix", rank=90),
        make(6, [("INT", "payments")], kind="provider_fact", job="services"),
        make(7, [("FTR", "FTR-003")], kind="rule", flags=["conflict"]),
        make(8, [("FTR", "FTR-004")], evidence="unverified", rank=95, store="appendix"),
    ]
    return {
        "program_id": "ses_x",
        "brief_hash": "h" * 8,
        "items": items,
        "slices": {
            "blueprint": {"item_ids": ["RI-001", "RI-002", "RI-003", "RI-004", "RI-007"]},
            "integrations": {"item_ids": ["RI-006"]},
        },
        "lane_decisions": [
            {
                "id": "BP-FEATURES",
                "lane": "blueprint",
                "text": "t",
                "item_ids": ["RI-003", "RI-001"],
                "unverified_item_ids": [],
                "best_evidence": "single_source",
                "gap": None,
            }
        ],
        "dossiers": [],
        "platform_constraints": [],
        "confirm": [
            {
                "item_id": "RI-007",
                "why": "w",
                "rank": 1,
                "group": "g",
                "class": "other",
                "top": True,
            }
        ],
        "conflicts": [{"topic": "t", "item_ids": ["RI-007", "RI-003"], "note": ""}],
    }


def in_packs(out: dict[str, Any]) -> list[str]:
    return [i["id"] for p in out["packs"] for i in p["items"]]


# --- selection by structure ----------------------------------------------------------------------------------------------------------


def test_direct_items_come_first_then_one_hop_and_the_rest_is_a_title_only_index() -> None:
    out = pack(small_research(), {"brief_ids": ["FTR-001"]})
    got = in_packs(out)
    assert got[:2] == ["RI-001", "RI-005"], (
        "items that name FTR-001, main store first, appendix after (rank order)"
    )
    assert "RI-002" in got, "one hop: RI-001 also names ENT-002, so RI-002 comes along"
    assert [i["why"] for p in out["packs"] for i in p["items"]][:3] == ["direct", "direct", "hop"]
    assert "RI-003" not in got and "RI-004" not in got and "RI-006" not in got
    assert {i["id"] for i in out["index"]} >= {"RI-003", "RI-004", "RI-006"}
    for row in out["index"]:
        assert set(row) - {"store"} == {"id", "kind", "label", "evidence"}, (
            "title only: no claim, no data"
        )


def test_new_and_integration_ids_can_be_named() -> None:
    assert in_packs(pack(small_research(), {"brief_ids": ["NEW"]}))[:1] == ["RI-004"]
    assert in_packs(pack(small_research(), {"brief_ids": ["INT:payments"]}))[:1] == ["RI-006"]
    assert in_packs(pack(small_research(), {"brief_ids": ["payments"]}))[:1] == ["RI-006"]


def test_a_hop_does_not_continue_past_one_step() -> None:
    r = small_research()
    r["items"].append(make(9, [("ENT", "ENT-005")], rank=10))
    r["items"].append(make(10, [("ENT", "ENT-005"), ("FLW", "FLW-001")], rank=11))
    r["items"].append(make(12, [("FLW", "FLW-001")], rank=13))
    assert in_packs(pack(r, {"brief_ids": ["FTR-009"]})) == ["RI-003"], (
        "RI-003 names nothing else, so nothing hops"
    )
    r["items"].append(make(11, [("FTR", "FTR-009"), ("ENT", "ENT-005")], rank=12))
    got = in_packs(pack(r, {"brief_ids": ["FTR-009"]}))
    assert {"RI-009", "RI-010"} <= set(got), "one hop reaches the entity's other items"
    assert "RI-012" not in got, "but not the neighbours of those items (two hops)"


def test_an_unknown_brief_id_selects_nothing_and_everything_is_indexed() -> None:
    r = small_research()
    out = pack(r, {"brief_ids": ["FTR-404"]})
    assert (
        out["packs"] == [] and len(out["index"]) == len(r["items"]) and out["stats"]["direct"] == 0
    )


def test_lane_essentials_the_best_of_each_kind_conflicts_and_the_decisions_items_come_along() -> (
    None
):
    out = pack(
        small_research(), {"brief_ids": ["FTR-404"], "lane": "blueprint", "essentials_per_kind": 1}
    )
    got = in_packs(out)
    assert {"RI-001", "RI-002", "RI-007", "RI-003"} <= set(got), (
        "best per kind (competitor, entity), a conflict, the decision's items"
    )
    assert {i["why"] for p in out["packs"] for i in p["items"]} == {"lane"}
    first = out["packs"][0]
    assert [d["id"] for d in first["lane_decisions"]] == ["BP-FEATURES"], (
        "the lane's decisions open the first pack"
    )
    assert [c["item_id"] for c in first["confirm"]] == ["RI-007"] and first["conflicts"], (
        "confirm and conflicts of selected items"
    )


def test_a_kind_filter_keeps_other_kinds_in_the_index() -> None:
    out = pack(small_research(), {"brief_ids": ["FTR-001"], "kinds": ["competitor"]})
    assert in_packs(out) == ["RI-001", "RI-005"]
    assert "RI-002" in {i["id"] for i in out["index"]}


def test_dossiers_of_the_selected_providers_come_with_the_items() -> None:
    r = small_research()
    r["dossiers"] = [
        {
            "id": "DS-001",
            "provider": "Payco",
            "capability": "payments",
            "integrations": ["payments"],
            "item_ids": ["RI-006"],
            "fields": {
                "fees": {
                    "value": None,
                    "null_reason": "not_researched",
                    "values": [],
                    "item_ids": [],
                }
            },
            "required": ["fees"],
            "completeness": {"filled": 0, "required": 1, "share": 0.0, "unverified": 0},
            "official_share": 0.0,
            "needs": [],
            "service_option_ids": [],
        },
        {
            "id": "DS-002",
            "provider": "Other",
            "capability": "messaging",
            "integrations": ["messaging"],
            "item_ids": ["RI-099"],
            "fields": {},
            "required": [],
            "completeness": {"filled": 0, "required": 0, "share": 0.0, "unverified": 0},
            "official_share": 0.0,
            "needs": [],
            "service_option_ids": [],
        },
    ]
    out = pack(r, {"brief_ids": ["INT:payments"]})
    assert [d["id"] for p in out["packs"] for d in p["dossiers"]] == ["DS-001"]
    assert out["packs"][0]["dossiers"][0]["fields"]["fees"] == {"null_reason": "not_researched"}, (
        "an unknown stays an explicit null with its reason"
    )


# --- the contract --------------------------------------------------------------------------------------------------------------------


def all_brief_ids(research: dict[str, Any]) -> set[str]:
    return {a["id"] for i in research["items"] for a in i["applies_to"] if a.get("id")}


@pytest.fixture(scope="module")
def runs() -> dict[str, dict[str, Any]]:
    if not RESEARCH_OUTPUT.exists():
        pytest.skip("the recorded research output is not in this checkout")
    research = json.loads(RESEARCH_OUTPUT.read_text(encoding="utf-8"))
    # the real file is the whole contract; a second, reordered and trimmed copy covers the rest
    trimmed = {
        **research,
        "items": [i for i in research["items"] if i.get("store", "main") == "main"],
    }
    return {"medium-produce-marketplace-nashik": research, "main-store-only": trimmed}


def test_it_never_misses_an_item_that_applies_to_a_named_brief_id(
    runs: dict[str, dict[str, Any]],
) -> None:
    for name, research in runs.items():
        ids = sorted(all_brief_ids(research))
        assert ids, name
        for brief_id in ids:
            out = pack(research, {"brief_ids": [brief_id], "token_budget": 2000})
            got = set(in_packs(out))
            applying = {
                i["id"]
                for i in research["items"]
                if any(a.get("id") == brief_id for a in i["applies_to"])
            }
            assert applying <= got, f"{name}: {brief_id} misses {sorted(applying - got)}"
        several = pack(research, {"brief_ids": ids[:5]})
        assert {
            i["id"]
            for i in research["items"]
            if {a.get("id") for a in i["applies_to"]} & set(ids[:5])
        } <= set(in_packs(several))


def test_every_item_is_in_a_pack_or_the_index_exactly_once(runs: dict[str, dict[str, Any]]) -> None:
    for research in runs.values():
        out = pack(
            research,
            {
                "brief_ids": sorted(all_brief_ids(research))[:3],
                "lane": "integrations",
                "token_budget": 1500,
            },
        )
        packed, indexed = in_packs(out), [i["id"] for i in out["index"]]
        assert len(packed) == len(set(packed)) and not set(packed) & set(indexed)
        assert sorted(packed + indexed) == sorted(i["id"] for i in research["items"]), (
            "nothing is left out of the file"
        )


def test_it_is_deterministic_whatever_the_order_of_the_items(
    runs: dict[str, dict[str, Any]],
) -> None:
    research = runs["medium-produce-marketplace-nashik"]
    card = {
        "brief_ids": sorted(all_brief_ids(research))[:4],
        "lane": "blueprint",
        "token_budget": 1200,
    }
    want = pack(research, card)
    assert pack(research, card) == want
    for seed in range(4):
        shuffled = copy.deepcopy(research)
        random.Random(seed).shuffle(shuffled["items"])
        assert pack(shuffled, card) == want


def test_a_pack_that_is_too_large_is_split_never_truncated(runs: dict[str, dict[str, Any]]) -> None:
    research = runs["main-store-only"]
    card = {"brief_ids": sorted(all_brief_ids(research))[:6], "lane": "integrations"}
    whole = pack(research, {**card, "token_budget": 10**9})
    assert len(whole["packs"]) == 1
    small = pack(research, {**card, "token_budget": MIN_TOKEN_BUDGET})
    assert len(small["packs"]) > 1, "a small budget splits the selection"
    assert small["packs"][-1]["of"] == len(small["packs"]) and [
        p["no"] for p in small["packs"]
    ] == list(range(1, len(small["packs"]) + 1))
    assert in_packs(small) == in_packs(whole), "the same items in the same order: split, not cut"
    by_id = {i["id"]: i for i in research["items"]}
    for p in small["packs"]:
        for item in p["items"]:
            full = compact_item(by_id[item["id"]], NOTES)
            assert {k: v for k, v in item.items() if k != "why"} == full, "an item is never cut"
    for key in ("lane_decisions", "dossiers", "platform_constraints", "confirm", "conflicts"):
        assert sum(len(p[key]) for p in small["packs"]) == sum(
            len(p[key]) for p in whole["packs"]
        ), key


def test_every_pack_respects_the_token_budget_unless_one_unit_stands_alone(
    runs: dict[str, dict[str, Any]],
) -> None:
    for research in runs.values():
        for budget in (MIN_TOKEN_BUDGET, 900, 2500, 8000):
            out = pack(
                research,
                {
                    "brief_ids": sorted(all_brief_ids(research))[:6],
                    "lane": "blueprint",
                    "token_budget": budget,
                },
            )
            for p in out["packs"]:
                units = sum(
                    len(p[k])
                    for k in (
                        "lane_decisions",
                        "items",
                        "dossiers",
                        "platform_constraints",
                        "confirm",
                        "conflicts",
                    )
                )
                assert p["tokens"] <= budget or (p["over_budget"] and units == 1), (
                    budget,
                    p["no"],
                    p["tokens"],
                    units,
                )
            assert out["stats"]["tokens"] == sum(p["tokens"] for p in out["packs"])


def test_a_unit_larger_than_the_budget_stands_alone_whole() -> None:
    r = small_research()
    r["items"][0]["claim"] = "word " * 4000
    out = pack(r, {"brief_ids": ["FTR-001"], "token_budget": MIN_TOKEN_BUDGET})
    big = next(p for p in out["packs"] if any(i["id"] == "RI-001" for i in p["items"]))
    assert big["over_budget"] and [i["id"] for i in big["items"]] == ["RI-001"]
    assert big["items"][0]["claim"] == "word " * 4000, "whole, never cut"


def test_the_card_is_normalised_and_the_input_is_not_changed() -> None:
    r = small_research()
    before = copy.deepcopy(r)
    out = pack(r, {"brief_ids": [" FTR-001 ", "FTR-001", ""], "token_budget": 1})
    assert r == before
    assert out["need_card"] == {
        "brief_ids": ["FTR-001"],
        "lane": None,
        "token_budget": MIN_TOKEN_BUDGET,
        "kinds": None,
        "essentials_per_kind": 2,
    }
    assert out["schema"] == "research.pack.v1" and out["program_id"] == "ses_x"


def test_it_uses_no_model_no_embedding_and_no_other_package_of_the_repo() -> None:
    """Pack is pure code; the whole intake package imports no agent, no engine and no model vendor (D-175)."""
    banned = {
        "mde_research_agent",
        "mde_compass",
        "mde_llm_gateway",
        "openai",
        "anthropic",
        "numpy",
    }
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        bad = {m for m in modules if m.split(".")[0] in banned or "embed" in m}
        assert not bad, (path.name, bad)
    code = (SRC / "pack.py").read_text(encoding="utf-8").split('"""', 2)[2]  # after the docstring
    assert "similarity" not in code and "cosine" not in code
