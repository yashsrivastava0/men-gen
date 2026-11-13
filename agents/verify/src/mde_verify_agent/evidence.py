"""evidence/verify.json and the manifest fields (doc 19 3.5). Deterministic text: sorted keys, no timestamps."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mde_spec_core.verify_port import CanaryResult, CriticVendor, Patch, VerifyReport

EVIDENCE_SCHEMA = "spec.verify.v1"
CHECKED_BY = ["self_check", "verify"]


def manifest_fields(critic_vendor: CriticVendor = "same") -> dict[str, Any]:
    """What MANIFEST.json carries once Verify ran: `checked_by` and `critic_vendor` (never a claim of independence
    the run did not have)."""
    return {"checked_by": list(CHECKED_BY), "critic_vendor": critic_vendor}


def evidence_dict(
    report: VerifyReport,
    canaries: Iterable[CanaryResult] = (),
    patches: Sequence[Patch] | None = None,
) -> dict[str, Any]:
    """`canaries` (merged per card) and `patches` default to the report's own."""
    cards: Mapping[str, CanaryResult] = {c.task_id: c for c in (list(canaries) or report.canaries)}
    pl = list(patches) if patches is not None else report.patches
    questions = sum(len(c.blocking_questions) for c in cards.values())
    return {
        "schema": EVIDENCE_SCHEMA,
        **manifest_fields(report.critic_vendor),
        "rounds_used": report.rounds_used,
        "findings": [d.model_dump(mode="json") for d in report.findings],
        "patches": [p.model_dump(mode="json") for p in pl],
        "degraded_nodes": sorted(set(report.degraded_nodes)),
        "canaries": {tid: cards[tid].model_dump(mode="json") for tid in sorted(cards)},
        "canary_summary": {
            "cards": len(cards),
            "blocking_questions": questions,
            "disagreements": sum(1 for c in cards.values() if c.disagreement),
            "flagged": sum(1 for c in cards.values() if c.flagged),
        },
        "differs_from_brief": list(report.differs_from_brief),
        "flagged_calls": sorted(set(report.flagged_calls)),
        "cost_micros": report.cost_micros,
        "notes": list(report.notes),
    }


def evidence_json(
    report: VerifyReport,
    canaries: Iterable[CanaryResult] = (),
    patches: Sequence[Patch] | None = None,
) -> str:
    """The content of evidence/verify.json: findings, patches, rounds and the canary results per task card."""
    return (
        json.dumps(
            evidence_dict(report, canaries, patches), sort_keys=True, indent=1, ensure_ascii=False
        )
        + "\n"
    )
