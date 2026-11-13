import json

import pytest

from mde_spec_core.callkit.ladder import FixKind, RCodeLog
from mde_spec_core.check import CONFIRM_TOP, confirm_list_ranked, spec_check
from mde_spec_core.graph import Rule, Screen
from mde_spec_core.pack import assemble
from mde_spec_core.planted import STRUCTURAL, Plant
from mde_spec_core.synth import make_graph
from mde_spec_core.validators import REGISTRY


@pytest.mark.parametrize("name", ["tiny", "small", "medium", "big", "program"])
def test_spec_check_is_ok_on_clean_presets(name):
    g = make_graph(name)
    r = spec_check(g, assemble(g))
    assert r.ok, [e["code"] + " " + e["where"] for e in r.errors if e["severity"] == "blocker"][:4]
    assert r.by_severity["blocker"] == 0 and not r.partial
    assert len(REGISTRY) == 45  # the whole suite ran


def planted(defect):
    g = make_graph("small")
    pack = assemble(g)
    plant = Plant(g, pack=pack, pack_files=dict(pack.files))
    defect.apply(plant)
    keep = defect.id in ("volatile_data_in_body", "task_over_budget")
    pack2 = pack if keep else assemble(g)
    if defect.id == "volatile_data_in_body":
        pack2.files = plant.pack_files
    kw = dict(plant.ctx_kwargs)
    if "research_items" in kw:
        items = kw.pop("research_items")
        kw["research"] = type("R", (), {"items": items})()
    return spec_check(g, pack2, **kw)


_WARNING_ONLY = {"V-25", "V-45"}


@pytest.mark.parametrize("defect", STRUCTURAL, ids=lambda d: d.id)
def test_planted_structural_defects_make_the_check_fail(defect):
    r = planted(defect)
    assert defect.expected in {f.code for f in r.findings}
    if defect.expected not in _WARNING_ONLY:
        assert not r.ok


def test_structured_errors_and_counts():
    r = planted(next(d for d in STRUCTURAL if d.id == "binding_to_missing_field"))
    e = next(x for x in r.errors if x["code"] == "V-06")
    assert set(e) >= {"code", "severity", "where", "why", "fix"} and e["catalogue"] == [
        "BINDING_UNRESOLVED"
    ]
    assert r.by_validator["V-06"] >= 1 and r.by_severity["blocker"] >= 1
    assert r.errors[0]["severity"] == "blocker"  # blockers first
    assert any("V-06" in line for line in r.plain_lines())


def test_a_missing_need_makes_the_check_not_ok_and_partial():
    g = make_graph("small")
    g.behaviors.clear()
    r = spec_check(g, assemble(g))
    assert not r.ok and r.partial and [n.id for n in r.missing] == ["BN-16"]
    assert any("BN-16" in line for line in r.plain_lines())


def test_the_pack_is_assembled_when_none_is_given():
    g = make_graph("tiny")
    assert spec_check(g).ok


def test_json_is_deterministic_and_carries_the_ladder_log():
    g = make_graph("small")
    log = RCodeLog()
    log.record(FixKind.SORT_BYTES, "SCR-001", "normalised", "a", "b")
    args = dict(
        rcode_log=log,
        reasks=[{"node": "SCR-002", "step": "cards"}],
        flags=["SCR-003 degraded"],
        banners=["weak_route cards"],
    )
    a = spec_check(g, assemble(g), **args).to_json()
    b = spec_check(make_graph("small"), assemble(make_graph("small")), **args).to_json()
    assert a == b and a.endswith("\n") and "\r" not in a
    d = json.loads(a)
    assert d["schema"] == "spec.self-check.v1" and d["ok"] is True
    assert d["rcode"]["changes"] == 1 and d["rcode"]["counts"]["sort_bytes"] == 1
    assert d["reasks"] == [{"node": "SCR-002", "step": "cards"}]
    assert d["flags"] == ["SCR-003 degraded"] and d["banners"] == ["weak_route cards"]
    assert len(d["builder_needs"]["needs"]) == 17 and "gaps" in d["coverage"]


def test_confirm_list_ranks_money_then_legal_then_identity_and_caps_at_twenty():
    g = make_graph("small")
    g.rules.clear()
    texts = [
        ("RUL-010", "ID verification", "the customer sends a passport scan", False),
        ("RUL-011", "Data retention", "privacy law keeps records for a fixed time", False),
        ("RUL-012", "Late fee", "a late fee applies after the due date", True),
        ("RUL-013", "Greeting", "the home screen says hello", False),
    ]
    for rid, sub, st, val in texts:
        g.add(
            "rules",
            Rule(
                id=rid,
                subject=sub,
                statement=st,
                confirm=True,
                value="4" if val else None,
                unit="percent" if val else None,
            ),
        )
    out = confirm_list_ranked(g)
    assert [x["id"] for x in out] == ["RUL-012", "RUL-011", "RUL-010", "RUL-013"]
    assert [x["category"] for x in out] == ["money", "legal", "identity", "other"]
    assert [x["rank"] for x in out] == [1, 2, 3, 4]
    for i in range(30):
        g.add(
            "rules",
            Rule(id=f"RUL-{100 + i}", subject="fee", statement="a fee applies", confirm=True),
        )
    assert len(confirm_list_ranked(g)) == CONFIRM_TOP == 20


def test_confirm_list_reads_research_items_and_screen_confirms():
    g = make_graph("tiny")
    g.rules.clear()
    sid = next(iter(g.screens))
    g.screens[sid].confirm = ["the refund window is 7 days"]
    rv = type(
        "R",
        (),
        {
            "items": [
                {"id": "RI-1", "claim": "tax of 18 percent", "evidence": "single_source"},
                {"id": "RI-2", "claim": "x", "evidence": "official"},
            ]
        },
    )()
    out = confirm_list_ranked(g, rv)
    assert {x["id"] for x in out} == {sid, "RI-1"} and all(x["category"] == "money" for x in out)
    assert isinstance(g.screens[sid], Screen)
