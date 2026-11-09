"""``FlowHooks``: the thin mixin that applies the pure flow modules to a turn.

``Service`` inherits it; every method here is called from a one to three line hook in ``service.py``. The
class body under ``TYPE_CHECKING`` only tells the type checker what the host service provides.

Hooks, in the order a turn meets them:

* ``_flow_voice_remove``   a dictation read-back list is on screen and the user says "remove 2";
* ``_flow_filter_updates`` references are handled here, so the understander's own reference update is dropped;
* ``_flow_references``     named references are stored as ``assumed`` (never Locked on a mention), a named
                           business is checked first, "exactly like" gets one patterns-only warning;
* ``_flow_on_message``     "everything is a must", five or more apps, voice echo and numbered read-back;
* ``_flow_pick`` / ``_flow_special_card`` / ``_flow_card_answer``  the ranking and reference cards;
* ``_flow_locked_edit`` / ``_flow_locked_chat``  the impact-preview confirm for Locked values;
* ``reopen_slot``          the typed back-edge (one targeted question, at most ``max_back_edge_loops``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue

from mde_compass.domain import Card, PatchOp
from mde_compass.domain.events import CardEvent, Notice
from mde_compass.engine import voi as voi_mod
from mde_compass.engine.flows import (
    apps_scope,
    data,
    locked_edit,
    priorities,
    references,
    reopen,
    voice,
)
from mde_compass.engine.ports import Caller, TurnResult
from mde_compass.safety.screen import screen

if TYPE_CHECKING:
    from mde_compass.catalog import Catalog
    from mde_compass.config import CompassConfig
    from mde_compass.engine.ports import Ports
    from mde_compass.engine.service import _Turn
    from mde_compass.store.ports import Store
    from mde_core.clock import Clock


def _strings(raw: object) -> list[str]:
    return [str(x) for x in raw] if isinstance(raw, list) else []


class _SystemEvent:
    """The synthetic inbound event of a turn the engine starts itself (the back-edge)."""

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        self.contributor_id: str | None = None


class FlowHooks:
    if TYPE_CHECKING:
        catalog: Catalog
        config: CompassConfig
        ports: Ports | None
        store: Store
        clock: Clock

        def _t(self, turn: _Turn, key: str, **values: str | int) -> str: ...
        def _label(self, turn: _Turn, slot_id: str) -> str: ...
        def _prov(self, turn: _Turn, kind: str, quote: str = "") -> Any: ...
        def _question_n(self, turn: _Turn) -> int: ...
        def _rebuild(self, turn: _Turn) -> None: ...
        def _ctx(self, turn: _Turn) -> Any: ...
        async def _reshow_or_advance(self, turn: _Turn) -> None: ...
        async def _advance(self, turn: _Turn) -> None: ...
        async def _load(self, tenant: str, session_id: str, event: Any) -> _Turn: ...
        async def _commit(self, turn: _Turn) -> None: ...

    # ------------------------------------------------------------------ small helpers
    def _flow_card_id(self, turn: _Turn) -> str:
        return f"Q-{len(turn.asked_cards()) + 1:03d}"

    def _flow_slot(self, turn: _Turn, slot_id: str) -> tuple[JsonValue, str]:
        rec = turn.slots.get(slot_id)
        return (rec.value, rec.status) if rec is not None else (None, "empty")

    def _flow_refs(self, turn: _Turn) -> list[references.RefInfo]:
        """The references the slot holds, as plain data (claimed-own is read from the provenance quotes)."""
        rec = turn.slots.get(references.SLOT)
        if rec is None or not isinstance(rec.value, list):
            return []
        out: list[references.RefInfo] = []
        for item in rec.value:
            if not isinstance(item, dict) or not str(item.get("label", "")).strip():
                continue
            meta = item.get("_meta")
            if isinstance(meta, dict):
                prov = meta.get("provenance")
                quotes = (
                    [str(p.get("quote", "")) for p in prov if isinstance(p, dict)]
                    if isinstance(prov, list)
                    else []
                )
                status = str(meta.get("status", rec.status))
            else:
                quotes = [p.quote for p in rec.provenance]
                status = rec.status
            borrow = _strings(item.get("borrow"))
            avoid = _strings(item.get("avoid"))
            out.append(
                references.info_of(str(item["label"]).strip(), borrow, avoid, quotes, status)
            )
        return out

    # ------------------------------------------------------------------ references
    def _flow_filter_updates(self, updates: Sequence[Any]) -> list[Any]:
        """Reference updates from the understander are handled by ``_flow_references`` (assumed, then confirmed)."""
        return [u for u in updates if u.slot_id != references.SLOT]

    def _flow_references(self, turn: _Turn, names: Sequence[str], text: str) -> None:
        """Store the references this message names as ``assumed`` and say what needs saying, once."""
        found: list[tuple[str, str]] = [(n, n) for n in names]
        own = references.own_business(text)
        if own is not None and own[0].casefold() not in {n.casefold() for n in names}:
            found.append((own[0], own[1]))
        if not found:
            return
        rec = turn.slots.get(references.SLOT)
        items: list[Any] = (
            list(rec.value) if rec is not None and isinstance(rec.value, list) else []
        )
        have = {str(i.get("label", "")).casefold() for i in items if isinstance(i, dict)}
        new: list[tuple[str, str]] = []
        for label, quote in found:
            if label.casefold() in have:
                continue
            have.add(label.casefold())
            new.append((label, quote))
            items.append({"label": label, "borrow": [], "avoid": []})
        if not new:
            return
        turn.slots.write(
            references.SLOT, items, state="assumed", provenance=self._prov(turn, "user_text", text[:300]),
            confidence=0.5, turn_id=turn.turn_id,
        )  # fmt: skip
        for label, quote in new:
            claimed = references.own_business(quote) is not None or (
                own is not None and own[0] == label
            )
            if claimed:
                turn.notice(
                    "reference_check", self._t(turn, "flows.reference.check_own", name=label)
                )
            elif references.exact_copy_cue(text):
                turn.notice(
                    "patterns_only", self._t(turn, "flows.reference.patterns_only", name=label)
                )

    def _flow_asked_ref_labels(self, turn: _Turn) -> set[str]:
        out: set[str] = set()
        for card in turn.asked_cards():
            if references.flow_card(card):
                for opt in card.options:
                    label = (
                        references.confirm_label(card, opt.id)
                        if opt.id in (references.OWN_ID, references.LIKE_ID)
                        else None
                    )
                    if label:
                        out.add(label.casefold())
        return out

    # ------------------------------------------------------------------ which card is next
    def _flow_pick(
        self, turn: _Turn, natural: voi_mod.QueueItem | None
    ) -> voi_mod.QueueItem | None:
        """Put a due ranking or references card before the planner's next card (dependency order is kept:
        priorities come after features, references after priorities)."""
        frames = self.catalog.frames

        def order(item: voi_mod.QueueItem | None) -> int:
            return (
                frames[item.frame_id].order if item is not None and item.frame_id in frames else 99
            )

        asked = [c.frame for c in turn.asked_cards()]
        feats, _ = self._flow_slot(turn, priorities.FEATURES_SLOT)
        prio, prio_status = self._flow_slot(turn, priorities.SLOT)
        if order(natural) > frames[priorities.FRAME].order and priorities.is_due(
            features=feats,
            scope_priority=prio,
            scope_priority_status=prio_status,
            asked_frames=asked,
        ):
            return voi_mod.QueueItem(
                frame_id=priorities.FRAME, slots=[priorities.SLOT], action="ask", score=99.0,
                must_confirm=True, reason="forced first-release ranking",
            )  # fmt: skip
        pending = turn.pending_card()
        if (
            order(natural) > frames[references.FRAME].order
            and self._question_n(turn) < self.config.questions.hard_cap
            and not (pending is not None and pending.frame == references.FRAME)
        ):
            refs = self._flow_refs(turn)
            form_asked = any(
                c.frame == references.FRAME and c.type == "quick_form" for c in turn.asked_cards()
            )
            if references.pending_confirm(refs, self._flow_asked_ref_labels(turn)) or references.needs_form(refs, form_asked=form_asked):  # fmt: skip
                return voi_mod.QueueItem(
                    frame_id=references.FRAME, slots=[references.SLOT], action="ask", score=98.0,
                    must_confirm=False, reason="what to borrow and avoid from the named references",
                )  # fmt: skip
        return natural

    def _flow_special_card(
        self, turn: _Turn, item: voi_mod.QueueItem, *, n: int, of_about: int
    ) -> Card | None:
        """Cards built by code from the user's own words (no model call); None means the generic writer."""
        card_id = self._flow_card_id(turn)
        if item.frame_id == priorities.FRAME:
            feats, _ = self._flow_slot(turn, priorities.FEATURES_SLOT)
            if len(priorities.rankable(feats)) < data.policy().min_features:
                return None
            return priorities.build_card(feats, catalog=self.catalog, card_id=card_id, n=n, of_about=of_about, language=turn.lang)  # fmt: skip
        if item.frame_id == references.FRAME:
            refs = self._flow_refs(turn)
            pend = references.pending_confirm(refs, self._flow_asked_ref_labels(turn))
            if pend is not None:
                return references.confirm_card(pend, catalog=self.catalog, card_id=card_id, n=n, of_about=of_about, language=turn.lang)  # fmt: skip
            form_asked = any(
                c.frame == references.FRAME and c.type == "quick_form" for c in turn.asked_cards()
            )
            ready = references.needs_form(refs, form_asked=form_asked)
            if ready:
                return references.form_card(ready, catalog=self.catalog, card_id=card_id, n=n, of_about=of_about, language=turn.lang)  # fmt: skip
        return None

    def _flow_adjust_card(self, turn: _Turn, card: Card) -> Card:
        """The features checklist starts fully ticked once the user said everything is a must."""
        prio, status = self._flow_slot(turn, priorities.SLOT)
        if (
            card.frame == priorities.FEATURES_FRAME
            and card.type == "pick_many"
            and priorities.signal_present(prio, status)
        ):
            return priorities.tick_all(card, self._t(turn, "flows.priority.ticked_reason"))
        return card

    # ------------------------------------------------------------------ answers to those cards
    async def _flow_card_answer(self, turn: _Turn, card: Card, ev: Any) -> bool:
        """Apply the answer to a ranking or reference card built here; False leaves it to the generic path."""
        if priorities.flow_card(card):
            self._flow_priority_answer(turn, card, ev)
        elif references.flow_card(card):
            self._flow_reference_answer(turn, card, ev)
        else:
            return False
        self._rebuild(turn)
        await self._advance(turn)
        return True

    def _flow_priority_answer(self, turn: _Turn, card: Card, ev: Any) -> None:
        placement = priorities.place(card, ev.value)
        feats, _ = self._flow_slot(turn, priorities.FEATURES_SLOT)
        prov = self._prov(turn, "user_tap", "")
        turn.slots.write(
            priorities.FEATURES_SLOT, priorities.apply_placement(feats, placement), state="confirmed",
            provenance=prov, confidence=0.9, turn_id=turn.turn_id,
        )  # fmt: skip
        turn.slots.write(
            priorities.SLOT, priorities.SIGNAL_VALUE, state="confirmed", provenance=prov, confidence=0.9, turn_id=turn.turn_id
        )  # fmt: skip
        must, later = placement.names(priorities.MUST), placement.names(priorities.LATER)
        key = "flows.priority.done" if later else "flows.priority.done_all"
        turn.notice(
            "priorities_set",
            self._t(turn, key, must=", ".join(must), later=", ".join(later)),
            chips=must,
        )
        turn.answered.append(
            (
                card.id,
                "answered",
                {"value": ev.value, "slots": [priorities.SLOT, priorities.FEATURES_SLOT]},
            )
        )  # type: ignore[arg-type]

    def _flow_reference_answer(self, turn: _Turn, card: Card, ev: Any) -> None:
        value, _ = self._flow_slot(turn, references.SLOT)
        prov = self._prov(turn, "user_tap", "")
        answered: dict[str, JsonValue] = {"value": ev.value, "slots": [references.SLOT]}
        if card.type == "choose_one":
            label = references.confirm_label(card, str(ev.value))
            if label and str(ev.value) == references.OWN_ID:
                turn.slots.write(references.SLOT, [{"label": label}], state="volunteered", provenance=prov, remove=True, turn_id=turn.turn_id)  # fmt: skip
                turn.notice("reference_own", self._t(turn, "flows.reference.own_noted", name=label))
            elif label:
                new = references.apply_form(value, {}, labels={label.casefold()})
                turn.slots.write(references.SLOT, new, state="confirmed", provenance=prov, confidence=0.9, turn_id=turn.turn_id)  # fmt: skip
                turn.notice(
                    "reference_confirmed", self._t(turn, "flows.reference.confirmed", name=label)
                )
        else:
            form = references.parse_form(card, ev.value)
            asked = {lab.casefold() for o in card.options if (lab := references.option_label(o))}
            new = references.apply_form(value, form, labels=asked)
            turn.slots.write(references.SLOT, new, state="confirmed", provenance=prov, confidence=0.9, turn_id=turn.turn_id)  # fmt: skip
        turn.answered.append((card.id, "answered", answered))  # type: ignore[arg-type]

    # ------------------------------------------------------------------ messages
    async def _flow_on_message(self, turn: _Turn, ev: Any, text: str, understood: Any) -> bool:
        """After the understander: "everything is a must", many apps, voice echo. True = a card was re-shown
        and the turn is done (slots are rebuilt into the Brief first)."""
        quote = priorities.everything_is_must(text)
        if quote is not None:
            _, status = self._flow_slot(turn, priorities.SLOT)
            if status != "confirmed":
                turn.slots.write(
                    priorities.SLOT, priorities.SIGNAL_VALUE, state="volunteered",
                    provenance=self._prov(turn, "voice" if ev.input_mode == "voice" else "user_text", quote),
                    confidence=0.8, turn_id=turn.turn_id,
                )  # fmt: skip
        self._flow_phase_apps(turn, text)
        if ev.input_mode == "voice":
            self._flow_voice(turn, text, understood)
        pending = turn.pending_card()
        if quote is not None and pending is not None and priorities.flow_card(pending):
            turn.say(self._t(turn, "flows.priority.nudge"))
            turn.out.append(CardEvent(card=pending))
            self._rebuild(turn)
            return True
        return False

    def _flow_phase_apps(self, turn: _Turn, text: str) -> None:
        held, status = self._flow_slot(turn, apps_scope.APPS_SLOT)
        named = apps_scope.named_apps(text)
        if len(named) < data.policy().apps_phase_at and status not in ("volunteered", "confirmed"):
            return
        plan = apps_scope.phase_plan(cast(JsonValue, apps_scope.union(named, held)))
        if plan is None or not named:  # only when THIS message lists the apps
            return
        status = status if status in ("volunteered", "confirmed") else "volunteered"
        prov = self._prov(turn, "user_text", text[:300])
        turn.slots.write(
            apps_scope.APPS_SLOT,
            cast(JsonValue, plan.now),
            state=status,
            provenance=prov,
            confidence=0.7,
            turn_id=turn.turn_id,
        )  # type: ignore[arg-type]
        feats, _ = self._flow_slot(turn, apps_scope.FEATURES_SLOT)
        records = apps_scope.later_records(plan.later, self._t(turn, "flows.apps.later_intent"))
        turn.slots.write(
            apps_scope.FEATURES_SLOT, apps_scope.merge_features(feats, records), state="volunteered",
            provenance=prov, confidence=0.7, turn_id=turn.turn_id,
        )  # fmt: skip
        pol = data.policy()
        turn.out.append(
            Notice(
                code="apps_phased",
                text=self._t(turn, "flows.apps.phased", now=apps_scope.names(plan.now), later=apps_scope.names(plan.later, pol.apps_max_listed_later)),
                chips=[a["name"] for a in plan.now],
            )
        )  # fmt: skip

    def _flow_voice(self, turn: _Turn, text: str, understood: Any) -> None:
        geo, _ = self._flow_slot(turn, "context.geo")
        brand, _ = self._flow_slot(turn, "brand.name")
        refs, _ = self._flow_slot(turn, references.SLOT)
        ambiguous = (
            bool(getattr(understood, "ambiguous", None))
            or float(getattr(understood, "confidence", 1.0)) < 0.5
        )
        plan = voice.plan(text, geo=geo, brand=brand, references=refs, ambiguous=ambiguous)
        chips = [e.text for e in plan.echoes]
        items = ", ".join(chips)
        if plan.readback:
            echo = self._t(turn, "flows.voice.echo_line", items=items) if chips else ""
            body = self._t(
                turn,
                "flows.voice.readback",
                n=len(plan.statements),
                list=voice.numbered(plan.statements),
                echo=echo,
            )
            turn.say(body, chips=chips)
        elif chips:
            turn.notice("voice_echo", self._t(turn, "flows.voice.echo", items=items), chips=chips)

    async def _flow_voice_remove(self, turn: _Turn, text: str) -> bool:
        """ "remove 2" right after a numbered read-back: clear what that line saved (honestly, or say nothing was)."""
        items = voice.last_list([line.text for line in turn.transcript if line.role == "assistant"])
        picked = voice.removal_request(text, len(items)) if items else []
        if not picked:
            return False
        from mde_compass.engine.language import normalize

        gone = [items[n - 1] for n in picked]
        cleared = 0
        for statement in gone:
            norm = normalize(statement)
            for slot_id, rec in turn.slots.all().items():
                quotes = [
                    normalize(p.quote) for p in rec.provenance if p.type == "voice" and p.quote
                ]
                if any(q and q in norm for q in quotes):
                    res = turn.slots.write(
                        slot_id, rec.value, state="volunteered", provenance=self._prov(turn, "voice", statement),
                        remove=True, turn_id=turn.turn_id,
                    )  # fmt: skip
                    cleared += 1 if res.applied else 0
        listed = ", ".join(gone)
        turn.say(
            self._t(
                turn,
                "flows.voice.removed" if cleared else "flows.voice.remove_nothing",
                items=listed,
            )
        )
        left = [s for i, s in enumerate(items, start=1) if i not in picked]
        if left:
            turn.say(self._t(turn, "flows.voice.remaining", list=voice.numbered(left)))
        self._rebuild(turn)
        await self._reshow_or_advance(turn)
        return True

    # ------------------------------------------------------------------ Locked values
    def _flow_locked_card(self, turn: _Turn, ops: Sequence[PatchOp]) -> bool:
        from mde_compass.engine.service import VARIANTS_FRAME

        changes = locked_edit.changed_locked(turn.brief, self.catalog, ops)
        if not changes:
            return False
        card = locked_edit.build_card(turn.brief, changes, catalog=self.catalog, card_id=self._flow_card_id(turn), language=turn.lang, frame=VARIANTS_FRAME)  # fmt: skip
        turn.asked.append((card, {"frame": VARIANTS_FRAME, "reason": "locked_change"}))
        turn.out.append(CardEvent(card=card))
        return True

    def _flow_locked_edit(self, turn: _Turn, ev: Any) -> bool:
        """A UI edit of a Locked value asks first (with what depends on it); anything else applies at once."""
        return self._flow_locked_card(turn, [PatchOp(op="set", path=ev.item_id, value=ev.value)])

    def _flow_locked_chat(self, turn: _Turn, ops: Sequence[PatchOp]) -> bool:
        """The same confirm for a change made in words, when the ops are plain edits of Locked slots."""
        return self._flow_locked_card(turn, ops)

    # ------------------------------------------------------------------ the typed back-edge
    async def reopen_slot(
        self, caller: Caller, session_id: str, slot_id: str, reason: str
    ) -> TurnResult:
        """A later stage (research) reopens ONE slot with a single targeted question; never a new interview.

        At most ``config.gate.max_back_edge_loops`` such questions per session, then a notice and nothing else.
        The card is answered like any variant pick (a new value, or "Keep what I said")."""
        import asyncio

        from mde_compass.engine import service as svc
        from mde_core.errors import Conflict, NotFound
        from mde_core.ids import new_id

        assert self.ports is not None, "Service.ports is not bound"
        slot = self.catalog.slots.get(slot_id)
        if slot is None:
            raise NotFound(f"slot {slot_id}")
        token = svc._TENANT.set(caller.tenant_id)
        try:
            locks: dict[str, asyncio.Lock] = self._locks  # type: ignore[attr-defined]
            async with locks.setdefault(session_id, asyncio.Lock()):
                async with self.store.begin(caller.tenant_id) as uow:
                    session = await uow.sessions.get(session_id)
                    if not await uow.sessions.try_begin_turn(session_id):
                        raise Conflict("a turn is already running for this session")
                    seq_before = session.outbound_seq
                try:
                    turn = await self._load(
                        caller.tenant_id, session_id, _SystemEvent(new_id("evt"))
                    )
                    await self._flow_reopen(turn, slot_id, screen(reason).text[:300])
                    await self._commit(turn)
                finally:
                    async with self.store.begin(caller.tenant_id) as uow:
                        await uow.sessions.end_turn(session_id)
                async with self.store.begin(caller.tenant_id) as uow:
                    produced = await uow.events.list_outbound(session_id, after_seq=seq_before)
                return TurnResult(accepted=True, events=produced)
        finally:
            svc._TENANT.reset(token)

    async def _flow_reopen(self, turn: _Turn, slot_id: str, reason: str) -> None:
        from mde_compass.engine.service import VARIANTS_FRAME

        assert self.ports is not None
        slot = self.catalog.slots[slot_id]
        if (
            reopen.loops_used([q.why for q in turn.questions])
            >= self.config.gate.max_back_edge_loops
        ):
            turn.notice("back_edge_limit", self._t(turn, "flows.reopen.limit"))
            return
        cur = turn.slots.get(slot_id)
        current = cur.value if cur is not None else None
        vs = await self.ports.variant_maker.variants(
            self._ctx(turn), path=slot.brief_path, current=current, reason=reason
        )
        variants = [(v.label, v.value, v.reason) for v in vs.variants]
        card = reopen.build_card(slot, catalog=self.catalog, card_id=self._flow_card_id(turn), reason=reason, current=current, variants=variants, language=turn.lang, frame=VARIANTS_FRAME)  # fmt: skip
        turn.asked.append(
            (card, {"frame": VARIANTS_FRAME, "reason": reopen.REASON, "slot": slot_id})
        )
        turn.out.append(CardEvent(card=card))
