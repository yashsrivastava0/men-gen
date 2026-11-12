"""Slots must be able to read what extraction really produces (D-160).

The Screens slot could never fill: a screen state is ``{state, content}`` and ``values_of`` read it as empty. The audit
below builds one element of every list attribute a slot counts and requires that it reads as a value, so the same class
of bug cannot come back with a new payload shape.
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import Any, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from mde_research_agent.catalog import load_catalog
from mde_research_agent.catalog.models import SlotSpec
from mde_research_agent.core.slots import evaluate_target, values_of
from mde_research_agent.domain.atoms import PAYLOADS

CAT = load_catalog()


def _slots() -> list[tuple[str, SlotSpec, str]]:
    out: list[tuple[str, SlotSpec, str]] = []
    for job in CAT.jobs.values():
        for tpl in job.targets:
            for slot in tpl.slots:
                for attr in [slot.attr, *(x.attr for x in slot.extra_attrs)]:
                    if attr:
                        out.append((f"{job.job}/{tpl.id}/{slot.key}", slot, attr))
    return out


def _example(model: type[BaseModel]) -> dict[str, Any]:
    """A minimal instance of a payload element: every required field set to a plausible placeholder."""
    kwargs: dict[str, Any] = {}
    for name, f in model.model_fields.items():
        if not f.is_required():
            continue
        ann = f.annotation
        if get_origin(ann) is Literal:
            kwargs[name] = get_args(ann)[0]
        elif ann is str:
            kwargs[name] = "x"
        elif ann is bool:
            kwargs[name] = True
        elif get_origin(ann) in (Union, types.UnionType):
            kwargs[name] = None
        else:
            kwargs[name] = []
    return model.model_validate(kwargs).model_dump()


def _element_model(annotation: Any) -> type[BaseModel] | None:
    if get_origin(annotation) is list:
        (arg,) = get_args(annotation)
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


@pytest.mark.parametrize(
    ("where", "slot", "attr"), _slots(), ids=[w + ":" + a for w, _, a in _slots()]
)
def test_every_slot_attribute_reads_as_values(where: str, slot: SlotSpec, attr: str) -> None:
    payload = PAYLOADS[slot.kind]
    assert attr in payload.model_fields, f"{where}: {slot.kind} has no field {attr}"
    elem = _element_model(payload.model_fields[attr].annotation)
    if elem is None:
        return  # a list of strings or a plain string: values_of reads those directly
    assert values_of([_example(elem)]), (
        f"{where}: {attr} is a list of {elem.__name__} that reads as empty"
    )


def test_a_screen_state_reads_as_its_name_and_an_unknown_shape_as_its_first_text() -> None:
    assert values_of({"state": "loading", "content": None}) == ["loading"]
    assert values_of([{"state": "empty", "content": "No classes"}, {"state": "error"}]) == [
        "empty",
        "error",
    ]
    assert values_of({"unlisted": "first", "other": "second"}) == ["first"]
    assert values_of({"unlisted": 3}) == []


@dataclass
class Atom:
    kind: str
    norm_key: str
    data: dict[str, Any] = field(default_factory=dict)


def _screen(key: str, *, actions: int, sections: int, states: int) -> Atom:
    names = ["loading", "empty", "error", "success", "default", "offline"]
    return Atom(
        "screen_pattern",
        key,
        {
            "sections": [f"section {i}" for i in range(sections)],
            "actions": [f"action {i}" for i in range(actions)],
            "states": [{"state": names[i], "content": None} for i in range(states)],
        },
    )


def _key_screens_slots() -> tuple[SlotSpec, ...]:
    tpl = next(t for t in CAT.jobs["screens"].targets if t.id == "key_screens")
    return tpl.slots


def test_the_key_screens_slot_fills_from_what_pages_state_and_states_do_not_block_it() -> None:
    atoms = [_screen(k, actions=3, sections=2, states=0) for k in ("home", "booking", "profile")]
    cov = evaluate_target(_key_screens_slots(), atoms)
    assert cov.covered and cov.required_total == 1 and cov.required_filled == 1
    wanted = next(f for f in cov.fills if not f.required)
    assert not wanted.filled and wanted.thin  # states are wanted, never required


def test_the_wanted_states_slot_fills_when_pages_do_state_them() -> None:
    atoms = [_screen(k, actions=3, sections=3, states=3) for k in ("home", "booking", "profile")]
    cov = evaluate_target(_key_screens_slots(), atoms)
    assert all(f.filled for f in cov.fills)


def test_two_thin_patterns_do_not_fill_the_slot() -> None:
    atoms = [
        _screen("home", actions=3, sections=2, states=0),
        _screen("tiny", actions=1, sections=1, states=0),
    ]
    assert not evaluate_target(_key_screens_slots(), atoms).covered
