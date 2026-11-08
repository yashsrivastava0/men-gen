"""B7: the critic. C12 compares the spec outline with the approved Brief (mismatches, contradictions,
missing flows or features) and sees what the validators found. Its findings are advice for the
repair step; the validators stay the authority on validity.

Fallback: ``pass`` (no findings) when the model is unavailable or answers something unusable.
"""

from __future__ import annotations

from collections.abc import Sequence

from mde_contracts.calls import (
    BriefContext,
    C12Input,
    C12Output,
    CriticFinding,
    ValidatorFinding,
)
from mde_contracts.gateway import CallSpec
from mde_contracts.spec import AppSpec
from mde_spec_builder.assemble import spec_ids
from mde_spec_builder.context import c12_input
from mde_spec_builder.llm import LlmRunner
from mde_spec_builder.sanitize import Reject, clean

MAX_FINDINGS = 5
CRITIC_VALIDATOR = (
    "critic"  # the ``validator`` name of a critic finding turned into a ValidatorFinding
)


def sanitize_findings(spec: AppSpec, output: C12Output) -> list[CriticFinding]:
    """At most five findings with clean messages and only ids that exist in the spec."""
    known = set(spec_ids(spec)) | {"APP", "DESIGN", "NAV", "DATA", "API", "ROLES", "BEHAVIOR"}
    out: list[CriticFinding] = []
    for item in output.findings:
        try:
            message = clean(item.message, 300, "message")
            fix = clean(item.suggested_fix, 200, "fix", required=False) or None
        except Reject:
            continue
        out.append(
            item.model_copy(
                update={
                    "message": message,
                    "suggested_fix": fix,
                    "target_ids": [i for i in dict.fromkeys(item.target_ids) if i in known],
                }
            )
        )
    return out[:MAX_FINDINGS]


async def run_critic(
    runner: LlmRunner,
    call_spec: CallSpec[C12Input, C12Output] | None,
    spec: AppSpec,
    brief: BriefContext,
    validator_findings: Sequence[ValidatorFinding],
) -> list[CriticFinding]:
    """C12 findings for ``spec`` (empty when the call falls back). ``call_spec`` is the loaded C12
    spec; ``None`` means it is unavailable and the critic passes."""
    if call_spec is None:
        return []
    out = await runner.call(call_spec, c12_input(brief, spec, validator_findings), step="critic")
    if out is None:
        return []
    return sanitize_findings(spec, out)


def as_validator_findings(findings: Sequence[CriticFinding]) -> list[ValidatorFinding]:
    """Critic findings in the common shape of ``BuildResult.findings``."""
    return [
        ValidatorFinding(
            validator=CRITIC_VALIDATOR,
            severity=f.severity,
            target_ids=list(f.target_ids),
            message=f.message,
        )
        for f in findings
    ]
