"""The port end to end on a synthetic deep graph, in mock mode through the real gateway pipeline."""

from __future__ import annotations

import importlib.metadata as md

import pytest
from verify_support.calls import GatewayCall

from mde_spec_core.canon import canonical_json
from mde_spec_core.check import spec_check
from mde_spec_core.graph import Entity, Lifecycle, Prov
from mde_spec_core.pack import Pack
from mde_spec_core.verify_port import (
    AcceptanceCover,
    BriefView,
    CanaryResult,
    FinalInput,
    LockedFact,
    RoundLedger,
    StructureInput,
    VerifyPort,
    canary_cards,
    load_verify_port,
    node_hash,
)
from mde_verify_agent import checks
from mde_verify_agent.calls.canary.schema import CanaryOut, CoverOut
from mde_verify_agent.calls.structure_critic.schema import FindingOut, StructureOut
from mde_verify_agent.port import VerifyAgent, build_verify_port


def card_reads(pack: Pack, card: dict) -> dict[str, str]:
    from mde_verify_agent.checks import card_read_paths

    return {p: pack.files[p] for p in card_read_paths(card) if p in pack.files}


def wave_cards(pack: Pack) -> list[dict]:
    return [t for t in pack.tasks if t["wave"] != "W0"]


async def test_structure_critic_runs_end_to_end_and_is_clean_on_a_good_graph(
    cfg, big_graph
) -> None:
    call = GatewayCall(cost_micros=1500)
    port = build_verify_port(call, cfg)
    report = await port.structure_critic(StructureInput(graph=big_graph))
    assert report.findings == [] and report.patches == [] and report.degraded_nodes == []
    assert report.rounds_used == 1 and report.critic_vendor == "same"
    assert report.cost_micros == 1500
    assert [c[0] for c in call.calls] == ["ver.structure_critic"]
    assert call.calls[0][1] == (
        "VERIFY:structure_critic",
    )  # Verify's own re-ask unit, never a graph node


async def test_verify_never_writes_into_the_graph(cfg, big_graph) -> None:
    before = big_graph.content_hash()
    port = build_verify_port(GatewayCall(), cfg)
    await port.structure_critic(StructureInput(graph=big_graph))
    await port.final_critic(FinalInput(graph=big_graph))
    assert big_graph.content_hash() == before


async def test_model_half_adds_what_code_cannot_judge(cfg, big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens[
        "SCR-001"
    ].tier = "locked"  # the fact's node exists and is Locked: only the words are missing
    g.screens["SCR-001"].provenance = [Prov(kind="user", ref="FTR-001")]
    brief = BriefView(
        summary="A booking app",
        locked_facts=[
            LockedFact(id="FACT-1", node="SCR-001", text="courier proof photograph on delivery")
        ],
    )
    report = await build_verify_port(GatewayCall(), cfg).structure_critic(
        StructureInput(graph=g, brief=brief)
    )
    assert [(d.code, d.source, d.cls) for d in report.findings] == [
        ("M-missing_flow", "model", "B")
    ]
    assert [p.node for p in report.patches] == ["SCR-001"]
    assert set(report.patches[0].model_dump()) == {"node", "field", "change", "reason", "cls"}


async def test_planted_defect_gets_a_patch_with_the_agreed_shape(cfg, big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    report = await build_verify_port(GatewayCall(), cfg).structure_critic(StructureInput(graph=g))
    assert any(d.code == "V-07" and d.node == "SCR-002" for d in report.findings)
    for p in report.patches:
        assert p.node and p.change and p.reason and p.cls in "ABC"


async def test_bg10_two_apps_disagree_on_statuses_is_a_class_b_patch_to_the_owning_module(
    cfg, big_graph
) -> None:
    g = big_graph.model_copy(deep=True)
    canon = g.entities["ENT-001"]
    owner = canon.owner_module
    g.add(
        "entities",
        Entity(
            id="ENT-900",
            name=canon.name,
            owner_module="MOD-002",
            lifecycle=Lifecycle(states=["new", "paid", "shipped"], initial="new"),
        ),
    )
    report = await build_verify_port(GatewayCall(), cfg).structure_critic(StructureInput(graph=g))
    xapp = [d for d in report.findings if d.code == "S-XAPP" and d.cls == "B" and d.node == owner]
    assert xapp, "the disagreement is reported against the OWNING module of the canonical entity"
    patch = next(p for p in report.patches if p.node == owner)
    assert patch.cls == "B" and "ENT-900" in patch.field
    # the general V-11 finding on the duplicate is folded into the more specific one
    assert not [d for d in report.findings if d.code == "V-11" and d.node == "ENT-900"]


async def test_locked_fact_dropped_changed_and_downgraded(cfg, big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    facts = [LockedFact(id="F-DROP", node="RUL-777", field="value", value="4")]
    report = await build_verify_port(GatewayCall(), cfg).structure_critic(
        StructureInput(graph=g, brief=BriefView(locked_facts=facts))
    )
    dropped = [d for d in report.findings if d.id == "S-LOCKED:F-DROP:dropped"]
    assert dropped and dropped[0].cls == "B" and dropped[0].severity == "blocker"


async def test_rounds_never_exceed_the_limit(cfg, big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    led = RoundLedger.from_config(cfg)
    call = GatewayCall()
    port = build_verify_port(call, cfg)
    r1 = await port.structure_critic(StructureInput(graph=g, ledger=led))
    r2 = await port.final_critic(FinalInput(graph=g, ledger=led))
    r3 = await port.final_critic(FinalInput(graph=g, ledger=led))  # a third pass: the limit holds
    assert (r1.rounds_used, r2.rounds_used, r3.rounds_used) == (1, 2, 2)
    assert led.rounds_used <= cfg.verify.max_llm_rounds == 2
    assert "ver.final_critic" in [c[0] for c in call.calls]
    assert [c[0] for c in call.calls].count(
        "ver.final_critic"
    ) == 1  # the third pass made no model call
    assert any("no LLM round left" in n for n in r3.notes)


async def test_a_node_takes_one_verify_repair_then_is_degraded(cfg, big_graph) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    led = RoundLedger.from_config(cfg)
    port = build_verify_port(GatewayCall(), cfg)
    r1 = await port.structure_critic(StructureInput(graph=g, ledger=led))
    assert any(p.node == "SCR-002" and p.cls != "A" for p in r1.patches) or any(
        d.node == "SCR-002" and d.cls == "A" for d in r1.findings
    )
    r2 = await port.final_critic(FinalInput(graph=g, ledger=led))
    b_c_nodes_r1 = {p.node for p in r1.patches if p.cls in ("B", "C")}
    b_c_nodes_r2 = {p.node for p in r2.patches if p.cls in ("B", "C")}
    assert not (b_c_nodes_r1 & b_c_nodes_r2), "a node never takes a second Verify-driven repair"
    blockers_bc = {d.node for d in r2.findings if d.cls in ("B", "C") and d.severity == "blocker"}
    assert (blockers_bc & b_c_nodes_r1) <= set(r2.degraded_nodes)


async def test_final_critic_reranks_the_confirm_list_and_lists_differences(cfg, big_graph) -> None:
    confirm = [
        {"rank": 1, "category": "other", "id": "RUL-1", "text": "opening hours"},
        {"rank": 2, "category": "money", "id": "RUL-2", "text": "late fee 4 percent"},
        {"rank": 3, "category": "legal", "id": "RUL-3", "text": "retention"},
    ]
    fin = FinalInput(
        graph=big_graph, confirm=confirm, differs_from_brief=["Two screens were merged"]
    )
    report = await build_verify_port(GatewayCall(), cfg).final_critic(fin)
    moves = [p for p in report.patches if p.field == "confirm_rank"]
    assert {p.node for p in moves} == {"RUL-2", "RUL-3"} and all(p.cls == "A" for p in moves)
    assert report.differs_from_brief == ["Two screens were merged"]
    assert any(d.code == "S-CONFIRM" for d in report.findings)


async def test_final_critic_uses_the_self_check_coverage_and_confirm(
    cfg, big_graph, big_pack
) -> None:
    check = spec_check(big_graph, big_pack)
    report = await build_verify_port(GatewayCall(), cfg).final_critic(
        FinalInput(graph=big_graph, check=check)
    )
    assert report.rounds_used == 1
    assert all(d.cls in "ABC" for d in report.findings)


async def test_a_failing_model_half_falls_back_flagged_and_code_checks_still_report(
    cfg, big_graph
) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    call = GatewayCall(overrides={"ver.structure_critic": lambda inp: RuntimeError("boom")})
    report = await build_verify_port(call, cfg).structure_critic(StructureInput(graph=g))
    assert "ver.structure_critic" in report.flagged_calls
    assert any("fell back" in n for n in report.notes)
    assert any(d.code == "V-07" for d in report.findings)


async def test_model_output_with_an_unknown_node_is_refused_and_falls_back(cfg, big_graph) -> None:
    bad = StructureOut(
        findings=[
            FindingOut(
                kind="other",
                node="SCR-424242",
                field="",
                change="c",
                reason="r",
                severity="blocker",
                cls="B",
            )
        ],
        summary="s",
    )
    call = GatewayCall(overrides={"ver.structure_critic": lambda inp: bad})
    report = await build_verify_port(call, cfg).structure_critic(StructureInput(graph=big_graph))
    assert report.findings == [] and "ver.structure_critic" in report.flagged_calls


async def test_the_canary_reads_every_card_of_a_deep_pack_and_is_within_the_blocking_rate(
    cfg, big_graph, big_pack
) -> None:
    port = build_verify_port(GatewayCall(), cfg)
    ids = canary_cards(big_pack.tasks, "deep", cfg)
    assert len(ids) == len(wave_cards(big_pack))  # deep = every card
    results = []
    for t in wave_cards(big_pack):
        res = await port.canary(t, card_reads(big_pack, t))
        assert res.task_id == t["id"] and res.outline
        results.append(res)
    rate, ok = checks.canary_rate(
        results, len(big_graph.screens), cfg.verify.canary_blocking_questions_per_screen_max
    )
    assert ok, f"{rate} blocking questions per screen"
    assert all(c.satisfiable for r in results for c in r.acceptance_coverage)


async def test_the_canary_asks_when_a_read_lacks_a_defined_id(cfg, big_pack) -> None:
    card = next(t for t in wave_cards(big_pack) if t["wave"] == "W3")
    reads = card_reads(big_pack, card)
    shard = next(p for p in reads if p.startswith("data-model/"))
    broken = {p: t for p, t in reads.items() if p != shard}  # the entity definitions are missing
    res = await build_verify_port(GatewayCall(), cfg).canary(card, broken)
    assert res.blocking_questions and "no read defines ENT-" in res.blocking_questions[0]
    assert any(not c.satisfiable for c in res.acceptance_coverage)
    # and the same card complete: none (the mock asks only for what code can see missing)
    assert not (await build_verify_port(GatewayCall(), cfg).canary(card, reads)).blocking_questions
    assert [d.cls for d in checks.canary_defects(res, card)] == ["C"] * len(res.blocking_questions)


async def test_the_canary_refuses_a_file_the_card_does_not_list(cfg, big_pack) -> None:
    card = next(t for t in wave_cards(big_pack) if t["wave"] == "W3")
    reads = {
        **card_reads(big_pack, card),
        "screens/SCR-001.md": big_pack.files["screens/SCR-001.md"],
    }
    if "screens/SCR-001.md" in card["reads"]["screens"]:
        pytest.skip("the card lists it")
    with pytest.raises(ValueError, match="exactly the card's reads"):
        await build_verify_port(GatewayCall(), cfg).canary(card, reads)


async def test_bg15_two_runs_disagree_union_of_questions_stricter_wins(cfg, big_pack) -> None:
    card = next(t for t in wave_cards(big_pack) if t["wave"] == "W3")
    reads = card_reads(big_pack, card)
    runs = iter(
        [
            CanaryOut(
                outline=["a"],
                blocking_questions=["Which currency?"],
                assumptions=[],
                acceptance_coverage=[],
            ),
            CanaryOut(
                outline=["b"],
                blocking_questions=["What is OP-9?", "which currency?"],
                assumptions=["x"],
                acceptance_coverage=[],
            ),
        ]
    )
    call = GatewayCall(overrides={"ver.canary": lambda inp: _with_criteria(next(runs), inp)})
    port = build_verify_port(call, cfg)
    r0 = await port.canary(card, reads, run_index=0)
    r1 = await port.canary(card, reads, run_index=1)
    merged = VerifyAgent.merge_canaries([r0, r1])
    assert merged.disagreement and merged.stricter_run == 1
    assert merged.blocking_questions == ["Which currency?", "What is OP-9?"]
    assert len(merged.runs) == 2
    # each run asked under its own re-ask unit, so run 1 never inherits run 0's spent re-ask
    assert len({c[1] for c in call.calls}) == 2


def _with_criteria(out: CanaryOut, inp) -> CanaryOut:
    return out.model_copy(
        update={
            "acceptance_coverage": [
                CoverOut(criterion=c, satisfiable=True, how="ok") for c in inp.criteria
            ]
        }
    )


async def test_reverify_checks_touched_nodes_by_hash_and_skips_the_unchanged(
    cfg, big_graph
) -> None:
    g = big_graph.model_copy(deep=True)
    port = build_verify_port(GatewayCall(), cfg)
    nodes = ["SCR-002", "SCR-003"]
    hashes = {n: node_hash(g, n) for n in nodes}
    clean = port.reverify(nodes, g, hashes)
    assert clean.hashes == {} and clean.findings == [] and "2 unchanged" in clean.notes[0]
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    after = port.reverify(nodes, g, hashes)
    assert set(after.hashes) == {"SCR-002"}  # only the touched node
    assert any(d.node == "SCR-002" for d in after.findings)
    assert all(d.node == "SCR-002" for d in after.findings)
    # a never-verified node is verified (no recorded hash)
    assert set(port.reverify(["SCR-003"], g, {}).hashes) == {"SCR-003"}


async def test_reverify_degrades_a_blocker_on_a_node_that_used_its_verify_repair(
    cfg, big_graph
) -> None:
    g = big_graph.model_copy(deep=True)
    g.screens["SCR-002"].actions[0].effect[-1].target = "SCR-999"
    port = build_verify_port(GatewayCall(), cfg)
    assert isinstance(port, VerifyAgent)
    port.ledger.charge_node("SCR-002")
    rep = port.reverify(["SCR-002"], g, {})
    bc_blockers = [d for d in rep.findings if d.cls in ("B", "C") and d.severity == "blocker"]
    assert bc_blockers and "SCR-002" in rep.degraded_nodes


def test_class_a_patches_do_not_count_as_a_round(cfg, big_graph) -> None:
    from mde_spec_core.verify_port import Defect
    from mde_verify_agent.repair import plan_patches

    led = RoundLedger.from_config(cfg)
    d = Defect(id="a", cls="A", node="SCR-002", change="fix", reason="r", severity="blocker")
    patches, degraded, _ = plan_patches([d], led, "freeze", big_graph)
    assert [p.cls for p in patches] == ["A"] and degraded == [] and led.node_rounds == {}


def test_entry_point_resolves_to_the_builder(cfg) -> None:
    eps = md.entry_points(group="mde.spec.verify")
    names = {e.name: e for e in eps}
    if (
        "verify" not in names
    ):  # the environment was not re-synced after the entry point was declared
        pytest.skip("entry point not installed in this environment; target tested directly below")
    assert names["verify"].value == "mde_verify_agent.port:build_verify_port"
    assert names["verify"].load() is build_verify_port


async def test_load_verify_port_finds_verify_through_the_entry_point(cfg, big_graph) -> None:
    ported = load_verify_port(GatewayCall(), cfg, profile="deep", critic_vendor="different")
    if ported is None:
        pytest.skip("entry point not installed in this environment")
    assert isinstance(ported, VerifyAgent) and ported.critic_vendor == "different"
    assert isinstance(ported, VerifyPort)
    assert load_verify_port(GatewayCall(), cfg, profile="compact") is None
    assert load_verify_port(GatewayCall(), cfg, profile="standard") is None


def test_manifest_and_vendor_honesty(cfg) -> None:
    same = build_verify_port(GatewayCall(), cfg)
    diff = build_verify_port(GatewayCall(), cfg, "different")
    assert same.critic_vendor == "same" and diff.critic_vendor == "different"
    assert same.manifest_fields() == {
        "checked_by": ["self_check", "verify"],
        "critic_vendor": "same",
    }  # type: ignore[attr-defined]
    assert canonical_json(CanaryResult(task_id="T").model_dump())  # models serialise
    assert AcceptanceCover(criterion="c", satisfiable=True).how == ""
