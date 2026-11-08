"""Scripted personas for ``mde sim``: a user with a way of talking, played against the HTTP API.

The file format and the step vocabulary are those of ``evals/datasets/personas`` (documented in
``evals/harness/persona.py``); this is a small player of its own, so the CLI never imports ``evals``.

    id: P04-confused-hinglish
    title: Confused, writes Hinglish
    language: hi-Latn            # en | hi | hi-Latn
    mode: guided                 # guided | enterprise
    policy: enterprise-it-services-firm      # optional
    script:
      - user: "pata nahi yaar"                # a typed message
      - upload: {filename: prd.md, text: "..."}
      - tap: {recommended: true}              # or option: A (id or label) / you_decide / skip
      - finish: true                          # the suggested answer on every card, until the Brief
      - approve: brief                        # every required role approves the gate shown
      - plan: true                            # wait for the plan job (research, build) to finish
      - ui: {action: finish_with_defaults}
    expect:                                   # checked over what the API shows
      state: HANDED_OFF
      spec_ready: 1
      max_cards: 10
      slots: {idea.archetype: commerce}
      notices: [secret_redacted]
      text_contains: ["..."]
      cards_shown_max: 12
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from mde_cli.client import ApiClient, ApiError
from mde_cli.conversation import (
    DEFAULT_RECOMMENDED_TEXT,
    DEFAULT_WAIT_S,
    Conversation,
    ConversationError,
    EventHook,
    resolve_options,
)
from mde_cli.errors import EXIT_BAD_INPUT, CliError, api_error_text
from mde_contracts.common import GateKind
from mde_contracts.events import AssistantMessage, CardEvent, Notice, OutboundEvent, SpecReady
from mde_core.paths import main_repo_root

PERSONAS_SUBDIR = Path("evals") / "datasets" / "personas"


class PersonaError(Exception):
    """A persona file is malformed (a typo must be loud, never silently skipped)."""


@dataclass(frozen=True)
class Persona:
    id: str
    title: str
    who: str
    language: str
    mode: str
    policy: str | None
    script: list[dict[str, Any]]
    expect: dict[str, Any]
    path: Path | None = None


def find_personas_dir(explicit: Path | None = None) -> Path:
    """``--personas DIR``, else ``evals/datasets/personas`` found from the working directory upwards,
    then from this file's checkout, then from the main repository (a worktree has its own copy)."""
    if explicit is not None:
        return explicit
    starts = [Path.cwd(), Path(__file__).resolve().parent]
    for start in starts:
        for directory in (start, *start.parents):
            candidate = directory / PERSONAS_SUBDIR
            if candidate.is_dir():
                return candidate
    root = main_repo_root()
    if root is not None and (root / PERSONAS_SUBDIR).is_dir():
        return root / PERSONAS_SUBDIR
    raise CliError(
        "I could not find evals/datasets/personas.",
        exit_code=EXIT_BAD_INPUT,
        hint="Run from inside the repository or pass --personas DIR.",
    )


def _persona_from(raw: object, path: Path) -> Persona:
    if not isinstance(raw, dict):
        raise PersonaError(f"{path.name}: the file must hold a mapping")
    data = cast("dict[str, Any]", raw)
    for key in ("id", "script"):
        if key not in data:
            raise PersonaError(f"{path.name}: missing '{key}'")
    script = data["script"]
    if not isinstance(script, list) or not all(
        isinstance(s, dict) for s in cast("list[Any]", script)
    ):
        raise PersonaError(f"{path.name}: 'script' must be a list of steps")
    return Persona(
        id=str(data["id"]),
        title=str(data.get("title", data["id"])),
        who=str(data.get("who", "")),
        language=str(data.get("language", "en")),
        mode=str(data.get("mode", "guided")),
        policy=cast("str | None", data.get("policy")),
        script=cast("list[dict[str, Any]]", script),
        expect=cast("dict[str, Any]", data.get("expect") or {}),
        path=path,
    )


def load_personas(root: Path) -> list[Persona]:
    if not root.is_dir():
        raise CliError(f"{root} is not a directory.", exit_code=EXIT_BAD_INPUT)
    out: list[Persona] = []
    for path in sorted(root.glob("P*.yaml")):
        try:
            out.append(_persona_from(yaml.safe_load(path.read_text(encoding="utf-8")), path))
        except (OSError, UnicodeDecodeError, yaml.YAMLError, PersonaError) as exc:
            raise CliError(
                f"cannot read the persona file {path.name}: {type(exc).__name__}",
                exit_code=EXIT_BAD_INPUT,
            ) from None
    return out


def select_personas(personas: Sequence[Persona], name: str) -> list[Persona]:
    """``all``, an exact id, or a prefix / fragment of an id (``P04``, ``hinglish``)."""
    if name == "all":
        return list(personas)
    wanted = name.lower()
    exact = [p for p in personas if p.id.lower() == wanted]
    if exact:
        return exact
    found = [p for p in personas if p.id.lower().startswith(wanted)]
    return found or [p for p in personas if wanted in p.id.lower()]


# --------------------------------------------------------------------------- playing one


@dataclass
class PersonaRun:
    persona: Persona
    session_id: str = ""
    state: str = "UNKNOWN"
    events: list[OutboundEvent] = field(default_factory=lambda: [])
    slots: dict[str, str] = field(default_factory=lambda: {})
    problems: list[str] = field(default_factory=lambda: [])
    seconds: float = 0.0

    @property
    def cards_asked(self) -> int:
        return len({e.card.instance_id for e in self.events if isinstance(e, CardEvent)})

    @property
    def spec_ready(self) -> int:
        return sum(1 for e in self.events if isinstance(e, SpecReady))

    @property
    def notice_codes(self) -> set[str]:
        return {e.code for e in self.events if isinstance(e, Notice)}

    @property
    def ok(self) -> bool:
        return not self.problems


async def approve_gate(conv: Conversation, gate: GateKind) -> None:
    """Every required role approves the gate that is open, as a person of its own (the requester is
    the session owner; every other role is ``u-<role>`` holding just that role)."""
    shown = conv.gate_events(gate)
    if not shown:
        raise ConversationError(f"no {gate} page is open")
    for role in shown[-1].required_roles or ["requester"]:
        who = None if role == "requester" else conv.owner.acting_as(f"u-{role}", (role,))
        await conv.decide("approve", gate=gate, role=role, identity=who)


async def _step(conv: Conversation, step: dict[str, Any], wait_s: float) -> None:
    if "user" in step:
        await conv.say(str(step["user"]))
    elif "upload" in step:
        up = cast("dict[str, Any]", step["upload"])
        await conv.upload(
            str(up.get("filename", "notes.md")),
            cast("str | None", up.get("text")),
            str(up.get("media_type", "text/markdown")),
        )
    elif "tap" in step:
        spec = cast("dict[str, Any]", step["tap"] or {})
        if spec.get("recommended"):
            await conv.tap_recommended(str(spec.get("other_text", DEFAULT_RECOMMENDED_TEXT)))
        else:
            snap = await conv.refresh()
            if snap.pending_card is None:
                raise ConversationError("There is no question waiting for an answer right now.")
            option = spec.get("option")
            picks = (
                [str(option)] if isinstance(option, str | int) else [str(o) for o in option or []]
            )
            await conv.tap(
                resolve_options(snap.pending_card.card, picks),
                other_text=cast("str | None", spec.get("other_text")),
                you_decide=bool(spec.get("you_decide", False)),
                skipped=bool(spec.get("skip", False)),
            )
    elif "finish" in step:
        await conv.answer_with_recommended()
    elif "approve" in step:
        await approve_gate(conv, cast("GateKind", step["approve"]))
    elif "plan" in step:
        await conv.wait_planning(wait_s)
    elif "ui" in step:
        ui = cast("dict[str, Any]", step["ui"])
        await conv.ui(str(ui["action"]), ui.get("target"), ui.get("value"))
    else:
        raise PersonaError(f"unknown persona step: {sorted(step)}")


async def play_persona(
    client: ApiClient,
    persona: Persona,
    *,
    wait_s: float = DEFAULT_WAIT_S,
    on_event: EventHook | None = None,
) -> PersonaRun:
    """Play the whole script in a fresh session and check ``expect`` against what the API showed."""
    started = time.monotonic()
    conv = Conversation(client, on_event=on_event)
    run = PersonaRun(persona)
    await conv.start(persona.mode, persona.policy)
    run.session_id = conv.session_id
    for number, step in enumerate(persona.script, start=1):
        try:
            await _step(conv, step, wait_s)
        except (ConversationError, PersonaError) as exc:
            run.problems.append(f"step {number} ({next(iter(step), '?')}): {exc}")
            break
        except ApiError as exc:
            run.problems.append(
                f"step {number} ({next(iter(step), '?')}): the API refused it: {api_error_text(exc)}"
            )
            break
    await conv.drain()
    snap = await conv.refresh()
    run.events = list(conv.log)
    run.state = snap.state
    run.slots = {key: value.value for key, value in snap.slots.items()}
    run.problems.extend(check_expectations(run))
    run.seconds = time.monotonic() - started
    return run


def _slot_text(want: object) -> str:
    if isinstance(want, bool):
        return "true" if want else "false"
    if isinstance(want, list):
        return ", ".join(str(item) for item in cast("list[object]", want))
    return str(want)


def check_expectations(run: PersonaRun) -> list[str]:
    """What the persona's ``expect`` block says, checked over the events and the final snapshot."""
    expect = run.persona.expect
    problems: list[str] = []
    if "state" in expect and run.state != expect["state"]:
        problems.append(f"state {run.state!r}, expected {expect['state']!r}")
    if "spec_ready" in expect and run.spec_ready != expect["spec_ready"]:
        problems.append(f"spec_ready x{run.spec_ready}, expected x{expect['spec_ready']}")
    if "max_cards" in expect and run.cards_asked > int(expect["max_cards"]):
        problems.append(f"{run.cards_asked} cards asked, the cap is {expect['max_cards']}")
    if "cards_shown_max" in expect and run.cards_asked > int(expect["cards_shown_max"]):
        problems.append(f"{run.cards_asked} cards shown, the limit is {expect['cards_shown_max']}")
    for slot_id, want in cast("dict[str, Any]", expect.get("slots", {})).items():
        got = run.slots.get(slot_id)
        if got is None or got.lower() != _slot_text(want).lower():
            problems.append(f"slot {slot_id}: {got!r}, expected {_slot_text(want)!r}")
    for code in expect.get("notices", []):
        if code not in run.notice_codes:
            problems.append(f"notice {code!r} was never shown (saw {sorted(run.notice_codes)})")
    blob = "\n".join(e.text for e in run.events if isinstance(e, AssistantMessage | Notice)).lower()
    for needle in expect.get("text_contains", []):
        if str(needle).lower() not in blob:
            problems.append(f"no message contains {needle!r}")
    return problems
