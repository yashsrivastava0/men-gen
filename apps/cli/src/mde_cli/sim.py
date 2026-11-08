"""``mde sim``: play personas against the API and print one compact line each.

Scripted by default (free, deterministic): the persona YAML of ``evals/datasets/personas`` is played
step by step and its ``expect`` block is checked over what the API showed. ``--llm`` lets a model
play the persona instead; it is refused unless live calls are allowed and budgeted
(``llm_user.check_live_allowed``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mde_cli.client import ApiClient, ApiError
from mde_cli.console import Out
from mde_cli.conversation import DEFAULT_WAIT_S, Conversation, EventHook
from mde_cli.errors import api_error_text
from mde_cli.persona import Persona, PersonaRun, play_persona
from mde_cli.render import render_event


@dataclass(frozen=True)
class SimOptions:
    personas_dir: Path | None = None
    persona: str = "all"
    llm: bool = False
    max_turns: int = 14
    wait_s: float = DEFAULT_WAIT_S
    transcript: bool = False


def transcript_hook(out: Out) -> EventHook:
    """Print every event of the session as ``mde chat`` would."""

    def show(event: object) -> None:
        for line in render_event(event):  # type: ignore[arg-type]
            out.line(line.text, line.style)

    return show


async def run_scripted(
    client: ApiClient, personas: Sequence[Persona], options: SimOptions, out: Out
) -> list[PersonaRun]:
    runs: list[PersonaRun] = []
    for persona in personas:
        if options.transcript:
            out.line(f"=== {persona.id}: {persona.title}", "bold")
        hook = transcript_hook(out) if options.transcript else None
        try:
            runs.append(await play_persona(client, persona, wait_s=options.wait_s, on_event=hook))
        except ApiError as exc:
            failed = PersonaRun(persona)
            failed.problems.append(f"the API refused a request: {api_error_text(exc)}")
            runs.append(failed)
    return runs


async def run_llm(
    client: ApiClient, personas: Sequence[Persona], options: SimOptions, out: Out, gateway: object
) -> list[PersonaRun]:
    from mde_cli.llm_user import play_llm_persona
    from mde_llm_gateway.gateway import Gateway

    assert isinstance(gateway, Gateway)
    runs: list[PersonaRun] = []
    for persona in personas:
        hook = transcript_hook(out) if options.transcript else None
        conv = Conversation(client, on_event=hook)
        runs.append(
            await play_llm_persona(
                conv, persona, gateway, max_turns=options.max_turns, wait_s=options.wait_s
            )
        )
    return runs


def render_runs(runs: Sequence[PersonaRun], *, scripted: bool) -> list[str]:
    """One line per persona (plus its problems), then the total."""
    width = max((len(r.persona.id) for r in runs), default=10)
    lines: list[str] = []
    for run in runs:
        verdict = ("ok  " if run.ok else "FAIL") if scripted else ("done" if run.ok else "STOP")
        lines.append(
            f"{run.persona.id:<{width}}  {verdict}  {run.state:<13} cards {run.cards_asked:<2}  "
            f"spec_ready {run.spec_ready}  {run.seconds:4.1f}s"
        )
        lines.extend(f"    - {problem}" for problem in run.problems)
    good = sum(1 for r in runs if r.ok)
    if scripted:
        lines.append(f"{good} of {len(runs)} personas met their expectations.")
    else:
        lines.append(f"{len(runs)} persona(s) played by a model; {good} without problems.")
    return lines
