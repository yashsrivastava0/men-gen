"""Critic (B7), repair (B8), the one question, and the runner that keeps every call non-fatal."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from helpers import DirectMockGateway, ScriptedGateway, make_brief, make_context, make_request

from mde_content.packs import core_features, default_features, load_pack
from mde_content.stack import load_stack_profile
from mde_contracts.calls import (
    C12Output,
    CriticFinding,
    KeyValue,
    Proposal,
    SpecProposals,
    ValidatorFinding,
)
from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec, ScreenAction
from mde_core.cancel import CancellationToken
from mde_core.clock import FakeClock
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CircuitOpen,
    ProviderError,
    StructuredOutputError,
)
from mde_core.ids import SequentialIds
from mde_prompts.registry import load_spec
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.context import brief_context, c12_input
from mde_spec_builder.critic import as_validator_findings, run_critic, sanitize_findings
from mde_spec_builder.llm import LlmRunner, run_all
from mde_spec_builder.proposals import validate_and_apply
from mde_spec_builder.repair import derive_question, error_findings, repair_loop
from mde_spec_builder.rules import load_rules
from mde_spec_builder.validators import run_validators

STACK = load_stack_profile()
REQ = make_request("commerce", brief=make_brief())
SPEC = build_baseline(REQ)
BRIEF = brief_context(REQ, SPEC)
C12 = load_spec("C12.critic")
C13 = load_spec("C13.repair")


def runner(gateway: Any) -> LlmRunner:
    return LlmRunner(gateway, make_context())


def broken_spec() -> AppSpec:
    screen = SPEC.screens[5]
    bad = screen.model_copy(
        update={"actions": [*screen.actions, ScreenAction(name="oops", go="SCR-099")]}
    )
    return SPEC.model_copy(
        update={"screens": [bad if s.id == screen.id else s for s in SPEC.screens]}
    )


def validate(spec: AppSpec) -> list[ValidatorFinding]:
    return run_validators(spec, stack_profile=STACK, limits=Limits())


# --------------------------------------------------------------------------- the runner


async def test_run_all_keeps_the_order_and_reraises_after_everything_finished() -> None:
    order: list[int] = []

    def job(n: int, delay: int):  # type: ignore[no-untyped-def]
        async def run() -> int:
            for _ in range(delay):
                await asyncio.sleep(0)
            order.append(n)
            return n

        return run

    jobs = [job(0, 5), job(1, 1), job(2, 3)]
    assert await run_all(jobs, 1) == [0, 1, 2] and order == [0, 1, 2]
    order.clear()
    assert await run_all(jobs, 3) == [0, 1, 2] and order == [1, 2, 0]  # finished out of order

    async def boom() -> int:
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        await run_all([job(0, 1), boom], 2)
    assert await run_all([], 3) == []


async def test_the_runner_returns_output_counts_calls_and_records_fallbacks() -> None:
    gateway = DirectMockGateway()
    r = runner(gateway)
    out = await r.call(
        C12,
        c12_input(BRIEF, SPEC, []),
        step="critic",
    )
    assert out is not None and out.verdict == "pass"
    assert (r.calls, r.failures, r.fallbacks, r.available) == (1, 0, [], True)
    assert gateway.calls[0][2].purpose == "build:critic"


@pytest.mark.parametrize("error", [StructuredOutputError("x"), ProviderError("500", status=500)])
async def test_a_gateway_error_is_a_fallback_and_retries_are_counted(error: Exception) -> None:
    gateway = ScriptedGateway(fallback=DirectMockGateway()).script("C12.critic", error, error)
    r = runner(gateway)
    assert await r.call(C12, c12_input(BRIEF, SPEC, []), step="critic", retries=1) is None
    assert (r.calls, r.failures, r.fallbacks, r.available) == (2, 2, ["C12.critic"], True)
    again = ScriptedGateway(fallback=DirectMockGateway()).script("C12.critic", error)
    r2 = runner(again)
    assert await r2.call(C12, c12_input(BRIEF, SPEC, []), step="critic", retries=1) is not None
    assert r2.failures == 1 and r2.fallbacks == []


@pytest.mark.parametrize("error", [BudgetExceeded("spent"), CircuitOpen("open")])
async def test_after_the_budget_or_the_circuit_the_runner_stops_calling(error: Exception) -> None:
    gateway = ScriptedGateway(fallback=DirectMockGateway()).script("C12.critic", error)
    r = runner(gateway)
    assert await r.call(C12, c12_input(BRIEF, SPEC, []), step="critic", retries=3) is None
    assert r.available is False and r.calls == 1
    assert await r.call(C12, c12_input(BRIEF, SPEC, []), step="critic") is None
    assert len(gateway.calls) == 1 and r.fallbacks == ["C12.critic", "C12.critic"]


async def test_cancellation_and_real_bugs_propagate() -> None:
    token = CancellationToken()
    r = LlmRunner(DirectMockGateway(), make_context(cancel=token))
    token.cancel("stop")
    with pytest.raises(CallCancelled):
        await r.call(C12, c12_input(BRIEF, SPEC, []), step="critic")
    raising = ScriptedGateway().script("C12.critic", CallCancelled("stop"))
    with pytest.raises(CallCancelled):
        await runner(raising).call(C12, c12_input(BRIEF, SPEC, []), step="critic")
    buggy = ScriptedGateway().script("C12.critic", ValueError("a bug"))
    with pytest.raises(ValueError, match="a bug"):
        await runner(buggy).call(C12, c12_input(BRIEF, SPEC, []), step="critic")


# --------------------------------------------------------------------------- critic


def test_critic_findings_are_cleaned_capped_and_limited_to_real_ids() -> None:
    findings = [
        CriticFinding(
            kind="mismatch",
            severity="error",
            message="  Home   is wrong ",
            target_ids=["SCR-004", "SCR-999", "SCR-004", "DATA"],
            suggested_fix=" Fix it ",
        ),
        CriticFinding(kind="other", severity="warn", message="m" * 301),  # too long: dropped
        CriticFinding(
            kind="other", severity="warn", message="token=hunter2hunter2"
        ),  # secret: dropped
        *[CriticFinding(kind="other", severity="info", message=f"note {i}") for i in range(8)],
    ]
    out = sanitize_findings(SPEC, C12Output(verdict="fix", findings=findings))
    assert len(out) == 5
    assert out[0].message == "Home is wrong" and out[0].target_ids == ["SCR-004", "DATA"]
    assert out[0].suggested_fix == "Fix it"
    converted = as_validator_findings(out[:2])
    assert [(f.validator, f.severity) for f in converted] == [
        ("critic", "error"),
        ("critic", "info"),
    ]


async def test_the_critic_passes_when_unavailable() -> None:
    assert await run_critic(runner(DirectMockGateway()), None, SPEC, BRIEF, []) == []
    failing = ScriptedGateway().script("C12.critic", BudgetExceeded("spent"))
    assert await run_critic(runner(failing), C12, SPEC, BRIEF, []) == []
    found = await run_critic(
        runner(DirectMockGateway()), C12, broken_spec(), BRIEF, validate(broken_spec())
    )
    assert [f.severity for f in found] == ["error"]  # the mock critic repeats validator errors


# --------------------------------------------------------------------------- repair


def fix_for(spec: AppSpec) -> SpecProposals:
    holder = next(s.id for s in spec.screens if any(a.name == "oops" for a in s.actions))
    return SpecProposals(
        proposals=[
            Proposal(
                kind="fix_reference",
                target=holder,
                source="repair",
                rationale="Dangling",
                pairs=[KeyValue(key="SCR-099", value="SCR-004")],
            )
        ]
    )


async def run_repair(spec: AppSpec, gateway: Any, **kw: Any) -> Any:
    findings = validate(spec)
    return await repair_loop(
        spec,
        BRIEF,
        findings,
        kw.pop("critic", []),
        runner=runner(gateway),
        repair_spec=C13,
        critic_spec=kw.pop("critic_spec", None),
        validate=validate,
        apply=lambda s, items: validate_and_apply(s, items),
        **kw,
    )


async def test_one_round_fixes_a_dangling_reference() -> None:
    spec = broken_spec()
    gateway = ScriptedGateway().script("C13.repair", fix_for(spec))
    outcome = await run_repair(spec, gateway)
    assert outcome.rounds == 1 and outcome.errors == [] and len(outcome.applied) == 1
    assert [e for e in outcome.validators if e.severity == "error"] == []


async def test_the_mock_repair_fixes_it_too() -> None:
    outcome = await run_repair(broken_spec(), DirectMockGateway())
    assert outcome.rounds == 1 and outcome.errors == []


async def test_never_more_than_two_rounds_even_when_asked_for_more() -> None:
    gateway = ScriptedGateway().script("C13.repair", SpecProposals(proposals=[]), repeat=True)
    outcome = await run_repair(broken_spec(), gateway, max_rounds=9)
    assert outcome.rounds == 2 and len(gateway.calls) == 2 and outcome.errors


async def test_a_failing_repair_call_ends_the_loop_and_leaves_the_errors() -> None:
    gateway = ScriptedGateway().script("C13.repair", StructuredOutputError("bad"))
    outcome = await run_repair(broken_spec(), gateway)
    assert outcome.rounds == 1 and outcome.errors
    nothing = await run_repair(SPEC, DirectMockGateway())
    assert nothing.rounds == 0 and nothing.errors == []  # no errors, no calls


async def test_no_rounds_when_the_model_is_unavailable_or_there_is_no_repair_spec() -> None:
    gone = runner(ScriptedGateway())
    gone.available = False
    outcome = await repair_loop(
        broken_spec(),
        BRIEF,
        validate(broken_spec()),
        [],
        runner=gone,
        repair_spec=C13,
        critic_spec=None,
        validate=validate,
        apply=lambda s, i: validate_and_apply(s, i),
    )
    assert outcome.rounds == 0 and outcome.errors
    none = await repair_loop(
        broken_spec(),
        BRIEF,
        validate(broken_spec()),
        [],
        runner=runner(ScriptedGateway()),
        repair_spec=None,
        critic_spec=None,
        validate=validate,
        apply=lambda s, i: validate_and_apply(s, i),
    )
    assert none.rounds == 0


async def test_a_critic_error_is_judged_again_after_each_round() -> None:
    err = CriticFinding(kind="mismatch", severity="error", message="Wrong thing")
    gateway = ScriptedGateway(fallback=DirectMockGateway())
    gateway.script("C12.critic", C12Output(verdict="pass", findings=[]))
    gateway.script("C13.repair", SpecProposals(proposals=[]))
    outcome = await run_repair(SPEC, gateway, critic=[err], critic_spec=C12)
    assert outcome.rounds == 1 and outcome.errors == [] and outcome.critic == []
    assert gateway.ids == ["C13.repair", "C12.critic"]


async def test_more_repairs_than_allowed_in_one_answer_are_dropped_not_applied() -> None:
    spec = broken_spec()
    many = SpecProposals(
        proposals=[
            Proposal(kind="add_acceptance_check", rationale="x", items=[f"Check number {i}"])
            for i in range(25)
        ]
    )
    outcome = await run_repair(spec, ScriptedGateway().script("C13.repair", many), max_rounds=1)
    assert len(outcome.applied) + len(outcome.dropped) == 25
    assert any(d.code == "limit" and "too many" in d.reason for d in outcome.dropped)


def test_errors_are_ordered_by_validator_then_critic() -> None:
    vf = [
        ValidatorFinding(validator="no_secrets", message="c"),
        ValidatorFinding(validator="schema", message="a"),
        ValidatorFinding(validator="limits", severity="warn", message="warn"),
        ValidatorFinding(validator="flow_endpoints", message="b"),
    ]
    cf = [
        CriticFinding(kind="other", severity="error", message="d"),
        CriticFinding(kind="other", severity="warn", message="e"),
    ]
    got = error_findings(vf, cf)
    assert [f.message for f in got] == ["a", "b", "c", "d"]


# --------------------------------------------------------------------------- the one question


@pytest.mark.parametrize(
    ("validator", "card_id", "slot"),
    [
        ("limits", "Q-build-limits", "scope.features"),
        ("stack_profile", "Q-build-stack_profile", "scope.features"),
        ("no_secrets", "Q-build-no_secrets", "brand.name"),
        ("navigation_targets", "Q-build-default", "scope.features"),
        ("schema", "Q-build-default", "scope.features"),
    ],
)
def test_the_question_comes_from_the_failing_check(validator: str, card_id: str, slot: str) -> None:
    finding = ValidatorFinding(validator=validator, message="Something failed")
    ids, clock = SequentialIds(), FakeClock()
    card, question_slot = derive_question([finding], REQ, load_rules(), clock=clock, new_id=ids)
    assert question_slot == slot == card.card.slot and card.card.id == card_id
    assert card.card.type == "choice3" and card.card.kind == "resolution"
    assert [o.id for o in card.card.options] == ["A", "B", "C"]
    assert [o.recommended for o in card.card.options] == [True, False, False]
    assert card.card.skip.assumes == "A" and card.card.other.enabled
    assert card.instance_id == "crd_TEST0000000000000000000001" and card.shown_at == clock.now()
    assert card.language == "en" and card.generated is False
    pack = load_pack("commerce")
    if slot == "scope.features":
        assert card.card.options[0].sets["scope.features"] == default_features(pack)
        assert card.card.options[1].sets["scope.features"] == core_features(pack)
        assert card.card.options[2].sets == {}


def test_a_critic_error_or_no_error_gets_the_general_question() -> None:
    critic = CriticFinding(kind="missing_flow", severity="error", message="No checkout")
    card, slot = derive_question(
        [critic], REQ, load_rules(), clock=FakeClock(), new_id=SequentialIds()
    )
    assert card.card.id == "Q-build-default" and slot == "scope.features"
    card2, _ = derive_question([], REQ, load_rules(), clock=FakeClock(), new_id=SequentialIds())
    assert card2.card.id == "Q-build-default"
    hindi = REQ.model_copy(update={"brief": make_brief(language="hi")})
    card3, _ = derive_question(
        [critic], hindi, load_rules(), clock=FakeClock(), new_id=SequentialIds()
    )
    assert card3.language == "hi"
