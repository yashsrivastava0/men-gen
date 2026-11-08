"""Org Profile -> seeds: the tenant's stored profile becomes the starting context of an Enterprise
session (spec 3.5 and 10, D-077).

The profile is a tolerant JSON document, not a strict model. This module reads only the keys the
data file ``data/org_profile.yaml`` documents, turns each into a value one card could have written,
and returns them as ``Seed`` objects; the engine stores them as ``assumed`` slots with source
``org_profile``. Everything else is dropped silently and only counted (never echoed).

The profile is org-supplied text, so it is untrusted data:

* the document is read only within bounds (64 KB of JSON, depth, nodes, list lengths); over any
  bound it is ignored whole;
* every text value goes through the existing ``SafetyScreen`` (a secret or an instruction-like
  string is dropped, never redacted-and-kept) and the existing ``PolicyGuard`` (a banned library in
  a name, a region outside the policy);
* option, theme and region values are looked up in a closed vocabulary (the question bank's cards
  and the themes), so a profile can only select what a card could offer;
* the slots in ``never_seed`` (the idea and its type) are refused when the data file loads.

Pure and deterministic; no LLM call, no I/O after the data file is read. Every regex is linear.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from mde_content.catalogs import load_catalogs
from mde_content.questions import QuestionBanks
from mde_contracts.policy import Policy
from mde_contracts.slots import SLOT_IDS, SlotData
from mde_discovery import copy as texts
from mde_discovery.policy_guard import PolicyGuard
from mde_discovery.safety import SafetyScreen
from mde_discovery.vocab import Vocab
from mde_prompts.calls.c1_understand import lexicon as lx

Kind = Literal["text", "url", "color", "choice", "list"]
MAX_ID_CHARS = 64  # a longer option id, region or theme name cannot be a real one
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")  # fullmatch only: fixed length, linear
_MARKUP = frozenset("<>`")
_MISSING = object()


# --------------------------------------------------------------------------- the data file


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Limits(_Model):
    max_json_bytes: int = 65_536
    max_depth: int = 6
    max_nodes: int = 4_000
    max_list_items: int = 32


class CopyIds(_Model):
    evidence: str
    notice: str


class ProfileMapping(_Model):
    path: tuple[str, ...]
    slot: str
    kind: Kind
    max_chars: int | None = None
    values: list[str] | None = None
    check: Literal["regions"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ProfileMapping:
        if not 1 <= len(self.path) <= 2 or not all(self.path):
            raise ValueError(f"{self.slot}: a path is one or two keys")
        if self.slot not in SLOT_IDS:
            raise ValueError(f"{self.slot}: not a slot id")
        if self.kind in ("text", "url") and not self.max_chars:
            raise ValueError(f"{self.slot}: a {self.kind} mapping needs max_chars")
        if self.check == "regions" and self.kind != "list":
            raise ValueError(f"{self.slot}: the regions check applies to a list")
        return self


class ProfileMap(_Model):
    version: int = 1
    limits: Limits = Limits()
    copy_ids: CopyIds
    never_seed: list[str]
    mappings: list[ProfileMapping]

    @model_validator(mode="after")
    def _consistent(self) -> ProfileMap:
        slots = [m.slot for m in self.mappings]
        paths = [m.path for m in self.mappings]
        if len(set(slots)) != len(slots) or len(set(paths)) != len(paths):
            raise ValueError("each slot and each path is mapped once")
        refused = sorted(set(slots) & set(self.never_seed))
        if refused:
            raise ValueError(f"never seeded: {refused}")
        sections = {m.path[0] for m in self.mappings if len(m.path) == 2}
        if sections & {m.path[0] for m in self.mappings if len(m.path) == 1}:
            raise ValueError("a key is either a value or a section, not both")
        return self


@lru_cache(maxsize=1)
def load_profile_map() -> ProfileMap:
    """Read and validate ``data/org_profile.yaml`` of this package (cached)."""
    text = (
        resources.files("mde_discovery")
        .joinpath("data/org_profile.yaml")
        .read_text(encoding="utf-8")
    )
    return ProfileMap.model_validate(yaml.safe_load(text))


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Seed:
    """One slot value taken from the profile."""

    slot_id: str
    value: SlotData
    evidence: str


@dataclass(frozen=True)
class ProfileReport:
    """What reading a profile produced. ``dropped`` counts keys and values left out (never echoed);
    ``rejected`` names why the whole document was ignored (no value, only a reason code)."""

    seeds: tuple[Seed, ...] = ()
    dropped: int = 0
    rejected: str | None = None


# --------------------------------------------------------------------------- reading


def _bounds_problem(profile: object, limits: Limits) -> str | None:
    """Why the document is too big to read, or None. Iterative (no recursion on deep input)."""
    nodes = 0
    stack: list[tuple[object, int]] = [(profile, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            return "too_many_nodes"
        if depth > limits.max_depth:
            return "too_deep"
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    try:
        size = len(json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return "not_json"
    return "too_large" if size > limits.max_json_bytes else None


def _lookup(profile: Mapping[str, object], path: Sequence[str]) -> object:
    node: object = profile
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]  # pyright: ignore[reportUnknownVariableType]
    return node


class OrgProfileMapper:
    """Reads a profile with the data file's mappings against the engine's vocabulary and rules."""

    def __init__(
        self,
        vocab: Vocab,
        guard: PolicyGuard,
        safety: SafetyScreen | None = None,
        profile_map: ProfileMap | None = None,
    ) -> None:
        self.vocab = vocab
        self.guard = guard
        self.safety = safety or SafetyScreen()
        self.map = profile_map or load_profile_map()
        for m in self.map.mappings:  # a broken data file stops the engine at start-up
            if m.kind in ("choice", "list") and self._allowed(m) is None:
                raise ValueError(f"{m.slot}: no card offers values for it; list them in `values`")
        self._sections: dict[str, set[str]] = {}
        for m in self.map.mappings:
            self._sections.setdefault(m.path[0], set()).update(m.path[1:])

    # ----------------------------------------------------------------- the public call
    def read(self, profile: object, policy: Policy, lang: str | None = "en") -> ProfileReport:
        """The seeds of ``profile`` (an Enterprise policy's session), in the data file's order."""
        if not isinstance(profile, dict):
            return ProfileReport(rejected="not_an_object")
        problem = _bounds_problem(profile, self.map.limits)
        if problem is not None:
            return ProfileReport(rejected=problem)
        doc: dict[str, object] = profile  # pyright: ignore[reportUnknownVariableType]
        evidence = texts.say(self.map.copy_ids.evidence, lang)
        seeds: list[Seed] = []
        dropped = self._unknown_keys(doc)
        for m in self.map.mappings:
            raw = _lookup(doc, m.path)
            if raw is _MISSING:
                continue
            value, lost = self._convert(m, raw, policy)
            dropped += lost
            if value not in (None, "", []):
                seeds.append(Seed(m.slot, value, evidence))
        return ProfileReport(tuple(seeds), dropped)

    # ----------------------------------------------------------------- unknown keys
    def _unknown_keys(self, doc: dict[str, object]) -> int:
        count = 0
        for key, node in doc.items():
            known = self._sections.get(key)
            if known is None:
                count += 1
            elif known and isinstance(node, dict):  # a section: its own unknown keys
                count += sum(1 for inner in node if inner not in known)  # pyright: ignore[reportUnknownVariableType]
        return count

    # ----------------------------------------------------------------- one value
    def _allowed(self, m: ProfileMapping) -> list[str] | None:
        return list(m.values) if m.values is not None else self.vocab.allowed(m.slot)

    def _convert(self, m: ProfileMapping, raw: object, policy: Policy) -> tuple[SlotData, int]:
        """``(value or None, number of things dropped)``."""
        if m.kind in ("text", "url"):
            text = self._text(m, raw, policy)
            return text, 0 if text is not None else 1
        if m.kind == "color":
            color = self._color(raw)
            return color, 0 if color is not None else 1
        if m.kind == "choice":
            choice = self._choice(self._allowed(m) or [], raw)
            return choice, 0 if choice is not None else 1
        return self._items(m, raw, policy)

    def _text(self, m: ProfileMapping, raw: object, policy: Policy) -> str | None:
        limit = m.max_chars or 0
        if not isinstance(raw, str) or len(raw) > limit * 4:
            return None
        screened = self.safety.screen(raw)
        if screened.had_secret or screened.injection or screened.truncated:
            return None
        text = " ".join(screened.text.split())
        if not text or len(text) > limit:
            return None
        if any(not ch.isprintable() or ch in _MARKUP for ch in text):
            return None
        if self.guard.scan(text, policy):  # a banned library named in the text
            return None
        if m.kind == "url" and not self._is_site(text):
            return None
        return text

    @staticmethod
    def _is_site(text: str) -> bool:
        """An https link to a named host, without credentials (a profile never carries a login)."""
        if not text.isascii() or " " in text:
            return False
        try:
            parts = urlsplit(text)
            host = parts.hostname
            parts.port  # noqa: B018 (raises ValueError on a bad port)
        except ValueError:
            return False
        if parts.scheme != "https" or not host or parts.username or parts.password:
            return False
        return "." in host and not host.replace(".", "").isdigit()

    @staticmethod
    def _color(raw: object) -> str | None:
        if not isinstance(raw, str) or len(raw) > MAX_ID_CHARS:
            return None
        text = raw.strip()
        if _HEX_COLOR.fullmatch(text):
            return text.upper()
        word = lx.normalize(text)
        return word if word in lx.load_lexicon().color_words else None

    @staticmethod
    def _choice(allowed: list[str], raw: object) -> str | None:
        if not isinstance(raw, str) or len(raw) > MAX_ID_CHARS:
            return None
        lookup = {a.casefold(): a for a in allowed}
        return lookup.get(raw.strip().casefold())

    def _items(self, m: ProfileMapping, raw: object, policy: Policy) -> tuple[SlotData, int]:
        if not isinstance(raw, list):
            return None, 1
        items: list[object] = list(raw)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        cap = self.map.limits.max_list_items
        dropped = max(0, len(items) - cap)
        allowed = self._allowed(m) or []
        found: list[str] = []
        for item in items[:cap]:
            canonical = self._choice(allowed, item)
            if canonical is None:
                dropped += 1
            elif canonical not in found:
                found.append(canonical)
        if m.check == "regions":
            outside = self.guard.disallowed_regions(found, policy)
            dropped += len(outside)
            found = [r for r in found if r not in outside]
        return found, dropped


def map_profile(
    profile: object,
    banks: QuestionBanks,
    policy: Policy,
    safety: SafetyScreen | None = None,
    *,
    vocab: Vocab | None = None,
    guard: PolicyGuard | None = None,
    lang: str | None = "en",
) -> list[Seed]:
    """The seeds of an Org Profile for a session under ``policy`` (the pure entry point).

    ``vocab`` and ``guard`` default to ones built from ``banks`` and the shipped data; the engine
    passes its own so nothing is built twice.
    """
    mapper = OrgProfileMapper(
        vocab or Vocab(banks, load_catalogs()), guard or PolicyGuard(), safety or SafetyScreen()
    )
    return list(mapper.read(profile, policy, lang).seeds)
