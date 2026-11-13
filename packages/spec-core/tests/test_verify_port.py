"""The VerifyPort: models, round ledger, canary merge (BG-15), vendor honesty, loader and re-verify helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mde_spec_core.config import default_config
from mde_spec_core.synth import make_graph
from mde_spec_core.verify_port import (
    ENTRY_POINT_GROUP,
    AcceptanceCover,
    CanaryResult,
    Defect,
    Patch,
    RoundLedger,
    VerifyReport,
    canary_cards,
    critic_vendor_for,
    entry_point_name,
    load_verify_port,
    merge_canaries,
    node_hash,
    verify_enabled,
)

CFG = default_config()


def _canary(run: int, questions: list[str], *, ok: bool = True) -> CanaryResult:
    return CanaryResult(
        task_id="T-0007",
        run_index=run,
        outline=[f"outline of run {run}"],
        blocking_questions=questions,
        assumptions=[f"assumption {run}"],
        acceptance_coverage=[AcceptanceCover(criterion="C1", satisfiable=ok, how=f"run {run}")],
    )


def test_entry_point_group_name() -> None:
    assert entry_point_name == ENTRY_POINT_GROUP == "mde.spec.verify"


def test_models_are_closed_and_patch_has_the_agreed_shape() -> None:
    p = Patch(node="SCR-001", field="role_ids", change="x", reason="y", cls="B")
    assert set(p.model_dump()) == {"node", "field", "change", "reason", "cls"}
    with pytest.raises(ValueError):
        Defect(id="d", cls="Z", node="n", change="c", reason="r")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Patch(node="n", change="c", reason="r", cls="A", extra="no")  # type: ignore[call-arg]
    assert VerifyReport().critic_vendor == "same"


def test_round_ledger_caps_rounds_and_nodes() -> None:
    led = RoundLedger.from_config(CFG)
    assert led.max_rounds == 2 and led.class_a_max_passes == 3
    assert led.open_round("freeze") and not led.open_round("freeze")  # one model call per phase
    assert led.open_round("final")
    assert not led.open_round("third")
    assert led.rounds_used == 2
    assert led.node_can_repair("SCR-1")
    led.charge_node("SCR-1")
    assert not led.node_can_repair("SCR-1")
    assert [led.next_class_a_pass() for _ in range(4)] == [True, True, True, False]
    again = RoundLedger.from_dict(led.to_dict())
    assert again.to_dict() == led.to_dict()


def test_unconverged_class_a_becomes_b() -> None:
    led = RoundLedger()
    out = led.reclassify_unconverged(
        [
            Patch(node="n", change="c", reason="r", cls="A"),
            Patch(node="m", change="c", reason="r", cls="C"),
        ]
    )
    assert [p.cls for p in out] == ["B", "C"]


def test_bg15_union_of_questions_and_stricter_run_wins() -> None:
    lenient = _canary(0, ["Q about ENT-1?"], ok=True)
    strict = _canary(1, ["q about ent-1?", "What is OP-9?"], ok=False)
    merged = merge_canaries([lenient, strict])
    assert merged.disagreement
    assert merged.stricter_run == 1 and merged.run_index == 1
    assert merged.blocking_questions == [
        "Q about ENT-1?",
        "What is OP-9?",
    ]  # union, case-insensitive dedupe
    assert [c.satisfiable for c in merged.acceptance_coverage] == [False]  # stricter wins
    assert merged.outline == ["outline of run 1"]
    assert [r.run_index for r in merged.runs] == [0, 1]  # both recorded
    assert set(merged.assumptions) == {"assumption 0", "assumption 1"}


def test_merge_agreeing_runs_is_not_a_disagreement_and_rejects_mixed_cards() -> None:
    a, b = _canary(0, ["Q?"]), _canary(1, ["Q?"])
    assert not merge_canaries([a, b]).disagreement
    assert merge_canaries([a]) is a
    other = a.model_copy(update={"task_id": "T-0008"})
    with pytest.raises(ValueError):
        merge_canaries([a, other])


def test_critic_vendor_honesty() -> None:
    assert critic_vendor_for({"writer": "v1", "critic": "v1"}) == "same"
    assert critic_vendor_for({"writer": "v1", "critic": "v2"}) == "different"
    assert critic_vendor_for({"writer": "v1"}) == "same"
    assert critic_vendor_for({}) == "same"


def test_verify_enabled_by_profile() -> None:
    assert verify_enabled(CFG, "deep")
    assert not verify_enabled(CFG, "compact")
    assert not verify_enabled(CFG, "standard")  # configurable: off in the default config
    cfg2 = CFG.model_copy(deep=True)
    cfg2.verify.profiles = ["standard", "deep"]
    assert verify_enabled(cfg2, "standard")
    cfg2.verify.profiles = ["compact", "deep"]
    assert not verify_enabled(cfg2, "compact")  # compact never


def test_canary_cards_deep_is_every_card_and_foundation_is_skipped() -> None:
    tasks = [
        {"id": "T-0001", "wave": "W0"},
        {"id": "T-0002", "wave": "W2"},
        {"id": "T-0003", "wave": "W3"},
    ]
    assert canary_cards(tasks, "deep", CFG) == ["T-0002", "T-0003"]
    assert canary_cards(tasks, "compact", CFG) == []


def test_load_verify_port_returns_none_without_verify() -> None:
    async def call(*a: object, **k: object) -> None:  # never used
        raise AssertionError

    ep = SimpleNamespace(
        name="verify", load=lambda: lambda c, cfg, cv: SimpleNamespace(critic_vendor=cv)
    )
    assert load_verify_port(call, CFG, profile="compact", entry_points=lambda: [ep]) is None
    assert load_verify_port(call, CFG, profile="standard", entry_points=lambda: [ep]) is None
    assert (
        load_verify_port(call, CFG, profile="deep", entry_points=lambda: []) is None
    )  # none installed
    port = load_verify_port(
        call, CFG, profile="deep", critic_vendor="different", entry_points=lambda: [ep]
    )
    assert port is not None and port.critic_vendor == "different"


def test_node_hash_changes_with_content() -> None:
    g = make_graph("tiny")
    sid = sorted(g.screens)[0]
    h1 = node_hash(g, sid)
    assert h1 and node_hash(g, sid) == h1
    g.screens[sid].purpose = "changed"
    assert node_hash(g, sid) != h1
    assert node_hash(g, "SCR-99999") == ""
