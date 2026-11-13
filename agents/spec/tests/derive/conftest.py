"""Shared fixtures of the derivation tests: the real exports, a Research view when one is on disk, tiny hand-made inputs."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import ResearchView, SpecInput, read_export, read_research
from mde_compass_intake.spec_input import (
    App,
    Entity,
    EntityField,
    Feature,
    Flow,
    FlowStep,
    Idea,
    Locales,
    Module,
    Role,
    Screen,
    ScreenUse,
    Setting,
    StructureProposal,
    Trust,
)

ROOT = Path(__file__).resolve().parents[4]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
RUNS = ROOT / "agents" / "research" / ".runtime" / "runs"

THREE = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
}


@cache
def load_export(name: str) -> SpecInput:
    path = EXPORTS / f"{name}.json"
    if not path.exists():
        pytest.skip(f"export {name} is not in the repository")
    return read_export(json.loads(path.read_text(encoding="utf-8")))


def best_research(spec_input: SpecInput) -> ResearchView | None:
    """The newest complete Research run made for this Brief, when one is on disk (the runtime folder is gitignored)."""
    best: tuple[str, ResearchView] | None = None
    if not RUNS.exists():
        return None
    for run in sorted(RUNS.iterdir()):
        path = run / "research.json"
        if not path.exists():
            continue
        try:
            view = read_research(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
        if view.is_for(spec_input.brief_hash) and view.status == "complete" and not view.thin:
            best = (run.name, view)
    return best[1] if best else None


@pytest.fixture(scope="session")
def export() -> Callable[[str], SpecInput]:
    return lambda key: load_export(THREE.get(key, key))


@pytest.fixture(scope="session")
def research() -> Callable[[SpecInput], ResearchView | None]:
    return best_research


# ---------------------------------------------------------------- tiny hand-made inputs


def tiny_input(**over: Any) -> SpecInput:
    """A one-app input with two roles, one entity the owner manages, three screens and one flow."""
    base: dict[str, Any] = dict(
        program_id="prog-tiny",
        tier="quick",
        idea=Idea(summary="A small booking app"),
        locales=Locales(codes=("en",)),
        auth_methods=Setting(values=("email_password",), trust=Trust(grade="locked")),
        features=(
            Feature(
                id="FTR-001",
                name="Book a slot",
                intent="Customers book a slot",
                priority="must",
                user_evidenced=True,
                quote_ids=("FCT-001",),
                trust=Trust(grade="locked", verified_quote=True),
            ),
            Feature(
                id="FTR-002",
                name="Manage slots",
                intent="The owner manages slots",
                priority="nice",
                user_evidenced=True,
                quote_ids=("FCT-002",),
                trust=Trust(grade="locked", verified_quote=True),
            ),
        ),
        roles=(
            Role(id="ROL-001", name="Customer", goal="book", trust=Trust(grade="locked")),
            Role(id="ROL-002", name="Owner", goal="run it", trust=Trust(grade="locked")),
        ),
        apps=(App(id="APP-001", name="The app", roles=("ROL-001", "ROL-002")),),
        modules=(
            Module(
                id="MOD-001",
                name="Slots",
                apps=("APP-001",),
                features=("FTR-001", "FTR-002"),
                entry="SCR-001",
            ),
        ),
        structure=StructureProposal(
            present=True,
            screens=(
                Screen(
                    id="SCR-001",
                    name="Slots",
                    module="MOD-001",
                    apps=("APP-001",),
                    roles=("ROL-001", "ROL-002"),
                    serves=("FTR-001",),
                    entities=(ScreenUse(entity="ENT-001", use="view"),),
                    pattern="list",
                    phase="core",
                ),
                Screen(
                    id="SCR-002",
                    name="Create slot",
                    module="MOD-001",
                    apps=("APP-001",),
                    roles=("ROL-002",),
                    serves=("FTR-002",),
                    entities=(ScreenUse(entity="ENT-001", use="create"),),
                    pattern="form",
                    phase="core",
                ),
                Screen(
                    id="SCR-003",
                    name="Edit slot",
                    module="MOD-001",
                    apps=("APP-001",),
                    roles=("ROL-002",),
                    serves=("FTR-002",),
                    entities=(ScreenUse(entity="ENT-001", use="edit"),),
                    pattern="form",
                    phase="core",
                ),
            ),
            flows=(
                Flow(
                    id="FLW-001",
                    name="Book",
                    role="ROL-001",
                    steps=(FlowStep(screen="SCR-001"),),
                    features=("FTR-001",),
                ),
            ),
            entities=(
                Entity(
                    id="ENT-001",
                    key="slot",
                    name="Slot",
                    module="MOD-001",
                    fields=(
                        EntityField(key="starts", type="datetime", required=True),
                        EntityField(key="price", type="money"),
                    ),
                    states=("open", "booked", "cancelled"),
                    access={"ROL-001": "view", "ROL-002": "manage"},
                ),
            ),
        ),
    )
    base.update(over)
    return SpecInput(**base)
