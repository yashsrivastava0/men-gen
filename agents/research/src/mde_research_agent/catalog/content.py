"""Typed models for the content data files: dossier layout, platform capabilities, depth by size, typed values and the
decisions of each Spec lane (``dossiers.yaml``, ``capabilities.yaml``, ``depth.yaml``, ``typed_values.yaml``, the
``decisions`` block of ``lanes.yaml``).

They live apart from ``models.py`` so the planner's models and these stay independent. Every model is frozen and refuses
unknown keys, so a typo in a data file fails at load time. ``dossier_config()`` and ``typed_values()`` read the packaged
data once for code that has no ``Catalog`` at hand (the extraction wire check validates a dossier field key and a typed
value); everything else reads the catalog.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FieldKind = Literal["list", "scalar", "typed"]
LoadClassName = Literal["focused", "standard", "deep"]
Wired = Literal["code", "data", "data_only"]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- dossiers ------------------------------------------------------------------------------------------------


class DossierFieldSpec(Frozen):
    kind: FieldKind
    text: str
    derived: Literal["official_source"] | None = None


class DossierLayout(Frozen):
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


class DossiersConfig(Frozen):
    fields: dict[str, DossierFieldSpec]
    default: DossierLayout
    capabilities: dict[str, DossierLayout]
    absence_after_sources: int

    @model_validator(mode="after")
    def _fields_exist(self) -> DossiersConfig:
        known = set(self.fields)
        for name, layout in {"default": self.default, **self.capabilities}.items():
            unknown = [f for f in (*layout.required, *layout.optional) if f not in known]
            if unknown:
                raise ValueError(f"dossiers.yaml {name}: unknown field {unknown}")
            both = set(layout.required) & set(layout.optional)
            if both:
                raise ValueError(
                    f"dossiers.yaml {name}: {sorted(both)} are both required and optional"
                )
        return self

    def layout_for(self, need: str | None) -> DossierLayout:
        return self.capabilities.get(need or "", self.default)

    def extractable(self) -> dict[str, DossierFieldSpec]:
        """The fields a provider_fact may carry (not the ones code derives)."""
        return {k: v for k, v in self.fields.items() if v.derived is None}


# --- platform capabilities -----------------------------------------------------------------------------------


class Capability(Frozen):
    id: str
    label: str
    signals: tuple[str, ...] = ()
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    bonus: int = 0
    terms: tuple[str, ...] = ()


class CapabilitiesConfig(Frozen):
    weak_units: int
    capabilities: tuple[Capability, ...]

    @model_validator(mode="after")
    def _unique(self) -> CapabilitiesConfig:
        ids = [c.id for c in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("capabilities.yaml: duplicate capability id")
        return self

    def by_id(self) -> dict[str, Capability]:
        return {c.id: c for c in self.capabilities}


# --- depth by size ---------------------------------------------------------------------------------------------


class DepthClass(Frozen):
    platform_capabilities: int


class DepthItem(Frozen):
    id: str
    classes: tuple[LoadClassName, ...]
    job: str
    template: str
    slot: str
    wired: Wired
    note: str
    floor: dict[LoadClassName, int] = {}


class DepthConfig(Frozen):
    classes: dict[LoadClassName, DepthClass]
    items: tuple[DepthItem, ...]

    def for_class(self, name: str) -> DepthClass:
        return self.classes[name]  # type: ignore[index]


# --- typed values -----------------------------------------------------------------------------------------------


class TypedValuesConfig(Frozen):
    currencies: dict[str, tuple[str, ...]]
    percent_units: tuple[str, ...]
    percent_evidence: tuple[str, ...]
    ignore_words: tuple[str, ...]


# --- the confirm list -------------------------------------------------------------------------------------------


class ConfirmClassRule(Frozen):
    kinds: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    typed_currency: bool = False
    needs_figure: bool = False
    words: tuple[str, ...] = ()


class ConfirmConfig(Frozen):
    top: int
    classes: dict[
        str, ConfirmClassRule
    ]  # in the order of the list; ``other`` is last and matches everything

    @model_validator(mode="after")
    def _closed(self) -> ConfirmConfig:
        if list(self.classes) != ["money", "legal", "identity", "other"]:
            raise ValueError(
                "confirm.yaml classes must be money, legal, identity, other (in that order)"
            )
        return self


# --- lane decisions ---------------------------------------------------------------------------------------------


class LaneDecision(Frozen):
    id: str
    text: str
    kinds: tuple[str, ...]
    fields: tuple[str, ...] = ()  # narrows provider_fact items to these dossier fields
    flags_any: tuple[str, ...] = ()  # only items carrying one of these flags


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def dossier_config() -> DossiersConfig:
    return DossiersConfig.model_validate(load_yaml(DATA_DIR / "dossiers.yaml"))


@lru_cache(maxsize=1)
def typed_values() -> TypedValuesConfig:
    return TypedValuesConfig.model_validate(load_yaml(DATA_DIR / "typed_values.yaml"))
