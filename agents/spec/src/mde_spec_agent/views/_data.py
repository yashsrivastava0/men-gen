"""Loaders for the view data files (``views/data/*.yaml``): wording and estimate numbers live in data, never in logic."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).parent / "data"


class DataError(ValueError):
    """A views data file is malformed."""


@cache
def _raw(name: str) -> dict[str, Any]:
    path = DATA_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "version" not in data:
        raise DataError(f"{path.name}: a mapping with a `version` key is required")
    return data


def words() -> dict[str, Any]:
    """The plain-words table (``views.yaml``). Read only: callers never mutate it."""
    return _raw("views")


def estimate_table() -> dict[str, Any]:
    """The [J] estimate table (``estimate.yaml``). Read only."""
    return _raw("estimate")
