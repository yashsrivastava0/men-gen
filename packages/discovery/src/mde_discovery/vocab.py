"""Slot vocabulary derived from the content bank: allowed values, labels, hints.

Nothing is hardcoded: allowed values are what the bank's cards can write (their ``sets``), the
archetype ids and the theme ids; labels are the option labels of those same cards in the user's
language. A slot no card writes (a name, a locale) is free text.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from mde_content.questions import QuestionBanks, lang_chain, localize
from mde_content.themes import load_themes
from mde_contracts.cards import Card, CardOption
from mde_contracts.content import Catalogs
from mde_contracts.slots import SlotData
from mde_discovery import copy as texts
from mde_prompts.calls.c1_understand import lexicon as lx

MAX_TEXT = 300


def _key(value: SlotData) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def humanize(value: str) -> str:
    if " " in value:
        return value
    spaced = value.replace("_", " ").replace("-", " ").strip()
    return spaced[:1].upper() + spaced[1:]


class Vocab:
    """Allowed values and labels for every slot, built once from banks and catalogs."""

    def __init__(self, banks: QuestionBanks, catalogs: Catalogs) -> None:
        self.banks = banks
        self.catalogs = catalogs
        self._kind = {s.slot_id: s.value_kind for s in catalogs.slots}
        self._default = {s.slot_id: s.default for s in catalogs.slots}
        self._index: dict[tuple[str, str], list[tuple[Card, CardOption]]] = {}
        self._scalars: dict[str, list[str]] = {}
        self._items: dict[str, list[str]] = {}
        for card in banks.all_cards():
            for option in card.options:
                for slot, value in option.sets.items():
                    self._index.setdefault((slot, _key(value)), []).append((card, option))
                    if isinstance(value, list):
                        bucket = self._items.setdefault(slot, [])
                        bucket.extend(v for v in value if v not in bucket)
                    elif isinstance(value, str):
                        bucket = self._scalars.setdefault(slot, [])
                        if value not in bucket:
                            bucket.append(value)
            if card.type == "multi":
                bucket = self._items.setdefault(card.slot, [])
                bucket.extend(o.id for o in card.options if o.id not in bucket)
        self._archetypes = [a.id for a in catalogs.archetypes]
        self._themes = {t.id: t.label for t in load_themes().themes}
        self._domains = {d.id: d for d in catalogs.sensitive_domains}

    # ----------------------------------------------------------------- allowed values
    def kind(self, slot_id: str) -> str:
        return self._kind.get(slot_id, "text")

    def feature_ids(self, archetype: str | None) -> list[str]:
        if archetype:
            return [fid for fid, _ in lx.feature_options(archetype)]
        out: list[str] = []
        for a in self._archetypes:
            out.extend(f for f, _ in lx.feature_options(a) if f not in out)
        return out

    def app_ids(self, archetype: str | None) -> list[str]:
        """Apps a card of this archetype can pick (empty when the archetype has no apps card)."""
        out: list[str] = []
        for card in self.banks.cards_for_slot("scope.apps", archetype, "guided"):
            for option in card.options:
                value = option.sets.get("scope.apps")
                if isinstance(value, list):
                    out.extend(v for v in value if v not in out)
        return out

    def allowed(self, slot_id: str, archetype: str | None = None) -> list[str] | None:
        """The values a slot may hold (items for a list slot), or None when it is free text."""
        if slot_id == "idea.archetype":
            return list(self._archetypes)
        if slot_id == "design.direction":
            return list(self._themes)
        if slot_id == "scope.features":
            return self.feature_ids(archetype)
        if slot_id == "scope.apps":
            return ["main", *self.app_ids(archetype)] if archetype else None
        if slot_id == "compliance.flags":
            flags = [f for d in self._domains.values() for f in d.compliance_flags]
            return [*dict.fromkeys(flags), "needs_expert_review"]
        kind = self.kind(slot_id)
        if kind == "list":
            return list(self._items.get(slot_id, [])) or None
        if kind == "choice":
            values = list(self._scalars.get(slot_id, []))
            default = self._default.get(slot_id)
            if isinstance(default, str) and default not in values:
                values.append(default)
            return values or None
        return None

    def validate(
        self, slot_id: str, value: SlotData, archetype: str | None = None
    ) -> SlotData | None:
        """The value as the slot would hold it, or None when it does not fit the vocabulary."""
        kind = self.kind(slot_id)
        allowed = self.allowed(slot_id, archetype)
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().casefold() in (
                "true",
                "yes",
                "false",
                "no",
            ):
                return value.strip().casefold() in ("true", "yes")
            return None
        if kind == "list":
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                return None
            items = [str(v).strip() for v in value if str(v).strip()]
            if allowed is not None:
                lookup = {a.casefold(): a for a in allowed}
                items = [lookup[i.casefold()] for i in items if i.casefold() in lookup]
            items = list(dict.fromkeys(items))
            return items or None
        if isinstance(value, list | bool) or value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if kind == "choice" and allowed is not None:
            lookup = {a.casefold(): a for a in allowed}
            return lookup.get(text.casefold())
        return text[:MAX_TEXT]

    # ----------------------------------------------------------------- labels
    def _lookup(
        self, slot_id: str, value: SlotData, archetype: str | None
    ) -> tuple[Card, CardOption] | None:
        """The card option that writes ``value`` (preferring the user's own archetype's card)."""
        hits = self._index.get((slot_id, _key(value)), [])
        for card, option in hits:
            if not archetype or not card.archetypes or archetype in card.archetypes:
                return card, option
        return hits[0] if hits else None

    def _option_label(self, card: Card, option: CardOption, lang: str | None) -> str:
        localized = localize(card, lang)
        for o in localized.options:
            if o.id == option.id:
                return o.label
        return option.label

    def _item_label(
        self, slot_id: str, item: str, lang: str | None, archetype: str | None = None
    ) -> str:
        for card in self.banks.all_cards():
            if archetype and card.archetypes and archetype not in card.archetypes:
                continue
            if card.slot == slot_id and card.type == "multi":
                for option in card.options:
                    if option.id == item:
                        return self._option_label(card, option, lang)
        hit = self._lookup(slot_id, [item], archetype)
        if hit:
            return self._option_label(*hit, lang)
        return humanize(item)

    def archetype_label(self, archetype: str, lang: str | None) -> str:
        for a in self.catalogs.archetypes:
            if a.id == archetype:
                for tag in lang_chain(lang):
                    if tag in a.i18n and "label" in a.i18n[tag]:
                        return a.i18n[tag]["label"]
                return a.label
        return humanize(archetype)

    def label(
        self, slot_id: str, value: SlotData, lang: str | None = "en", archetype: str | None = None
    ) -> str:
        """Plain-language name of a slot value in the user's language."""
        if isinstance(value, bool):
            return texts.say("on" if value else "off", lang)
        if value is None or value == "":
            return ""
        if isinstance(value, list):
            hit = self._lookup(slot_id, value, archetype)
            if hit and slot_id != "scope.features":
                return self._option_label(*hit, lang)
            return ", ".join(self._item_label(slot_id, str(v), lang, archetype) for v in value)
        if slot_id == "idea.archetype":
            return self.archetype_label(str(value), lang)
        if slot_id == "design.direction" and str(value) in self._themes:
            hit = self._lookup(slot_id, value, archetype)
            return self._option_label(*hit, lang) if hit else self._themes[str(value)]
        hit = self._lookup(slot_id, value, archetype)
        if hit:
            return self._option_label(*hit, lang)
        return humanize(str(value)) if self.kind(slot_id) == "choice" else str(value)

    # ----------------------------------------------------------------- prompts
    def hint(self, slot_id: str, archetype: str | None = None) -> str:
        allowed = self.allowed(slot_id, archetype)
        kind = self.kind(slot_id)
        if kind == "bool":
            return "true or false"
        if allowed:
            shown = ", ".join(allowed[:14])
            return f"list of: {shown}" if kind == "list" else f"one of: {shown}"
        return "list of short ids" if kind == "list" else "short text"

    def themes(self) -> dict[str, str]:
        return dict(self._themes)

    def domains(self) -> Iterable[str]:
        return self._domains.keys()
