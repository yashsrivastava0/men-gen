"""Slot records: the working value of one catalog slot before it lands in the Brief.

Slot ids are plain strings validated at runtime against ``catalog/slots.yaml`` (the catalog is data;
``mde_contracts`` has a closed slot vocabulary that Compass deliberately does not use).
"""

from __future__ import annotations

from pydantic import JsonValue

from mde_compass.domain.common import ItemMeta


class SlotRecord(ItemMeta):
    slot_id: str
    value: JsonValue = None
    turn_id: str | None = None
    contributor_id: str | None = None
    custom_text: str | None = None  # the user's own words next to the normalized candidate
    needs_confirmation: bool = False  # custom item or voice echo awaiting a yes
