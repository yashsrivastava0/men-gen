"""DiscoverEngine: the turn loop of the Discover step (spec 3.1), implementing ``DiscoveryEngine``.

One loop, five steps, repeated for every event until the Brief is complete. Code decides, the LLM
helps with language:

1. **Receive** an event (the flow already made it idempotent).
2. **Understand.** A tap needs no LLM; typed text takes a deterministic fast path (greeting,
   "you decide", stop, ...) or goes to C1. The first idea (or a long upload) also goes to C2.
3. **Apply** (code): the SlotStore writes values with provenance under the spec 3.3 rules.
4. **Reply.** Code answers taps and plain answers with a few words; C3 only for questions and
   chit-chat.
5. **Next** (code): the QuestionSelector picks the next card (C5 writes one only when the bank has
   none); when nothing required is left the Brief is complete.

The engine is pure with respect to state: it returns the new ``DiscoveryState`` and the events to
emit, and never writes anywhere. Every LLM call has a deterministic fallback, so no provider
failure reaches the user as an error.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from mde_content import stack
from mde_content.catalogs import load_catalogs
from mde_content.expr import holds
from mde_content.questions import QuestionBanks, load_question_banks
from mde_contracts.brief import Brief
from mde_contracts.calls import C1Output, C2Output, ChangeOp, SlotUpdate, TurnLite
from mde_contracts.cards import Card, CardInstance, CardOption, OtherInput, SkipSpec
from mde_contracts.content import Catalogs
from mde_contracts.engines import (
    DiscoveryRequest,
    DiscoveryResult,
    RunContext,
    UploadMeta,
)
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    InboundEvent,
    Notice,
    OptionSelected,
    UiAction,
    UploadAdded,
    UserMessage,
)
from mde_contracts.slots import SlotData, SlotSource, SlotState
from mde_core.clock import Clock, SystemClock
from mde_core.ids import new_id
from mde_core.logging import get_logger
from mde_discovery import copy as texts
from mde_discovery.cards import COUNTED_KINDS, DESCRIBE_LOOK_ID, CardRenderer
from mde_discovery.compiler import BriefCompiler
from mde_discovery.components import (
    BriefInterpreter,
    CardWriter,
    ChangeInterpreter,
    Responder,
    Understander,
)
from mde_discovery.helpers import detect_sensitive, region_ids
from mde_discovery.llm import FALLBACKS, Llm
from mde_discovery.org_profile import OrgProfileMapper
from mde_discovery.policy_guard import PolicyGuard, Violation
from mde_discovery.router import IntentRouter, Route
from mde_discovery.safety import HIDDEN_MESSAGE, SafetyScreen, Screened
from mde_discovery.selector import (
    COMPLIANCE_SLOT,
    IDEA_SLOT,
    LOOK_SLOT,
    QuestionSelector,
    Selection,
    limit_reached,
)
from mde_discovery.slots import SlotStore, detect_contradiction
from mde_discovery.summary import append_line
from mde_discovery.turn import MAX_RECENT_TURNS, Turn
from mde_discovery.vocab import Vocab
from mde_prompts.calls.c1_understand import lexicon as lx
from mde_prompts.registry import load_spec

APPROVAL_CONFIDENCE = 0.75  # typed approval counts only above this (gate rule 3)
THIN_CONFIDENCE = 0.4  # an idea below this is "vague": help mode
LONG_TEXT_CHARS = 600  # a long paste or upload also goes to C2
SKIPS_TO_OFFER = 3  # three skips in a row: offer "finish with smart defaults"
NUDGE_AFTER = 2  # off-topic turns before one gentle nudge; the next one assumes
REVIEW_STATES = ("BRIEF_REVIEW", "PLAN_REVIEW", "HANDED_OFF")
# Company-rule cards are built from the policy, so they have no bank id: these ids tell them apart.
POLICY_REGION_CARD = "Q-policy-region"
POLICY_LIBRARY_PREFIX = "Q-policy-lib:"  # followed by the banned library as the policy writes it
POLICY_QUEUE_KEY = "policy_queue"  # DiscoveryState.extra: violations waiting for their card (JSON)
ORG_PROFILE_SOURCE: SlotSource = "org_profile"
log = get_logger(__name__)
LLM_CALLS = (
    "C1.understand",
    "C2.interpret_brief",
    "C3.respond",
    "C5.write_card",
    "C11.write_summary",
    "C14.interpret_change",
)


class DiscoverEngine:
    """The Discover engine. Build it with a ``GatewayClient``; everything else is optional."""

    def __init__(
        self,
        gateway: Any,
        *,
        ids: Callable[[str], str] | None = None,
        clock: Clock | None = None,
        banks: QuestionBanks | None = None,
        catalogs: Catalogs | None = None,
    ) -> None:
        self._ids: Callable[[str], str] = ids or new_id
        self._clock: Clock = clock or SystemClock()
        self.banks = banks or load_question_banks()
        self.catalogs = catalogs or load_catalogs()
        self.vocab = Vocab(self.banks, self.catalogs)
        self.selector = QuestionSelector(self.banks, self.catalogs)
        self.router = IntentRouter(self.banks)
        self.safety = SafetyScreen()
        self.guard = PolicyGuard()
        self.org_profile = OrgProfileMapper(self.vocab, self.guard, self.safety)
        llm = Llm(gateway)
        self.understander = Understander(llm, self.vocab, self.catalogs)
        self.interpreter = BriefInterpreter(llm, self.catalogs)
        self.responder = Responder(llm, self.banks)
        self.changer = ChangeInterpreter(llm, self.understander)
        self.writer = CardWriter(llm, self.vocab, self.catalogs)
        self.renderer = CardRenderer(self.banks, self._ids, self._now)
        self.compiler = BriefCompiler(llm, self.vocab, self.catalogs, self.selector)
        for call_id in LLM_CALLS:
            name = load_spec(call_id).fallback
            if name is None or name not in FALLBACKS:
                raise RuntimeError(f"{call_id}: fallback {name!r} is not registered")

    # ================================================================= protocol
    async def start(self, req: DiscoveryRequest, ctx: RunContext) -> DiscoveryResult:
        """First turn: the canned greeting and six starter chips. No LLM call.

        An Enterprise session also starts from the tenant's Org Profile when the flow passes one
        (``req.org_profile``): see :meth:`_seed_org_profile`. Guided sessions never read it.
        """
        turn = self._turn(req, ctx, None)
        lang = req.language_hint or turn.state.language
        turn.set(greeted=True, language=lang)
        seeded = self._seed_org_profile(turn)
        self._greet(turn)
        if seeded:
            self._notice(turn, "org_profile_loaded", texts.say("org_profile_loaded", turn.lang))
        turn.intent = "greeting"
        return self._finish(turn, count_turn=False)

    async def handle(
        self, req: DiscoveryRequest, event: InboundEvent, ctx: RunContext
    ) -> DiscoveryResult:
        turn = self._turn(req, ctx, event.event_id)
        counted = True
        if isinstance(event, UserMessage):
            await self._on_text(turn, event.text)
        elif isinstance(event, OptionSelected):
            await self._on_option(turn, event)
        elif isinstance(event, UploadAdded):
            await self._on_upload(turn, event)
        elif isinstance(event, UiAction):
            counted = False
            await self._on_ui(turn, event)
        else:  # a gate decision belongs to the flow, not to Discover
            counted = False
            turn.intent = "other"
        self._enforce_policy(turn)
        return self._finish(turn, count_turn=counted)

    async def reopen_slot(
        self, req: DiscoveryRequest, slot_id: str, ctx: RunContext
    ) -> DiscoveryResult:
        """Gate 1 "change something": show the card of one slot only (not counted in the cap)."""
        turn = self._turn(req, ctx, None)
        card, generated = await self._card_for_slot(turn, slot_id)
        self._say(turn, texts.say("reopen_intro", turn.lang))
        turn.set(extra={**turn.state.extra, "reopened": slot_id})
        self._show_card(turn, card, counted=False, generated=generated)
        turn.intent = "change"
        return self._finish(turn, count_turn=False)

    async def apply_changes(
        self, req: DiscoveryRequest, ops: list[ChangeOp], source: SlotSource, ctx: RunContext
    ) -> DiscoveryResult:
        """Apply patch operations (taps on toggles, C14 output) and look for what is now missing."""
        turn = self._turn(req, ctx, None)
        self._apply_ops(turn, ops, source)
        turn.change_ops = list(ops)
        turn.intent = "change"
        await self._advance(turn, enforce_caps=False)
        return self._finish(turn, count_turn=False)

    async def compile_brief(self, req: DiscoveryRequest, ctx: RunContext) -> Brief:
        """Deterministic rows from the slots plus the C11 sentence."""
        return await self.compiler.compile(req, ctx)

    # ================================================================= plumbing
    def _now(self) -> datetime:
        return self._clock.now()

    def _turn(self, req: DiscoveryRequest, ctx: RunContext, event_id: str | None) -> Turn:
        state = req.state
        if req.language_hint and not state.turn_count and not state.greeted:
            state = state.model_copy(update={"language": req.language_hint})
        return Turn(
            req=req,
            ctx=ctx,
            state=state,
            store=SlotStore(state.slots, self._now),
            ids=self._ids,
            now=self._now,
            in_reply_to=event_id,
        )

    def _event_base(self, turn: Turn) -> dict[str, Any]:
        return {
            "event_id": self._ids("evt"),
            "session_id": turn.req.session_id,
            "at": self._now(),
            "in_reply_to": turn.in_reply_to,
        }

    def _say(
        self, turn: Turn, text: str, *, chips: list[str] | None = None, canned: bool = True
    ) -> None:
        turn.events.append(
            AssistantMessage(
                **self._event_base(turn),
                text=text,
                language=turn.lang,
                chips=list(chips or []),
                canned=canned,
            )
        )
        turn.spoken.append(text)

    def _notice(self, turn: Turn, code: str, text: str, level: str = "info") -> None:
        turn.events.append(
            Notice(**self._event_base(turn), code=code, text=text, level=level)  # type: ignore[arg-type]
        )

    def _ack(self, turn: Turn) -> None:
        self._say(turn, texts.say(f"ack_{turn.state.turn_count % 3 + 1}", turn.lang))

    def _greet(self, turn: Turn) -> None:
        chips = [c.label for c in self.banks.chips(None, turn.lang)]
        self._say(turn, texts.say("greeting", turn.lang), chips=chips, canned=True)

    def _archetype(self, turn: Turn) -> str | None:
        return turn.store.text_value("idea.archetype")

    def _context(self, turn: Turn) -> dict[str, SlotData]:
        return turn.store.context(turn.state.sensitive_flags)

    # ----------------------------------------------------------------- the Org Profile
    def _seed_org_profile(self, turn: Turn) -> list[str]:
        """Start an Enterprise session from the tenant's Org Profile (spec 3.5 and 10, D-077).

        Each value the profile gives becomes an ``assumed`` slot with source ``org_profile``:
        proposed, never applied silently as the user's answer. It shows in the Brief's "We
        assumed" list, any tap overwrites it, and the must-confirm slots (single sign-on,
        compliance, payments) are still asked. The profile is screened and checked against the
        company rules before anything is stored (:mod:`mde_discovery.org_profile`). The seed is
        not a change the user made, so ``store.changed`` is left as it was. Returns the slot ids
        written.
        """
        profile = turn.req.org_profile
        if turn.mode != "enterprise" or profile is None:
            return []
        report = self.org_profile.read(profile, turn.policy, turn.lang)
        changed_before = list(turn.store.changed)
        written = [
            seed.slot_id
            for seed in report.seeds
            if turn.store.write(
                seed.slot_id,
                seed.value,
                state="assumed",
                source=ORG_PROFILE_SOURCE,
                evidence=seed.evidence,
            ).applied
        ]
        turn.store.changed[:] = changed_before
        # counts only: neither a key nor a value of the profile is ever logged
        log.info(
            "org_profile_read",
            seeded=len(written),
            dropped=report.dropped,
            rejected=report.rejected,
        )
        return written

    # ----------------------------------------------------------------- showing cards
    def _show_card(
        self, turn: Turn, card: Card, *, counted: bool = True, generated: bool = False
    ) -> None:
        state = turn.state
        counts = counted and card.kind in COUNTED_KINDS
        instance = self.renderer.render(
            card,
            lang=turn.lang,
            mode=turn.mode,
            policy=turn.policy,
            store=turn.store,
            asked=state.card_count + (1 if counts else 0),
            generated=generated,
        )
        changes: dict[str, Any] = {"pending_card": instance}
        if counts:
            asked = list(state.cards_asked)
            entry = card.id if card.id not in asked else f"{card.id}:{len(asked) + 1}"
            changes["cards_asked"] = [*asked, entry]
            changes["card_count"] = state.card_count + 1
        if card.type == "visual3":
            seen = list(dict.fromkeys([*state.seen_option_ids, *self.renderer.shown_values(card)]))
            changes["seen_option_ids"] = seen
            if card.id not in state.visual_rounds and counted:
                changes["visual_rounds"] = {**state.visual_rounds, card.id: 1}
        turn.set(**changes)
        turn.events.append(CardEvent(**self._event_base(turn), card=instance))
        turn.spoken.append(instance.card.text)

    def _represent(self, turn: Turn) -> None:
        """Show the pending card again (after an answer to a question, a nudge, ...)."""
        pending = turn.state.pending_card
        if pending is not None:
            turn.events.append(CardEvent(**self._event_base(turn), card=pending))
            turn.spoken.append(pending.card.text)

    async def _card_for_slot(self, turn: Turn, slot_id: str) -> tuple[Card, bool]:
        """The bank card of a slot (for reopening it), or one written by C5."""
        archetype = self._archetype(turn)
        if slot_id == COMPLIANCE_SLOT and turn.state.sensitive_flags:
            card = self.selector.compliance_card(
                turn.state.sensitive_flags[0], archetype, turn.mode
            )
            if card is not None:
                return card, False
        ctx = self._context(turn)
        cards = self.selector.standard_cards(slot_id, archetype, turn.mode)
        fits = [c for c in cards if holds(c.ask_if, ctx)]
        if fits or cards:
            return (fits or cards)[0], False
        return await self.writer.write(turn, slot_id), True

    # ================================================================= finishing a turn
    def _finish(self, turn: Turn, *, count_turn: bool) -> DiscoveryResult:
        state = turn.state
        updates: dict[str, Any] = {"slots": turn.store.snapshot()}
        if count_turn:
            updates["turn_count"] = state.turn_count + 1
        turns = list(state.recent_turns)
        if turn.user_text:
            turns.append(TurnLite(role="user", text=turn.user_text[:400]))
        if turn.spoken:
            turns.append(TurnLite(role="assistant", text=" ".join(turn.spoken)[:600]))
        updates["recent_turns"] = turns[-MAX_RECENT_TURNS:]
        turn.set(**updates)
        self._forget_stale_nudges(turn)
        if turn.fallback_used:
            self._notice(turn, "taking_longer", texts.say("slow", turn.lang))
        complete = (
            turn.state.pending_card is None
            and self.selector.next(
                state=turn.state, store=turn.store, mode=turn.mode, policy=turn.policy
            ).kind
            == "complete"
        )
        return DiscoveryResult(
            state=turn.state,
            events=list(turn.events),
            intent=turn.intent,
            brief_complete=complete,
            approval_confident=turn.approval_confident,
            change_ops=list(turn.change_ops),
            changed_slots=list(turn.store.changed),
            stop_requested=turn.stop_requested,
            pause_requested=turn.pause_requested,
            start_over_requested=turn.start_over_requested,
            llm_calls=turn.llm_calls,
        )

    @staticmethod
    def _forget_stale_nudges(turn: Turn) -> None:
        """Only the card that is pending can hold a nudge: an answer (or an assumption) resets it."""
        pending = turn.state.pending_card
        nudged = turn.state.nudged_card_ids
        keep = [i for i in nudged if pending is not None and i == pending.card.id]
        if keep != nudged:
            turn.set(nudged_card_ids=keep)

    def _remember(self, turn: Turn, line: str | None) -> None:
        if line:
            turn.set(summary=append_line(turn.state.summary, line))

    # ================================================================= typed text
    async def _on_text(self, turn: Turn, raw: str) -> None:
        screened = self.safety.screen(raw)
        text = screened.text
        turn.user_text = text
        if screened.had_secret:
            self._notice(turn, "secret_redacted", self.safety.secret_notice(turn.lang), "warn")
        if screened.injection:
            self._notice(turn, "injection_suspected", self.safety.injection_notice(turn.lang))
        self._scan_policy(turn, text)
        pending = turn.state.pending_card
        if pending is not None and pending.card.id == DESCRIBE_LOOK_ID:
            self._describe_look(turn, text)
            await self._advance(turn)
            return
        route = self.router.route(text, pending=pending)
        if route is not None:
            await self._on_route(turn, route, text)
            return
        if len(text) >= LONG_TEXT_CHARS:  # a long paste is material, not an instruction
            out = await self.understander.understand(turn, text[:300], attachment_text=text)
        else:
            out = await self.understander.understand(turn, text)
        await self._on_understood(turn, text, out, screened)

    async def _on_route(self, turn: Turn, route: Route, text: str) -> None:
        kind = route.kind
        if kind == "greeting":
            turn.intent = "greeting"
            self._greet(turn)
            self._represent(turn)
        elif kind == "you_decide":
            turn.intent = "answer"
            await self._you_decide(turn)
        elif kind == "finish_with_defaults":
            turn.intent = "other"
            await self._finish_with_defaults(turn)
        elif kind == "stop":
            turn.intent = "stop"
            turn.stop_requested = True
        elif kind == "pause":
            turn.intent = "pause"
            turn.pause_requested = True
        elif kind == "start_over":
            turn.intent = "start_over"
            turn.start_over_requested = True
        elif kind == "none_of_these":
            turn.intent = "answer"
            await self._none_of_these(turn)
        elif route.chip is not None:
            await self._on_chip(turn, route.chip.sets, route.chip.prefill, route.chip.label)

    async def _on_chip(
        self, turn: Turn, sets: dict[str, SlotData], prefill: str | None, label: str
    ) -> None:
        """A starter chip: the same as typing its text, with the archetype already known (0 calls)."""
        turn.intent = "idea"
        if not sets:  # "I am not sure yet"
            await self._help_mode(turn)
            return
        text = prefill or label
        turn.store.write(IDEA_SLOT, text, state="volunteered", source="tap", evidence=label)
        for slot, value in sets.items():
            res = turn.store.write(slot, value, state="volunteered", source="tap", evidence=label)
            if slot == "idea.archetype" and res.value_changed:
                self._cascade_archetype(turn)
        self._add_sensitive(turn, detect_sensitive(text, self._archetype(turn), self.catalogs))
        self._remember(turn, text)
        turn.set(help_mode=False)
        self._ack(turn)
        await self._advance(turn)

    # ----------------------------------------------------------------- C1 result
    async def _on_understood(
        self, turn: Turn, text: str, out: C1Output, screened: Screened
    ) -> None:
        words = len(text.split())
        non_latin = any(ord(c) > 0x2FF for c in text if c.isalpha())
        if out.language and out.language != turn.lang and (words >= 3 or non_latin):
            turn.set(language=out.language)
        turn.intent = out.intent
        flags = out.safety
        hide = flags.secret_detected and not screened.had_secret
        if hide:
            turn.user_text = HIDDEN_MESSAGE
            self._notice(turn, "secret_redacted", self.safety.secret_notice(turn.lang), "warn")
        if flags.injection_suspected and not screened.injection:
            self._notice(turn, "injection_suspected", self.safety.injection_notice(turn.lang))
        if flags.unsafe or out.intent == "unsafe":
            await self._decline(turn, flags.category)
            return
        ups = [] if hide else self._valid_updates(turn, out.slot_updates)
        intent = out.intent
        review = turn.req.gate_open is not None or turn.req.session_state in REVIEW_STATES
        if intent == "greeting":
            self._greet(turn)
            self._represent(turn)
        elif intent == "stop":
            turn.stop_requested = True
        elif intent == "pause":
            turn.pause_requested = True
        elif intent == "start_over":
            turn.start_over_requested = True
        elif intent == "approve":
            await self._on_approve(turn, text, out, ups)
        elif intent == "confused":
            await self._help_mode(turn)
        elif intent in ("question", "chitchat"):
            await self._conversational(turn, text, out, ups)
        elif intent == "change":
            await self._on_change(turn, text)
        elif review and ups:
            self._apply_updates_as_changes(turn, ups)
            await self._advance(turn, enforce_caps=False)
        elif review:
            return  # nothing to apply; the flow shows the gate again
        else:
            await self._on_content(turn, text, out, ups, hide=hide)

    def _valid_updates(
        self, turn: Turn, updates: list[SlotUpdate]
    ) -> list[tuple[SlotUpdate, SlotData]]:
        """Updates whose values fit the slot vocabulary (the app type scopes the feature ids)."""
        archetype = self._archetype(turn)
        for u in updates:
            raw = u.to_data()
            if u.slot_id == "idea.archetype" and not u.negate and raw is not None:
                checked = self.vocab.validate("idea.archetype", raw)
                if isinstance(checked, str):
                    archetype = checked
        out: list[tuple[SlotUpdate, SlotData]] = []
        for u in sorted(updates, key=lambda x: x.slot_id != "idea.archetype"):
            raw = u.to_data()
            if raw is None:
                if u.negate:
                    out.append((u, None))
                continue
            value = self.vocab.validate(u.slot_id, raw, archetype)
            if value is None:
                continue
            if u.slot_id == "scope.apps" and isinstance(value, list) and len(value) > 1:
                # One app per project (spec 3.3; the `multi_app` notice says "let's start with
                # one"): when free text names several, none is chosen here and the "which should we
                # build first?" card asks. Found by live L1 (D-081): the mock never returns more
                # than one app, the real model volunteered all of them.
                continue
            out.append((u, value))
        return out

    def _apply_updates(
        self,
        turn: Turn,
        ups: list[tuple[SlotUpdate, SlotData]],
        *,
        answering: Card | None = None,
        skip: frozenset[str] = frozenset(),
    ) -> None:
        for u, value in ups:
            if u.slot_id in skip:
                continue
            state: SlotState = (
                "confirmed"
                if answering is not None and u.slot_id == answering.slot
                else "volunteered"
            )
            res = turn.store.apply_update(u, value, state=state, source="text")
            if u.slot_id == "idea.archetype" and res.value_changed:
                self._cascade_archetype(turn)

    def _cascade_archetype(self, turn: Turn) -> None:
        """A new app type invalidates features and apps that belong to the old one."""
        archetype = self._archetype(turn)
        if archetype is None:
            return
        valid = set(self.vocab.feature_ids(archetype))
        stale = [f for f in turn.store.list_value("scope.features") if f not in valid]
        if stale:
            turn.store.remove_items("scope.features", stale)
        allowed_apps = {"main", *self.vocab.app_ids(archetype)}
        stale_apps = [a for a in turn.store.list_value("scope.apps") if a not in allowed_apps]
        if stale_apps:
            turn.store.remove_items("scope.apps", stale_apps)

    def _add_sensitive(self, turn: Turn, domains: list[str]) -> None:
        known = set(self.vocab.domains())
        fresh = list(
            dict.fromkeys(d for d in domains if d in known and d not in turn.state.sensitive_flags)
        )
        if fresh:
            turn.set(sensitive_flags=[*turn.state.sensitive_flags, *fresh])

    # ----------------------------------------------------------------- the first idea
    async def _on_content(
        self,
        turn: Turn,
        text: str,
        out: C1Output,
        ups: list[tuple[SlotUpdate, SlotData]],
        *,
        hide: bool,
    ) -> None:
        store = turn.store
        first_idea = not store.is_filled(IDEA_SLOT)
        pending = turn.state.pending_card
        thin = out.intent == "other" or (out.intent == "idea" and out.confidence < THIN_CONFIDENCE)
        asked_idea = pending is not None and pending.card.slot == IDEA_SLOT
        if first_idea and thin and not ups and not asked_idea:
            await self._help_mode(turn)
            return
        if first_idea:
            long_paste = len(text) >= LONG_TEXT_CHARS
            c2 = await self.interpreter.interpret(
                turn, text[:200] if long_paste else text, text if long_paste else None
            )
            self._ingest_idea(turn, text, out, c2, ups, source_text=text)
            self._remember(turn, out.summary_update if not hide else None)
            if pending is not None and (
                pending.card.slot == IDEA_SLOT or pending.card.kind in ("help", "inspiration")
            ):
                turn.set(pending_card=None)  # the idea is in: the help card is no longer needed
            if long_paste:
                sentence = turn.store.text_value(IDEA_SLOT) or ""
                self._say(turn, texts.say("paste_understood", turn.lang, summary=sentence))
            else:
                self._ack(turn)
            self._after_idea(turn, text, c2)
            await self._advance(turn)
            return
        answered = self._answer_pending_from_c1(turn, text, out, ups)
        self._apply_updates(turn, ups)
        for ref in out.references:
            self._note_reference(turn, ref.text, ref.kind)
        self._remember(turn, None if hide else out.summary_update)
        self._drop_answered_pending(turn)
        if answered:
            self._ack(turn)
            turn.set(off_topic_streak=0, skips_in_row=0)
        elif not ups:
            await self._not_understood(turn)
            return
        else:
            self._ack(turn)
        await self._advance(turn)

    def _ingest_idea(
        self,
        turn: Turn,
        text: str,
        out: C1Output,
        c2: C2Output,
        ups: list[tuple[SlotUpdate, SlotData]],
        *,
        source_text: str,
        source: SlotSource = "text",
    ) -> None:
        store = turn.store
        sentence = (c2.one_sentence_idea or source_text).strip()[:160]
        store.write(
            IDEA_SLOT, sentence, state="volunteered", source=source, evidence=source_text[:80]
        )
        candidate = c2.archetype_candidates[0].archetype_id if c2.archetype_candidates else None
        archetype = self.vocab.validate("idea.archetype", candidate) if candidate else None
        if not isinstance(archetype, str):
            archetype = "custom"
        store.write(
            "idea.archetype", archetype, state="volunteered", source=source, evidence=sentence[:80]
        )
        touched = {IDEA_SLOT, "idea.archetype"}
        self._apply_updates(
            turn, [(u, v) for u, v in ups if u.slot_id not in touched], skip=frozenset()
        )
        written = set(store.changed)
        extra = [
            (u, v)
            for u, v in self._valid_updates(turn, c2.volunteered)
            if u.slot_id not in written and u.slot_id not in touched
        ]
        self._apply_updates(turn, extra)
        regions = region_ids(c2.regions)
        if regions:
            store.write("regions", regions, state="volunteered", source=source)
        for name in c2.named_references:
            self._note_reference(turn, name, "like_app")
        for ref in out.references:
            self._note_reference(turn, ref.text, ref.kind)
        self._add_sensitive(
            turn, [*c2.sensitive_flags, *detect_sensitive(source_text, archetype, self.catalogs)]
        )
        turn.set(help_mode=False)

    def _after_idea(self, turn: Turn, text: str, c2: C2Output) -> None:
        """Messages that follow the first idea: unsupported type, reference, several apps, size."""
        lang = turn.lang
        known = {u.id for u in stack.load_stack_profile().unsupported_types}
        if c2.unsupported_type in known:
            item = stack.unsupported_type(str(c2.unsupported_type))
            self._say(
                turn,
                texts.say(
                    "unsupported_type",
                    lang,
                    type=item.label,
                    reason=item.reason,
                    closest=item.closest_option,
                ),
            )
            turn.set(extra={**turn.state.extra, "unsupported": item.id})
        new_refs = [r for r in turn.state.references if r not in turn.req.state.references]
        for ref in new_refs[:1]:
            self._say(turn, texts.say("reference_app", lang, app=ref))
        if c2.multi_app and (c2.app_count_hint or 2) >= 2:
            self._say(turn, texts.say("multi_app", lang))
        if c2.size_estimate == "large":
            self._say(turn, texts.say("scope_large", lang))
            turn.set(extra={**turn.state.extra, "size": "large"})
        if lx.contains_phrase(text, "finish_with_defaults"):
            self._say(turn, texts.say("defaults_path", lang))
            turn.set(extra={**turn.state.extra, "defaults_path": "1"})

    def _note_reference(self, turn: Turn, name: str, kind: str) -> None:
        if kind in ("like_app", "company") and name and name not in turn.state.references:
            turn.set(references=[*turn.state.references, name])
            self._remember(turn, f"Likes {name}")

    # ----------------------------------------------------------------- answering the pending card
    def _answer_pending_from_c1(
        self,
        turn: Turn,
        text: str,
        out: C1Output,
        ups: list[tuple[SlotUpdate, SlotData]],
    ) -> bool:
        """A typed reply answers the pending card (and its other updates still apply)."""
        pending = turn.state.pending_card
        if pending is None:
            return False
        card = pending.card
        if self._is_policy_card(card):
            return (
                False  # only a tap on one of the allowed alternatives answers a company-rule card
            )
        option_ids = {o.id for o in card.options}
        option = out.selected_option_id if out.selected_option_id in option_ids else None
        mine = [(u, v) for u, v in ups if u.slot_id == card.slot and not u.negate]
        text_card = card.type in ("text", "upload")
        if not (option or mine or (out.answers_pending_card and text_card)):
            return False
        if card.kind == "nudge":
            self._nudge_choice(turn, option)
            return True
        answered = False
        if option is not None:
            answered = self._apply_card_answer(turn, card, [option], None, source="text")
        if not answered and mine:
            for u, v in mine:
                turn.store.apply_update(u, v, state="confirmed", source="text")
            self._finish_answer(turn, card)
            answered = True
        if not answered and out.answers_pending_card and text_card and text.strip():
            answered = self._apply_card_answer(turn, card, [], text, source="text")
        if answered:
            self._cascade_after(turn, card)
        return answered

    def _drop_answered_pending(self, turn: Turn) -> None:
        """A card whose slot the user just filled need not be asked (spec 3.2 "volunteered")."""
        pending = turn.state.pending_card
        if pending is None or pending.card.kind not in ("standard", "resolution"):
            return
        card = pending.card
        if self._is_policy_card(card):
            regions = turn.store.list_value("regions")
            typed_allowed = bool(regions) and not self.guard.disallowed_regions(
                regions, turn.policy
            )
            if card.id == POLICY_REGION_CARD and typed_allowed:
                turn.set(pending_card=None)  # an allowed region was typed: the card is settled
            return
        ctx = self._context(turn)
        if card.kind == "resolution":
            still = detect_contradiction(
                self.banks, turn.store, self._archetype(turn), turn.mode, turn.state.sensitive_flags
            )
            if still is None or still.id != card.id:
                turn.set(pending_card=None)
            return
        state = turn.store.state(card.slot)
        settled = state == "confirmed" or (
            state == "volunteered" and not self.selector.needs_confirmation(card, ctx)
        )
        if settled:
            turn.set(pending_card=None)

    def _cascade_after(self, turn: Turn, card: Card) -> None:
        if card.slot == "idea.archetype":
            self._cascade_archetype(turn)

    def _apply_card_answer(
        self,
        turn: Turn,
        card: Card,
        option_ids: list[str],
        other_text: str | None,
        *,
        source: SlotSource,
    ) -> bool:
        """Write what the answer says. Returns False when nothing usable was given."""
        store = turn.store
        state: SlotState = "volunteered" if card.kind in ("help", "inspiration") else "confirmed"
        if card.type == "multi":
            chosen = [o.id for o in card.options if o.id in option_ids]
            if not chosen:
                return False
            store.write(card.slot, chosen, state=state, source=source)
        elif card.type in ("text", "upload"):
            text = (other_text or "").strip()
            if not text:
                return False
            self._write_text_answer(turn, card, text, source)
        else:
            option = next((o for o in card.options if o.id in option_ids), None)
            if option is None:
                return False
            for slot, value in option.sets.items():
                res = store.write(slot, value, state=state, source=source, evidence=option.label)
                if slot == "idea.archetype" and res.value_changed:
                    self._cascade_archetype(turn)
        self._finish_answer(turn, card)
        return True

    def _write_text_answer(self, turn: Turn, card: Card, text: str, source: SlotSource) -> None:
        store = turn.store
        if card.slot == "brand.name" and (text.startswith(("http://", "https://", "www."))):
            host = text.split("//")[-1].split("/")[0].removeprefix("www.").split(".")[0]
            store.write("brand.assets", text, state="volunteered", source=source)
            if host:
                store.write("brand.name", host.title(), state="volunteered", source=source)
            return
        store.write(card.slot, text[:300], state="confirmed", source=source, evidence=text[:80])

    def _finish_answer(self, turn: Turn, card: Card) -> None:
        """Bookkeeping when a card is answered: clear it, reset counters, note compliance."""
        changes: dict[str, Any] = {"pending_card": None, "skips_in_row": 0, "off_topic_streak": 0}
        if card.kind == "compliance":
            domain = self.selector.domain_of(card)
            if domain and domain not in turn.state.compliance_confirmed:
                changes["compliance_confirmed"] = [*turn.state.compliance_confirmed, domain]
        if card.kind in ("help", "inspiration"):
            changes["help_mode"] = False
        extra = dict(turn.state.extra)
        if extra.get("reopened") == card.slot:
            extra.pop("reopened")
            changes["extra"] = extra
        turn.set(**changes)

    # ----------------------------------------------------------------- taps
    async def _on_option(self, turn: Turn, event: OptionSelected) -> None:
        pending = turn.state.pending_card
        turn.intent = "answer"
        if pending is None or pending.instance_id != event.card_instance_id:
            self._notice(turn, "stale_card", texts.say("stale_card", turn.lang))
            self._represent(turn)
            return
        card = pending.card
        if self._is_policy_card(card):
            await self._on_policy_choice(turn, card, event)
            return
        if card.kind == "nudge":
            self._nudge_choice(turn, event.option_ids[0] if event.option_ids else None)
            await self._advance(turn)
            return
        other = (event.other_text or "").strip()
        if event.you_decide or event.skipped:
            if event.skipped and not card.skip.enabled:
                self._represent(turn)
                return
            await self._you_decide(turn, count_skip=True)
            return
        if not event.option_ids and not other and card.type == "visual3":
            await self._none_of_these(turn)
            return
        if not event.option_ids and not other and card.type == "multi":
            await self._you_decide(turn, count_skip=True)  # nothing ticked: same as skipping
            return
        if other and card.slot == IDEA_SLOT and card.type == "text":
            await self._on_text(turn, other)  # the idea typed into its card is a first idea
            return
        if other and card.type not in ("text", "upload"):
            if event.option_ids and card.type == "multi":  # ticks answer; the text adds wishes
                self._apply_card_answer(turn, card, list(event.option_ids), None, source="tap")
                await self._typed_extra(turn, card, other)
                await self._advance(turn)
                return
            await self._on_typed_other(turn, card, other)
            return
        if not self._apply_card_answer(
            turn, card, list(event.option_ids), other or None, source="tap"
        ):
            self._represent(turn)
            return
        self._cascade_after(turn, card)
        self._ack(turn)
        await self._advance(turn)

    async def _typed_extra(self, turn: Turn, card: Card, text: str) -> None:
        """Text typed next to ticked options ("Add my own"): mined for slots, never lost."""
        screened = self.safety.screen(text)
        turn.user_text = screened.text
        if screened.had_secret:
            self._notice(turn, "secret_redacted", self.safety.secret_notice(turn.lang), "warn")
        self._scan_policy(turn, screened.text)
        out = await self.understander.understand(turn, screened.text)
        ups = self._valid_updates(turn, out.slot_updates)
        self._apply_updates(turn, ups, skip=frozenset({card.slot}))
        self._custom_text(turn, card, screened.text)

    async def _on_typed_other(self, turn: Turn, card: Card, text: str) -> None:
        """Something else, typed on an option card: through C1, and it answers the card."""
        screened = self.safety.screen(text)
        turn.user_text = screened.text
        if screened.had_secret:
            self._notice(turn, "secret_redacted", self.safety.secret_notice(turn.lang), "warn")
        self._scan_policy(turn, screened.text)
        out = await self.understander.understand(turn, screened.text)
        ups = self._valid_updates(turn, out.slot_updates)
        answered = self._answer_pending_from_c1(turn, screened.text, out, ups)
        self._apply_updates(turn, ups, skip=frozenset({card.slot}) if answered else frozenset())
        self._remember(turn, out.summary_update)
        if not answered:
            if card.slot == LOOK_SLOT:
                self._describe_look(turn, screened.text)
            else:
                self._custom_text(turn, card, screened.text)
                self._finish_answer(turn, card)
        self._ack(turn)
        await self._advance(turn)

    def _custom_text(self, turn: Turn, card: Card, text: str) -> None:
        """Text we could not map to an option: kept as the user's own words, never as a slot value."""
        if card.slot == "scope.features":
            custom = turn.state.extra.get("custom_features", "")
            joined = "; ".join(x for x in (custom, text[:80]) if x)
            turn.set(extra={**turn.state.extra, "custom_features": joined[:300]})
        self._remember(turn, text[:120])

    # ----------------------------------------------------------------- you decide, defaults
    async def _you_decide(self, turn: Turn, *, count_skip: bool = True) -> None:
        pending = turn.state.pending_card
        if pending is None:
            await self._advance(turn)
            return
        card = pending.card
        sd = self.selector.slot_def(card.slot)
        if turn.mode in sd.must_confirm_in and card.kind != "nudge":
            self._say(turn, texts.say("must_confirm", turn.lang))
            self._represent(turn)
            return
        if card.kind == "nudge":
            self._nudge_choice(turn, "finish" if card.id == "Q-nudge-02" else "decide")
            await self._advance(turn)
            return
        if not card.skip.enabled:
            self._say(turn, texts.say("didnt_catch", turn.lang))
            self._represent(turn)
            return
        self._say(turn, self._assume_card(turn, card))
        turn.set(pending_card=None, off_topic_streak=0)
        if count_skip:
            turn.set(skips_in_row=turn.state.skips_in_row + 1)
        await self._advance(turn)

    def _assume_card(self, turn: Turn, card: Card) -> str:
        """Take the recommended option as ``assumed`` with a one-line reason; returns the ack text."""
        archetype = self._archetype(turn)
        ctx = self._context(turn)
        writes = self.selector.assumption(card.slot, archetype, turn.mode, ctx, card=card)
        described = {slot: turn.store.value(slot) for slot, _ in writes}
        for slot, value in writes:
            reason = self._reason(turn, slot)
            res = turn.store.write(slot, value, state="assumed", source="default", evidence=reason)
            if slot == "idea.archetype" and res.value_changed:
                self._cascade_archetype(turn)
        if card.kind == "compliance":
            domain = self.selector.domain_of(card)
            if domain and domain not in turn.state.compliance_confirmed:
                turn.set(compliance_confirmed=[*turn.state.compliance_confirmed, domain])
        slot, value = writes[0] if writes else (card.slot, None)
        label = self._short_label(slot, value, turn.lang, archetype)
        if described.get(slot) not in (None, "", []) and described.get(slot) == value:
            reason = texts.say("assumed_as_described", turn.lang)
        else:
            reason = self._reason(turn, card.slot)
        return texts.say("assumed_ack", turn.lang, option=label, reason=reason)

    def _short_label(
        self, slot: str, value: SlotData, lang: str, archetype: str | None = None
    ) -> str:
        if value in (None, "", []):
            return ""
        if isinstance(value, list) and len(value) > 4:
            return f"{self.vocab.label(slot, value[:4], lang, archetype)}, ..."
        return self.vocab.label(slot, value, lang, archetype)

    def _slot_ids(self) -> set[str]:
        return {s.slot_id for s in self.catalogs.slots}

    def _reason(self, turn: Turn, slot: str) -> str:
        """The one-line reason a slot is assumed, in the session language (content-bank copy)."""
        if slot not in self._slot_ids():
            return ""
        return texts.reason(slot, turn.lang, self.selector.slot_def(slot).default_reason)

    def _nudge_choice(self, turn: Turn, choice: str | None) -> None:
        """The answer to the finish-with-smart-defaults card: finish, or keep going."""
        extra = dict(turn.state.extra)
        extra.pop("offered_finish", None)
        turn.set(pending_card=None, extra=extra, skips_in_row=0)
        if choice == "finish":
            self._apply_defaults(turn)

    async def _finish_with_defaults(self, turn: Turn) -> None:
        self._settle_policy_with_defaults(turn)  # while a company-rule card may still be pending
        turn.set(pending_card=None)
        self._apply_defaults(turn)
        await self._advance(turn, enforce_caps=False)

    def _apply_defaults(self, turn: Turn) -> None:
        """ "Finish with smart defaults": assume everything left, except must-confirm slots."""
        self._settle_policy_with_defaults(turn)
        turn.set(finished_with_defaults=True, skips_in_row=0, pending_card=None)
        self.fill_defaults(turn)
        self._say(turn, texts.say("defaults_applied", turn.lang))

    def fill_defaults(self, turn: Turn) -> list[str]:
        """Assume every empty applicable slot (default or recommended option) with a reason.

        Must-confirm slots of the mode (Enterprise payments, compliance, SSO) are left for a human.
        Returns the slot ids written.
        """
        store = turn.store
        written: list[str] = []
        archetype = self._archetype(turn)
        changed_before = list(store.changed)  # defaults are not changes the user made
        for sd in self.selector.slots_in_order(turn.mode, turn.policy):
            if sd.slot_id == IDEA_SLOT or turn.mode in sd.must_confirm_in:
                continue
            ctx = self._context(turn)
            if not holds(sd.ask_if, ctx):
                continue
            if sd.slot_id == COMPLIANCE_SLOT:
                written += self._assume_compliance(turn)
                continue
            if store.state(sd.slot_id) != "empty":
                continue
            writes = self.selector.assumption(sd.slot_id, archetype, turn.mode, ctx)
            if sd.slot_id == "locale" and turn.lang != "en":
                writes = [("locale", turn.lang)]
            for slot, value in writes:
                if store.state(slot) not in ("empty", "assumed"):
                    continue
                reason = self._reason(turn, slot)
                res = store.write(slot, value, state="assumed", source="default", evidence=reason)
                if res.applied:
                    written.append(slot)
                if slot == "idea.archetype" and res.value_changed:
                    self._cascade_archetype(turn)
                    archetype = self._archetype(turn)
        store.changed[:] = changed_before
        return written

    def _assume_compliance(self, turn: Turn) -> list[str]:
        written: list[str] = []
        for domain in turn.state.sensitive_flags:
            if domain in turn.state.compliance_confirmed:
                continue
            card = self.selector.compliance_card(domain, self._archetype(turn), turn.mode)
            if card is None:
                continue
            for slot, value in self.selector.assumption(
                card.slot, self._archetype(turn), turn.mode, {}, card=card
            ):
                reason = self._reason(turn, slot)
                if turn.store.write(
                    slot, value, state="assumed", source="default", evidence=reason
                ).applied:
                    written.append(slot)
            turn.set(compliance_confirmed=[*turn.state.compliance_confirmed, domain])
        return written

    # ----------------------------------------------------------------- visual cards
    async def _none_of_these(self, turn: Turn) -> None:
        """ "I don't like these": three new looks, then (after two rounds) describe or upload."""
        pending = turn.state.pending_card
        if pending is None:
            return
        card = pending.card
        primary = turn.state.extra.get("visual_primary", card.id)
        rounds = turn.state.visual_rounds.get(primary, 1)
        extra = {**turn.state.extra, "visual_primary": primary}
        turn.set(visual_rounds={**turn.state.visual_rounds, primary: rounds + 1}, extra=extra)
        if rounds < 2:
            pool = self.renderer.pool_card(
                card.slot, self._archetype(turn), turn.mode, turn.state.seen_option_ids
            )
            if pool is not None:
                self._show_card(turn, pool, counted=False)
                return
        self._show_card(turn, self.renderer.describe_look_card(turn.lang), counted=False)

    def _describe_look(self, turn: Turn, text: str) -> None:
        """A typed description of the look, mapped to the nearest preset (no LLM)."""
        turn.intent = "answer"
        themes = self.vocab.themes()
        theme_id = lx.nearest_theme(text, themes)
        if theme_id is None:
            ctx = self._context(turn)
            writes = self.selector.assumption(LOOK_SLOT, self._archetype(turn), turn.mode, ctx)
            value = writes[0][1] if writes else next(iter(themes))
            theme_id = str(value)
        turn.store.write(LOOK_SLOT, theme_id, state="confirmed", source="text", evidence=text[:80])
        norm = lx.normalize(text)
        color = lx.color_word(text)
        if color and any(c in norm for c in ("color", "colour", "accent", "rang")):
            turn.store.write("design.accent", color, state="confirmed", source="text")
        if "dark" in norm:
            turn.store.write("design.mode", "dark", state="confirmed", source="text")
        turn.set(pending_card=None, skips_in_row=0, off_topic_streak=0)
        label = self.vocab.label(LOOK_SLOT, theme_id, turn.lang)
        self._say(turn, texts.say("look_mapped", turn.lang, look=label.lower()))
        self._remember(turn, f"Look described: {text[:80]}")

    # ----------------------------------------------------------------- help, questions, chat
    async def _help_mode(self, turn: Turn) -> None:
        """Confused or vague: explain the choice in a line, or show three inspiration cards."""
        turn.set(help_mode=True)
        turn.intent = "confused" if turn.intent in (None, "confused") else turn.intent
        pending = turn.state.pending_card
        if pending is not None and pending.card.kind in ("standard", "compliance", "resolution"):
            self._say(turn, pending.card.help or texts.say("help_intro", turn.lang))
            self._represent(turn)
            return
        cards = self.banks.inspiration(self._archetype(turn))
        self._say(turn, texts.say("help_intro", turn.lang))
        if cards:
            self._show_card(turn, cards[0], counted=False)
        else:
            card, generated = await self._card_for_slot(turn, IDEA_SLOT)
            self._show_card(turn, card, counted=False, generated=generated)

    async def _conversational(
        self,
        turn: Turn,
        text: str,
        out: C1Output,
        ups: list[tuple[SlotUpdate, SlotData]],
    ) -> None:
        """A question or chit-chat: C3 answers; every message is still mined for slot values."""
        pending = turn.state.pending_card
        self._apply_updates(turn, ups)
        self._remember(turn, out.summary_update)
        if out.intent == "chitchat" and pending is not None:
            turn.set(off_topic_streak=turn.state.off_topic_streak + 1)
        stuck = not turn.store.is_filled(IDEA_SLOT)
        reply = await self.responder.respond(turn, out.intent, text, stuck=stuck)
        self._say(turn, reply.reply, chips=reply.suggested_chips, canned=False)
        if pending is None:
            await self._advance(turn)
            return
        streak = turn.state.off_topic_streak
        card = pending.card
        if streak >= NUDGE_AFTER and out.intent == "chitchat":
            if card.id in turn.state.nudged_card_ids:
                sd = self.selector.slot_def(card.slot)
                if (
                    turn.mode not in sd.must_confirm_in
                    and card.skip.enabled
                    and card.kind != "nudge"
                ):
                    self._say(turn, self._assume_card(turn, card))
                    turn.set(pending_card=None, off_topic_streak=0)
                    await self._advance(turn)
                    return
            else:
                turn.set(nudged_card_ids=[*turn.state.nudged_card_ids, card.id])
                self._say(turn, texts.say("nudge", turn.lang))
        self._represent(turn)

    async def _not_understood(self, turn: Turn) -> None:
        """ "I did not catch that; here are the options" (also when a fallback could not place it)."""
        pending = turn.state.pending_card
        self._say(turn, texts.say("didnt_catch", turn.lang))
        if pending is not None:
            turn.set(off_topic_streak=turn.state.off_topic_streak + 1)
            self._represent(turn)
        else:
            await self._advance(turn)

    async def _decline(self, turn: Turn, category: str | None) -> None:
        """Unsafe or disallowed: decline briefly, say why, offer a safe alternative; log it."""
        turn.intent = "unsafe"
        count = int(turn.state.extra.get("unsafe_count", "0")) + 1
        turn.set(extra={**turn.state.extra, "unsafe_count": str(count)})
        self._say(turn, self.safety.decline(category, turn.lang))
        self._notice(turn, "unsafe_declined", f"declined: {category or 'other'}", "warn")
        if turn.state.pending_card is not None:
            self._represent(turn)

    # ----------------------------------------------------------------- approval and change
    async def _on_approve(
        self,
        turn: Turn,
        text: str,
        out: C1Output,
        ups: list[tuple[SlotUpdate, SlotData]],
    ) -> None:
        turn.intent = "approve"
        gate = turn.req.gate_open
        confident = out.confidence >= APPROVAL_CONFIDENCE and not ups
        if gate is not None:
            if not confident and len(text.split()) > 3:  # "ok but make it blue": look for a change
                change = await self.changer.interpret(turn, text, gate_open=gate)
                if change.ops:
                    turn.intent = "change"
                    self._apply_ops(turn, change.ops, "text")
                    turn.change_ops = list(change.ops)
                    await self._advance(turn, enforce_caps=False)
                    return
            if ups:
                self._apply_updates_as_changes(turn, ups)
                turn.intent = "change"
                return
            turn.approval_confident = confident
            return
        pending = turn.state.pending_card
        if pending is not None and pending.card.type == "confirm" and confident:
            option = next((o for o in pending.card.options if o.recommended), None)
            if option is not None and self._apply_card_answer(
                turn, pending.card, [option.id], None, source="text"
            ):
                self._cascade_after(turn, pending.card)
                self._ack(turn)
                await self._advance(turn)
                return
        self._say(turn, texts.say("no_gate_open", turn.lang))
        self._represent(turn)

    async def _on_change(self, turn: Turn, text: str) -> None:
        turn.intent = "change"
        change = await self.changer.interpret(turn, text)
        if change.is_approval and not change.ops:
            turn.intent = "approve"
            turn.approval_confident = (
                turn.req.gate_open is not None and change.confidence >= APPROVAL_CONFIDENCE
            )
            return
        if not change.ops:
            self._say(turn, change.clarify_question or texts.say("didnt_catch", turn.lang))
            self._represent(turn)
            return
        before = list(turn.store.changed)
        self._apply_ops(turn, change.ops, "text")
        turn.change_ops = list(change.ops)
        noted = self._note_screen_ops(turn, change.ops)
        what = self._what_changed(turn, [s for s in turn.store.changed if s not in before])
        if noted and not what:
            self._say(turn, texts.say("screen_note", turn.lang))
        elif what:
            self._say(turn, texts.say("change_applied", turn.lang, what=what))
        else:
            self._ack(turn)
        await self._advance(turn, enforce_caps=False)

    def _note_screen_ops(self, turn: Turn, ops: list[ChangeOp]) -> bool:
        """Details about one screen cannot change a slot: keep them as notes for the builder."""
        notes = [
            op.instruction
            for op in ops
            if op.kind in ("patch_screen", "set_copy", "add_screen", "remove_screen")
            and op.instruction
        ]
        if not notes:
            return False
        joined = "; ".join(x for x in (turn.state.extra.get("screen_notes", ""), *notes) if x)
        turn.set(extra={**turn.state.extra, "screen_notes": joined[-600:]})
        for note in notes:
            self._remember(turn, f"Screen detail: {note[:120]}")
        return True

    def _what_changed(self, turn: Turn, slots: list[str]) -> str:
        return ", ".join(lx.slot_label(s, turn.lang).lower() for s in slots)

    def _apply_updates_as_changes(self, turn: Turn, ups: list[tuple[SlotUpdate, SlotData]]) -> None:
        """At a gate, slot values found in the text are a change request in their own right."""
        ops: list[ChangeOp] = []
        for u, value in ups:
            if u.negate:
                ops.append(ChangeOp(kind="clear_slot", slot_id=u.slot_id))
            elif isinstance(value, list):
                ops.append(
                    ChangeOp(kind="set_slot", slot_id=u.slot_id, value_list=[str(v) for v in value])
                )
            elif isinstance(value, bool):
                ops.append(ChangeOp(kind="set_slot", slot_id=u.slot_id, value_bool=value))
            elif value is not None:
                ops.append(ChangeOp(kind="set_slot", slot_id=u.slot_id, value_text=str(value)))
        self._apply_ops(turn, ops, "text")
        turn.change_ops = list(ops)
        turn.intent = "change"

    def _apply_ops(self, turn: Turn, ops: list[ChangeOp], source: SlotSource) -> None:
        """Patch operations on slots. Operations on screens and research belong to the flow."""
        state: SlotState = "assumed" if source in ("default", "research") else "confirmed"
        store = turn.store
        for op in ops:
            archetype = self._archetype(turn)
            if op.kind == "set_slot" and op.slot_id:
                raw: SlotData = (
                    op.value_list
                    if op.value_list is not None
                    else (op.value_bool if op.value_bool is not None else op.value_text)
                )
                value = self.vocab.validate(op.slot_id, raw, archetype)
                if value is None:
                    continue
                res = store.write(op.slot_id, value, state=state, source=source)
                if op.slot_id == "idea.archetype" and res.value_changed:
                    self._cascade_archetype(turn)
            elif op.kind == "clear_slot" and op.slot_id:
                store.clear(op.slot_id)
            elif op.kind == "toggle_feature" and op.target_id:
                self._toggle_feature(turn, op.target_id, op.value_bool is not False, state, source)
            elif (
                op.kind == "other"
                and op.instruction == "toggle_assumption"
                and op.slot_id
                and op.value_bool is False
            ):
                store.clear(op.slot_id)
                extra = {k: v for k, v in turn.state.extra.items() if k != "defaults_path"}
                turn.set(finished_with_defaults=False, extra=extra)  # ask it now
                self._forget_asked(turn, op.slot_id)

    def _forget_asked(self, turn: Turn, slot_id: str) -> None:
        """A slot that must be asked again may have its card shown again."""
        keep: list[str] = []
        for entry in turn.state.cards_asked:
            try:
                same_slot = self.banks.card(entry.split(":")[0]).slot == slot_id
            except KeyError:
                same_slot = False
            if not same_slot:
                keep.append(entry)
        turn.set(cards_asked=keep)

    def _toggle_feature(
        self, turn: Turn, feature: str, on: bool, state: SlotState, source: SlotSource
    ) -> None:
        archetype = self._archetype(turn)
        if feature not in self.vocab.feature_ids(archetype):
            return
        current = turn.store.list_value("scope.features")
        if on and feature not in current:
            turn.store.write("scope.features", [*current, feature], state=state, source=source)
        elif not on and feature in current:
            turn.store.remove_items("scope.features", [feature])

    # ================================================================= uploads
    async def _on_upload(self, turn: Turn, event: UploadAdded) -> None:
        turn.intent = "upload"
        name = event.filename
        meta = UploadMeta(upload_id=event.upload_id, filename=name, media_type=event.media_type)
        pending = turn.state.pending_card
        if event.text is not None:
            await self._on_document(turn, event, meta)
            return
        # an image or other file we do not read: keep it as the brand / look reference
        turn.store.write(
            "brand.assets",
            f"upload:{event.upload_id}",
            state="volunteered",
            source="upload",
            evidence=name,
        )
        meta = meta.model_copy(update={"summary": "reference file"})
        turn.set(uploads=[*turn.state.uploads, meta])
        self._say(turn, texts.say("upload_image", turn.lang, name=name))
        if pending is not None and pending.card.id == DESCRIBE_LOOK_ID:
            writes = self.selector.assumption(
                LOOK_SLOT, self._archetype(turn), turn.mode, self._context(turn)
            )
            if writes:
                turn.store.write(LOOK_SLOT, writes[0][1], state="assumed", source="upload")
            turn.set(pending_card=None)
        elif pending is not None and pending.card.slot == "brand.assets":
            turn.set(pending_card=None)
        await self._advance(turn)

    async def _on_document(self, turn: Turn, event: UploadAdded, meta: UploadMeta) -> None:
        name = event.filename
        screened = self.safety.screen(event.text or "")
        if screened.had_secret:
            self._notice(turn, "secret_redacted", self.safety.secret_notice(turn.lang), "warn")
        if screened.injection:
            meta = meta.model_copy(update={"injection_suspected": True})
            self._notice(turn, "injection_suspected", self.safety.injection_notice(turn.lang))
        body = screened.text.strip()
        self._scan_policy(turn, body)
        if not body:
            turn.set(uploads=[*turn.state.uploads, meta])
            self._say(turn, texts.say("upload_empty", turn.lang, name=name))
            await self._advance(turn)
            return
        out = await self.understander.understand(turn, f"(uploaded {name})", attachment_text=body)
        ups = self._valid_updates(turn, out.slot_updates)
        first_idea = not turn.store.is_filled(IDEA_SLOT)
        c2: C2Output | None = None
        if first_idea or len(body) >= LONG_TEXT_CHARS:
            c2 = await self.interpreter.interpret(
                turn, "" if first_idea else (turn.store.text_value(IDEA_SLOT) or ""), body
            )
        summary = name
        if c2 is not None and first_idea:
            self._ingest_idea(
                turn, body, out, c2, ups, source_text=c2.one_sentence_idea or name, source="upload"
            )
            self._add_sensitive(turn, detect_sensitive(body, self._archetype(turn), self.catalogs))
            summary = c2.one_sentence_idea or name
        else:
            self._apply_updates(turn, ups)
            if c2 is not None:
                self._add_sensitive(turn, c2.sensitive_flags)
                extra = [
                    (u, v)
                    for u, v in self._valid_updates(turn, c2.volunteered)
                    if u.slot_id not in turn.store.changed
                ]
                self._apply_updates(turn, extra)
                summary = c2.one_sentence_idea or out.summary_update or name
        meta = meta.model_copy(update={"summary": summary[:160]})
        turn.set(uploads=[*turn.state.uploads, meta])
        self._remember(turn, f"Shared {name}: {summary[:100]}")
        self._say(turn, texts.say("upload_understood", turn.lang, name=name, summary=summary[:160]))
        if c2 is not None:
            self._after_idea(turn, body, c2)
        pending = turn.state.pending_card
        if pending is not None and pending.card.slot == IDEA_SLOT:
            turn.set(pending_card=None)
        self._drop_answered_pending(turn)
        await self._advance(turn)

    # ================================================================= buttons
    async def _on_ui(self, turn: Turn, event: UiAction) -> None:
        action = event.action
        turn.intent = "other"
        if action == "idle":
            await self._on_idle(turn)
        elif action == "finish_with_defaults":
            await self._finish_with_defaults(turn)
        elif action == "stop":
            turn.intent = "stop"
            turn.stop_requested = True
        elif action == "pause":
            turn.intent = "pause"
            turn.pause_requested = True
        elif action == "restart":
            turn.intent = "start_over"
            turn.start_over_requested = True
        elif action == "resume":
            self._represent(turn)
            if turn.state.pending_card is None:
                await self._advance(turn)
        elif action in ("toggle_assumption", "toggle_feature") and event.target:
            turn.intent = "change"
            if action == "toggle_feature":
                op = ChangeOp(kind="toggle_feature", target_id=event.target, value_bool=event.value)
            else:
                op = ChangeOp.model_validate(
                    {
                        "kind": "other",
                        "slot_id": event.target,
                        "value_bool": event.value,
                        "instruction": "toggle_assumption",
                    }
                )
            self._apply_ops(turn, [op], "tap")
            turn.change_ops = [op]
            await self._advance(turn, enforce_caps=False)
        elif action == "edit_row" and event.target and event.target in self._slot_ids():
            card, generated = await self._card_for_slot(turn, event.target)
            self._show_card(turn, card, counted=False, generated=generated)
            turn.intent = "change"

    # ================================================================= company rules (policy)
    def _scan_policy(self, turn: Turn, text: str) -> None:
        """Note what the user's words ask for that the company rules do not allow (no LLM call)."""
        turn.policy_hits.extend(self.guard.scan(text, turn.policy))

    @staticmethod
    def _is_policy_card(card: Card) -> bool:
        return card.id == POLICY_REGION_CARD or card.id.startswith(POLICY_LIBRARY_PREFIX)

    @staticmethod
    def _policy_card_id(violation: Violation) -> str:
        if violation.kind == "region":
            return POLICY_REGION_CARD
        return f"{POLICY_LIBRARY_PREFIX}{violation.keys[0]}"

    @staticmethod
    def _in_review(turn: Turn) -> bool:
        return turn.req.gate_open is not None or turn.req.session_state in REVIEW_STATES

    def _policy_queue(self, turn: Turn) -> list[Violation]:
        raw = turn.state.extra.get(POLICY_QUEUE_KEY)
        if not raw:
            return []
        try:
            items = json.loads(raw)
        except ValueError:
            return []
        found = [Violation.from_json(i) for i in items] if isinstance(items, list) else []
        return [v for v in found if v is not None]

    def _save_policy_queue(self, turn: Turn, queue: list[Violation]) -> None:
        extra = dict(turn.state.extra)
        if queue:
            extra[POLICY_QUEUE_KEY] = json.dumps([v.to_json() for v in queue])
        else:
            extra.pop(POLICY_QUEUE_KEY, None)
        if extra != turn.state.extra:
            turn.set(extra=extra)

    def _enforce_policy(self, turn: Turn) -> None:
        """After the event is handled: never accept a banned library or a disallowed region silently.

        What the text named (``turn.policy_hits``) and what the ``regions`` slot now holds are
        checked against the policy. A disallowed region is taken out of the slot; the user gets the
        constraint in plain words and a card of the allowed alternatives, instead of the question
        that was about to be asked (it is asked again afterwards). Several violations queue up and
        are shown one at a time. Only in the interview: at a gate the flow decides what a change
        means.
        """
        policy = turn.policy
        controlled = turn.stop_requested or turn.pause_requested or turn.start_over_requested
        if not self.guard.active(policy) or self._in_review(turn) or controlled:
            return
        found = list(turn.policy_hits)
        bad = self.guard.disallowed_regions(turn.store.list_value("regions"), policy)
        if bad:
            self._strip_regions(turn, bad)
            found.append(Violation("region", tuple(bad)))
        queue = self._policy_queue(turn)
        pending = turn.state.pending_card
        shown_id = pending.card.id if pending is not None else None
        for v in found:
            queued = any(
                q.kind == v.kind and (v.kind == "region" or q.keys == v.keys) for q in queue
            )
            if self._policy_card_id(v) != shown_id and not queued:
                queue.append(v)
        if (pending is not None and self._is_policy_card(pending.card)) or not queue:
            self._save_policy_queue(turn, queue)
            return
        first = queue.pop(0)
        self._save_policy_queue(turn, queue)
        self._show_policy(turn, first)

    def _strip_regions(self, turn: Turn, bad: list[str]) -> None:
        """Take disallowed regions out of the slot; a slot nobody had filled before stays untouched."""
        store = turn.store
        store.remove_items("regions", bad)
        untouched = not store.is_filled("regions") and "regions" not in turn.req.state.slots
        if untouched and "regions" in store.changed:
            store.changed.remove("regions")

    def _region_name(self, turn: Turn, region_id: str) -> str:
        label = self.guard.region_label(region_id, turn.lang)
        return label or self.vocab.label("regions", [region_id], turn.lang)

    def _policy_card(self, turn: Turn, violation: Violation) -> Card | None:
        """The card of allowed alternatives for one violation (options come from policy and data)."""
        lang = turn.lang
        if violation.kind == "library":
            key = violation.keys[0]
            options = [
                CardOption(
                    id=a.id,
                    label=a.label,
                    description=a.description or None,
                    recommended=n == 0,
                )
                for n, a in enumerate(self.guard.alternatives(key, lang))
            ]
            return Card(
                id=self._policy_card_id(violation),
                slot="idea.summary",
                type="choice3",
                kind="resolution",
                text=texts.say("policy_library_question", lang, library=key),
                help=texts.say("policy_library_help", lang),
                options=options,
                other=OtherInput(enabled=False),
                skip=SkipSpec(enabled=False),
                ask_if="false",
            )
        allowed = list(dict.fromkeys(turn.policy.allowed_regions))
        if not allowed:
            return None
        region_options = [
            CardOption(
                id=region,
                label=self._region_name(turn, region),
                recommended=n == 0,
                sets={"regions": [region]},
            )
            for n, region in enumerate(allowed)
        ]
        return Card(
            id=POLICY_REGION_CARD,
            slot="regions",
            type="multi" if len(region_options) > 1 else "choice3",
            kind="resolution",
            text=texts.say("policy_region_question", lang),
            help=texts.say("policy_region_help", lang),
            options=region_options,
            other=OtherInput(enabled=False),
            skip=SkipSpec(enabled=False),
            ask_if="false",
        )

    def _show_policy(self, turn: Turn, violation: Violation) -> None:
        """Explain the constraint, remember the original request, show the allowed alternatives."""
        card = self._policy_card(turn, violation)
        if card is None:
            return
        lang = turn.lang
        if violation.kind == "library":
            key = violation.keys[0]
            explanation = texts.say("policy_library_explain", lang, library=key)
            asked = f"Asked for {key}, which company rules do not allow"
        else:
            names = ", ".join(self._region_name(turn, r) for r in violation.keys)
            allowed = ", ".join(self._region_name(turn, r) for r in turn.policy.allowed_regions)
            explanation = texts.say("policy_region_explain", lang, allowed=allowed, asked=names)
            asked = f"Asked for data in {names}, which company rules do not allow"
        self._displace_pending(turn)
        self._say(turn, explanation)
        self._remember(turn, asked)
        self._show_card(turn, card, counted=False)

    def _displace_pending(self, turn: Turn) -> None:
        """Take the pending card away (it is asked again later); a card shown this turn is withdrawn."""
        pending = turn.state.pending_card
        if pending is None:
            return
        mine = [
            e
            for e in turn.events
            if isinstance(e, CardEvent) and e.card.instance_id == pending.instance_id
        ]
        for event in mine:
            turn.events.remove(event)
            if pending.card.text in turn.spoken:
                turn.spoken.remove(pending.card.text)
        changes: dict[str, Any] = {"pending_card": None}
        if pending.card.kind in COUNTED_KINDS:
            state = turn.state
            changes["cards_asked"] = [
                a for a in state.cards_asked if a.split(":")[0] != pending.card.id
            ]
            changes["card_count"] = max(0, state.card_count - 1)
        turn.set(**changes)

    async def _on_policy_choice(self, turn: Turn, card: Card, event: OptionSelected) -> None:
        """A tap on an allowed alternative: apply it (source ``tap``) and carry on."""
        picked = [o for o in card.options if o.id in event.option_ids]
        if event.you_decide or event.skipped or not picked:
            self._say(turn, texts.say("didnt_catch", turn.lang))
            self._represent(turn)
            return
        if card.id == POLICY_REGION_CARD:
            labels = ", ".join(o.label for o in picked)
            turn.store.write(
                "regions",
                [o.id for o in picked],
                state="confirmed",
                source="tap",
                evidence=labels,
            )
            self._remember(turn, f"Data region set to {labels} (company rules)")
            self._say(turn, texts.say("policy_region_applied", turn.lang, region=labels))
        else:
            key = card.id.removeprefix(POLICY_LIBRARY_PREFIX)
            views = self.guard.alternatives(key, turn.lang)
            view = next((a for a in views if a.id == picked[0].id), None)
            phrase = view.phrase if view is not None else picked[0].label
            self._rewrite_library(turn, key, phrase)
            self._remember(turn, f"Company rule: {phrase} instead of {key}")
            applied = texts.say(
                "policy_library_applied", turn.lang, choice=picked[0].label, library=key
            )
            self._say(turn, applied)
        turn.set(pending_card=None, skips_in_row=0, off_topic_streak=0)
        await self._advance(turn)

    def _rewrite_library(self, turn: Turn, key: str, phrase: str) -> None:
        """Say ``phrase`` wherever the user's own words named the banned library."""
        store = turn.store
        idea = store.text_value(IDEA_SLOT)
        if idea:
            rewritten = self.guard.replace_library(idea, key, phrase)[:300]
            if rewritten != idea:
                store.write(IDEA_SLOT, rewritten, state="volunteered", source="text")
        listed = store.list_value("integrations.list")
        named = [i for i in listed if self.guard.names_library(i, key)]
        if named:
            store.remove_items("integrations.list", named)
        custom = turn.state.extra.get("custom_features", "")
        if custom and self.guard.names_library(custom, key):
            replaced = self.guard.replace_library(custom, key, phrase)
            turn.set(extra={**turn.state.extra, "custom_features": replaced})

    def _settle_policy_with_defaults(self, turn: Turn) -> None:
        """ "Finish with smart defaults" with a company-rule card open: the rule wins.

        The first allowed alternative is used for a banned library (a disallowed region was never
        written to the slot, so the policy's own regions apply).
        """
        waiting = self._policy_queue(turn)
        pending = turn.state.pending_card
        if pending is not None and pending.card.id.startswith(POLICY_LIBRARY_PREFIX):
            key = pending.card.id.removeprefix(POLICY_LIBRARY_PREFIX)
            waiting.insert(0, Violation("library", (key,)))
        for v in waiting:
            if v.kind != "library":
                continue
            alternatives = self.guard.alternatives(v.keys[0], turn.lang)
            if alternatives:
                phrase = alternatives[0].phrase
                self._rewrite_library(turn, v.keys[0], phrase)
                self._remember(turn, f"Company rule: {phrase} instead of {v.keys[0]}")
        self._save_policy_queue(turn, [])

    # ================================================================= silence
    async def _on_idle(self, turn: Turn) -> None:
        """Silence on the pending card, reported by the client (spec 3.4, 9: "One nudge, then assume").

        The first silence on a card says one gentle line and shows the same card again; the next
        takes the recommended option as ``assumed`` with its one-line reason, exactly like "You
        decide" (so three in a row still offer "Finish with smart defaults"). Where a person must
        answer (must-confirm slots, a contradiction, a company-rule card) or the card is a help,
        inspiration or finish-offer card, silence never answers for the user: a waiting line once,
        then nothing. With no card pending, at a gate or outside the interview it does nothing:
        silence never approves anything.
        """
        pending = turn.state.pending_card
        if (
            pending is None
            or turn.req.gate_open is not None
            or turn.req.session_state != "DISCOVERING"
        ):
            return
        card = pending.card
        if card.kind == "nudge":
            return  # the offer to finish with defaults is itself the nudge
        assumable = self._can_assume(turn, card)
        if card.id not in turn.state.nudged_card_ids:
            turn.set(nudged_card_ids=[*turn.state.nudged_card_ids, card.id])
            self._say(turn, texts.say("nudge" if assumable else "nudge_wait", turn.lang))
            self._represent(turn)
            return
        if assumable:
            await self._you_decide(turn)

    def _can_assume(self, turn: Turn, card: Card) -> bool:
        """The engine may answer this card for the user (an ordinary or compliance card)."""
        if card.kind not in ("standard", "compliance") or not card.skip.enabled:
            return False
        return turn.mode not in self.selector.slot_def(card.slot).must_confirm_in

    # ================================================================= what next
    async def _advance(self, turn: Turn, *, enforce_caps: bool = True) -> None:
        """Pick the next card, or complete: fill the defaults for what was never asked."""
        pending = turn.state.pending_card
        if pending is not None:
            self._displace_for_contradiction(turn, pending)
            if turn.state.pending_card is pending:
                return
        selection = self.selector.next(
            state=turn.state, store=turn.store, mode=turn.mode, policy=turn.policy
        )
        if selection.kind == "complete":
            self.fill_defaults(turn)
            return
        state = turn.state
        if enforce_caps and not selection.resolution:
            if limit_reached(state, turn.mode, turn.policy):
                self._offer_finish(turn, cap=True)
                return
            if state.skips_in_row >= SKIPS_TO_OFFER:
                self._offer_finish(turn, cap=False)
                return
        await self._show_selection(turn, selection)

    def _displace_for_contradiction(self, turn: Turn, pending: CardInstance) -> None:
        """A new contradiction goes first: the card it displaces is asked again afterwards."""
        if pending.card.kind == "resolution":
            return
        found = detect_contradiction(
            self.banks,
            turn.store,
            self._archetype(turn),
            turn.mode,
            turn.state.sensitive_flags,
        )
        if found is None:
            return
        state = turn.state
        changes: dict[str, Any] = {"pending_card": None}
        if pending.card.kind in COUNTED_KINDS:
            asked = [a for a in state.cards_asked if a.split(":")[0] != pending.card.id]
            changes["cards_asked"] = asked
            changes["card_count"] = max(0, state.card_count - 1)
        turn.set(**changes)

    async def _show_selection(self, turn: Turn, selection: Selection) -> None:
        if selection.kind == "write_card" and selection.slot:
            card, generated = await self.writer.write(turn, selection.slot), True
            self._show_card(turn, card, generated=generated)
            return
        if selection.card is None:  # pragma: no cover - the selector always sets a card
            return
        if selection.resolution:
            self._say(turn, texts.say("contradiction_intro", turn.lang))
            turn.store.mark_disputed(selection.card.slot)
        self._show_card(turn, selection.card)

    def _offer_finish(self, turn: Turn, *, cap: bool) -> None:
        """At a cap or after three skips: offer "finish with smart defaults" instead of asking more."""
        card = self.renderer.finish_offer_card(turn.lang, only_finish=cap)
        self._say(
            turn, texts.say("cap_reached" if cap else "finish_with_defaults_offer", turn.lang)
        )
        turn.set(extra={**turn.state.extra, "offered_finish": "1"})
        self._show_card(turn, card, counted=False)


def build_engine(gateway: Any, **kwargs: Any) -> DiscoverEngine:
    """Convenience constructor: ``build_engine(gateway)``."""
    return DiscoverEngine(gateway, **kwargs)
