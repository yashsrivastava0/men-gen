"""QuestionSelector: which card comes next (spec 3.4 "Order", 3.1 caps, 3.3 levels).

Code decides, data answers: the order is ``SlotDef.order`` (the catalog encodes the dependency
order), the cards come from the question bank, preconditions are the safe ``ask_if`` language.
A slot is *settled* when it is confirmed, or assumed by a default or skip (never asked twice), or
volunteered by the user and no card asks to confirm or tick it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mde_content.expr import holds, referenced_slots
from mde_content.questions import QuestionBanks
from mde_contracts.cards import Card
from mde_contracts.common import Mode
from mde_contracts.content import Catalogs
from mde_contracts.engines import DiscoveryState
from mde_contracts.policy import Policy
from mde_contracts.slots import SlotData, SlotDef, SlotLevel
from mde_discovery.slots import SlotStore, detect_contradiction

ALL_KINDS = ("standard", "compliance", "resolution", "help", "inspiration", "nudge")
IDEA_SLOT = "idea.summary"
COMPLIANCE_SLOT = "compliance.flags"
LOOK_SLOT = "design.direction"
MAX_VISUAL_CARDS_GUIDED = 2


@dataclass(frozen=True)
class Selection:
    kind: Literal["card", "write_card", "complete"]
    card: Card | None = None
    slot: str | None = None
    domain: str | None = None  # the sensitive domain a compliance card is about
    resolution: bool = False  # a contradiction card


def asked_base_ids(state: DiscoveryState) -> set[str]:
    """Card ids asked so far (a repeated card is stored as ``id:2``)."""
    return {entry.split(":")[0] for entry in state.cards_asked}


def limit_reached(state: DiscoveryState, mode: Mode, policy: Policy) -> bool:
    """Guided stops asking at the card or turn cap (10 and 30). Enterprise asks as many as needed."""
    if mode != "guided":
        return False
    return (
        state.card_count >= policy.limits.max_cards or state.turn_count >= policy.limits.max_turns
    )


class QuestionSelector:
    def __init__(self, banks: QuestionBanks, catalogs: Catalogs) -> None:
        self.banks = banks
        self.catalogs = catalogs
        ordered = sorted(catalogs.slots, key=lambda s: (s.order, s.slot_id))
        self._defs = ordered
        self._group_rank: dict[str, int] = {}
        for sd in ordered:
            self._group_rank.setdefault(sd.group, len(self._group_rank))
        self._domain_ids = [d.id for d in catalogs.sensitive_domains]
        self._domain_cache: dict[str, str | None] = {}

    # ----------------------------------------------------------------- levels
    @staticmethod
    def level(sd: SlotDef, mode: Mode, policy: Policy) -> SlotLevel:
        return policy.slot_policy.get(sd.slot_id) or sd.level[mode]

    def asked_levels(self, mode: Mode) -> tuple[SlotLevel, ...]:
        return ("must",) if mode == "guided" else ("must", "should")

    def slots_in_order(self, mode: Mode, policy: Policy) -> list[SlotDef]:
        """Must slots by order, then (Enterprise) should slots grouped by their group."""

        def key(sd: SlotDef) -> tuple[int, int, int, str]:
            lvl = self.level(sd, mode, policy)
            grouped = 0 if lvl == "must" else self._group_rank.get(sd.group, 0) + 1
            return (0 if lvl == "must" else 1, grouped, sd.order, sd.slot_id)

        return sorted(self._defs, key=key)

    def slot_def(self, slot_id: str) -> SlotDef:
        for sd in self._defs:
            if sd.slot_id == slot_id:
                return sd
        raise KeyError(slot_id)

    # ----------------------------------------------------------------- cards
    @staticmethod
    def needs_confirmation(card: Card, ctx: dict[str, SlotData]) -> bool:
        """A card that checks a detected value (its ``ask_if`` reads its own slot) or ticks a list.

        Only a card that still applies with the slot filled counts: the idea card
        (``idea.summary == null``) stops applying once the idea is known.
        """
        if card.kind != "standard" or not holds(card.ask_if, ctx):
            return False
        if card.type == "multi":
            return True
        return bool(card.ask_if) and card.slot in referenced_slots(card.ask_if)

    def domain_of(self, card: Card) -> str | None:
        """The sensitive domain a compliance card is about (found by evaluating its ``ask_if``)."""
        if card.id not in self._domain_cache:
            found: str | None = None
            reads_domains = bool(card.ask_if) and "sensitive.domains" in referenced_slots(
                card.ask_if
            )
            if card.kind == "compliance" and reads_domains:
                found = next(
                    (d for d in self._domain_ids if holds(card.ask_if, {"sensitive.domains": [d]})),
                    None,
                )
            self._domain_cache[card.id] = found
        return self._domain_cache[card.id]

    def standard_cards(self, slot_id: str, archetype: str | None, mode: Mode) -> list[Card]:
        return list(self.banks.cards_for_slot(slot_id, archetype, mode, kinds=("standard",)))

    def applicable_cards(
        self, slot_id: str, archetype: str | None, mode: Mode, ctx: dict[str, SlotData]
    ) -> list[Card]:
        return [c for c in self.standard_cards(slot_id, archetype, mode) if holds(c.ask_if, ctx)]

    def compliance_card(self, domain: str, archetype: str | None, mode: Mode) -> Card | None:
        for card in self.banks.cards_for_slot(
            COMPLIANCE_SLOT, archetype, mode, kinds=("compliance",)
        ):
            if self.domain_of(card) == domain:
                return card
        return None

    def _visual_asked(self, asked: set[str]) -> int:
        count = 0
        for card_id in asked:
            try:
                if self.banks.card(card_id).type == "visual3":
                    count += 1
            except KeyError:
                continue
        return count

    # ----------------------------------------------------------------- the choice
    def next(
        self, *, state: DiscoveryState, store: SlotStore, mode: Mode, policy: Policy
    ) -> Selection:
        """The next card to show, or ``complete`` when no slot is left to ask."""
        archetype = store.text_value("idea.archetype")
        ctx = store.context(state.sensitive_flags)
        asked = asked_base_ids(state)

        contradiction = detect_contradiction(
            self.banks, store, archetype, mode, state.sensitive_flags
        )
        if contradiction is not None:
            return Selection("card", contradiction, contradiction.slot, resolution=True)

        if not store.is_filled(IDEA_SLOT):
            cards = self.standard_cards(IDEA_SLOT, archetype, mode)
            if cards:
                return Selection("card", cards[0], IDEA_SLOT)
            return Selection("write_card", slot=IDEA_SLOT)

        defaults_path = state.extra.get("defaults_path") == "1"
        levels = self.asked_levels(mode)
        for sd in self.slots_in_order(mode, policy):
            if self.level(sd, mode, policy) not in levels or sd.slot_id == IDEA_SLOT:
                continue
            if not holds(sd.ask_if, ctx):
                continue
            must_confirm = mode in sd.must_confirm_in
            quiet = state.finished_with_defaults or (defaults_path and sd.slot_id != LOOK_SLOT)
            if quiet and not must_confirm:
                continue
            if sd.slot_id == COMPLIANCE_SLOT:
                picked = self._compliance(state, archetype, mode, asked)
                if picked is not None:
                    return picked
                continue
            picked = self._for_slot(sd, store, archetype, mode, ctx, asked, must_confirm)
            if picked is not None:
                return picked
        return Selection("complete")

    def _compliance(
        self, state: DiscoveryState, archetype: str | None, mode: Mode, asked: set[str]
    ) -> Selection | None:
        for domain in state.sensitive_flags:
            if domain in state.compliance_confirmed:
                continue
            card = self.compliance_card(domain, archetype, mode)
            if card is not None:  # must-confirm: asked again until it is answered
                return Selection("card", card, COMPLIANCE_SLOT, domain=domain)
        return None

    def _for_slot(
        self,
        sd: SlotDef,
        store: SlotStore,
        archetype: str | None,
        mode: Mode,
        ctx: dict[str, SlotData],
        asked: set[str],
        must_confirm: bool,
    ) -> Selection | None:
        state = store.state(sd.slot_id)
        if state in ("confirmed", "disputed"):
            return None
        if state == "assumed" and not must_confirm:
            return None
        applicable = self.applicable_cards(sd.slot_id, archetype, mode, ctx)
        if state == "volunteered":
            applicable = [c for c in applicable if self.needs_confirmation(c, ctx)]
            if not applicable:
                return None
        candidates = [c for c in applicable if must_confirm or c.id not in asked]
        if mode == "guided" and self._visual_asked(asked) >= MAX_VISUAL_CARDS_GUIDED:
            candidates = [c for c in candidates if c.type != "visual3"]
        if candidates:
            return Selection("card", candidates[0], sd.slot_id)
        has_any = self.banks.cards_for_slot(sd.slot_id, archetype, mode, kinds=ALL_KINDS)
        if not has_any and state == "empty":
            return Selection("write_card", slot=sd.slot_id)
        return None

    # ----------------------------------------------------------------- assumptions
    def assumption(
        self,
        slot_id: str,
        archetype: str | None,
        mode: Mode,
        ctx: dict[str, SlotData],
        card: Card | None = None,
    ) -> list[tuple[str, SlotData]]:
        """What "You decide", a skip or "finish with defaults" writes for a slot.

        The card's recommended option (or ``skip.assumes``); the pre-ticked set for a checklist; the
        slot's default for a text card or when no card applies.
        """
        sd = self.slot_def(slot_id)
        if card is None:
            usable = self.applicable_cards(slot_id, archetype, mode, ctx)
            card = usable[0] if usable else None
        if card is not None and card.type == "multi":
            ticked: SlotData = [o.id for o in card.options if o.recommended]
            return [(card.slot, ticked)] if ticked else self._default(sd)
        if card is not None and card.options:
            wanted = card.skip.assumes
            option = next((o for o in card.options if o.id == wanted), None) or next(
                (o for o in card.options if o.recommended), None
            )
            if option is not None and option.sets:
                return list(option.sets.items())
        return self._default(sd)

    @staticmethod
    def _default(sd: SlotDef) -> list[tuple[str, SlotData]]:
        return [(sd.slot_id, sd.default)] if sd.default not in (None, "", []) else []
