from __future__ import annotations

import re

import pytest
import yaml

from mde_content.recipes import load_recipes, recipe, recipe_ids
from mde_contracts.content import Recipe, RecipeLibrary

REQUIRED = {
    "welcome",
    "login-otp",
    "login-password",
    "signup",
    "profile",
    "settings",
    "list",
    "list-with-filters",
    "detail",
    "detail-with-sticky-cta",
    "form",
    "multi-step-form",
    "search",
    "notifications",
    "dashboard",
    "checkout-review",
    "confirmation",
    "tracking",
    "calendar-slot-picker",
    "approvals-queue",
    "empty-state-only",
}


def test_library_loads_and_is_cached() -> None:
    lib = load_recipes()
    assert isinstance(lib, RecipeLibrary)
    assert load_recipes() is lib


def test_required_recipes_present() -> None:
    assert recipe_ids() >= REQUIRED
    assert len(load_recipes().recipes) >= 20


def test_ids_unique_and_kebab_case() -> None:
    ids = [r.id for r in load_recipes().recipes]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", i) for i in ids)


@pytest.mark.parametrize("item", load_recipes().recipes, ids=lambda r: r.id)
def test_each_recipe_is_complete(item: Recipe) -> None:
    assert item.name and item.description
    assert item.default_sections, "a recipe lists its default sections"
    assert item.kit_components, "a recipe names the kit components it uses"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", s) for s in item.default_sections)
    assert len(item.default_sections) == len(set(item.default_sections))
    assert all(re.fullmatch(r"[A-Z][A-Za-z0-9]*", c) for c in item.kit_components)
    assert len(item.kit_components) == len(set(item.kit_components))
    # Every recipe says how it behaves while loading data or without network.
    assert item.states_provided, "states are provided by the recipe, not repeated per screen"


def test_data_reading_recipes_provide_all_four_states() -> None:
    for rid in ("list", "list-with-filters", "detail", "search", "notifications", "dashboard"):
        assert set(recipe(rid).states_provided) == {"loading", "empty", "error", "offline"}


def test_recipe_lookup() -> None:
    assert recipe("detail-with-sticky-cta").kind == "detail"
    with pytest.raises(KeyError):
        recipe("nope")


def test_yaml_round_trips_through_the_contract_model() -> None:
    lib = load_recipes()
    again = RecipeLibrary.model_validate(yaml.safe_load(yaml.safe_dump(lib.model_dump())))
    assert again == lib
