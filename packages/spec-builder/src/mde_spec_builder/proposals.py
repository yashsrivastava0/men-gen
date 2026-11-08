"""B3/B4: validated proposals. A model never edits the spec; it *proposes* (``Proposal``), code
checks each one against the spec and the validators and applies it or drops it with a reason.

``validate_and_apply(spec, proposals, ...)`` returns ``(spec, applied, dropped)``:

* proposals are applied **one at a time**, in order; after each one the cheap validators run
  (everything that does not need the file rendered: structure, reference graph, screen count,
  Stack Profile) and a proposal that would introduce a new error is rolled back;
* a proposal is dropped, never repaired: a wrong target, a duplicate route, an unknown recipe, a
  token that is not a token, a copy string that is too long or looks like a secret, a research
  reference that is missing or switched off, the primary journey, a cap that would be exceeded;
* ``source: research`` proposals need a valid ``research_ref`` of the Research Note that was not
  toggled off, and what they create is tagged ``source="research"`` (+ ``research_ref`` where the
  item has one);
* the build sequence is recomputed after every change so it never refers to a removed screen.

Everything a proposal can say is capped and secret-scanned (``sanitize``), so what is applied reads
back from the rendered file exactly as written. The full nine validators run once per build (B6)
and after every repair round; ``strict=True`` runs them after every proposal (tests, debugging).
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from typing import NamedTuple

from pydantic import ValidationError

from mde_content.recipes import load_recipes
from mde_content.stack import load_stack_profile
from mde_contracts.calls import Proposal, SpecProposals, ValidatorFinding
from mde_contracts.common import Frozen
from mde_contracts.content import StackProfile
from mde_contracts.policy import Limits
from mde_contracts.research import ResearchNote
from mde_contracts.spec import AppSpec
from mde_spec_builder.proposal_ops import HANDLERS, Change, OpsContext
from mde_spec_builder.sanitize import Reject, RejectCode
from mde_spec_builder.sequence import build_sequence
from mde_spec_builder.validators import errors as error_findings
from mde_spec_builder.validators import run_validators
from mde_spec_builder.validators._common import finding
from mde_spec_builder.validators.actions import action_targets
from mde_spec_builder.validators.bindings import data_bindings
from mde_spec_builder.validators.capabilities import stack_profile as stack_findings
from mde_spec_builder.validators.flows import flow_endpoints
from mde_spec_builder.validators.gates import role_gates
from mde_spec_builder.validators.navigation import navigation_targets
from mde_spec_builder.validators.structure import (
    _data_model,
    _design,
    _header,
    _references,
    _uniqueness,
)
from mde_spec_loader.tokens import estimate_tokens

log = logging.getLogger(__name__)
_DEFAULT_TOKENS = 60_000


class AppliedProposal(Frozen):
    proposal: Proposal
    touched_ids: list[str]  # ids edited or created
    created_ids: list[str] = []  # ids created


class DroppedProposal(Frozen):
    proposal: Proposal
    code: RejectCode
    reason: str


class ApplyResult(NamedTuple):
    """Unpacks as ``(spec, applied, dropped)``."""

    spec: AppSpec
    applied: list[AppliedProposal]
    dropped: list[DroppedProposal]


def quick_findings(spec: AppSpec, limits: Limits, stack: StackProfile) -> list[ValidatorFinding]:
    """The validators that do not need the file rendered (about 10 ms for a 30-screen spec)."""
    out = [
        *_uniqueness(spec),
        *_header(spec),
        *_data_model(spec),
        *_design(spec),
        *_references(spec),
        *navigation_targets(spec),
        *data_bindings(spec),
        *action_targets(spec),
        *role_gates(spec),
        *flow_endpoints(spec),
        *stack_findings(spec, stack),
    ]
    if len(spec.screens) > limits.max_screens:
        out.append(
            finding(
                "limits",
                f"The app has {len(spec.screens)} screens, over the cap of {limits.max_screens}",
                *[s.id for s in spec.screens[limits.max_screens :]],
            )
        )
    return out


def _error_keys(findings: Sequence[ValidatorFinding]) -> set[tuple[str, str]]:
    return {(f.validator, f.message) for f in error_findings(findings)}


def _check_research(p: Proposal, ctx: OpsContext) -> None:
    if p.source != "research":
        return
    ref = p.research_ref
    note = ctx.note
    if not ref:
        raise Reject("research", "a research proposal needs the research_ref it comes from")
    if note is None or note.status == "unavailable":
        raise Reject("research", "there is no research to draw on")
    if ref not in {s.id for s in note.suggestions}:
        raise Reject("research", f"{ref} is not in the Research Note")
    if ref in ctx.excluded:
        raise Reject("research", f"{ref} was switched off")


def validate_and_apply(
    spec: AppSpec,
    proposals: SpecProposals | Sequence[Proposal],
    *,
    research: ResearchNote | None = None,
    excluded: Collection[str] = (),
    limits: Limits | None = None,
    stack_profile: StackProfile | None = None,
    max_tokens: int | None = None,
    tokens_used: int | None = None,
    allow_real: bool = False,
    strict: bool = False,
) -> ApplyResult:
    """Apply the valid proposals to ``spec`` one at a time; drop the rest with a reason.

    ``max_tokens`` with ``tokens_used`` (the spec's current size estimate) stops proposals that
    would grow the file past its budget. ``allow_real`` lets ``set_integration_mode`` connect an
    integration for real (never the default).
    """
    items = list(proposals.proposals if isinstance(proposals, SpecProposals) else proposals)
    limits = limits or Limits()
    stack = stack_profile or load_stack_profile()
    ctx = OpsContext(
        recipes={r.id: r for r in load_recipes().recipes},
        limits=limits,
        note=research,
        excluded=frozenset(excluded),
        allow_real=allow_real,
    )
    current = spec
    start = quick_findings(current, limits, stack)
    if strict:
        start += run_validators(
            current, stack_profile=stack, limits=limits, max_tokens=max_tokens or _DEFAULT_TOKENS
        )
    known_errors = _error_keys(start)
    used = tokens_used
    applied: list[AppliedProposal] = []
    dropped: list[DroppedProposal] = []
    for proposal in items:
        try:
            cost = estimate_tokens(proposal.model_dump_json())
            if max_tokens is not None and used is not None and used + cost > max_tokens:
                raise Reject("limit", "the spec file would pass its size budget")
            _check_research(proposal, ctx)
            handler = HANDLERS.get(proposal.kind)
            if handler is None:  # pragma: no cover - ProposalKind is closed
                raise Reject("unsupported", f"unknown proposal kind {proposal.kind}")
            change: Change = handler(current, proposal, ctx)
            candidate = change.spec.model_copy(update={"build": build_sequence(change.spec)})
            found = quick_findings(candidate, limits, stack)
            if strict:
                found = [
                    *found,
                    *run_validators(
                        candidate,
                        stack_profile=stack,
                        limits=limits,
                        max_tokens=max_tokens or _DEFAULT_TOKENS,
                    ),
                ]
            fresh = [
                f for f in error_findings(found) if (f.validator, f.message) not in known_errors
            ]
            if fresh:
                raise Reject(
                    "breaks_validator", f"it would break {fresh[0].validator}: {fresh[0].message}"
                )
        except Reject as exc:
            dropped.append(DroppedProposal(proposal=proposal, code=exc.code, reason=exc.reason))
            log.info(
                "proposal dropped",
                extra={"kind": proposal.kind, "code": exc.code, "reason": exc.reason},
            )
            continue
        except ValidationError as exc:
            reason = "a value does not fit the spec model: " + ", ".join(
                str(e["loc"][0]) for e in exc.errors() if e["loc"]
            )
            dropped.append(DroppedProposal(proposal=proposal, code="invalid", reason=reason))
            log.info(
                "proposal dropped",
                extra={"kind": proposal.kind, "code": "invalid", "reason": reason},
            )
            continue
        current = candidate
        known_errors = _error_keys(found)
        used = None if used is None else used + cost
        applied.append(
            AppliedProposal(
                proposal=proposal, touched_ids=change.touched, created_ids=list(change.created)
            )
        )
    return ApplyResult(current, applied, dropped)
