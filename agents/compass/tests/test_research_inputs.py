"""The three saved Compass runs in ``docs/compass/research-inputs`` stay what the research agent will read: complete,
approved, hash-true, valid against the current Brief model, and carrying the research guidance of their size band.

Each file is the full output of a real browser session against the live model (one per tier, three unrelated ideas):
the Brief approved at Gate 1, the four consumer views, the structure, the interview with every fact and its quote."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.engine.validate import validate_brief
from mde_compass.views.consumers import build_handoff

FOLDER = Path(__file__).resolve().parents[3] / "docs" / "compass" / "research-inputs"
DEPTH = {"small": "light", "medium": "standard", "large": "deep"}

# name -> (tier, band, at least features, features set as must, roles that must exist, at least research requests)
RUNS: dict[str, tuple[str, str, int, int, tuple[str, ...], int]] = {
    "quick-yoga-studio-bengaluru": ("quick", "small", 5, 3, ("Members", "Instructors"), 3),
    "standard-produce-marketplace-nashik": ("standard", "medium", 8, 4, ("Farmer", "Buyer"), 5),
    "thorough-freight-fleet-india": ("thorough", "large", 25, 3, ("dispatchers", "drivers"), 15),
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FOLDER / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def brief_of(data: dict[str, Any]) -> Brief:
    return Brief.model_validate({k: v for k, v in data["brief"].items() if not k.startswith("_")})


def test_the_folder_holds_exactly_the_three_runs_and_a_readme() -> None:
    assert {p.stem for p in FOLDER.glob("*.json")} == set(RUNS)
    assert (FOLDER / "README.md").is_file()


@pytest.mark.parametrize("name", sorted(RUNS))
def test_a_run_is_a_complete_approved_full_output(name: str) -> None:
    d = load(name)
    assert d["export"]["format"] == "compass-full-output" and d["export"]["format_version"] == 2
    assert (
        d["session"]["state"] == "APPROVED"
        and d["session"]["approved_version"] == d["export"]["brief_version"]
    )
    for key in (
        "session",
        "brief",
        "consumer_views",
        "structure",
        "interview",
        "sources",
        "spend",
        "brief_versions",
    ):
        assert key in d, key
    assert set(d["consumer_views"]) == {"research", "blueprint", "design", "verify"}
    assert d["interview"]["facts"] and d["interview"]["qa"]
    active = [f for f in d["interview"]["facts"] if f["status"] == "active"]
    quoted = [f for f in active if f["quote"]]
    assert len(quoted) >= 0.95 * len(active), "almost every active fact has its verbatim quote"


@pytest.mark.parametrize("name", sorted(RUNS))
def test_the_hashes_are_true_and_the_approval_is_of_exactly_this_brief(name: str) -> None:
    d = load(name)
    b = brief_of(d)
    assert brief_hash(b) == d["brief"]["_hash"] == d["export"]["brief_hash"]
    assert section_hashes(b) == d["brief"]["_section_hashes"]
    assert d["export"]["approved_baseline"]["hash"] == d["brief"]["_hash"]
    assert d["export"]["approved_baseline"]["version"] == d["export"]["brief_version"]


@pytest.mark.parametrize("name", sorted(RUNS))
def test_the_brief_is_valid_and_its_research_view_is_what_the_code_builds_from_it(
    name: str,
) -> None:
    d = load(name)
    b = brief_of(d)
    assert validate_brief(b, load_catalog()) == []
    views = build_handoff(b)
    assert views["research"] == d["consumer_views"]["research"]
    assert set(views) == set(d["consumer_views"])


@pytest.mark.parametrize("name", sorted(RUNS))
def test_the_research_view_says_how_big_the_app_is_and_how_deep_to_go(name: str) -> None:
    tier, band, *_ = RUNS[name]
    d = load(name)
    guidance = d["consumer_views"]["research"]["guidance"]
    assert guidance["size_band"] == band and guidance["depth"] == DEPTH[band]
    assert d["structure"]["band"] == band and d["structure"]["source"] == "tier"
    assert d["brief"]["meta"]["interview"]["budget"] == tier
    assert guidance["category"] and guidance["search_budget"] >= 6
    assert d["consumer_views"]["research"]["feature_intents"], "what to research per feature"


@pytest.mark.parametrize("name", sorted(RUNS))
def test_the_structure_block_agrees_with_the_brief(name: str) -> None:
    d = load(name)
    b = brief_of(d)
    counts = d["structure"]["counts"]
    assert counts == {
        "modules": len(b.modules),
        "screens": len(b.screens),
        "flows": len(b.flows),
        "entities": len(b.entities),
    }
    assert [s["module"]["id"] for s in d["structure"]["slices"]] == [m.id for m in b.modules]
    ids = {
        r.id
        for sec in (b.roles, b.apps, b.scope, b.modules, b.screens, b.flows, b.entities)
        for r in sec
    }
    assert {s.module for s in b.screens} <= {m.id for m in b.modules}
    assert {r for s in b.screens for r in s.roles} <= ids


@pytest.mark.parametrize("name", sorted(RUNS))
def test_what_the_person_asked_for_is_in_the_brief(name: str) -> None:
    _, _band, min_features, min_must, roles, min_requests = RUNS[name]
    b = brief_of(load(name))
    assert len(b.scope) >= min_features
    assert sum(1 for f in b.scope if f.priority == "must") >= min_must
    names = {r.name_in_user_words for r in b.roles}
    assert set(roles) <= names, f"{set(roles) - names} missing from {sorted(names)}"
    assert b.apps and len(b.research_requests) >= min_requests
    assert b.idea["category"].value and b.context["geo"].value
    assert len({f.name.casefold() for f in b.scope}) == len(b.scope), (
        "no two features with the same name"
    )
    assert len({r.name_in_user_words.casefold() for r in b.roles}) == len(b.roles)


@pytest.mark.parametrize("name", sorted(RUNS))
def test_a_run_holds_no_secret(name: str) -> None:
    raw = (FOLDER / f"{name}.json").read_text(encoding="utf-8")
    assert not re.search(
        r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{12}|-----BEGIN [A-Z ]*PRIVATE KEY", raw
    )
    assert "OPENAI_API_KEY" not in raw


def test_the_readme_names_every_run_and_says_what_each_is_for() -> None:
    text = (FOLDER / "README.md").read_text(encoding="utf-8")
    for name in RUNS:
        assert f"{name}.json" in text
    for must in ("consumer_views.research", "guidance", "Known limits", "structure"):
        assert must in text, must
