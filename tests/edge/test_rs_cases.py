"""SPC-RS-01..12: input from Research (doc 15 section 12.2). The recorded real Research run for the standard export, mutated or
cut down, plus small synthetic files; full offline runs through the service in the gateway's mock mode."""

from __future__ import annotations

from functools import cache
from typing import Any

import pytest
from edge_support.ea_support import (
    cached_flow,
    derive,
    raw,
    real,
    research_raw,
)
from edge_support.registry import case

from mde_compass_intake import pack, read_research


def _blob(obj: Any) -> str:
    return str(obj).lower()


@cache
def view_of(label: str):
    return read_research(_variant(label))


def _variant(label: str) -> dict[str, Any]:
    r = research_raw()
    if label == "partial":
        r["status"] = "partial"
    elif label == "mostly_unverified":
        for i, it in enumerate(r["items"]):
            it["evidence"] = "unverified" if i % 2 else "single_source"
    elif label == "stale":
        r["brief_hash"] = "f" * 64
    elif label == "no_screens_lane":
        r["slices"]["screens"] = {"item_ids": []}
        r["norms"] = [n for n in r["norms"] if n["area"] != "screen"]
    elif label == "new_schema":
        r["schema"] = "research.v6"
        r["brand_new_section"] = {"anything": [1, 2, 3]}
        r["items"][0]["a_field_from_the_future"] = "x"
    elif label == "no_provider":
        r["dossiers"] = []
        r["dossiers_by_integration"] = {}
        r["slices"]["integrations"] = {"item_ids": []}
        r["lane_decisions"].append(
            {
                "id": "INT-GAP",
                "lane": "integrations",
                "text": "no payment provider found for this market",
                "item_ids": [],
                "unverified_item_ids": [],
                "best_evidence": "",
                "gap": "no in-market payment provider found; only global providers",
            }
        )
    return r


# ------------------------------------------------------------------------------------------- RS-01, RS-02


@case("spc-rs-01")
def test_failed_or_missing_research_gives_a_thin_lane_marked_assumed_and_never_a_crash(
    tmp_path_factory,
):
    failed = {"schema": "research.v5", "status": "failed", "items": []}
    for label, research in (("none", None), ("failed", failed)):
        f = cached_flow(tmp_path_factory, f"rs-{label}", lambda: raw("standard"), research=research)
        assert f.status == "approved", label
        g2 = f.g2()
        assert "thin" in _blob(g2["risks"]), label  # G2 says the research was incomplete
        assert not g2["suggestions"] or all("peer-" not in s["id"] for s in g2["suggestions"])
        # libraries fill, marked assumed
        assert g2["assumptions"]
        # nothing cites research
        provs = [p.kind for n in f.graph.all_nodes() for p in n.provenance]
        assert "research" not in provs, label


@case("spc-rs-02")
def test_partial_research_uses_what_it_has_and_flags_the_gap(tmp_path_factory):
    v = view_of("partial")
    assert v.thin and "partial" in v.thin_reasons[0] and len(v.items) == 361
    f = cached_flow(
        tmp_path_factory, "rs-partial", lambda: raw("standard"), research=_variant("partial")
    )
    assert f.status == "approved"
    g2 = f.g2()
    assert "thin" in _blob(g2["risks"])  # the gap is flagged
    assert g2["suggestions"]  # what the file holds is still read by the derivation (peer check)
    assert next(i.provider_ids for i in f.graph.integrations.values())  # dossiers still read


# ------------------------------------------------------------------------------------------- RS-03, RS-04, RS-05


@case("spc-rs-03")
def test_mostly_unverified_research_gives_suggestions_and_confirm_only_no_defaults(
    tmp_path_factory,
):
    f = cached_flow(
        tmp_path_factory,
        "rs-unverified",
        lambda: raw("standard"),
        research=_variant("mostly_unverified"),
    )
    assert f.status == "approved"
    g2 = f.g2()
    assert len(g2["suggestions"]) >= 10 and len(g2["confirm_list"]) >= 10
    assert all(not s["included"] for s in g2["suggestions"])  # suggestions are off by default
    # no node rests on research as a Default or Locked value
    cited = [
        n
        for n in f.graph.all_nodes()
        if any(p.kind == "research" for p in n.provenance) and n.tier != "open"
    ]
    assert cited == []
    assert not [
        s for s in f.graph.screens.values() if any(p.kind == "research" for p in s.provenance)
    ]
    assert all(
        "(unverified)" in c["text"] or "(single_source)" in c["text"] for c in g2["confirm_list"]
    )


@case("spc-rs-04")
def test_a_research_conflict_keeps_both_values_and_goes_to_confirm(tmp_path_factory):
    v = view_of("stale")  # any variant: the conflicts block is unchanged
    assert v.conflicts and v.conflicts[0].item_ids == ("RI-153", "RI-163", "RI-164")
    f = cached_flow(tmp_path_factory, "rs-real", lambda: raw("standard"), research=research_raw())
    text = _blob(f.g2()["confirm_list"])
    assert "5,000" in text and "7,500" in text  # both competing values are in front of the owner


@case("spc-rs-05")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-RS-2: with no provider dossier and an integrations lane gap the payment integration is still mock, but nothing says 'choose a provider': no confirm item, no provider-neutral marker; provider names come from the Compass export's own wording",
)
def test_no_in_market_provider_gives_a_neutral_flow_a_mock_adapter_and_a_confirm(
    tmp_path_factory,
):
    f = cached_flow(
        tmp_path_factory,
        "rs-noprovider",
        lambda: raw("standard"),
        research=_variant("no_provider"),
    )
    pay = next(i for i in f.graph.integrations.values() if i.need_key == "payments")
    assert pay.mode == "mock" and pay.provider_ids == [] and pay.provider_side_flow
    assert "choose a provider" in _blob([f.g2()["confirm_list"], f.g2()["risks"]])


@case("spc-rs-05")
def test_no_provider_dossier_means_a_mock_integration_with_no_provider_names(tmp_path_factory):
    f = cached_flow(
        tmp_path_factory,
        "rs-noprovider",
        lambda: raw("standard"),
        research=_variant("no_provider"),
    )
    pay = next(i for i in f.graph.integrations.values() if i.need_key == "payments")
    assert f.status == "approved" and pay.mode == "mock" and pay.provider_ids == []
    assert pay.provider_side_flow and pay.flow  # a provider-neutral flow exists


# ------------------------------------------------------------------------------------------- RS-06, RS-07, RS-08, RS-09


@case("spc-rs-06")
def test_a_research_file_for_another_brief_is_known_to_be_stale():
    si = real("standard")
    assert read_research(research_raw()).is_for(si.brief_hash)
    assert not view_of("stale").is_for(si.brief_hash)  # the reader can tell


@case("spc-rs-06")
def test_stale_research_raises_a_flag_and_a_g2_banner(tmp_path_factory):
    f = cached_flow(
        tmp_path_factory, "rs-stale", lambda: raw("standard"), research=_variant("stale")
    )
    text = _blob([f.banners, f.flags, f.g2()["risks"], f.g2()["banners"]])
    assert "stale" in text or "another brief" in text or "older brief" in text


def _store(n: int, big_claim: str = "") -> dict[str, Any]:
    items = []
    for i in range(1, n + 1):
        items.append(
            {
                "id": f"RI-{i:03d}",
                "job": "features",
                "kind": "competitor" if i % 3 else "entity_pattern",
                "label": f"Label {i}",
                "claim": (big_claim if i == 7 else "A claim about it. " * 4),
                "impact": "normal",
                "evidence": "single_source",
                "tier": "default",
                "confidence": "medium",
                "use_note": "",
                "flags": [],
                "applies_to": [
                    {"kind": "FTR", "id": f"FTR-{(i % 40) + 1:03d}", "relation": "expects"}
                ],
                "sources": [{"id": f"S-{i:03d}"}],
                "rank": i,
                "store": "main" if i < n // 2 else "appendix",
            }
        )
    return {
        "schema": "research.v5",
        "status": "complete",
        "program_id": "p",
        "brief_hash": "h",
        "items": items,
        "slices": {"blueprint": {"item_ids": [x["id"] for x in items[:60]]}},
        "lane_decisions": [],
        "dossiers": [],
        "platform_constraints": [],
        "confirm": [],
        "conflicts": [],
    }


@case("spc-rs-07")
def test_an_over_budget_unit_travels_alone_and_a_narrower_card_gives_a_second_pack():
    store = _store(400, big_claim="very long claim. " * 600)
    wide = pack(store, {"brief_ids": [f"FTR-{k:03d}" for k in range(1, 41)], "token_budget": 1500})
    in_packs = [i["id"] for p in wide["packs"] for i in p["items"]]
    assert len(wide["packs"]) > 3
    over = [p for p in wide["packs"] if p.get("over_budget")]
    assert over and len(over[0]["items"]) == 1 and over[0]["items"][0]["id"] == "RI-007"
    # nothing truncated: the big claim is whole; nothing in both the packs and the index
    big = next(i for p in wide["packs"] for i in p["items"] if i["id"] == "RI-007")
    assert len(big["claim"]) == len("very long claim. " * 600)
    assert not set(in_packs) & {i["id"] for i in wide["index"]}
    assert sorted(in_packs + [i["id"] for i in wide["index"]]) == sorted(
        i["id"] for i in store["items"]
    )  # an item known only by title is in the index
    # the second, narrower card: a deterministic subset
    narrow = pack(store, {"brief_ids": ["FTR-008"], "token_budget": 1500})
    again = pack(store, {"brief_ids": ["FTR-008"], "token_budget": 1500})
    assert narrow == again
    narrow_ids = {i["id"] for p in narrow["packs"] for i in p["items"]}
    assert narrow_ids and narrow_ids < set(in_packs)


@case("spc-rs-08")
def test_with_no_screens_lane_the_state_library_and_compass_patterns_are_all_there_is(
    tmp_path_factory,
):
    v = view_of("no_screens_lane")
    assert "screens" in v.thin_lanes
    d = derive(real("standard"), v)
    assert d.inventory.peers_usually_have == []  # no invented peer practice
    assert not [p for p in d.inventory.screens if p.source == "peer"]
    f = cached_flow(
        tmp_path_factory,
        "rs-noscreens",
        lambda: raw("standard"),
        research=_variant("no_screens_lane"),
    )
    ids = {i["id"] for i in _variant("no_screens_lane")["items"]}
    for s in f.graph.screens.values():
        for u in s.usually:
            assert u.evidence == "library" or u.item in ids  # every peer note cites a real item
    assert not [s for s in f.g2()["suggestions"] if s["id"].startswith("peer-")]


@case("spc-rs-09")
def test_new_items_are_suggestions_at_g2_never_scope(tmp_path_factory):
    r = research_raw()
    new = [i for i in r["items"] if any(a["kind"] == "NEW" for a in i["applies_to"])]
    assert len(new) > 50  # the real file has plenty of items about things not in the Brief
    baseline = derive(real("standard"))
    with_research = derive(real("standard"), read_research(r))
    assert set(with_research.graph.features) == set(baseline.graph.features)  # no feature added
    f = cached_flow(tmp_path_factory, "rs-real", lambda: raw("standard"), research=r)
    assert set(f.graph.features) == set(baseline.graph.features)
    sugg = f.g2()["suggestions"]
    assert sugg and all(s["included"] is False for s in sugg)
    # a single-source or unverified peer pattern is never scope
    gaps = with_research.inventory.peers_usually_have
    assert gaps and all(
        g.added is False for g in gaps if g.evidence in ("single_source", "unverified")
    )


# ------------------------------------------------------------------------------------------- RS-10, RS-11, RS-12


@case("spc-rs-10")
def test_a_thin_compliance_register_still_gives_a_policy_backed_deletion_flow():
    si = real("hospital")
    d = derive(si)  # no research file at all: the register is empty
    names = " ".join(s.name.lower() for s in d.graph.screens.values())
    assert "delete account" in names  # deletion comes from the policy sheet, not from research
    policy = {p.ref for s in d.graph.screens.values() for p in s.provenance if p.kind == "policy"}
    assert any("DELETION" in p for p in policy)
    assert all(p.kind != "research" for n in d.graph.all_nodes() for p in n.provenance)
    # the user's own consent rule is a Locked rule, compliance itself is information only
    consent = [r for r in d.graph.rules.values() if "consent" in r.subject.lower()]
    assert consent and consent[0].tier == "locked"


@case("spc-rs-10")
def test_a_thin_register_still_gives_consent_and_disclosure_flows_from_signals():
    d = derive(real("hospital"))
    assert "sensitive_data" in d.facts
    assert "consent" in " ".join(s.name.lower() for s in d.graph.screens.values())


@case("spc-rs-11")
def test_a_research_file_of_a_new_schema_version_reads_and_unknown_fields_are_ignored(
    tmp_path_factory,
):
    v = view_of("new_schema")
    assert v.schema_name == "research.v6" and not v.thin and len(v.items) == 361
    assert not hasattr(v, "brand_new_section")
    assert not hasattr(v.items[0], "a_field_from_the_future")
    same = read_research(research_raw())
    assert [i.id for i in v.items] == [i.id for i in same.items]
    f = cached_flow(
        tmp_path_factory, "rs-v6", lambda: raw("standard"), research=_variant("new_schema")
    )
    assert f.status == "approved" and f.g2()["suggestions"]


@case("spc-rs-12")
def test_a_user_number_is_a_locked_rule_and_a_research_number_is_never_defaulted(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "rs-real", lambda: raw("standard"), research=research_raw())
    commission = next(r for r in f.graph.rules.values() if "4%" in r.statement)
    assert commission.tier == "locked" and not commission.assumed
    assert any(p.kind in ("user", "compass") for p in commission.provenance)
    # no rule or other node takes a number from research as a Default or Locked value
    for rule in f.graph.rules.values():
        if any(p.kind == "research" for p in rule.provenance):
            assert rule.tier == "open" and rule.confirm
    g2 = f.g2()
    money = [c for c in g2["confirm_list"] if c["category"] == "money"]
    assert money and all(
        "(unverified)" in c["text"] or "(single_source)" in c["text"] for c in money
    )  # research numbers wait for the owner
