"""Component catalogues as data: two dated public snapshots (fixtures) and one synthetic catalogue (about 25 components,
used only to plant gaps). The `shapes_it_can_show` field is the snapshot author's mapping to `mde_spec_core.vocab`
shapes, with a `confidence`; the lookup probe never shows it to the consumer (it is the answer key)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from evals.spec.common import DATA_DIR, FIXTURES_DIR
from mde_spec_core import vocab

CATALOGUE_DIR = FIXTURES_DIR / "catalogues"
REAL_LIBRARIES = ("react-native-paper", "react-native-elements")
SOLID = ("high", "medium")  # a mapping below this confidence is left out of the answer key


@dataclass(frozen=True)
class Component:
    name: str
    purpose: str
    source_url: str
    shapes: tuple[str, ...]
    confidence: str
    notes: str = ""


@dataclass
class Catalogue:
    library: str
    key: str
    source_url: str
    checked_on: str
    components: list[Component]
    planted_gaps: list[str] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)

    def lines(self) -> list[str]:
        """What the consumer reads: name and purpose only (never the shapes: that is the answer key)."""
        return [f"{c.name}: {c.purpose}" for c in self.components]

    def fitting(self, shape: str) -> list[str]:
        """Components whose mapping to `shape` is solid (the answer key)."""
        return [c.name for c in self.components if shape in c.shapes and c.confidence in SOLID]

    def maybe_fitting(self, shape: str) -> list[str]:
        """Components that might fit (low confidence): a need only they could serve is left out of scoring."""
        return [c.name for c in self.components if shape in c.shapes and c.confidence not in SOLID]

    def gap_shapes(self) -> list[str]:
        """Vocabulary shapes no component can show with solid confidence (the gaps of this library)."""
        return sorted(
            s for s in vocab.shapes() if not self.fitting(s) and not self.maybe_fitting(s)
        )


def _load(path: Path, key: str) -> Catalogue:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return Catalogue(
        library=raw["library"],
        key=key,
        source_url=raw["source_url"],
        checked_on=raw["checked_on"],
        components=[
            Component(
                name=c["name"],
                purpose=c["purpose"],
                source_url=c.get("source_url", ""),
                shapes=tuple(c.get("shapes_it_can_show", [])),
                confidence=c.get("confidence", "low"),
                notes=c.get("notes", ""),
            )
            for c in raw["components"]
        ],
        planted_gaps=list(raw.get("planted_gaps", [])),
        excluded=list(raw.get("excluded", [])),
    )


@cache
def load_catalogue(key: str) -> Catalogue:
    if key == "synthetic":
        return _load(DATA_DIR / "synthetic-catalogue.json", "synthetic")
    path = CATALOGUE_DIR / f"{key}.json"
    if not path.exists():
        raise KeyError(f"no catalogue {key!r}")
    return _load(path, key)


def synthetic_catalogue() -> Catalogue:
    return load_catalogue("synthetic")


def real_catalogues() -> list[Catalogue]:
    return [load_catalogue(k) for k in REAL_LIBRARIES]


def by_library_name(name: str) -> Catalogue:
    for key in (*REAL_LIBRARIES, "synthetic"):
        c = load_catalogue(key)
        if c.library == name or c.key == name:
            return c
    raise KeyError(name)
