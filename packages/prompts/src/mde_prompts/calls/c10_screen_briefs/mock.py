"""Offline handler for C10: copy, a section and a check for every screen of the batch.

Deterministic and schema-valid. It only adds what is missing (an ``intro`` line, a ``help_hint``
section, one check) so running it twice proposes the same, small, valid edits.
"""

from __future__ import annotations

import re

from mde_contracts.calls import (
    C10Input,
    C10Output,
    KeyValue,
    Proposal,
    ScreenDraftLite,
    SpecProposals,
)
from mde_prompts.calls.c10_screen_briefs.detail import split_detail

INTRO_KEY = "intro"
HELP_SECTION = "help_hint"
MAX_INTRO = 100
MAX_CHECK = 160
_SENTENCE = re.compile(r"^(.+?[.!?])(\s|$)")


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def _intro(screen: ScreenDraftLite) -> str:
    purpose = " ".join(split_detail(screen.purpose)[0].split()) or "This screen"
    match = _SENTENCE.match(purpose)
    sentence = match.group(1) if match else purpose
    return _trim(sentence.rstrip("."), MAX_INTRO)


def _check(screen: ScreenDraftLite) -> str:
    purpose = " ".join(split_detail(screen.purpose)[0].split()) or "its job"
    lowered = purpose[:1].lower() + purpose[1:]
    return _trim(f"Everything this screen promises works: {lowered.rstrip('.')}", MAX_CHECK)


def _detail_check(detail: str) -> str:
    return _trim(f"The screen has what you asked for: {detail.rstrip('.')}", MAX_CHECK)


def handle(input: C10Input) -> C10Output:
    proposals: list[Proposal] = []
    for screen in input.screens:
        detail = split_detail(screen.purpose)[1]
        if detail:
            proposals.append(
                Proposal(
                    kind="add_screen_check",
                    target=screen.id,
                    rationale="The person asked for this on this screen.",
                    items=[_detail_check(detail)],
                )
            )
        if INTRO_KEY not in screen.copy_keys:
            proposals.append(
                Proposal(
                    kind="set_screen_copy",
                    target=screen.id,
                    rationale="A short intro line makes the screen's job clear to the user.",
                    pairs=[KeyValue(key=INTRO_KEY, value=_intro(screen))],
                )
            )
        if HELP_SECTION not in screen.sections:
            proposals.append(
                Proposal(
                    kind="add_screen_section",
                    target=screen.id,
                    rationale="A small help hint lowers confusion for first-time users.",
                    items=[HELP_SECTION],
                )
            )
        proposals.append(
            Proposal(
                kind="add_screen_check",
                target=screen.id,
                rationale="One testable sentence ties the screen to its purpose.",
                items=[_check(screen)],
            )
        )
    return SpecProposals(proposals=proposals, notes="mock: copy, a section and a check per screen")
