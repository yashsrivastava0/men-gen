"""CardRenderer: bank cards as ``CardInstance`` objects, localised, plus the situation cards.

The cards themselves are content (``mde_content``). This module only localises them
(``mde_content.questions.localize``), pre-ticks a checklist from a volunteered value, attaches the
progress counter and builds the three cards no bank file carries: the "describe the look" text
card, the "finish with smart defaults" offer (from the bank's nudge card) and the re-roll cards
for "none of these".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from mde_content.questions import QuestionBanks, localize
from mde_contracts.cards import Card, CardInstance, CardProgress, OtherInput, SkipSpec
from mde_contracts.common import Mode
from mde_contracts.policy import Policy
from mde_discovery import copy as texts
from mde_discovery.slots import SlotStore

DESCRIBE_LOOK_ID = "Q-gen-describe-look"
FINISH_NUDGE_ID = "Q-nudge-02"
LOOK_SLOT = "design.direction"
COUNTED_KINDS = ("standard", "compliance", "resolution")


class CardRenderer:
    def __init__(
        self, banks: QuestionBanks, ids: Callable[[str], str], now: Callable[[], datetime]
    ) -> None:
        self.banks = banks
        self._ids = ids
        self._now = now

    def render(
        self,
        card: Card,
        *,
        lang: str,
        mode: Mode,
        policy: Policy,
        store: SlotStore,
        asked: int,
        generated: bool = False,
    ) -> CardInstance:
        """A shown instance of ``card`` in ``lang`` (a volunteered checklist is pre-ticked)."""
        shown = localize(card, lang)
        if card.type == "multi":
            current = store.list_value(card.slot)
            if any(o.id in current for o in shown.options):  # no match keeps the card's defaults
                options = [
                    o.model_copy(update={"recommended": o.id in current}) for o in shown.options
                ]
                shown = shown.model_copy(update={"options": options})
        elif card.type in ("choice3", "visual3"):
            shown = self._prefer_org_profile(shown, store)
        progress = None
        if mode == "guided" and card.kind in COUNTED_KINDS:
            progress = CardProgress(asked=asked, cap=policy.limits.max_cards)
        return CardInstance(
            instance_id=self._ids("crd"),
            card=shown,
            shown_at=self._now(),
            language=lang,
            progress=progress,
            generated=generated,
        )

    @staticmethod
    def _prefer_org_profile(shown: Card, store: SlotStore) -> Card:
        """A choice card whose slot holds an Org Profile value recommends that option.

        The profile's value is a proposal (spec 3.5), so it is the highlighted option and what
        "You decide" would take; the user can still tap any other. A value that no single option
        writes leaves the card as the bank wrote it.
        """
        held = store.get(shown.slot)
        if held is None or held.source != "org_profile" or held.state == "empty":
            return shown
        match = [o for o in shown.options if o.sets.get(shown.slot) == held.value]
        if len(match) != 1:
            return shown
        options = [o.model_copy(update={"recommended": o.id == match[0].id}) for o in shown.options]
        update: dict[str, object] = {"options": options}
        if shown.skip.enabled:
            update["skip"] = shown.skip.model_copy(update={"assumes": match[0].id})
        return shown.model_copy(update=update)

    # ----------------------------------------------------------------- situation cards
    def describe_look_card(self, lang: str) -> Card:
        """Free text (or an upload) for the look, mapped to the nearest preset by the engine."""
        return Card(
            id=DESCRIBE_LOOK_ID,
            slot=LOOK_SLOT,  # type: ignore[arg-type]
            type="text",
            kind="help",
            text=texts.say("describe_look", lang),
            other=OtherInput(enabled=True, placeholder=None),
            skip=SkipSpec(enabled=True),
            ask_if="false",
        )

    def finish_offer_card(self, lang: str, *, only_finish: bool) -> Card:
        """The bank's "skipped a few, finish with smart defaults?" card (just the finish option at a cap)."""
        card = localize(self.banks.card(FINISH_NUDGE_ID), lang)
        if only_finish:
            card = card.model_copy(
                update={"options": [o for o in card.options if o.id == "finish"]}
            )
        return card

    def pool_card(
        self, slot_id: str, archetype: str | None, mode: Mode, seen: list[str]
    ) -> Card | None:
        """A visual card with the options not shown yet (the bank's pool card), or None."""
        for card in self.banks.cards_for_slot(slot_id, archetype, mode, kinds=("standard",)):
            if card.type != "visual3" or card.ask_if != "false":
                continue
            fresh = [o for o in card.options if o.sets.get(slot_id) not in seen]
            if fresh:
                return card.model_copy(update={"options": fresh})
        return None

    @staticmethod
    def shown_values(card: Card) -> list[str]:
        """The slot values a card offers (theme ids for a look card), to remember what was seen."""
        return [
            str(v)
            for o in card.options
            for k, v in o.sets.items()
            if k == card.slot and isinstance(v, str)
        ]
