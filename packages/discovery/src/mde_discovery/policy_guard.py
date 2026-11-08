"""PolicyGuard: company rules (``Policy.banned_libraries``, ``Policy.allowed_regions``) against a request.

Spec section 9, "Enterprise: request violates org policy (banned library, data region)": explain the
constraint and show the allowed alternatives as a card. This module is the cheap, deterministic half
(no LLM call): it recognises a banned library or a region the policy does not allow in the user's
words and in the ``regions`` slot, and it knows what can be offered instead.

Everything that varies is data. The policy says what is banned or allowed; how people name a
library or a region, and what to offer instead, is ``mde_content/data/policy_alternatives.yaml``
(aliases, alternatives and labels in English, Hindi and Hinglish). Matching is case-insensitive and
whole-word (``lx.normalize``): ``Code-Push`` and ``code push`` both name ``react-native-code-push``
through its aliases, ``codepusher`` does not. No company, library or region is named in code.

What is recognised:

* a banned library, by its policy id and its aliases, in any typed text or document;
* a region id (``us-east``, ``eu-west-1``) in typed text, from the table and the cloud-style pattern
  of the data file;
* a region in the ``regions`` slot: a group value (``in``, ``eu``, ``us``, ``other``; fine when some
  allowed region belongs to that group) or a region id the policy does not list.

A country or a city in running text ("customers in Texas") is not read as a data-region request
here; when the understander turns it into a ``regions`` value, the slot check catches it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from mde_content.questions import resolve_text
from mde_contracts.policy import Policy
from mde_prompts.calls.c1_understand import lexicon as lx

Text3 = dict[str, str]  # language tag -> text (en, hi, hi-Latn)
_TRAILING_DIGITS = re.compile(r"-\d+$")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Alternative(_Model):
    id: str
    label: Text3
    description: Text3 = {}
    phrase: Text3 = {}  # replaces the mention inside a sentence


class LibraryEntry(_Model):
    aliases: list[str] = []
    match_name: bool = True  # False: the bare id is an everyday word, only aliases count
    alternatives: list[Alternative] = []


class GenericEntry(_Model):
    alternatives: list[Alternative]


class RegionEntry(_Model):
    id: str
    group: str
    label: Text3
    aliases: list[str] = []


class PolicyData(_Model):
    version: int = 1
    libraries: dict[str, LibraryEntry] = {}
    generic: GenericEntry
    regions: list[RegionEntry] = []
    region_patterns: list[str] = []
    prefix_groups: dict[str, str] = {}


@lru_cache(maxsize=1)
def load_policy_data() -> PolicyData:
    """Read and validate ``data/policy_alternatives.yaml`` of the content package (cached)."""
    text = (
        resources.files("mde_content")
        .joinpath("data/policy_alternatives.yaml")
        .read_text(encoding="utf-8")
    )
    return PolicyData.model_validate(yaml.safe_load(text))


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Violation:
    """One thing the request asks for that the company rules do not allow."""

    kind: Literal["library", "region"]
    keys: tuple[str, ...]  # the policy's library id, or the region ids that are not allowed

    def to_json(self) -> dict[str, object]:
        return {"kind": self.kind, "keys": list(self.keys)}

    @staticmethod
    def from_json(raw: object) -> Violation | None:
        if not isinstance(raw, dict):
            return None
        kind, keys = raw.get("kind"), raw.get("keys")
        if not isinstance(keys, list) or not keys:
            return None
        names = tuple(str(k) for k in keys)
        if kind == "library":
            return Violation("library", names)
        if kind == "region":
            return Violation("region", names)
        return None


@dataclass(frozen=True)
class AlternativeView:
    """An allowed alternative, in the user's language."""

    id: str
    label: str
    description: str
    phrase: str


def _pick(table: Text3, lang: str | None, fallback: str = "") -> str:
    return resolve_text(table, lang, fallback) if table else fallback


def _slug(value: str) -> str:
    return lx.normalize(value)


def _region_base(region_id: str) -> str:
    """``US-East-1`` as ``us-east``: case and a trailing zone number do not matter."""
    return _TRAILING_DIGITS.sub("", region_id.strip().casefold())


@lru_cache(maxsize=512)
def _term_regex(term: str) -> re.Pattern[str]:
    """The term as it may be written: its words joined by any punctuation, whole-word, any case."""
    words = [re.escape(w) for w in lx.normalize(term).split()]
    return re.compile(r"(?<!\w)" + r"[\W_]+".join(words) + r"(?!\w)", re.IGNORECASE)


class PolicyGuard:
    """Recognises policy violations and offers the allowed alternatives. Pure and deterministic."""

    def __init__(self, data: PolicyData | None = None) -> None:
        self.data = data or load_policy_data()
        self._libraries = {_slug(k): v for k, v in self.data.libraries.items()}
        self._regions = {_region_base(r.id): r for r in self.data.regions}
        self._patterns = [re.compile(p, re.IGNORECASE) for p in self.data.region_patterns]
        self._groups = {r.group for r in self.data.regions} | set(self.data.prefix_groups.values())

    # ----------------------------------------------------------------- is there anything to check
    @staticmethod
    def active(policy: Policy) -> bool:
        return bool(policy.banned_libraries or policy.allowed_regions)

    # ----------------------------------------------------------------- libraries
    def library_terms(self, key: str) -> list[str]:
        """Every way the library is named: its policy id (unless it is an everyday word) and aliases."""
        entry = self._libraries.get(_slug(key))
        terms = [key] if entry is None or entry.match_name else []
        if entry is not None:
            terms.extend(entry.aliases)
        return terms

    def names_library(self, text: str, key: str) -> bool:
        return bool(lx.keyword_hits(text, self.library_terms(key)))

    def scan_libraries(self, text: str, policy: Policy) -> list[Violation]:
        """One violation per banned library the text names (in the policy's order)."""
        return [
            Violation("library", (key,))
            for key in dict.fromkeys(policy.banned_libraries)
            if self.names_library(text, key)
        ]

    def alternatives(self, key: str, lang: str | None) -> list[AlternativeView]:
        """Allowed alternatives for a banned library: its own (data), then the generic ones.

        At most three, the first is the recommended one. A library the data does not know gets the
        generic alternatives (an approved option, or ask the IT team).
        """
        entry = self._libraries.get(_slug(key))
        own = list(entry.alternatives) if entry is not None else []
        generic = self.data.generic.alternatives
        chosen = [*own, *generic[-1:]] if own else list(generic)
        return [
            AlternativeView(
                id=a.id,
                label=_pick(a.label, lang),
                description=_pick(a.description, lang),
                phrase=_pick(a.phrase, lang, _pick(a.label, lang)),
            )
            for a in chosen[:3]
        ]

    def replace_library(self, text: str, key: str, phrase: str) -> str:
        """``text`` with every mention of the library replaced by ``phrase``."""
        out = text
        for term in sorted(self.library_terms(key), key=len, reverse=True):
            if lx.normalize(term):
                out = _term_regex(term).sub(lambda _m: phrase, out)
        return out

    # ----------------------------------------------------------------- regions
    def _group_of(self, region_id: str) -> str:
        base = _region_base(region_id)
        known = self._regions.get(base)
        if known is not None:
            return known.group
        if base in self._groups:
            return base
        return self.data.prefix_groups.get(base.split("-")[0], "other")

    def region_allowed(self, region_id: str, policy: Policy) -> bool:
        """The region (a region id, or a region group such as ``eu``) is within the policy."""
        if not policy.allowed_regions:
            return True
        allowed = {_region_base(r) for r in policy.allowed_regions}
        base = _region_base(region_id)
        if base in allowed:
            return True
        if base in self._groups:  # a group is fine when some allowed region belongs to it
            return any(self._group_of(a) == base for a in allowed)
        return self._group_of(base) in (allowed & self._groups)  # a fine id inside an allowed group

    def disallowed_regions(self, items: list[str], policy: Policy) -> list[str]:
        """The items of a ``regions`` value the policy does not allow, in order."""
        return [i for i in dict.fromkeys(items) if not self.region_allowed(i, policy)]

    def regions_in_text(self, text: str) -> list[str]:
        """Region ids named in the text (``us-east``, ``eu-west-1``), normalised, in order."""
        found: dict[int, str] = {}
        for pattern in self._patterns:
            for m in pattern.finditer(text):
                found[m.start()] = _region_base(m.group(0))
        for base, entry in self._regions.items():
            terms = [entry.id, *entry.aliases]
            for term in terms:
                for m in _term_regex(term).finditer(text):
                    found.setdefault(m.start(), base)
        return list(dict.fromkeys(found[pos] for pos in sorted(found)))

    def scan_regions(self, text: str, policy: Policy) -> list[Violation]:
        if not policy.allowed_regions:
            return []
        bad = self.disallowed_regions(self.regions_in_text(text), policy)
        return [Violation("region", tuple(bad))] if bad else []

    def region_label(self, region_id: str, lang: str | None) -> str | None:
        """The localised name of a region the data file knows, else None."""
        entry = self._regions.get(_region_base(region_id))
        return _pick(entry.label, lang) if entry is not None else None

    # ----------------------------------------------------------------- everything at once
    def scan(self, text: str, policy: Policy) -> list[Violation]:
        """Every violation in a piece of text (libraries first, then regions)."""
        if not self.active(policy) or not text.strip():
            return []
        return [*self.scan_libraries(text, policy), *self.scan_regions(text, policy)]
