"""Progress labels (plain language, data in ``data/progress_labels.yaml``)."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import cast

import yaml


@lru_cache(maxsize=1)
def _labels() -> dict[str, dict[str, str]]:
    text = (resources.files("mde_research") / "data" / "progress_labels.yaml").read_text(
        encoding="utf-8"
    )
    raw = cast("dict[str, object]", yaml.safe_load(text))
    return cast("dict[str, dict[str, str]]", raw["labels"])


def label_for(key: str, language: str = "en") -> str:
    """The label for a step key (``plan_research``, ``search:R1``, ``extract``, ``synthesize``)."""
    entry = _labels().get(key) or _labels()["extract"]
    return entry.get(language) or entry.get(language.split("-")[0]) or entry["en"]


def label_keys() -> list[str]:
    return list(_labels())
