"""Readiness: can Spec go on with this input? Fatal ONLY for no idea statement or no feature (doc 15 section 3.1).

Scope cannot be invented (Research never sets it), so those two stop Spec and hand the orchestrator a plain list of what
Compass must still ask. Spec asks the user nothing itself. Everything else proceeds as ``thin_input``: roles are
derived and marked ``assumed``, the locale defaults, and the warnings below travel to the gates.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mde_compass_intake.formats import ExportRefused
from mde_compass_intake.models import Frozen
from mde_compass_intake.reader import read_export
from mde_compass_intake.spec_input import SpecInput

NO_IDEA = "What the app is: one plain sentence saying what it does and who it is for."
NO_FEATURE = "What people must be able to do in the app: at least one feature that is in scope."
NO_BRIEF = "A Compass export with a Brief: the file given has none."


class Readiness(Frozen):
    ready: bool
    missing: tuple[str, ...] = ()  # what Compass must still ask, in plain words (empty when ready)
    warnings: tuple[str, ...] = ()  # not fatal: the input is thin or weak, the gates say so


class Intake(Frozen):
    """The result of reading a file: the input (None when the file is no export at all) and whether Spec can go on."""

    spec_input: SpecInput | None
    readiness: Readiness
    refused: str | None = None  # the ExportRefused code, when the file was refused


def check_readiness(spec_input: SpecInput) -> Readiness:
    missing: list[str] = []
    if not spec_input.idea.summary:
        missing.append(NO_IDEA)
    if not spec_input.in_scope_features():
        missing.append(NO_FEATURE)
    warnings = [f"{a.field}: {a.reason}" for a in spec_input.assumptions]
    if spec_input.trust.weak_inputs:
        warnings.append("weak inputs: Compass was degraded or the approval cannot be verified")
    return Readiness(ready=not missing, missing=tuple(missing), warnings=tuple(warnings))


def intake(data: Mapping[str, Any], *, program_id: str | None = None) -> Intake:
    """Read a file and check readiness in one step. A refused file is a readiness failure with a plain list, not an
    exception, so the orchestrator always has something to relay."""
    try:
        spec_input = read_export(data, program_id=program_id)
    except ExportRefused as refused:
        missing = (NO_BRIEF,) if refused.code == "not_an_export" else (NO_IDEA, NO_FEATURE)
        return Intake(
            spec_input=None,
            readiness=Readiness(ready=False, missing=missing),
            refused=refused.code,
        )
    return Intake(spec_input=spec_input, readiness=check_readiness(spec_input))
