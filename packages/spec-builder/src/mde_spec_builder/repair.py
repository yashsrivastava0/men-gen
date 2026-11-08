"""B8: repair, and the one question when repair is not enough.

``repair_loop`` hands the error findings (validators and critic) to C13, applies what comes back
through the same ``validate_and_apply`` as every other proposal, re-validates, and repeats: **at
most two rounds**. If errors remain, ``derive_question`` builds ONE plain-language ``choice3``
question from the first failing check (a ``CardInstance`` built in code, data in
``rules.yaml``) and the engine returns ``needs_answer``: the flow sends the user back to Discover
for that slot only and re-runs only the failing writer (``BuildRequest.answer_slots``).

Fallback: no proposals when the model is unavailable or answers something unusable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from mde_content import packs as content_packs
from mde_contracts.calls import (
    BriefContext,
    C12Input,
    C12Output,
    C13Input,
    C13Output,
    CriticFinding,
    Proposal,
    ValidatorFinding,
)
from mde_contracts.cards import Card, CardInstance, CardOption, OtherInput, SkipSpec
from mde_contracts.engines import BuildRequest
from mde_contracts.gateway import CallSpec
from mde_contracts.spec import AppSpec
from mde_core.clock import Clock
from mde_spec_builder.baseline import resolve_pack_id
from mde_spec_builder.context import c13_input
from mde_spec_builder.critic import run_critic
from mde_spec_builder.llm import LlmRunner
from mde_spec_builder.proposals import AppliedProposal, ApplyResult, DroppedProposal
from mde_spec_builder.rules import Rules
from mde_spec_builder.slots import SlotView
from mde_spec_builder.validators import VALIDATOR_NAMES

MAX_ROUNDS = 2
MAX_REPAIR_PROPOSALS = 20
Finding = ValidatorFinding | CriticFinding
_ORDER = {name: i for i, name in enumerate(VALIDATOR_NAMES)}
_OPTION_IDS = ("A", "B", "C")


def error_findings(
    validators: Sequence[ValidatorFinding], critic: Sequence[CriticFinding]
) -> list[Finding]:
    """The blocking findings: validator errors first (in validator order), then critic errors."""
    ordered = sorted(
        (f for f in validators if f.severity == "error"),
        key=lambda f: _ORDER.get(f.validator, len(_ORDER)),
    )
    return [*ordered, *(f for f in critic if f.severity == "error")]


@dataclass
class RepairOutcome:
    spec: AppSpec
    validators: list[ValidatorFinding]
    critic: list[CriticFinding]
    rounds: int = 0
    applied: list[AppliedProposal] = field(default_factory=list)
    dropped: list[DroppedProposal] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return error_findings(self.validators, self.critic)


async def repair_loop(
    spec: AppSpec,
    brief: BriefContext,
    validators: list[ValidatorFinding],
    critic: list[CriticFinding],
    *,
    runner: LlmRunner,
    repair_spec: CallSpec[C13Input, C13Output] | None,
    critic_spec: CallSpec[C12Input, C12Output] | None,
    validate: Callable[[AppSpec], list[ValidatorFinding]],
    apply: Callable[[AppSpec, list[Proposal]], ApplyResult],
    max_rounds: int = MAX_ROUNDS,
) -> RepairOutcome:
    """Up to ``max_rounds`` rounds of C13 on the error findings; ``validate`` is the full set of
    validators, ``apply`` is ``validate_and_apply`` bound to the build's settings."""
    outcome = RepairOutcome(spec=spec, validators=validators, critic=critic)
    while (
        outcome.errors and outcome.rounds < min(max_rounds, MAX_ROUNDS) and repair_spec is not None
    ):
        if not runner.available:
            break
        outcome.rounds += 1
        out = await runner.call(
            repair_spec,
            c13_input(brief, outcome.spec, outcome.errors, round_number=outcome.rounds),
            step="repair",
        )
        if out is None:
            break
        proposals = list(out.proposals)
        result = apply(outcome.spec, proposals[:MAX_REPAIR_PROPOSALS])
        for extra in proposals[MAX_REPAIR_PROPOSALS:]:
            result.dropped.append(
                DroppedProposal(
                    proposal=extra, code="limit", reason="too many repairs in one answer"
                )
            )
        outcome.spec = result.spec
        outcome.applied += result.applied
        outcome.dropped += result.dropped
        outcome.validators = validate(outcome.spec)
        if outcome.critic:  # judge the repaired spec again
            outcome.critic = await run_critic(
                runner, critic_spec, outcome.spec, brief, outcome.validators
            )
    return outcome


# --------------------------------------------------------------------------- the question


def _resolve_sets(
    sets: dict[str, str | list[str]], core: list[str], defaults: list[str]
) -> dict[str, str | list[str] | None]:
    tokens = {"$core_features": core, "$default_features": defaults}
    return {
        slot: (tokens[v] if isinstance(v, str) and v in tokens else v) for slot, v in sets.items()
    }


def derive_question(
    errors: Sequence[Finding],
    req: BuildRequest,
    rules: Rules,
    *,
    clock: Clock,
    new_id: Callable[[str], str],
) -> tuple[CardInstance, str]:
    """ONE plain question (a ``choice3`` card) from the first failing check, and its slot."""
    first = errors[0] if errors else None
    key = first.validator if isinstance(first, ValidatorFinding) else None
    template = rules.questions.get(key or "", rules.questions["default"])
    pack = content_packs.load_pack(resolve_pack_id(SlotView.of(req)))
    core = content_packs.core_features(pack)
    defaults = content_packs.default_features(pack)
    options = [
        CardOption(
            id=_OPTION_IDS[i],
            label=option.label,
            description=option.description or None,
            recommended=i == 0,
            sets=_resolve_sets(option.sets, core, defaults),  # type: ignore[arg-type]
        )
        for i, option in enumerate(template.options[: len(_OPTION_IDS)])
    ]
    card = Card(
        id=f"Q-build-{key if key in rules.questions else 'default'}",
        slot=template.slot,  # type: ignore[arg-type]
        type="choice3",
        text=template.text,
        options=options,
        other=OtherInput(enabled=True, placeholder="Tell me in your own words"),
        skip=SkipSpec(enabled=True, assumes=options[0].id),
        kind="resolution",
    )
    instance = CardInstance(
        instance_id=new_id("crd"),
        card=card,
        shown_at=clock.now(),
        language=req.brief.language,
        generated=False,
    )
    return instance, template.slot
