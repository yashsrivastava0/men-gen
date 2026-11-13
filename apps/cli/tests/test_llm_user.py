"""``mde sim --llm``: refused unless live calls are switched on AND budgeted; the loop itself is
exercised offline, through a gateway in mock mode (nothing here can spend a cent)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from mde_cli.client import ApiClient
from mde_cli.conversation import Conversation
from mde_cli.errors import EXIT_REFUSED
from mde_cli.llm_user import (
    SIM_USER_SPEC,
    LiveRefused,
    UserTurnIn,
    UserTurnOut,
    check_live_allowed,
    describe_situation,
    mock_turn,
    play_llm_persona,
)
from mde_cli.main import app
from mde_cli.persona import Persona
from mde_core.errors import BudgetExceeded
from mde_core.settings import Settings
from mde_llm_gateway import StaticRegistry, create_gateway
from mde_llm_gateway.gateway import Gateway

LIVE_ENV = {"LLM_MODE": "live"}


def settings(**fields: Any) -> Settings:
    base: dict[str, Any] = {"llm_mode": "live", "forbid_live": False}
    return Settings(_env_file=None, **{**base, **fields})  # type: ignore[call-arg]


class FakeLedger:
    def __init__(self, spent: int = 0, reserved: int = 0, problem: str | None = None) -> None:
        self._snap = SimpleNamespace(spent_micros=spent, reserved_micros=reserved)
        self._problem = problem

    def integrity_problem(self) -> str | None:
        return self._problem

    def peek(self) -> Any:
        return self._snap


def persona(**fields: Any) -> Persona:
    base: dict[str, Any] = {
        "id": "P00-llm",
        "title": "A hurried shop owner",
        "who": "Sells candles, short on time.",
        "language": "en",
        "mode": "guided",
        "policy": None,
        "script": [],
        "expect": {},
    }
    return Persona(**{**base, **fields})


# --------------------------------------------------------------------------- the money gate


def test_the_command_refuses_without_live_mode_and_plays_nothing(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["sim", "--llm", "--local", "--memory", "--persona", "P01"])
    assert result.exit_code == EXIT_REFUSED
    assert "needs LLM_MODE=live in the environment" in result.stderr
    assert "P01-hi-only" not in result.stdout  # no persona ran, no session was created


def test_live_mode_is_still_refused_while_live_is_forbidden(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in LIVE_ENV.items():
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).forbid_live  # type: ignore[call-arg]  # the test fixture sets MDE_FORBID_LIVE=1
    result = runner.invoke(app, ["sim", "--llm", "--local", "--memory", "--persona", "P01"])
    assert result.exit_code == EXIT_REFUSED
    assert "MDE_FORBID_LIVE=1" in result.stderr and "P01-hi-only" not in result.stdout


def test_a_run_is_allowed_when_live_mode_and_the_budget_agree() -> None:
    check_live_allowed(settings(), env=LIVE_ENV, ledger=FakeLedger())  # type: ignore[arg-type]
    check_live_allowed(  # 50% of the cap is the warn line: still open
        settings(llm_budget_usd="6.00"),
        env=LIVE_ENV,
        ledger=FakeLedger(spent=3_100_000),  # type: ignore[arg-type]
    )


def test_a_run_is_refused_at_the_must_run_tripwire_or_on_a_damaged_ledger() -> None:
    with pytest.raises(LiveRefused, match="must_run_only_80") as raised:
        check_live_allowed(settings(), env=LIVE_ENV, ledger=FakeLedger(spent=4_900_000))  # type: ignore[arg-type]
    assert raised.value.exit_code == EXIT_REFUSED
    with pytest.raises(LiveRefused, match="refuse_95"):
        check_live_allowed(settings(), env=LIVE_ENV, ledger=FakeLedger(spent=1, reserved=5_800_000))  # type: ignore[arg-type]
    with pytest.raises(LiveRefused, match="cannot be trusted: truncated"):
        check_live_allowed(settings(), env=LIVE_ENV, ledger=FakeLedger(problem="truncated"))  # type: ignore[arg-type]


def test_the_environment_not_the_settings_object_decides_live_mode() -> None:
    with pytest.raises(LiveRefused, match="LLM_MODE=live"):
        check_live_allowed(settings(), env={}, ledger=FakeLedger())  # type: ignore[arg-type]
    with pytest.raises(LiveRefused, match="MDE_FORBID_LIVE"):
        check_live_allowed(settings(forbid_live=True), env=LIVE_ENV, ledger=FakeLedger())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the call spec


def test_the_call_spec_is_versioned_and_has_a_mock_handler_and_a_fallback_name() -> None:
    spec = SIM_USER_SPEC
    assert (spec.id, spec.version) == ("X1.sim_user", 1)
    assert spec.mock_handler == "mde_cli.llm_user:mock_turn" and spec.fallback == "sim_user_quits"
    assert not spec.tools  # no tools in a call that reads conversation text
    for name in (spec.system_template, spec.user_template):
        assert (spec.template_root / name).is_file()
    base = {
        "persona_title": "t",
        "persona_who": "w",
        "language": "en",
        "turn": 1,
        "max_turns": 5,
        "history": [],
    }
    assert mock_turn(UserTurnIn(**base, kind="page", situation="s")).action == "approve"
    assert mock_turn(UserTurnIn(**base, kind="question", situation="s")).option == "recommended"
    assert mock_turn(UserTurnIn(**base, kind="greeting", situation="s")).action == "say"


# --------------------------------------------------------------------------- the loop, offline


def mock_gateway() -> Gateway:
    mock_settings = Settings(_env_file=None, llm_mode="mock")  # type: ignore[call-arg]
    return create_gateway(mock_settings, StaticRegistry([SIM_USER_SPEC]))


async def test_a_model_playing_the_persona_gets_through_both_gates(api: ApiClient) -> None:
    run = await play_llm_persona(
        Conversation(api), persona(), mock_gateway(), max_turns=40, wait_s=30
    )
    assert run.problems == [] and run.state == "HANDED_OFF" and run.spec_ready == 1
    assert run.cards_asked >= 3 and run.slots["idea.archetype"] == "commerce"


class Scripted:
    """A gateway stand-in that answers with a fixed list of actions (then quits)."""

    def __init__(self, *turns: UserTurnOut | Exception) -> None:
        self.turns = list(turns)
        self.inputs: list[UserTurnIn] = []

    async def run(self, spec: Any, input: UserTurnIn, ctx: Any) -> Any:
        self.inputs.append(input)
        item = self.turns.pop(0) if self.turns else UserTurnOut(action="quit", text="", option="")
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(output=item)


def turn(action: str, text: str = "", option: str = "") -> UserTurnOut:
    return UserTurnOut(action=action, text=text, option=option)  # type: ignore[arg-type]


async def test_every_action_the_model_can_choose_is_carried_out(api: ApiClient) -> None:
    gateway = Scripted(
        turn("say", "I want an online shop for candles"),
        turn("pick", option="you_decide"),
        turn("pick", option="skip"),
        turn("pick", option="2"),
        turn("pick", option="recommended"),
        turn("pick", option="not a number"),  # becomes a message
        turn("say"),  # an empty text becomes "ok"
        turn("finish_with_defaults"),
        turn("change", "make it bold and dark"),
        turn("approve"),
    )
    conv = Conversation(api)
    run = await play_llm_persona(conv, persona(), cast(Gateway, gateway), max_turns=30, wait_s=30)
    assert run.problems == [], run.problems
    kinds = [i.kind for i in gateway.inputs]
    assert kinds[0] in ("greeting", "message") and "question" in kinds and "page" in kinds
    assert all(i.persona_title == "A hurried shop owner" for i in gateway.inputs)
    turns = [i.turn for i in gateway.inputs]
    assert turns == sorted(set(turns)) and turns[0] == 1
    assert any(i.history for i in gateway.inputs[1:])  # the model sees what happened so far


async def test_a_failed_model_call_makes_the_person_leave_with_a_problem(api: ApiClient) -> None:
    gateway = Scripted(turn("say", "An online shop"), BudgetExceeded("the cap was reached"))
    run = await play_llm_persona(Conversation(api), persona(), cast(Gateway, gateway), wait_s=30)
    assert len(run.problems) == 1 and "the model call failed (budget_exceeded)" in run.problems[0]
    assert run.state == "DISCOVERING"


async def test_an_action_that_cannot_be_done_is_a_problem_not_a_crash(api: ApiClient) -> None:
    class Failing(Scripted):
        async def run(self, spec: Any, input: UserTurnIn, ctx: Any) -> Any:
            return SimpleNamespace(output=turn("approve"))

    gateway = Failing()
    run = await play_llm_persona(
        Conversation(api), persona(), cast(Gateway, gateway), max_turns=3, wait_s=30
    )
    assert (
        run.problems == [] and run.state == "DISCOVERING"
    )  # approving with no page says "ok" instead


async def test_the_situation_describes_a_question_a_page_or_the_last_message(
    api: ApiClient,
) -> None:
    conv = Conversation(api)
    await conv.start("guided")
    assert describe_situation(conv)[0] == "message"
    await conv.say("An online shop for handmade candles")
    kind, text = describe_situation(conv)
    assert kind == "question" and "1) " in text and "(suggested)" in text
    assert describe_situation(Conversation(api))[0] == "greeting"
    await conv.answer_with_recommended()
    kind, text = describe_situation(conv)
    assert kind == "page" and "(brief)" in text and "1) Looks right" in text
