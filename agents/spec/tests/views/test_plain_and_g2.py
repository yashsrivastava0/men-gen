"""Plain words, the confirm list, content maps and the G2 specifics."""

from __future__ import annotations

import json
import re

from mde_spec_agent import views as V
from mde_spec_agent.views.names import ID_RE, plain
from mde_spec_core import libraries
from mde_spec_core.check import confirm_list_ranked
from mde_spec_core.graph import SpecGraph


def g2_of(b):
    return V.g2(b.graph, b.pack, b.check, b.derived, rcode_rows=b.rcode_rows, mock=b.mocks)


def test_no_bare_id_reaches_the_owner_in_g15_or_g2(built, strings_of):
    for name, view in (("g15", V.g15(built.graph, built.derived)), ("g2", g2_of(built))):
        bad = [s for s in strings_of(view) if ID_RE.search(s)]
        assert not bad, (name, bad[:3])


def test_plain_replaces_ids_by_names_and_never_leaves_one():
    g = SpecGraph()
    from mde_spec_core.graph import Entity

    g.add("entities", Entity(id="ENT-001", name="Booking"))
    assert plain("ENT-001 is shown", g) == "Booking is shown"
    assert plain("entity ENT-001 'Booking' here", g) == "Booking here"
    out = plain("see FCT-024, ENT-009 and SCR-S004", g)
    assert not ID_RE.search(out) and "something you said in the Brief" in out


def test_confirm_list_is_ranked_by_code_and_capped_at_twenty(built):
    v = g2_of(built)["confirm_list"]
    assert len(v) <= 20
    expected = confirm_list_ranked(built.graph, None, 20)
    assert [c["category"] for c in v] == [c["category"] for c in expected]
    order = {"money": 0, "legal": 1, "identity": 2, "other": 3}
    assert [order[c["category"]] for c in v] == sorted(order[c["category"]] for c in v)


def test_confirm_list_puts_money_legal_identity_first_and_caps_at_twenty(quick):
    from mde_spec_core.graph import Rule

    g = quick.graph.model_copy(deep=True)
    texts = ["delivery slots", "refund fee", "privacy consent", "phone number verification"] + [
        f"opening hours {i}" for i in range(30)
    ]
    for i, t in enumerate(texts):
        g.add("rules", Rule(id=f"RUL-X{i:03d}", subject=t, statement=f"{t} applies", confirm=True))
    v = V.g2(g, quick.pack, quick.check, None, mock=quick.mocks)["confirm_list"]
    assert len(v) == 20
    cats = [c["category"] for c in v]
    assert cats[:3] == ["money", "legal", "identity"] and cats.index("other") >= 3


def test_every_release_screen_has_a_content_map(built):
    g = g2_of(built)
    live = sorted(sid for sid, s in built.graph.screens.items() if s.status == "in")
    assert [m["id"] for m in g["content_maps"]] == live
    release = built.derived.plan.release_screens
    assert set(release) <= {m["id"] for m in g["content_maps"]}


FORBIDDEN_KEYS = {
    "order",
    "position",
    "index",
    "x",
    "y",
    "row",
    "col",
    "column",
    "z",
    "layout",
    "slot",
    "rank",
}


def test_content_map_has_no_position_and_regions_carry_importance(built):
    for m in g2_of(built)["content_maps"]:
        names = [r["name"].lower() for r in m["regions"]]
        assert names == sorted(names), (
            "regions are listed alphabetically: the list order means nothing"
        )
        for r in m["regions"]:
            assert r["importance"] in {"must", "usually", "could"}
            assert not (FORBIDDEN_KEYS & set(r)), set(r)
        text = " ".join(
            [m["purpose"]] + [r["text"] for r in m["regions"]] + [a["effect"] for a in m["actions"]]
        )
        found = [f for f in libraries.form_word_findings(text) if f]
        assert not found, (m["id"], found[:2])
        assert not re.search(
            r"\b(top|bottom|left|right|above|below|sidebar|header bar|footer)\b", text, re.I
        ), m["id"]


def test_three_voices_are_kept_apart(quick):
    g = quick.graph.model_copy(deep=True)
    from mde_spec_core.graph import Could, Usually

    s = g.screens[sorted(g.screens)[0]]
    s.usually.append(Usually(note="people expect a day view", evidence="corroborated"))
    s.could.append(
        Could(
            id="CP-1",
            kind="delight",
            when="a class is booked",
            effect="a calm moment says what happens next",
            basis="journey step",
            basis_kind="journey_moment",
            motion="state_change",
            reduced_motion_alt="a still message",
        )
    )
    v = V.screen_view(g, s.id, "typical", mock=quick.mocks)
    voices = {r["importance"] for r in v["regions"]}
    assert voices == {"must", "usually", "could"}
    could = next(r for r in v["regions"] if r["importance"] == "could")
    assert could["text"] == "a calm moment says what happens next"
    assert any("reduced motion" in i for i in could["items"])
    usually = [r for r in v["regions"] if r["name"] == "What similar apps do"]
    assert usually and usually[0]["items"] == ["several sources agree"]


def test_mock_profiles_change_the_example_records(quick):
    g = quick.graph
    sid = next(
        s
        for s in sorted(g.screens)
        if g.screens[s].regions and any(r.bind for r in g.screens[s].regions)
    )
    empty = V.screen_view(g, sid, "empty", mock=quick.mocks)["hero_records"]
    typical = V.screen_view(g, sid, "typical", mock=quick.mocks)["hero_records"]
    broken = V.screen_view(g, sid, "broken", mock=quick.mocks)["hero_records"]
    offline = V.screen_view(g, sid, "offline", mock=quick.mocks)["hero_records"]
    assert empty and all("No " in x for x in empty)
    assert typical and typical != empty
    assert broken and all("fails" in x for x in broken)
    assert offline and offline[0].startswith("There is no network")
    unknown = V.screen_view(g, sid, "nonsense", mock=quick.mocks)
    assert unknown["mock_profile"] == "typical"


def test_store_rules_are_in_plain_words(built):
    rules = g2_of(built)["store_rules"]
    assert rules
    assert (
        any("delete" in r["plain"].lower() and "account" in r["plain"].lower() for r in rules)
        or "accounts_creatable_in_app" not in built.graph.meta.facts
    )
    assert all(r["plain"] and not re.search(r"POL-|policy:", r["plain"]) for r in rules)
    assert {r["store"] for r in rules} <= {
        "Apple App Store",
        "Google Play",
        "Accessibility standards",
    }


def test_account_deletion_rule_is_explained_for_an_app_with_sign_up(quick):
    assert "accounts_creatable_in_app" in quick.graph.meta.facts
    plain_rules = [r["plain"] for r in g2_of(quick)["store_rules"]]
    assert (
        "The stores require a way to delete the whole account and its personal data from inside the app."
        in plain_rules
    )


def test_every_assumed_node_is_listed(built):
    v = g2_of(built)
    n = sum(1 for node in built.graph.all_nodes() if node.assumed)
    listed = [a for a in v["assumptions"] if a.startswith("The ") and "is assumed" in a]
    assert len(listed) == min(n, 300) or len(listed) == n


def test_summary_is_a_template_from_code_or_the_stored_paragraph(quick):
    base = V.g2(quick.graph, quick.pack, quick.check, quick.derived, mock=quick.mocks)
    assert (
        base["summary"]["source"] == "template"
        and "screens in the first release" in base["summary"]["text"]
    )
    stored = V.g2(
        quick.graph,
        quick.pack,
        quick.check,
        quick.derived,
        mock=quick.mocks,
        summary={"text": "A model wrote this."},
    )
    assert (
        stored["summary"]["text"] == "A model wrote this."
        and stored["summary"]["source"] == "model"
    )
    assert (
        V.g2(quick.graph, quick.pack, quick.check, None, mock=quick.mocks, summary="Plain text.")[
            "summary"
        ]["text"]
        == "Plain text."
    )


def test_code_fixes_are_counted_and_flags_and_banners_are_carried(quick):
    rows = [
        {"kind": "sort_bytes", "node_id": "SCR-001", "what": "order", "before": "", "after": ""},
        {"kind": "sort_bytes", "node_id": "SCR-002", "what": "order", "before": "", "after": ""},
        {
            "kind": "exact_synonym",
            "node_id": "ENT-001",
            "what": "word",
            "before": "a",
            "after": "b",
        },
    ]
    flags = [
        {
            "kind": "gate2_blocker",
            "code": "V-04",
            "severity": "blocker",
            "where": "SCR-001",
            "why": "not reachable",
            "fix": "add a link",
        }
    ]
    banners = [{"code": "weak_route", "message": "weak route", "step": "screens"}]
    v = V.g2(
        quick.graph,
        quick.pack,
        quick.check,
        None,
        rcode_rows=rows,
        flags=flags,
        banners=banners,
        mock=quick.mocks,
    )
    assert v["code_fixes_total"] == 3
    assert {f["count"] for f in v["code_fixes"]} == {2, 1}
    assert v["flags"][0]["what"] == "not reachable" and v["flags"][0]["where"] != "SCR-001"
    assert v["banners"] == [{"code": "weak_route", "message": "weak route"}]
    assert any("weak route" in r["text"] for r in v["risks"])


def test_missing_builder_need_is_listed_as_blocking(quick):
    d = json.loads(quick.check.to_json())
    d["builder_needs"]["needs"][0]["status"] = "missing"
    v = V.g2(quick.graph, quick.pack, d, None, mock=quick.mocks)
    assert v["blocking_needs"] and d["builder_needs"]["needs"][0]["title"] in v["blocking_needs"][0]
    assert any(r["severity"] == "blocker" for r in v["risks"])


def test_degraded_nodes_checked_by_and_hash(quick):
    g = quick.graph.model_copy(deep=True)
    ent = next(iter(g.entities.values()))
    ent.degraded = True
    v = V.g2(g, quick.pack, quick.check, None, mock=quick.mocks)
    assert any(ent.name in x for x in v["degraded_nodes"])
    assert v["checked_by"] == ["self_check"] and v["critic_vendor"] is None
    assert v["content_hash"] == quick.pack.content_hash


def test_verify_report_feeds_the_g2_view(quick):
    verify = {
        "findings": [
            {
                "id": "D1",
                "cls": "B",
                "node": "SCR-001",
                "change": "x",
                "reason": "y",
                "severity": "blocker",
            }
        ],
        "patches": [],
        "rounds_used": 2,
        "critic_vendor": "same",
        "canaries": [{"task_id": "T-0001", "blocking_questions": ["q?"]}],
        "degraded_nodes": ["SCR-002"],
        "differs_from_brief": ["a screen was merged"],
        "summary": "one pass",
    }
    v = V.g2(quick.graph, quick.pack, quick.check, None, verify_report=verify, mock=quick.mocks)
    assert (
        v["verify"]["enabled"]
        and v["verify"]["open_blockers"] == 1
        and v["verify"]["canary_blocking_questions"] == 1
    )
    assert v["critic_vendor"] == "same" and any(
        "a screen was merged" in x for x in v["differences_from_brief"]
    )
    assert any("no repair round left" in x for x in v["degraded_nodes"])


def test_differences_name_phased_and_pulled_in_work(thorough):
    v = g2_of(thorough)["differences_from_brief"]
    assert any(x.startswith("Phased:") or x.startswith("Deferred:") for x in v)
    assert any(x.startswith("Pulled in:") for x in v)
    assert any(x.startswith("Merged:") for x in v)
    assert any(x.startswith("Added:") for x in v)


def test_suggestions_are_open_items_and_never_included_by_default(built):
    for s in g2_of(built)["suggestions"]:
        assert s["included"] is False


def test_capability_tiers_flag_b_and_c(thorough):
    tiers = g2_of(thorough)["capability_tiers"]
    assert tiers
    for t in tiers:
        assert t["flagged"] == (t["tier"] in ("B", "C"))
    assert [t["tier"] for t in tiers] == sorted((t["tier"] for t in tiers), reverse=True)
