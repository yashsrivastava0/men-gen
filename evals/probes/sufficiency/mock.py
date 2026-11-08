"""Offline reader for the sufficiency probe: answers from the slice text and nothing else.

A rule-based reader, not a model: it parses the fenced YAML blocks of the slice (the block id is in
the fence header, ``yaml id=SCR-003 kind=screen``), finds the screen the question is about and reads
the field the question asks for. A field the slice does not contain is "unknown" (``found=false``),
which is exactly how a missing field shows up in a real run. Deterministic, no network.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from evals.probes.sufficiency.models import ProbeAnswer, ProbeInput, ProbeOutput, ProbeQuestion

_FENCE = re.compile(r"```yaml id=(?P<id>\S+)[^\n]*\n(?P<body>.*?)```", re.DOTALL)
UNKNOWN = "unknown"


def _blocks(slice_text: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for match in _FENCE.finditer(slice_text):
        try:
            data = yaml.safe_load(match.group("body"))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            found[match.group("id")] = data  # pyright: ignore[reportUnknownArgumentType]
    return found


def _answer(question: ProbeQuestion, screen: dict[str, Any] | None) -> ProbeAnswer:
    if screen is None:
        return ProbeAnswer(id=question.id, answer=UNKNOWN, found=False)
    value: object = None
    if question.kind == "route":
        value = screen.get("route")
    elif question.kind == "recipe":
        value = screen.get("recipe")
    elif question.kind == "visibility":
        value = screen.get("auth", "public")  # "public" is the default and is not written
    elif question.kind == "flow":
        value = screen.get("flow") or "none"
    elif question.kind == "bound_data":
        data = screen.get("data") or []
        value = data[0].get("bind") if data and isinstance(data[0], dict) else "none"  # pyright: ignore[reportUnknownMemberType]
    elif question.kind == "action_target":
        for action in screen.get("actions") or []:
            # the file writes an action as `- sign_in: {go: SCR-002}` (name -> details)
            if isinstance(action, dict) and question.detail in action:
                details = action[question.detail]  # pyright: ignore[reportUnknownVariableType]
                if isinstance(details, dict):
                    value = details.get("go") or details.get("calls") or "none"  # pyright: ignore[reportUnknownMemberType]
                else:
                    value = "none"
                break
    if value is None:
        return ProbeAnswer(id=question.id, answer=UNKNOWN, found=False)
    return ProbeAnswer(id=question.id, answer=str(value), found=True)


def handle(input: ProbeInput) -> ProbeOutput:
    blocks = _blocks(input.slice_text)
    return ProbeOutput(
        answers=[_answer(q, blocks.get(q.subject)) for q in input.questions],
    )
