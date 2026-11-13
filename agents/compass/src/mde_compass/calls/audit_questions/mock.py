"""Mock handler of ``cmp.audit_questions``: the same deterministic heuristic as the fallback, with every ``fix``
labelled ``[mock]`` so a mock session is never mistaken for a real review. Offline tests and demos only."""

from __future__ import annotations

from mde_compass.calls.audit_questions.fallback import heuristic
from mde_compass.calls.audit_questions.schema import AuditInput, AuditOutput


def handle(inp: AuditInput) -> AuditOutput:
    return heuristic(inp, mock=True)
