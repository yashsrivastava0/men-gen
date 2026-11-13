"""``applies_to`` validation (RV-16): every id an item points at must exist in the approved Brief or be NEW.

The Brief index is built from the ``ResearchInput``: features, roles, apps, modules, entities and flows by
their ids, and integrations by their normalized key (plus the integration needs research found). An entry
whose id is unknown is re-tagged ``NEW`` (Spec decides) rather than allowed to point at nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from mde_research_agent.domain.input import ResearchInput
from mde_research_agent.domain.research import AppliesTo

PREFIX_KIND = {"FTR": "FTR", "ROL": "ROL", "APP": "APP", "MOD": "MOD", "ENT": "ENT", "FLW": "FLW"}


@dataclass(frozen=True)
class BriefIndex:
    ids: dict[str, frozenset[str]] = field(default_factory=dict)  # kind -> ids
    integration_keys: frozenset[str] = frozenset()

    @classmethod
    def from_input(
        cls, inp: ResearchInput, extra_integration_keys: Iterable[str] = ()
    ) -> BriefIndex:
        from mde_research_agent.core.norm_keys import clean_key

        ids = {
            "FTR": frozenset(f.id for f in inp.features),
            "ROL": frozenset(r.id for r in inp.roles),
            "APP": frozenset(a.id for a in inp.apps),
            "MOD": frozenset(m.id for m in inp.modules),
            "ENT": frozenset(e.id for e in inp.entities),
            "FLW": frozenset(f.id for f in inp.flows),
        }
        keys = {clean_key(i.name) for i in inp.integrations} | {
            clean_key(k) for k in extra_integration_keys
        }
        return cls(ids=ids, integration_keys=frozenset(k for k in keys if k))

    def exists(self, kind: str, ident: str | None) -> bool:
        if kind == "NEW":
            return ident is None
        if kind == "INT":
            return ident in self.integration_keys
        return ident is not None and ident in self.ids.get(kind, frozenset())


def problem(entry: AppliesTo, index: BriefIndex) -> str | None:
    """Why ``entry`` is invalid (None when it is fine)."""
    if entry.kind == "NEW":
        return None if entry.id is None else "NEW takes no id"
    if entry.id is None:
        return f"{entry.kind} needs an id"
    if entry.kind != "INT" and not entry.id.startswith(entry.kind + "-"):
        return f"id {entry.id} does not belong to kind {entry.kind}"
    return None if index.exists(entry.kind, entry.id) else f"{entry.id} is not in the Brief"


def retag(entries: Iterable[AppliesTo], index: BriefIndex) -> tuple[list[AppliesTo], int]:
    """Entries with every invalid one re-tagged ``NEW`` (relation ``suggests``); how many were changed."""
    out: list[AppliesTo] = []
    changed = 0
    seen: set[tuple[str, str | None, str]] = set()
    for e in entries:
        fixed = e
        if problem(e, index):
            fixed = AppliesTo(kind="NEW", id=None, relation="suggests")
            changed += 1
        key = (fixed.kind, fixed.id, fixed.relation)
        if key not in seen:
            seen.add(key)
            out.append(fixed)
    return out, changed
