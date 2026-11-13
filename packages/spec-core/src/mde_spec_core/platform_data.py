"""Platform capability sheet, delight patterns and script-fallback fonts: loaders (doc 15 7.3, doc 17 5.3 and 5.4).

All three are dated data files with a source on every entry. Code only loads and validates them and answers small
questions (the tier of a capability, the fallback family of a script). Nothing about a platform lives in code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from .config import DATA_DIR

CAPABILITY_PATH = DATA_DIR / "platform" / "capability_sheet.yaml"
DELIGHT_PATH = DATA_DIR / "delight" / "delight_patterns.yaml"
FONTS_PATH = DATA_DIR / "fonts" / "script_fallbacks.yaml"

Tier = Literal["A", "B", "C"]


class PlatformDataError(ValueError):
    pass


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Omitted(_M):
    topic: str
    reason: str


# ---------------------------------------------------------------- capability sheet
class StackRead(_M):
    expo_sdk_read: str
    expo_package_version_read: str
    react_native_read: str
    source_url: str
    bundled_modules_url: str
    project_stack_kit: str
    project_stack_kit_pinned: bool
    project_stack_kit_note: str


class Capability(_M):
    id: str
    label: str
    tier: Tier
    expo_go: bool | None = (
        None  # what the page says about the stock preview client; None = page is silent
    )
    in_sdk_bundled_modules: bool | None = None
    platforms: list[str]
    production_recommended: bool = True
    source_url: str
    checked_on: str
    note: str


class CapabilitySheet(_M):
    version: int
    checked_on: str
    stack: StackRead
    capabilities: list[Capability]
    omitted_unverified: list[Omitted] = []

    @model_validator(mode="after")
    def _well_formed(self) -> CapabilitySheet:
        ids = [c.id for c in self.capabilities]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise PlatformDataError(f"duplicate capability ids: {dupes}")
        for c in self.capabilities:
            if not c.source_url or not c.checked_on:
                raise PlatformDataError(f"{c.id} has no source_url or checked_on")
        return self

    def get(self, capability_id: str) -> Capability:
        for c in self.capabilities:
            if c.id == capability_id:
                return c
        raise KeyError(capability_id)

    def tier_of(self, capability_id: str) -> Tier:
        return self.get(capability_id).tier

    def by_tier(self, tier: Tier) -> list[Capability]:
        return [c for c in self.capabilities if c.tier == tier]


def load_capability_sheet(path: Path | None = None) -> CapabilitySheet:
    p = path or CAPABILITY_PATH
    return CapabilitySheet.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_capability_sheet() -> CapabilitySheet:
    return load_capability_sheet()


# ---------------------------------------------------------------- delight patterns
class Basis(_M):
    source_url: str
    says: str  # what the public guidance says, in a few plain words (a paraphrase)


class DelightPattern(_M):
    """Intent and effect only: no layout, no widget, no animation library (doc 17 5.3, doc 15 freedom contract)."""

    id: str
    name: str
    moment: str  # the user moment it fits
    intent: str
    effect: str
    tier: Tier
    capability_ref: str | None = None  # a capability id when the pattern needs one
    basis: list[Basis]
    reduced_motion_alternative: str
    checked_on: str


class DelightLibrary(_M):
    version: int
    checked_on: str
    patterns: list[DelightPattern]
    omitted_unverified: list[Omitted] = []

    @model_validator(mode="after")
    def _well_formed(self) -> DelightLibrary:
        ids = [p.id for p in self.patterns]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise PlatformDataError(f"duplicate delight ids: {dupes}")
        for p in self.patterns:
            if not p.basis:
                raise PlatformDataError(f"{p.id} has no basis")
        return self

    def get(self, pattern_id: str) -> DelightPattern:
        for p in self.patterns:
            if p.id == pattern_id:
                return p
        raise KeyError(pattern_id)


def load_delight_patterns(path: Path | None = None) -> DelightLibrary:
    p = path or DELIGHT_PATH
    return DelightLibrary.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_delight_patterns() -> DelightLibrary:
    return load_delight_patterns()


# ---------------------------------------------------------------- script fallback fonts
class ScriptFont(_M):
    script: str
    iso15924: list[str]
    rtl: bool
    family: str
    variants: dict[
        str, str
    ] = {}  # region or sub-script key -> family, when one family is not enough
    licence: str  # SPDX id
    licence_url: str
    coverage_note: str
    source_url: str
    checked_on: str


class ScriptFontSheet(_M):
    version: int
    checked_on: str
    licence_text_source: str
    scripts: list[ScriptFont]
    omitted_unverified: list[Omitted] = []

    @model_validator(mode="after")
    def _well_formed(self) -> ScriptFontSheet:
        names = [s.script for s in self.scripts]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise PlatformDataError(f"duplicate scripts: {dupes}")
        for s in self.scripts:
            if not s.source_url or not s.checked_on:
                raise PlatformDataError(f"{s.script} has no source_url or checked_on")
        return self

    def get(self, script: str) -> ScriptFont:
        for s in self.scripts:
            if s.script.lower() == script.lower():
                return s
        raise KeyError(script)

    def family_for(self, script: str, variant: str | None = None) -> str:
        entry = self.get(script)
        if variant is not None:
            return entry.variants[variant]
        return entry.family


def load_script_fonts(path: Path | None = None) -> ScriptFontSheet:
    p = path or FONTS_PATH
    return ScriptFontSheet.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_script_fonts() -> ScriptFontSheet:
    return load_script_fonts()
