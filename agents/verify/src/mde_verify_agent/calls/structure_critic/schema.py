"""Input and output of the structure critic. The output is the PORTABLE SUBSET: flat closed records in one array,
no optional or nullable fields, small enums, depth 3."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_spec_core.verify_port import VerifyCallInput

Kind = Literal[
    "contradiction",
    "missing_flow",
    "unreachable_screen",
    "dead_end_screen",
    "role_mismatch",
    "entity_mismatch",
    "cross_app",
    "locked_changed",
    "other",
]


class StructureIn(VerifyCallInput):
    language: str
    brief: str  # the Brief's summary and quotes as ONE block: the user's words, DATA
    facts: list[
        str
    ]  # Locked facts of the Brief and the node that must carry each (chunks of lines)
    digest: list[str]  # the frozen structure in plain lines (chunks)
    code_findings: list[str]  # what code already found (chunks): do not repeat


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingOut(W):
    kind: Kind
    node: str  # an id from the digest (SCR-012, ENT-004, MOD-002, FLW-001, ...)
    field: str  # the part of the node, or an empty string
    change: str  # what should change, in one sentence
    reason: str  # why, in one sentence, quoting the Brief or the structure
    severity: Literal["blocker", "warning"]
    cls: Literal["B", "C"]  # B: the node is re-written; C: a person must decide


class StructureOut(W):
    findings: list[FindingOut]
    summary: str
