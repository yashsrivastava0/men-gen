"""Loader for ``data/catalogs.yaml``: archetypes, sensitive domains and the slot table."""

from __future__ import annotations

from collections.abc import Collection
from functools import lru_cache
from importlib import resources

import yaml

from mde_contracts.common import Mode
from mde_contracts.content import ArchetypeDef, Catalogs, SensitiveDomain
from mde_contracts.slots import SlotDef, SlotLevel


@lru_cache(maxsize=1)
def load_catalogs() -> Catalogs:
    """Parse and validate ``catalogs.yaml`` (cached; the models are frozen)."""
    text = (resources.files("mde_content") / "data" / "catalogs.yaml").read_text(encoding="utf-8")
    return Catalogs.model_validate(yaml.safe_load(text))


def slot_def(slot_id: str) -> SlotDef:
    """The definition of one slot; ``KeyError`` for an unknown id."""
    for slot in load_catalogs().slots:
        if slot.slot_id == slot_id:
            return slot
    raise KeyError(slot_id)


def archetype(archetype_id: str) -> ArchetypeDef:
    """One archetype by id; ``KeyError`` for an unknown id."""
    for item in load_catalogs().archetypes:
        if item.id == archetype_id:
            return item
    raise KeyError(archetype_id)


def archetype_ids() -> list[str]:
    return [a.id for a in load_catalogs().archetypes]


def sensitive_domain(domain_id: str) -> SensitiveDomain:
    """One sensitive domain by id; ``KeyError`` for an unknown id."""
    for item in load_catalogs().sensitive_domains:
        if item.id == domain_id:
            return item
    raise KeyError(domain_id)


def slots_for_mode(mode: Mode, levels: Collection[SlotLevel] | None = None) -> list[SlotDef]:
    """Slots in asking order (``order`` then id) for ``mode``.

    ``levels`` keeps only slots whose level in that mode is one of the given levels, for example
    ``("must",)`` for the Guided interview or ``("must", "should")`` for Enterprise.
    """
    chosen = [s for s in load_catalogs().slots if levels is None or s.level[mode] in levels]
    return sorted(chosen, key=lambda s: (s.order, s.slot_id))
