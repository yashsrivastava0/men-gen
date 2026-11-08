"""``mde chat``: the interview, the two gates and the hand-off in a terminal.

What you type, in a moment of each kind:

* **a question (card)**: ``1``, ``2``, ... taps that option (a multi-choice card takes ``1,3``);
  ``r`` the suggested answer; ``d`` "you decide"; ``s`` skip; anything else is a message (a text
  card takes it as its answer);
* **a gate (the Brief, then the Plan)**: ``1`` Looks right / Send for approval, ``2`` Change something
  (``2 make it blue`` in one go); typing a change in your own words works too;
* **any time**: ``stop``, ``pause``, ``resume``, ``restart``, ``finish`` (finish with smart
  defaults), ``quit``, ``help`` as plain words, and ``/status``, ``/wait``, ``/brief``, ``/plan``,
  ``/spec``, ``/upload FILE``, ``/undo``, ``/archive``.

The class below is the conversation logic (async, easy to test); :func:`run_loop` is the keyboard
loop on top of it (interactive, or ``--script``: one input per line, deterministic).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from mde_cli.client import ApiClient, ApiError, ApiUnreachable
from mde_cli.console import Out
from mde_cli.conversation import (
    DEFAULT_WAIT_S,
    Conversation,
    ConversationError,
    PlanTimeout,
    recommended_ids,
)
from mde_cli.errors import api_error_text
from mde_cli.render import (
    Line,
    card_kind,
    render_brief,
    render_card,
    render_event,
    render_gate_snapshot,
    render_plan,
)
from mde_cli.runtime import LoopRunner
from mde_cli.strings import normalise_language, say
from mde_contracts.cards import CardInstance
from mde_contracts.events import OutboundEvent, SpecReady
from mde_core.redact import redact_text
from mde_flow.models import SessionSnapshot

PROMPT: Final = "> "
MAX_UPLOAD_BYTES: Final = 400_000
UI_COMMANDS: Final[dict[str, str]] = {
    "stop": "stop",
    "pause": "pause",
    "resume": "resume",
    "restart": "restart",
    "finish": "finish_with_defaults",
    "undo": "undo",
    "archive": "archive",
}
BARE_COMMANDS: Final = frozenset({*UI_COMMANDS, "quit", "exit", "help", "?"}) - {"undo", "archive"}
SLASH_COMMANDS: Final = frozenset(
    {*UI_COMMANDS, "quit", "exit", "help", "status", "wait", "brief", "plan", "spec", "upload"}
)
HELP: Final = (
    "Answer a question with its number (1, 2, ...), or 1,3 on a pick-several question.",
    "  r = the suggested answer, d = you decide, s = skip, r* = the suggested answer to every question.",
    "  Anything else is a message.",
    "At a page (the Brief, the Plan): 1 = looks right, 2 = change something (2 <what to change>).",
    "Commands: stop, pause, resume, restart, finish (finish with smart defaults), quit, help.",
    "Also: /status  /wait  /brief  /plan  /spec  /upload FILE  /undo  /archive",
)

# --------------------------------------------------------------------------- parsing one input


@dataclass(frozen=True)
class Tap:
    """A tap on the pending card."""

    option_ids: tuple[str, ...] = ()
    other_text: str | None = None
    you_decide: bool = False
    skipped: bool = False


@dataclass(frozen=True)
class Say:
    """A typed message."""

    text: str


@dataclass(frozen=True)
class Hint:
    """Input the CLI cannot use as it stands: say why, send nothing."""

    text: str


@dataclass(frozen=True)
class Command:
    name: str
    args: str = ""


def parse_command(text: str) -> Command | None:
    """``stop``, ``quit``, ... as plain words; every command with a leading ``/``."""
    stripped = text.strip()
    if stripped.startswith("/"):
        name, _, args = stripped[1:].partition(" ")
        return Command(name.lower(), args.strip())
    low = stripped.lower()
    return Command(low) if low in BARE_COMMANDS else None


_NUMBERS = re.compile(r"^\d+(?:[\s,]+\d+)*$")


def parse_card_input(
    instance: CardInstance, text: str, language: str | None = None
) -> Tap | Say | Hint:
    """What typing ``text`` means for this pending card."""
    card = instance.card
    kind = card_kind(instance)
    low = text.strip().lower()
    if low == "d":
        return Tap(you_decide=True)
    if low == "s":
        return Tap(skipped=True)
    if low == "r":
        ids = recommended_ids(card)
        if kind == "text":
            return Hint(say("card_hint_text", language))
        return Tap(tuple(ids)) if ids else Tap(you_decide=True)
    if kind != "text" and _NUMBERS.match(low):
        picks = [int(p) for p in re.split(r"[\s,]+", low)]
        count = len(card.options)
        if any(not 1 <= p <= count for p in picks):
            return Hint(say("bad_number", language, count=count))
        if kind == "choice" and len(picks) != 1:
            return Hint(say("pick_one", language))
        ids = tuple(dict.fromkeys(card.options[p - 1].id for p in picks))
        return Tap(ids)
    if kind == "text":
        return Tap(other_text=text.strip())
    return Say(text.strip())


@dataclass(frozen=True)
class GateInput:
    """``approve`` (1), ``change`` (2, with the text when it came on the same line) or a message."""

    action: str  # "approve" | "change" | "say"
    text: str | None = None


def parse_gate_input(text: str) -> GateInput:
    stripped = text.strip()
    if stripped == "1":
        return GateInput("approve")
    match = re.match(r"^2(?:\s+(.*))?$", stripped, flags=re.S)
    if match:
        return GateInput("change", (match.group(1) or "").strip() or None)
    return GateInput("say", stripped)


# --------------------------------------------------------------------------- the session


@dataclass
class ChatOptions:
    mode: str = "guided"
    policy: str | None = None
    lang: str = "auto"
    wait_s: float = DEFAULT_WAIT_S
    script: bool = False
    session: str | None = None


@dataclass
class ChatSession:
    client: ApiClient
    out: Out
    options: ChatOptions = field(default_factory=ChatOptions)
    conv: Conversation = field(init=False)
    problems: int = 0  # API errors and unusable inputs (a script run exits 1 when there were any)
    fatal: bool = False
    awaiting_change: bool = False
    handoff_hint_shown: bool = False
    banner_extra: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        self.conv = Conversation(self.client, on_event=self._show)

    # ------------------------------------------------------------------ output

    @property
    def language(self) -> str | None:
        """The language of the CLI's own hints: ``--lang``, else the session's, else English."""
        if self.options.lang != "auto":
            return normalise_language(self.options.lang)
        snap = self.conv.snap
        return normalise_language(snap.language) if snap is not None else None

    def _lines(self, lines: Iterable[Line]) -> None:
        for line in lines:
            self.out.line(line.text, line.style)

    def _show(self, event: OutboundEvent) -> None:
        explicit = None if self.options.lang == "auto" else self.language
        self._lines(render_event(event, language=explicit))

    def echo(self, text: str) -> None:
        """Script mode only: show the input line (a pasted secret is masked, never repeated)."""
        self.out.line(f"{PROMPT}{redact_text(text)[0]}", "cyan")

    def problem(self, text: str) -> None:
        self.problems += 1
        self.out.line(f"Problem: {text}", "red")

    # ------------------------------------------------------------------ opening

    async def open(self) -> None:
        if self.options.session:
            snap = await self.conv.resume(self.options.session)
            self.out.line(f"Resumed session {snap.session_id} ({snap.mode}, {snap.state}).", "bold")
            self._show_pending(snap)
        else:
            hook, self.conv.on_event = self.conv.on_event, None  # header first, then the greeting
            try:
                greeting = await self.conv.start(self.options.mode, self.options.policy)
            finally:
                self.conv.on_event = hook
            self.out.line(f"Session {self.conv.session_id} ({self.options.mode}).", "dim")
            for event in greeting:
                self._show(event)

    def _show_pending(self, snap: SessionSnapshot) -> None:
        if snap.gate is not None:
            self._lines(render_gate_snapshot(snap.gate, language=self.language))
        elif snap.pending_card is not None:
            self._lines(render_card(snap.pending_card, language=self.language))
        if snap.state == "PLANNING":
            self.out.line("The plan is being prepared. Type /wait to watch it.", "dim")
        elif snap.state == "HANDED_OFF":
            self.out.line(say("handed_off_hint", self.language), "dim")

    # ------------------------------------------------------------------ one input

    async def handle(self, line: str) -> bool:
        """Process one line of input; ``False`` means leave."""
        text = line.strip()
        if not text:
            return True
        try:
            command = parse_command(text)
            if command is not None:
                self.awaiting_change = False
                return await self._command(command)
            if self.awaiting_change:
                self.awaiting_change = False
                await self._send_change(text)
            else:
                await self._input(text)
            await self._after_send()
        except ApiUnreachable as exc:
            self.problem(str(exc))
            if exc.hint:
                self.out.line(f"Hint: {exc.hint}", "dim")
            self.fatal = self.options.script
            return not self.fatal
        except ApiError as exc:
            self.problem(api_error_text(exc))
        except ConversationError as exc:
            self.problem(str(exc))
        return True

    async def _input(self, text: str) -> None:
        snap = self.conv.snap or await self.conv.refresh()
        if text == "r*" and snap.state == "DISCOVERING" and snap.pending_card is not None:
            await self.conv.answer_with_recommended()  # the suggested answer to every question
            return
        if snap.state == "DISCOVERING" and snap.pending_card is not None:
            parsed = parse_card_input(snap.pending_card, text, self.language)
            if isinstance(parsed, Hint):
                self.problems += 1
                self.out.line(parsed.text, "yellow")
            elif isinstance(parsed, Tap):
                await self.conv.tap(
                    parsed.option_ids,
                    other_text=parsed.other_text,
                    you_decide=parsed.you_decide,
                    skipped=parsed.skipped,
                )
            else:
                await self.conv.say(parsed.text)
            return
        if snap.gate is not None and snap.state in ("BRIEF_REVIEW", "PLAN_REVIEW"):
            parsed_gate = parse_gate_input(text)
            if parsed_gate.action == "approve":
                await self.conv.decide("approve")
            elif parsed_gate.action == "change":
                if parsed_gate.text:
                    await self.conv.decide("change", comment=parsed_gate.text)
                else:
                    self.awaiting_change = True
                    self.out.line(say("change_prompt", self.language), "bold")
            else:
                await self.conv.say(parsed_gate.text or text)
            return
        await self.conv.say(text)

    async def _send_change(self, text: str) -> None:
        snap = self.conv.snap or await self.conv.refresh()
        if snap.gate is not None:
            await self.conv.decide("change", comment=text)
        else:
            await self.conv.say(text)

    async def _after_send(self) -> None:
        """Follow the plan job when one is running; hint once the spec is handed off."""
        snap = self.conv.snap
        if snap is not None and snap.state == "PLANNING":
            await self._follow_plan()
        snap = self.conv.snap
        if (
            snap is not None
            and snap.state == "HANDED_OFF"
            and not self.handoff_hint_shown
            and any(isinstance(e, SpecReady) for e in self.conv.log)
        ):
            self.handoff_hint_shown = True
            self.out.line(say("handed_off_hint", self.language), "dim")

    async def _follow_plan(self) -> None:
        self.out.line(say("waiting", self.language), "dim")
        try:
            await self.conv.wait_planning(self.options.wait_s)
        except PlanTimeout as exc:
            self.out.line(str(exc), "yellow")

    # ------------------------------------------------------------------ commands

    async def _command(self, command: Command) -> bool:
        name = command.name
        if name in ("quit", "exit"):
            return False
        if name in ("help", "?"):
            self.out.lines(HELP)
        elif name in UI_COMMANDS:
            await self.conv.ui(UI_COMMANDS[name])
            await self._after_send()
        elif name == "status":
            self._status(await self.conv.refresh())
        elif name == "wait":
            await self._wait()
        elif name == "brief":
            self._lines(render_brief(await self.client.brief(self.conv.session_id)))
        elif name == "plan":
            self._lines(render_plan(await self.client.plan(self.conv.session_id)))
        elif name == "spec":
            await self._spec(command.args)
        elif name == "upload":
            await self._upload(command.args)
        else:
            self.out.line(f"I do not know the command /{name}. Type help for the list.", "yellow")
        return True

    def _status(self, snap: SessionSnapshot) -> None:
        phase = f", {snap.plan_phase}" if snap.plan_phase else ""
        self.out.line(
            f"Session {snap.session_id}: {snap.state}{phase} ({snap.mode}, {snap.policy_id})"
        )
        if snap.pending_card is not None:
            self.out.line(f"  waiting for an answer to: {snap.pending_card.card.text}")
        if snap.gate is not None:
            self.out.line(
                f"  waiting for a decision on the {snap.gate.gate} (version {snap.gate.version})"
            )
        if snap.active_job is not None:
            self.out.line(f"  job {snap.active_job.id}: {snap.active_job.state}")

    async def _wait(self) -> None:
        snap = await self.conv.refresh()
        if snap.state == "PLANNING":
            await self._follow_plan()
        else:
            await self.conv.watch_for(min(self.options.wait_s, 30.0))
        self._status(await self.conv.refresh())

    async def _spec(self, args: str) -> None:
        text, headers = await self.client.spec(self.conv.session_id)
        self.out.line(
            f"Spec version {headers.get('x-spec-version', '?')}, status "
            f"{headers.get('x-spec-status', '?')}, hash {headers.get('x-spec-hash', '?')}, "
            f"{len(text.splitlines())} lines."
        )
        if args.strip().lower() == "full":
            self.out.line(text)

    async def _upload(self, args: str) -> None:
        if not args:
            self.out.line("Usage: /upload FILE (a text or Markdown file)", "yellow")
            return
        path = Path(args.strip().strip('"'))
        try:
            raw = await asyncio.to_thread(path.read_bytes)
            body = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            self.problem(f"I could not read {path.name} as a text file.")
            return
        if len(raw) > MAX_UPLOAD_BYTES:
            self.problem(f"{path.name} is too large to send ({len(raw)} bytes).")
            return
        media = "text/markdown" if path.suffix.lower() in (".md", ".markdown") else "text/plain"
        await self.conv.upload(path.name, body, media)
        await self._after_send()

    # ------------------------------------------------------------------ closing

    def goodbye(self) -> None:
        snap = self.conv.snap
        if not self.conv.session_id:
            return
        state = snap.state if snap is not None else "UNKNOWN"
        self.out.line("")
        self.out.line(f"Session {self.conv.session_id} is in state {state}.", "dim")
        self.out.line(f"Come back to it with: mde chat --session {self.conv.session_id}", "dim")


# --------------------------------------------------------------------------- the keyboard loop


def run_loop(
    session: ChatSession,
    runner: LoopRunner,
    script: Iterable[str] | None = None,
    *,
    read: Callable[[str], str] = input,
) -> int:
    """Open the session and process input until the user leaves. Returns the exit code: 0, or 1
    for a script run in which something went wrong."""
    runner.run(session.open())
    if session.options.session is None:
        session.out.line("(Type help for commands, quit to leave.)", "dim")
    if script is not None:
        return _run_script(session, runner, script)
    while True:
        try:
            line = read(PROMPT)
        except EOFError:
            break
        except KeyboardInterrupt:
            session.out.line("")
            break
        if _step(session, runner, line) is False:
            break
    session.goodbye()
    return 0


def _step(session: ChatSession, runner: LoopRunner, line: str) -> bool:
    finished, keep = runner.run_interruptible(session.handle(line))
    if not finished:
        session.out.line(
            "Stopped watching; the work carries on. Type /wait to watch again, or stop to cancel it.",
            "yellow",
        )
        return True
    return bool(keep)


def _run_script(session: ChatSession, runner: LoopRunner, script: Iterable[str]) -> int:
    for raw in script:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        session.echo(line)
        if _step(session, runner, line) is False:
            break
    snap = runner.run(session.conv.refresh())
    ready = sum(1 for e in session.conv.log if isinstance(e, SpecReady))
    session.out.line("")
    session.out.line(
        f"-- script finished: session {snap.session_id} is {snap.state}"
        f" (spec_ready: {ready}, problems: {session.problems})",
        "dim",
    )
    return 1 if session.problems else 0
