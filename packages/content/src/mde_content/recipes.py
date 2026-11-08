"""Base recipe library loader (brief 8.6).

Recipes are data (``data/recipes.yaml``) validated by ``mde_contracts.content.RecipeLibrary``.
A recipe supplies the loading / empty / error / offline states and the default sections, so a
pack screen (or a spec screen block) only lists deviations.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

import yaml

from mde_contracts.content import Recipe, RecipeLibrary


@lru_cache(maxsize=1)
def load_recipes() -> RecipeLibrary:
    """Load and validate the base recipe library (cached; the result is immutable)."""
    text = resources.files("mde_content").joinpath("data/recipes.yaml").read_text(encoding="utf-8")
    return RecipeLibrary.model_validate(yaml.safe_load(text))


def recipe_ids() -> frozenset[str]:
    """Ids of every recipe in the library."""
    return frozenset(r.id for r in load_recipes().recipes)


def recipe(recipe_id: str) -> Recipe:
    """Return one recipe by id; ``KeyError`` for an unknown id."""
    for item in load_recipes().recipes:
        if item.id == recipe_id:
            return item
    raise KeyError(f"unknown recipe: {recipe_id!r}")
