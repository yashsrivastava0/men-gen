"""Checks on what a session produced: the spec, its hash, the consumer contract, the hand-off repo.

Shared by the e2e tests and the eval graders (one definition of "a valid APP_SPEC.md").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mde_content.stack import load_stack_profile
from mde_contracts.calls import ValidatorFinding
from mde_contracts.policy import Policy
from mde_contracts.records import SpecVersionRecord
from mde_core.hashing import sha256_hex
from mde_flow.handoff import LOCK_FILE, SPEC_FILE, LocalGitProjectRepo
from mde_spec_builder.render import render_spec
from mde_spec_builder.validators import VALIDATOR_NAMES, run_validators
from mde_spec_loader import (
    ConsumerReport,
    SpecIndex,
    compute_spec_hash,
    parse_spec,
    simulate_consumer,
    verify_hash,
)

HashRule = Literal["loader", "sha256", "none"]


@dataclass
class SpecChecks:
    findings: list[ValidatorFinding]
    errors: list[ValidatorFinding]
    validators_run: tuple[str, ...]
    embedded_hash_verifies: bool  # the loader rule: sha256 of the file with `hash: ""`
    record_hash_rule: HashRule  # which rule the stored record hash follows
    round_trips: bool  # parse -> render gives the same bytes
    consumer: ConsumerReport
    problems: list[str] = field(default_factory=lambda: [])

    @property
    def ok(self) -> bool:
        return not self.problems


def check_spec(record: SpecVersionRecord, policy: Policy) -> SpecChecks:
    """Everything a valid ``APP_SPEC.md`` must satisfy, as one list of problems (empty = valid)."""
    text = record.spec_text
    problems: list[str] = []
    parsed = parse_spec(text)  # raises SpecParseError if the file does not parse back
    findings = run_validators(parsed, stack_profile=load_stack_profile(), limits=policy.limits)
    errors = [f for f in findings if f.severity == "error"]
    problems += [f"validator {f.validator}: {f.message}" for f in errors]
    embedded = verify_hash(text)
    if not embedded:
        problems.append("the embedded hash does not verify (loader rule)")
    rule: HashRule = (
        "loader"
        if record.hash == compute_spec_hash(text)
        else "sha256"
        if record.hash == sha256_hex(text)
        else "none"
    )
    if rule == "none":
        problems.append("the record hash follows neither the loader rule nor sha256(file)")
    round_trips = render_spec(parsed) == text
    if not round_trips:
        problems.append("parse then render does not reproduce the file bytes")
    report = simulate_consumer(SpecIndex(text))
    problems += [f"consumer: {p}" for p in report.problems]
    problems += [
        f"consumer: slice {s.id} is {s.tokens} tokens, over the budget"
        for s in report.slices
        if s.over_budget
    ]
    return SpecChecks(
        findings=findings,
        errors=errors,
        validators_run=VALIDATOR_NAMES,
        embedded_hash_verifies=embedded,
        record_hash_rule=rule,
        round_trips=round_trips,
        consumer=report,
        problems=problems,
    )


@dataclass
class RepoCheck:
    repo_dir: Path
    spec_text: str
    lock: dict[str, object]
    commits: int
    tags: list[str]


def read_handoff_repo(repo: LocalGitProjectRepo, tenant_id: str, app_id: str) -> RepoCheck:
    """What the hand-off left on disk: the file, the lock, the commits and tags."""
    repo_dir = repo.repo_dir(tenant_id, app_id)
    git = shutil.which("git") or "git"

    def run(*args: str) -> str:
        out = subprocess.run(  # noqa: S603 - fixed argv in our own temp repo
            [git, "-C", str(repo_dir), *args], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()

    return RepoCheck(
        repo_dir=repo_dir,
        spec_text=(repo_dir / SPEC_FILE).read_bytes().decode("utf-8"),
        lock=json.loads((repo_dir / LOCK_FILE).read_text(encoding="utf-8")),
        commits=int(run("rev-list", "--count", "HEAD") or 0),
        tags=run("tag", "--list").split(),
    )
