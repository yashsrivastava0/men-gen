"""evidence/verify.json and the manifest fields."""

from __future__ import annotations

import json

from mde_spec_core.verify_port import (
    AcceptanceCover,
    CanaryResult,
    Defect,
    Patch,
    VerifyReport,
)
from mde_verify_agent.evidence import evidence_dict, evidence_json, manifest_fields


def _report() -> VerifyReport:
    return VerifyReport(
        findings=[
            Defect(id="d1", cls="B", node="SCR-1", change="c", reason="r", severity="blocker")
        ],
        patches=[Patch(node="SCR-1", change="c", reason="r", cls="B")],
        rounds_used=2,
        critic_vendor="same",
        degraded_nodes=["SCR-2", "SCR-2"],
        cost_micros=42,
    )


def test_manifest_fields_say_checked_by_and_the_vendor_honestly() -> None:
    assert manifest_fields() == {"checked_by": ["self_check", "verify"], "critic_vendor": "same"}
    assert manifest_fields("different")["critic_vendor"] == "different"


def test_evidence_has_findings_patches_rounds_and_canaries_per_card() -> None:
    canaries = [
        CanaryResult(task_id="T-2", blocking_questions=["q1", "q2"]),
        CanaryResult(
            task_id="T-1",
            acceptance_coverage=[AcceptanceCover(criterion="c", satisfiable=True, how="h")],
            disagreement=True,
        ),
    ]
    data = evidence_dict(_report(), canaries)
    assert data["schema"] == "spec.verify.v1" and data["rounds_used"] == 2
    assert data["checked_by"] == ["self_check", "verify"] and data["critic_vendor"] == "same"
    assert data["findings"][0]["node"] == "SCR-1" and set(data["patches"][0]) == {
        "node",
        "field",
        "change",
        "reason",
        "cls",
    }
    assert list(data["canaries"]) == ["T-1", "T-2"]  # per card, sorted
    assert data["canary_summary"] == {
        "cards": 2,
        "blocking_questions": 2,
        "disagreements": 1,
        "flagged": 0,
    }
    assert data["degraded_nodes"] == ["SCR-2"]


def test_evidence_json_is_deterministic_text() -> None:
    a = evidence_json(_report(), [CanaryResult(task_id="T-1")])
    b = evidence_json(_report(), [CanaryResult(task_id="T-1")])
    assert a == b and a.endswith("\n") and "\r" not in a
    assert json.loads(a)["schema"] == "spec.verify.v1"
    assert a == json.dumps(json.loads(a), sort_keys=True, indent=1, ensure_ascii=False) + "\n"


async def test_evidence_from_a_real_run(cfg, big_graph) -> None:
    from verify_support.calls import GatewayCall

    from mde_spec_core.verify_port import StructureInput
    from mde_verify_agent.port import VerifyAgent

    port = VerifyAgent(GatewayCall(cost_micros=7), cfg)
    report = await port.structure_critic(StructureInput(graph=big_graph))
    text = port.evidence_json(report)
    assert json.loads(text)["cost_micros"] == 7
