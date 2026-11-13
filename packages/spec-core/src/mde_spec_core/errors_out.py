"""Structured, agent-readable errors out of findings (doc 15 7.2, doc 17 2.9, doc 18 3).

One shape for every consumer: ``{code, severity, where, why, fix}`` plus the repair class and, when the
error catalogue knows the validator, its catalogue code. Plain words for the owner come from the same fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import libraries
from .validators.base import Finding

_SEVERITY_ORDER = {"blocker": 0, "warning": 1, "info": 2}


def catalogue_codes(validator_id: str) -> list[str]:
    """The catalogue codes whose `validators` list names this validator (empty when none)."""
    return sorted(c.code for c in libraries.errors().values() if validator_id in c.validators)


def error_for_finding(f: Finding) -> dict[str, Any]:
    """{code, severity, where, why, fix, repair, catalogue}: the structured error for one finding."""
    return {
        "code": f.code,
        "severity": f.severity,
        "where": f.where,
        "why": f.why,
        "fix": f.fix,
        "repair": f.repair,
        "catalogue": catalogue_codes(f.code) if f.code.startswith("V-") else [],
    }


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.code, f.where, f.why)
    )


def errors_for(findings: Iterable[Finding]) -> list[dict[str, Any]]:
    """Structured errors, blockers first, in a fixed order (deterministic output)."""
    return [error_for_finding(f) for f in sort_findings(findings)]


def finding_from_catalogue(
    code: str, where_code: str = "", *, severity: str | None = None, **args: Any
) -> Finding:
    """A Finding built from a catalogue entry (make_error fills where, why and fix from the templates)."""
    err = libraries.make_error(code, **args)
    sev = severity or {"error": "blocker", "warning": "warning", "info": "info"}[err["severity"]]
    ent = libraries.error(code)
    return Finding(
        where_code or (ent.validators[0] if ent.validators else code),
        sev,  # type: ignore[arg-type]
        err["where"],
        err["why"],
        err["fix"],
        err["repair_class"],  # type: ignore[arg-type]
        detail=code,
    )


def plain_words(f: Finding) -> str:
    """One owner-readable line: where, what is wrong, what to do."""
    tag = {"blocker": "BLOCKER", "warning": "warning", "info": "note"}.get(f.severity, f.severity)
    return f"[{tag}] {f.code} at {f.where}: {f.why}. Fix: {f.fix}"


def counts(findings: Iterable[Finding]) -> dict[str, dict[str, int]]:
    """Counts by severity and by validator code (every severity present, zeros included)."""
    by_sev = {"blocker": 0, "warning": 0, "info": 0}
    by_code: dict[str, int] = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        by_code[f.code] = by_code.get(f.code, 0) + 1
    return {"by_severity": by_sev, "by_validator": dict(sorted(by_code.items()))}
