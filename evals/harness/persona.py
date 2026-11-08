"""Scripted personas: a user with a way of talking, played against the real product.

A persona file (``evals/datasets/personas/P??-*.yaml``)::

    id: P04-confused-hinglish
    title: Confused, writes Hinglish          # one line for the report
    language: hi-Latn                         # en | hi | hi-Latn
    mode: guided                              # guided | enterprise
    policy: enterprise-it-services-firm       # optional
    script:                                   # played in order
      - user: "pata nahi yaar"                # a typed message
      - upload: {filename: prd.md, text: "..."}
      - tap: {recommended: true}              # or option: A / you_decide: true / skip: true
      - finish: true                          # tap the recommended option until the interview ends
      - approve: brief                        # every required role approves the gate shown
      - plan: true                            # run the queued plan job
      - ui: {action: finish_with_defaults}
    expect:                                   # what a good outcome looks like
      state: HANDED_OFF
      spec_ready: 1
      max_cards: 10
      slots: {idea.archetype: commerce}
      notices: [secret_redacted]              # all must have been shown
      text_contains: ["..."]                  # in the assistant's messages

The same script runs offline in mock mode (the e2e suite and the mock eval runner) and, with a
live gateway, in batch L1; ``mde sim`` plays the same shape against the HTTP API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from evals.harness.driver import FlowDriver
from evals.harness.journeys import approve_gate
from evals.harness.stack import Stack
from mde_contracts.common import Mode
from mde_contracts.events import AssistantMessage, CardEvent, Notice, OutboundEvent, SpecReady

DATASETS = Path(__file__).resolve().parents[1] / "datasets"


@dataclass(frozen=True)
class Persona:
    id: str
    title: str
    who: str
    language: str
    mode: Mode
    policy: str | None
    script: list[dict[str, Any]]
    expect: dict[str, Any]
    path: Path | None = None


def load_personas(root: Path = DATASETS / "personas") -> list[Persona]:
    out: list[Persona] = []
    for path in sorted(root.glob("P*.yaml")):
        raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))
        out.append(
            Persona(
                id=str(raw["id"]),
                title=str(raw["title"]),
                who=str(raw.get("who", "")),
                language=str(raw.get("language", "en")),
                mode=cast("Mode", raw.get("mode", "guided")),
                policy=raw.get("policy"),
                script=cast("list[dict[str, Any]]", raw["script"]),
                expect=cast("dict[str, Any]", raw.get("expect") or {}),
                path=path,
            )
        )
    return out


@dataclass
class PersonaRun:
    persona: Persona
    driver: FlowDriver
    stack: Stack
    events: list[OutboundEvent]
    state: str
    problems: list[str] = field(default_factory=lambda: [])

    @property
    def spec_ready(self) -> int:
        return sum(1 for e in self.events if isinstance(e, SpecReady))


async def _step(d: FlowDriver, step: dict[str, Any]) -> None:
    if "user" in step:
        await d.say(str(step["user"]))
    elif "upload" in step:
        up = cast("dict[str, Any]", step["upload"])
        await d.upload(
            str(up.get("filename", "notes.md")),
            up.get("text"),
            media_type=str(up.get("media_type", "text/markdown")),
        )
    elif "tap" in step:
        spec = cast("dict[str, Any]", step["tap"] or {})
        if spec.get("recommended"):
            await d.tap_recommended(str(spec.get("other_text", "Blue Basket")))
        else:
            await d.tap(
                spec.get("option"),
                other_text=spec.get("other_text"),
                you_decide=bool(spec.get("you_decide", False)),
                skipped=bool(spec.get("skip", False)),
            )
    elif "finish" in step:
        await d.answer_with_recommended()
    elif "approve" in step:
        await approve_gate(d, cast("Any", step["approve"]))
    elif "plan" in step:
        await d.run_plan()
    elif "ui" in step:
        ui = cast("dict[str, Any]", step["ui"])
        await d.ui(ui["action"], ui.get("target"), ui.get("value"))
    else:  # pragma: no cover - a typo in a persona file must be loud
        raise ValueError(f"unknown persona step: {step}")


async def play_persona(
    stack: Stack, persona: Persona, *, until: Literal["end", "brief"] = "end"
) -> PersonaRun:
    """Play the script. ``until="brief"`` stops before the first approval (the Discover part only:
    what live batch L1 pays for); the state and ``spec_ready`` expectations are then not checked."""
    d = FlowDriver.for_user(stack)
    await d.start(mode=persona.mode, policy_id=persona.policy)
    for step in persona.script:
        if until == "brief" and "approve" in step:
            break
        await _step(d, step)
    events = await d.events()
    snap = await d.snapshot()
    run = PersonaRun(persona, d, stack, events, snap.state)
    run.problems = await check_expectations(run, full=until == "end")
    return run


async def check_expectations(run: PersonaRun, *, full: bool = True) -> list[str]:
    expect = run.persona.expect
    problems: list[str] = []
    if full and "state" in expect and run.state != expect["state"]:
        problems.append(f"state {run.state!r}, expected {expect['state']!r}")
    if full and "spec_ready" in expect and run.spec_ready != expect["spec_ready"]:
        problems.append(f"spec_ready x{run.spec_ready}, expected x{expect['spec_ready']}")
    record = await run.driver.session_record()
    if "max_cards" in expect:
        asked = record.discovery.card_count
        if asked > int(expect["max_cards"]):
            problems.append(f"{asked} cards asked, the cap is {expect['max_cards']}")
    for slot_id, want in cast("dict[str, Any]", expect.get("slots", {})).items():
        got = record.discovery.slots.get(slot_id)
        if got is None or got.value != want:
            problems.append(f"slot {slot_id}: {got.value if got else None!r}, expected {want!r}")
    codes = {e.code for e in run.events if isinstance(e, Notice)}
    for code in expect.get("notices", []):
        if code not in codes:
            problems.append(f"notice {code!r} was never shown (saw {sorted(codes)})")
    blob = "\n".join(e.text for e in run.events if isinstance(e, AssistantMessage | Notice)).lower()
    for needle in expect.get("text_contains", []):
        if str(needle).lower() not in blob:
            problems.append(f"no message contains {needle!r}")
    if "cards_shown_max" in expect:
        shown = sum(1 for e in run.events if isinstance(e, CardEvent))
        if shown > int(expect["cards_shown_max"]):
            problems.append(f"{shown} card events, the limit is {expect['cards_shown_max']}")
    return problems
