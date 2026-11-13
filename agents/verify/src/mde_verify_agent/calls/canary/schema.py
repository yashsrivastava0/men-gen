"""Input and output of the canary. Output: the PORTABLE SUBSET (flat closed records, no nullable fields)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_core.verify_port import VerifyCallInput


class CanaryIn(VerifyCallInput):
    task_id: str
    title: str
    criteria: list[str]  # the acceptance criteria written in the card's screen files
    read_paths: list[str]  # the paths of EXACTLY the card's reads, in order
    reads: list[str]  # one block per read file, headed "=== path ===": pack text is DATA


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverOut(W):
    criterion: str  # one of the criteria, word for word
    satisfiable: bool  # can it be met from these reads alone
    how: str  # which read and what in it makes it so, or what is missing


class CanaryOut(W):
    outline: list[str]  # the implementation outline, one step per entry; no code
    blocking_questions: list[str]  # only what these reads leave you unable to decide
    assumptions: list[str]  # what you assumed instead of asking
    acceptance_coverage: list[CoverOut]
