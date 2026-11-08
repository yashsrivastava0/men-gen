"""``mde sim --llm``: a persona played by a model instead of a script.

This is the ONLY place the CLI can spend money, so it is fenced twice:

1. :func:`check_live_allowed` refuses unless ``ALLOW_LIVE_LLM=1`` and ``LLM_MODE=live`` are in the
   environment, the gateway's own live check passes (``MDE_FORBID_LIVE`` unset) and the budget ledger
   says there is room (below the 80% tripwire, ledger intact). Nothing else runs before it.
2. Every call goes through the gateway (budget guard, ledger, retries) with a versioned call spec,
   a mock handler and a deterministic fallback (the persona leaves the conversation).

The model sees a short description of what the assistant is showing and replies with one structured
action. It never sees a key, a token or the API's internals. Nobody runs this live but the lead.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from mde_cli.chat import Hint, Say, Tap, parse_card_input
from mde_cli.conversation import (
    DEFAULT_WAIT_S,
    Conversation,
    ConversationError,
    recommended_ids,
)
from mde_cli.errors import EXIT_REFUSED, CliError
from mde_cli.persona import Persona, PersonaRun, approve_gate
from mde_contracts.common import Frozen
from mde_contracts.events import (
    AssistantMessage,
    BriefPage,
    Notice,
    OutboundEvent,
)
from mde_contracts.gateway import CallContext, CallSpec
from mde_core.errors import LiveCallForbidden, MdeError
from mde_core.ids import new_id
from mde_core.paths import anchor_to_main_repo
from mde_core.settings import Settings
from mde_llm_gateway.budget import BudgetConfig
from mde_llm_gateway.gateway import Gateway
from mde_llm_gateway.gating import require_live
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.registry import AnySpec

TEMPLATE_ROOT: Final = Path(__file__).resolve().parent / "data" / "llm_user"
HISTORY_LINES: Final = 8
DEFAULT_MAX_TURNS: Final = 14
OPEN_TRIPWIRES: Final = frozenset(
    {"ok", "warn_50"}
)  # beyond 50% the 80% line is near: must-run only

Action = Literal["say", "pick", "approve", "change", "finish_with_defaults", "quit"]


class LiveRefused(CliError):
    """``--llm`` is not allowed right now (the message says which switch or limit stops it)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_REFUSED)


def check_live_allowed(
    settings: Settings,
    *,
    env: Mapping[str, str] | None = None,
    ledger: FileLedger | None = None,
) -> None:
    """Raise :class:`LiveRefused` unless an LLM-driven run may start. Reads, never spends."""
    environ = os.environ if env is None else env
    wrong = [
        f"{name}={want}"
        for name, want in (("ALLOW_LIVE_LLM", "1"), ("LLM_MODE", "live"))
        if environ.get(name) != want
    ]
    if wrong:
        raise LiveRefused(
            "mde sim --llm spends real money, so it needs "
            + " and ".join(wrong)
            + " in the environment. Scripted personas (the default) cost nothing."
        )
    try:
        require_live(settings)
    except LiveCallForbidden as exc:
        raise LiveRefused(str(exc)) from None
    config = BudgetConfig.load(cap_usd=settings.llm_budget_usd)
    ledger = ledger or FileLedger(anchor_to_main_repo(settings.llm_budget_ledger))
    problem = ledger.integrity_problem()
    if problem:
        raise LiveRefused(f"the budget ledger cannot be trusted: {problem}")
    snapshot = ledger.peek()
    tripwire = config.tripwire(snapshot.spent_micros + snapshot.reserved_micros)
    if tripwire not in OPEN_TRIPWIRES:
        raise LiveRefused(
            f"the live budget is at its '{tripwire}' tripwire: only must-run work may spend now "
            "(see `mde budget`)."
        )


# --------------------------------------------------------------------------- the call


class UserTurnIn(Frozen):
    persona_title: str
    persona_who: str
    language: str
    turn: int
    max_turns: int
    kind: Literal["greeting", "question", "page", "message"]
    situation: str
    history: list[str]


class UserTurnOut(Frozen):
    action: Action = Field(description="What the person does next.")
    text: str = Field(description="What they type for say or change; empty otherwise.")
    option: str = Field(
        description="For pick: the option number, or recommended, you_decide or skip; else empty."
    )


def mock_turn(input: UserTurnIn) -> UserTurnOut:
    """Offline handler: agree with every suggestion, approve every page, open with a plain idea."""
    if input.kind == "page":
        return UserTurnOut(action="approve", text="", option="")
    if input.kind == "question":
        return UserTurnOut(action="pick", text="", option="recommended")
    return UserTurnOut(action="say", text="I want an online shop for my candles.", option="")


SIM_USER_SPEC: AnySpec = CallSpec(
    id="X1.sim_user",
    version=1,
    input_model=UserTurnIn,
    output_model=UserTurnOut,
    system_template="system.jinja",
    user_template="user.jinja",
    template_root=TEMPLATE_ROOT,
    route="fast",
    reasoning_effort="low",
    max_output_tokens=400,
    mock_handler="mde_cli.llm_user:mock_turn",
    fallback="sim_user_quits",
)  # type: ignore[arg-type]


def describe_situation(conv: Conversation) -> tuple[str, str]:
    """What the assistant is showing right now, in words for the model: ``(kind, text)``."""
    snap = conv.snap
    if snap is not None and snap.gate is not None:
        page = snap.gate.page
        what = page.brief.app_summary if isinstance(page, BriefPage) else page.plan.summary
        buttons = "; ".join(f"{n}) {label}" for n, label in enumerate(snap.gate.buttons, start=1))
        return "page", f"A page to check ({snap.gate.gate}): {what}\nButtons: {buttons}"
    if snap is not None and snap.pending_card is not None:
        card = snap.pending_card.card
        options = "\n".join(
            f"{n}) {o.label}{' (suggested)' if o.recommended else ''}"
            for n, o in enumerate(card.options, start=1)
        )
        extra = "\nYou may also type your own answer." if card.other.enabled else ""
        return "question", f"Question: {card.text}\n{options}{extra}"
    last = next((e for e in reversed(conv.log) if isinstance(e, AssistantMessage)), None)
    return ("message", last.text) if last is not None else ("greeting", "The assistant said hello.")


def _history_line(event: OutboundEvent) -> str | None:
    if isinstance(event, AssistantMessage | Notice):
        return f"assistant: {event.text}"
    return None


async def play_llm_persona(
    client_conv: Conversation,
    persona: Persona,
    gateway: Gateway,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    wait_s: float = DEFAULT_WAIT_S,
    scope: str | None = None,
) -> PersonaRun:
    """Let a model play ``persona`` for at most ``max_turns`` turns. Returns what happened (no
    pass or fail: an LLM-driven run explores, it does not follow a script)."""
    conv = client_conv
    started = time.monotonic()
    run = PersonaRun(persona)
    history: list[str] = []
    scope = scope or f"eval:sim-{new_id('run')}"
    await conv.start(persona.mode, persona.policy)
    run.session_id = conv.session_id
    seen = 0
    for turn in range(1, max_turns + 1):
        snap = await conv.refresh()
        history.extend(line for e in conv.log[seen:] if (line := _history_line(e)) is not None)
        seen = len(conv.log)
        if snap.state in ("HANDED_OFF", "STOPPED", "ARCHIVED"):
            break
        if snap.state == "PLANNING":
            await conv.wait_planning(wait_s)
            continue
        kind, situation = describe_situation(conv)
        ctx = CallContext(
            request_id=new_id("req"), budget_scope=scope, purpose="sim-user", session_id=None
        )
        try:
            result = await gateway.run(
                SIM_USER_SPEC,
                UserTurnIn(
                    persona_title=persona.title,
                    persona_who=persona.who,
                    language=persona.language,
                    turn=turn,
                    max_turns=max_turns,
                    kind=kind,  # type: ignore[arg-type]
                    situation=situation,
                    history=history[-HISTORY_LINES:],
                ),
                ctx,
            )
        except MdeError as exc:  # the deterministic fallback: the person leaves
            run.problems.append(f"turn {turn}: the model call failed ({exc.code}); leaving")
            break
        out = result.output
        if not isinstance(out, UserTurnOut) or out.action == "quit":
            break
        try:
            history.append(f"you: {await _act(conv, out)}")
        except ConversationError as exc:
            run.problems.append(f"turn {turn}: {exc}")
            break
    await conv.drain()
    snap = await conv.refresh()
    run.events = list(conv.log)
    run.state = snap.state
    run.slots = {key: value.value for key, value in snap.slots.items()}
    run.seconds = time.monotonic() - started
    return run


async def _act(conv: Conversation, out: UserTurnOut) -> str:
    """Do what the model chose; returns a short note for the history."""
    snap = conv.snap
    if out.action == "finish_with_defaults":
        await conv.ui("finish_with_defaults")
        return "finish with smart defaults"
    if out.action == "approve" and snap is not None and snap.gate is not None:
        await approve_gate(conv, snap.gate.gate)
        return "approve"
    if out.action == "change" and snap is not None and snap.gate is not None:
        await conv.decide("change", comment=out.text)
        return f"change: {out.text}"
    if out.action == "pick" and snap is not None and snap.pending_card is not None:
        card = snap.pending_card
        wanted = out.option.strip().lower()
        if wanted in ("you_decide", "decide"):
            await conv.tap(you_decide=True)
        elif wanted == "skip":
            await conv.tap(skipped=True)
        elif wanted == "recommended":
            await conv.tap_recommended()  # a text card gets a default answer
        else:
            parsed = parse_card_input(card, wanted)
            if isinstance(parsed, Tap):
                await conv.tap(
                    parsed.option_ids,
                    other_text=parsed.other_text,
                    you_decide=parsed.you_decide,
                    skipped=parsed.skipped,
                )
            elif isinstance(parsed, Say):
                await conv.say(parsed.text)
            else:
                assert isinstance(parsed, Hint)
                await conv.tap(recommended_ids(card.card))
        return f"pick {out.option}"
    text = out.text or "ok"
    await conv.say(text)
    return text
