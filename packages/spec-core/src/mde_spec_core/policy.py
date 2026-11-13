"""Store-policy sheet loader (doc 15 7.2 and 7.4).

The sheet is data: dated store and platform rules and accessibility floors, each with a source page. Code here only
loads it, checks it is well formed, evaluates `applies_when` over a set of facts, and builds the provenance string
`policy:<id>@<date>` that lets a Locked item rest on policy evidence (V-33, V-17). No rule text lives in code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DATA_DIR

POLICY_PATH = DATA_DIR / "policy" / "store_policy_sheet.yaml"

Platform = Literal["apple", "google", "standard"]
EffectKind = Literal["screen", "behaviour", "requirement", "artifact", "constraint"]


class PolicyError(ValueError):
    pass


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Fact(_M):
    id: str
    description: str


class Predicate(_M):
    """One fact, or all / any of several predicates, or the negation of one."""

    fact: str | None = None
    all_: list[Predicate] | None = Field(default=None, alias="all")
    any_: list[Predicate] | None = Field(default=None, alias="any")
    not_: Predicate | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _exactly_one(self) -> Predicate:
        used = [x for x in (self.fact, self.all_, self.any_, self.not_) if x is not None]
        if len(used) != 1:
            raise PolicyError("a predicate has exactly one of fact, all, any, not")
        return self

    def facts_used(self) -> set[str]:
        if self.fact is not None:
            return {self.fact}
        if self.not_ is not None:
            return self.not_.facts_used()
        out: set[str] = set()
        for p in self.all_ or self.any_ or []:
            out |= p.facts_used()
        return out

    def evaluate(self, facts: dict[str, bool]) -> bool:
        """A fact that is not given is false, except `always`."""
        if self.fact is not None:
            return True if self.fact == "always" else bool(facts.get(self.fact, False))
        if self.not_ is not None:
            return not self.not_.evaluate(facts)
        if self.all_ is not None:
            return all(p.evaluate(facts) for p in self.all_)
        return any(p.evaluate(facts) for p in self.any_ or [])


class Effect(_M):
    kind: EffectKind
    ref: str
    detail: str


class Floor(_M):
    """A numeric accessibility floor. `value` is a string so it loads as an exact decimal, never a float."""

    metric: str
    value: str
    unit: str
    applies_to: str
    hard_minimum: str | None = None
    spacing: str | None = None


class PolicyRule(_M):
    id: str
    platform: Platform
    category: str
    statement: str
    source_url: str
    source_section: str = ""
    checked_on: str
    volatile: bool = False  # likely to change soon: re-check on a schedule
    applies_when: Predicate
    effect: Effect
    floor: Floor | None = None


class Omitted(_M):
    topic: str
    reason: str


class PolicySheet(_M):
    version: int
    checked_on: str
    facts: list[Fact]
    rules: list[PolicyRule]
    omitted_unverified: list[Omitted] = []

    @model_validator(mode="after")
    def _well_formed(self) -> PolicySheet:
        ids = [r.id for r in self.rules]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise PolicyError(f"duplicate policy ids: {dupes}")
        fact_ids = {f.id for f in self.facts}
        for r in self.rules:
            unknown = r.applies_when.facts_used() - fact_ids
            if unknown:
                raise PolicyError(f"{r.id} uses undeclared facts {sorted(unknown)}")
            if not r.source_url or not r.checked_on:
                raise PolicyError(f"{r.id} has no source_url or checked_on")
        return self

    def get(self, rule_id: str) -> PolicyRule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    def floors(self) -> list[PolicyRule]:
        return [r for r in self.rules if r.floor is not None]

    def applicable(
        self, facts: dict[str, bool], platform: Platform | None = None
    ) -> list[PolicyRule]:
        """Rules whose predicate holds for these facts, optionally for one platform (plus the standards)."""
        out = []
        for r in self.rules:
            if platform is not None and r.platform not in (platform, "standard"):
                continue
            if r.applies_when.evaluate(facts):
                out.append(r)
        return out


def load_policy_sheet(path: Path | None = None) -> PolicySheet:
    p = path or POLICY_PATH
    return PolicySheet.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_policy_sheet() -> PolicySheet:
    return load_policy_sheet()


def policy_ref(rule_id: str, sheet: PolicySheet | None = None) -> str:
    """`policy:<id>@<date>`: the provenance string a policy-Locked item carries (doc 15 7.4)."""
    rule = (sheet or default_policy_sheet()).get(rule_id)
    return f"policy:{rule.id}@{rule.checked_on}"


def parse_policy_ref(ref: str) -> tuple[str, str]:
    """Split `policy:<id>@<date>` into (id, date); raise PolicyError on any other shape."""
    if not ref.startswith("policy:") or "@" not in ref:
        raise PolicyError(f"not a policy reference: {ref!r}")
    rule_id, _, date = ref[len("policy:") :].partition("@")
    if not rule_id or not date:
        raise PolicyError(f"not a policy reference: {ref!r}")
    return rule_id, date


def policy_ref_is_current(ref: str, sheet: PolicySheet | None = None) -> bool:
    """True when the id exists in the sheet and the date is the rule's current check date."""
    rule_id, date = parse_policy_ref(ref)
    try:
        return (sheet or default_policy_sheet()).get(rule_id).checked_on == date
    except KeyError:
        return False
