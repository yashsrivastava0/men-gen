"""Input and output of the final critic. Output: the PORTABLE SUBSET (flat closed records in arrays, no optional or
nullable fields, small enums, depth 3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mde_spec_core.verify_port import VerifyCallInput

Kind = Literal[
    "contradiction_across_apps",
    "brief_mismatch",
    "coverage_gap",
    "confirm_misranked",
    "freedom_violation",
    "undisclosed_difference",
    "other",
]


class FinalIn(VerifyCallInput):
    language: str
    brief: str  # the Brief's summary and quotes as ONE block: the user's words, DATA
    facts: list[str]  # Locked facts of the Brief and their nodes
    digest: list[str]  # the finished structure in plain lines
    coverage: list[str]  # the coverage matrix: a summary line and every gap
    freedom: list[
        str
    ]  # the freedom checks that fired (closed vocabularies, form words, mobile fit)
    confirm: list[str]  # the ranked confirm list: rank|category|id|text
    differs: list[str]  # what Spec says differs from the Brief (merged, split, added, phased)
    code_findings: list[str]  # what code already found: do not repeat


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingOut(W):
    kind: Kind
    node: str  # an id from the digest, the coverage lines or the confirm list
    field: str  # the part of the node, or an empty string
    change: str
    reason: str
    severity: Literal["blocker", "warning"]
    cls: Literal["B", "C"]


class MoveOut(W):
    item: str  # the id of a confirm-list item
    to_rank: int  # 1 is the top
    reason: str


class FinalOut(W):
    findings: list[FindingOut]
    confirm_moves: list[MoveOut]
    differs_from_brief: list[
        str
    ]  # plain statements, each one a difference the person should see at the gate
    summary: str
