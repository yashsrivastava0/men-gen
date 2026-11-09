"""Changing a Locked, tap-confirmed value (IV-02, DC-08): show what depends on it and ask first.

A ``brief_edit`` (or a chat change) that rewrites a Locked item is not applied at once: the engine shows a one
question card with an IMPACT PREVIEW (the consumers named by the catalog slot, "research, the blueprint") and
applies the change only on "Yes, change it". Edits of anything that is not Locked stay immediate. The card is a
``choose_one`` in the change-variants frame, so its answer goes through the same path as every other
variant pick (the option ``sets`` carry the new value). Pure functions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

import yaml
from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.domain.brief import DICT_SECTIONS, Brief
from mde_compass.domain.cards import (
    Card,
    CardOption,
    CardProgress,
    CustomInput,
    SkipSpec,
    YouDecide,
)
from mde_compass.domain.changes import PatchOp
from mde_compass.engine import assumptions as assumptions_mod
from mde_compass.engine.flows import data

APPLY_ID = "A"
KEEP_ID = "B"


@lru_cache(maxsize=1)
def _consumer_names() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(assumptions_mod.TEXT_PATH.read_text(encoding="utf-8"))
    return {
        str(k): {str(lang): str(v) for lang, v in names.items()}
        for k, names in raw["consumers"].items()
    }


def slot_for_path(catalog: Catalog, path: str) -> SlotDef | None:
    """The catalog slot whose Brief path is exactly ``path`` and that lives in a dict section."""
    for slot in catalog.slots.values():
        if slot.brief_path == path and path.split(".")[0] in DICT_SECTIONS:
            return slot
    return None


def _item(brief: Brief, path: str) -> Any:
    section, _, key = path.partition(".")
    holder = getattr(brief, section, None)
    return holder.get(key) if isinstance(holder, dict) else None


def is_locked(brief: Brief, path: str) -> bool:
    """The Brief item at ``path`` holds a value, is Locked and was confirmed by the user's answer (a tap or a
    typed answer to a card). Words the user volunteered themselves are theirs to edit freely, so they are not
    asked about again."""
    item = _item(brief, path)
    return bool(
        item is not None
        and item.tier == "Locked"
        and item.status == "confirmed"
        and item.has_value()
    )


def value_text(value: JsonValue) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(value_text(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {value_text(v)}" for k, v in value.items())
    return str(value)


def changed_locked(
    brief: Brief, catalog: Catalog, ops: Sequence[PatchOp]
) -> list[tuple[SlotDef, JsonValue]]:
    """The Locked slots that ``ops`` would change, with their new values; empty when the ops are not all plain
    ``set`` edits of dict-section slots (those apply at once, as before)."""
    out: list[tuple[SlotDef, JsonValue]] = []
    for op in ops:
        if op.op != "set":
            return []
        slot = slot_for_path(catalog, op.path)
        if slot is None:
            return []
        item = _item(brief, op.path)
        if (
            item is not None
            and is_locked(brief, op.path)
            and value_text(item.value) != value_text(op.value)
        ):
            out.append((slot, op.value))
    return out


def impact_names(slot: SlotDef, language: str) -> str:
    """ "research, the blueprint": who plans from this slot (from the catalog ``consumers``)."""
    names = _consumer_names()
    listed = [
        names[c].get(language) or names[c]["en"]
        for c in slot.consumers
        if c in names and c != "all"
    ]
    if not listed or "all" in slot.consumers:
        listed = [names["all"].get(language) or names["all"]["en"]]
    return ", ".join(dict.fromkeys(listed[: data.policy().locked_max_consumers]))


def build_card(
    brief: Brief,
    changes: Sequence[tuple[SlotDef, JsonValue]],
    *,
    catalog: Catalog,
    card_id: str,
    language: str,
    frame: str,
    section: str = "",
) -> Card:
    """The one-tap confirm card with the impact preview (the consumers that plan from it, and its section)."""
    t = catalog.t
    slot, new_value = changes[0]
    item = _item(brief, slot.brief_path)
    current = value_text(item.value if item is not None else None)
    labels = ", ".join(s.label.get(language) for s, _ in changes)
    consumers = ", ".join(dict.fromkeys(impact_names(s, language) for s, _ in changes))
    question = t(
        "flows.locked.question", language,
        label=labels, current=current[:80], new=value_text(new_value)[:80],
    )  # fmt: skip
    sets: dict[str, JsonValue] = {s.id: v for s, v in changes}
    options = [
        CardOption(id=APPLY_ID, label=t("flows.locked.apply", language), basis="your_words", sets=sets),
        CardOption(id=KEEP_ID, label=t("flows.locked.keep", language), recommended=True, reason=t("flows.locked.keep_reason", language)),
    ]  # fmt: skip
    return Card(
        id=card_id, frame=frame, slots=[s.id for s, _ in changes], type="choose_one",
        question=question, why_we_ask=t("flows.locked.impact", language, consumers=consumers, section=section or labels),
        options=options, custom_input=CustomInput(enabled=False), you_decide=YouDecide(enabled=False),
        skip=SkipSpec(enabled=False), progress=CardProgress(n=0, of_about=0), language=language,
        must_confirm=True,
    )  # fmt: skip


def ops_summary(ops: Sequence[PatchOp], catalog: Catalog) -> Mapping[str, JsonValue]:
    """``{slot_id: value}`` of plain slot ``set`` ops (for tests and logging)."""
    out: dict[str, JsonValue] = {}
    for op in ops:
        slot = slot_for_path(catalog, op.path)
        if op.op == "set" and slot is not None:
            out[slot.id] = op.value
    return out
