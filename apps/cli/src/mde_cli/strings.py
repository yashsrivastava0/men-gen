"""The CLI's own hint lines in the user's language (data in ``data/strings.yaml``)."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import cast

import yaml

DEFAULT_LANGUAGE = "en"
LANGUAGES: tuple[str, ...] = ("en", "hi", "hi-Latn")


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, str]]:
    text = (resources.files("mde_cli") / "data" / "strings.yaml").read_text(encoding="utf-8")
    return cast("dict[str, dict[str, str]]", yaml.safe_load(text))


def normalise_language(language: str | None) -> str:
    """One of :data:`LANGUAGES` (anything else, or ``None`` or ``auto``, is English)."""
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def say(key: str, language: str | None = None, **values: object) -> str:
    """The string ``key`` in ``language`` (English when that language lacks it)."""
    entry = _table().get(key, {})
    text = entry.get(normalise_language(language)) or entry.get(DEFAULT_LANGUAGE, key)
    return text.format(**values) if values else text
