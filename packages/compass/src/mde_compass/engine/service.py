"""The Compass service: the turn loop that wires ports, engine functions, store and views together.

Plain code controls flow (prompt 6.2): the model never chooses what to do next, it fills schemas through the
ports. One event at a time per session (an in-process lock queues events in order; the store's
``try_begin_turn`` is the cross-process backstop). A turn is three phases, because a transaction never spans a
model call (ADR 0004): (1) read and claim the turn, (2) think (model calls, pure code), (3) write everything
in one unit of work.

Everything the user sees is an outbound event stored with a per-session sequence number (SSE replay). The Brief
is the single source of truth: slots are rebuilt from it on every turn (``slots_from_brief``), and every
committed content change is a new version.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, JsonValue

from mde_compass.budget import CompassBudget
from mde_compass.catalog import Catalog
from mde_compass.config import DEFAULT_CONFIG, CompassConfig
from mde_compass.domain import (
    Brief,
    Card,
    CardOption,
    ChangeSet,
    Contributor,
    DiscoveryPlan,
    PatchOp,
    Provenance,
    ResearchRequest,
    SessionRecord,
    SessionState,
    StoredEvent,
    brief_hash,
    changed_sections,
    section_hashes,
)
from mde_compass.domain.cards import CardProgress, CustomInput, SkipSpec, YouDecide
from mde_compass.domain.changes import ChangeSource
from mde_compass.domain.common import ProvenanceType
from mde_compass.domain.events import (
    AssistantMessage,
    AssumptionDecision,
    BriefApproved,
    BriefEdit,
    BriefFeedback,
    BriefPatch,
    BriefReady,
    CardAnswer,
    CardBack,
    CardEvent,
    CardSkip,
    CardYouDecide,
    Command,
    ContributorSwitch,
    ErrorEvent,
    GateDecision,
    InboundEvent,
    LinkAdded,
    LookupEdit,
    LookupPreview,
    Notice,
    Playback,
    PlaybackItem,
    ProbeResultEvent,
    Progress,
    UploadAdded,
    UserMessage,
)
from mde_compass.domain.situation import SituationProfile
from mde_compass.engine import assumptions as assumptions_mod
from mde_compass.engine import cards as cards_mod
from mde_compass.engine import gate as gate_mod
from mde_compass.engine import patch as patch_mod
from mde_compass.engine import state as state_mod
from mde_compass.engine import voi as voi_mod
from mde_compass.engine.app_structure import expand_structure
from mde_compass.engine.brief_builder import apply_slots, empty_brief, slots_from_brief
from mde_compass.engine.language import detect_language
from mde_compass.engine.ports import (
    Caller,
    ChangeInput,
    HandoffPort,
    Ports,
    ProbeKind,
    ResolvedTarget,
    SlotView,
    TurnContext,
    TurnLine,
    TurnResult,
)
from mde_compass.engine.router import route as route_text
from mde_compass.engine.slots import SlotStore
from mde_compass.engine.validate import must_slots_missing
from mde_compass.safety.screen import screen
from mde_compass.store.ports import BriefVersionRecord, CallLogEntry, QuestionRecord, Store
from mde_compass.views.build import build_view_model
from mde_compass.views.consumers import build_handoff
from mde_compass.views.diff import diff_briefs
from mde_compass.views.provenance import provenance_for
from mde_compass.views.readiness import compute_readiness
from mde_core.clock import Clock, SystemClock
from mde_core.errors import (
    Conflict,
    GateError,
    InvalidTransition,
    MdeError,
    NotFound,
    TenantViolation,
)
from mde_core.ids import new_id
from mde_core.logging import get_logger

log = get_logger(__name__)

_TENANT: contextvars.ContextVar[str | None] = contextvars.ContextVar("compass_tenant", default=None)


@contextlib.contextmanager
def tenant_scope(tenant: str) -> Iterator[None]:
    """Bind the tenant for call-log writes made outside ``handle_event`` (smoke and eval drivers)."""
    token = _TENANT.set(tenant)
    try:
        yield
    finally:
        _TENANT.reset(token)


WELCOME_FRAME = "welcome"
VARIANTS_FRAME = "change_variants"
SECTION_STRING = {
    "idea": "section.idea",
    "context": "section.idea",
    "audience": "section.who",
    "apps": "section.apps_roles",
    "roles": "section.apps_roles",
    "scope": "section.features",
    "references": "section.look",
    "experience": "section.look",
    "access": "section.access",
    "integrations": "section.integrations_data",
    "data": "section.integrations_data",
    "constraints": "section.limits",
    "glossary": "section.words",
    "assumptions": "section.assumed",
    "open_questions": "section.unknown",
    "research_requests": "section.lookup",
    "assets": "section.sources",
    "sources": "section.sources",
    "declined": "section.cannot_build",
}
# Sections whose change re-seeds research, and which probes a change re-runs.
# Research is re-seeded only when an op touches what the requests are built from (prompt 6.10 step 6):
# geography, category, audience, references or features. A wording edit must not re-seed (undo stays exact).
RESEED_PATH_PREFIXES = (
    "idea.category",
    "idea.subcategory",
    "context.geo",
    "audience.",
    "references",
    "scope.",
)
PROBES_BY_SECTION = {
    "research": {
        "idea",
        "context",
        "audience",
        "scope",
        "references",
        "glossary",
        "research_requests",
    },
    "blueprint": {"apps", "roles", "scope", "integrations", "data", "constraints", "access"},
    "design": {"experience", "audience", "references"},
}
IDEA_CHARS = 500


def _text(value: JsonValue) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_text(v)}" for k, v in value.items())
    return str(value)


@lru_cache(maxsize=1)
def _chat_language_cues() -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = yaml.safe_load(
        (Path(__file__).parent / "chat_language.yaml").read_text(encoding="utf-8")
    )
    return tuple(data["chat_cues"]), tuple(data["app_cues"])


def _is_chat_language_switch(text: str) -> bool:
    """True when the message is about the chat language only (never about the app's languages)."""
    low = text.lower()
    chat, app = _chat_language_cues()
    return any(c in low for c in chat) and not any(a in low for a in app)


def _chat_language_target(text: str) -> str | None:
    """The language a chat-language switch asks for, when it names one (``en`` or ``hi``)."""
    low = text.lower()
    if "english" in low or "अंग्रेज़ी" in low or "अंग्रेजी" in low:
        return "en"
    if "hindi" in low or "हिंदी" in low or "हिन्दी" in low:
        return "hi"
    return None


def _idea_like(text: str) -> bool:
    """A first message must carry some content before it can become the idea (a bare "yes" cannot)."""
    return len(text.split()) >= 3 or len(text.strip()) >= 20


@dataclass
class _Turn:
    tenant: str
    session: SessionRecord
    rec: BriefVersionRecord
    brief: Brief
    slots: SlotStore
    questions: list[QuestionRecord]
    event: Any
    lang: str
    state: SessionState
    transcript: list[TurnLine] = field(default_factory=list)
    out: list[BaseModel] = field(default_factory=list)
    asked: list[tuple[Card, dict[str, JsonValue]]] = field(default_factory=list)
    answered: list[tuple[str, str, JsonValue]] = field(default_factory=list)
    change_set: ChangeSet | None = None
    approval: tuple[int, str] | None = None  # (version, label) when a baseline is created
    handoff_views: dict[str, dict[str, JsonValue]] | None = None
    original_text: str = ""

    @property
    def turn_id(self) -> str:
        return str(getattr(self.event, "event_id", ""))

    def say(self, text: str, chips: Sequence[str] = ()) -> None:
        self.out.append(AssistantMessage(text=text, language=self.lang, chips=list(chips)))

    def notice(
        self, code: str, text: str, *, chips: Sequence[str] = (), warn: bool = False
    ) -> None:
        self.out.append(
            Notice(code=code, text=text, chips=list(chips), severity="warning" if warn else "info")
        )

    def pending_card(self) -> Card | None:
        for q in reversed([*self.questions]):
            if q.outcome == "open" and q.card.frame != WELCOME_FRAME:
                return q.card
        for card, _ in reversed(self.asked):
            return card
        return None

    def asked_cards(self) -> list[Card]:
        return [q.card for q in self.questions] + [c for c, _ in self.asked]


class Service:
    """Implements ``mde_compass.engine.ports.CompassService``."""

    def __init__(
        self,
        *,
        store: Store,
        catalog: Catalog,
        budget: CompassBudget,
        handoff: HandoffPort,
        ports: Ports | None = None,
        clock: Clock | None = None,
        config: CompassConfig = DEFAULT_CONFIG,
        new_session_id: Callable[[], str] | None = None,
        mode_label: str = "mock",
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.budget_guard = budget
        self.handoff_port = handoff
        self.ports = ports
        self.clock = clock or SystemClock()
        self.config = config
        self.mode_label = mode_label
        self._new_session_id = new_session_id or (lambda: new_id("ses"))
        self._locks: dict[str, asyncio.Lock] = {}

    def bind_ports(self, ports: Ports) -> None:
        self.ports = ports

    # ------------------------------------------------------------------ call log sink
    async def log_call(self, session_id: str, entry: CallLogEntry) -> None:
        tenant = _TENANT.get()
        if tenant is None:
            return
        async with self.store.begin(tenant) as uow:
            await uow.call_log.record(session_id, entry)
            await uow.sessions.add_spend(session_id, entry.cost_micros)

    # ------------------------------------------------------------------ sessions
    async def create_session(
        self, caller: Caller, *, mode: str, locale: str, user_label: str
    ) -> SessionRecord:
        now = self.clock.now()
        sid = self._new_session_id()
        lang = locale if locale in self.config.supported_languages else self.config.default_language
        contributors = [
            Contributor(
                id=caller.user_id,
                name=user_label or caller.user_id,
                role="requester",
                decision_owner=True,
            )
        ]
        if mode == "enterprise":
            contributors += [
                Contributor(id="approver", name="Approver", role="approver"),
                Contributor(id="commenter", name="Commenter", role="commenter"),
            ]
        record = SessionRecord(
            id=sid, tenant_id=caller.tenant_id, user_id=caller.user_id,
            mode="enterprise" if mode == "enterprise" else "guided", locale=lang,
            user_label=user_label, state="WELCOME", brief_version=1, contributors=contributors,
            created_at=now, updated_at=now,
        )  # fmt: skip
        brief = empty_brief(sid, record.mode, lang)
        brief.version = 1
        welcome = self._welcome_card(lang)
        async with self.store.begin(caller.tenant_id) as uow:
            await uow.sessions.create(record)
            for c in contributors:
                await uow.contributors.upsert(sid, c)
            await uow.briefs.save_version(sid, brief)
            await uow.questions.record_asked(sid, welcome, {"why": "welcome"})
            await uow.events.append_outbound(
                sid, "assistant_message",
                AssistantMessage(text=self.catalog.t("welcome.greeting", lang), language=lang).model_dump(mode="json"),
            )  # fmt: skip
            await uow.events.append_outbound(
                sid, "card", CardEvent(card=welcome).model_dump(mode="json")
            )
            return await uow.sessions.get(sid)

    def _welcome_card(self, lang: str) -> Card:
        t = self.catalog.t
        opts = [
            CardOption(id="describe", label=t("welcome.start_describe", lang), basis="suggestion", recommended=True, reason=t("welcome.greeting", lang)),
            CardOption(id="upload", label=t("welcome.start_upload", lang)),
            CardOption(id="like_app", label=t("welcome.start_like_app", lang)),
            CardOption(id="just_build", label=t("welcome.start_just_build", lang)),
        ]  # fmt: skip
        return Card(
            id="Q-001", frame=WELCOME_FRAME, slots=[], type="choose_one",
            question=t("welcome.greeting", lang), options=opts,
            custom_input=CustomInput(enabled=False), you_decide=YouDecide(enabled=False),
            skip=SkipSpec(enabled=False), language=lang,
        )  # fmt: skip

    async def list_sessions(self, caller: Caller) -> list[SessionRecord]:
        async with self.store.begin(caller.tenant_id) as uow:
            return await uow.sessions.list_for_user(caller.user_id)

    async def get_session(self, caller: Caller, session_id: str) -> SessionRecord:
        async with self.store.begin(caller.tenant_id) as uow:
            return await uow.sessions.get(session_id)

    # ------------------------------------------------------------------ events
    async def handle_event(
        self, caller: Caller, session_id: str, event: InboundEvent
    ) -> TurnResult:
        assert self.ports is not None, "Service.ports is not bound"
        token = _TENANT.set(caller.tenant_id)
        try:
            lock = self._locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                event = self._sanitize(event)
                async with self.store.begin(caller.tenant_id) as uow:
                    session = await uow.sessions.get(session_id)
                    fresh = await uow.events.record_inbound(
                        session_id, event.event_id, event.type, event.model_dump(mode="json")
                    )
                    if not fresh:
                        return TurnResult(accepted=True, duplicate=True)
                    if not await uow.sessions.try_begin_turn(session_id):
                        raise Conflict("a turn is already running for this session")
                    seq_before = session.outbound_seq
                try:
                    try:
                        await self._process(caller, session_id, event)
                    except (NotFound, Conflict, TenantViolation):
                        raise
                    except Exception as exc:
                        log.exception("compass_turn_failed", session=session_id, inbound=event.type)
                        await self._emit_error(caller.tenant_id, session_id, event.event_id, exc)
                finally:
                    async with self.store.begin(caller.tenant_id) as uow:
                        await uow.sessions.end_turn(session_id)
                async with self.store.begin(caller.tenant_id) as uow:
                    produced = await uow.events.list_outbound(session_id, after_seq=seq_before)
                return TurnResult(accepted=True, events=produced)
        finally:
            _TENANT.reset(token)

    def _sanitize(self, event: InboundEvent) -> InboundEvent:
        """Redact secrets and mask card numbers in every free-text field before anything is stored."""
        if isinstance(event, UserMessage):
            return event.model_copy(update={"text": screen(event.text).text})
        if isinstance(event, BriefFeedback):
            return event.model_copy(update={"text": screen(event.text).text})
        if isinstance(event, LookupEdit):
            return event.model_copy(update={"text": screen(event.text).text})
        if isinstance(event, CardAnswer) and event.custom_text:
            return event.model_copy(update={"custom_text": screen(event.custom_text).text})
        return event

    async def _emit_error(self, tenant: str, session_id: str, turn_id: str, exc: Exception) -> None:
        lang = self.config.default_language
        err = ErrorEvent(code="turn_failed", message=self.catalog.t("error.generic", lang))
        async with self.store.begin(tenant) as uow:
            await uow.events.append_outbound(
                session_id, "error", err.model_dump(mode="json"), turn_id=turn_id
            )

    # ------------------------------------------------------------------ the three-phase turn
    async def _process(self, caller: Caller, session_id: str, event: InboundEvent) -> None:
        turn = await self._load(caller.tenant_id, session_id, event)
        if isinstance(event, UserMessage):
            await self._on_user_message(turn, event)
        elif isinstance(event, CardAnswer):
            await self._on_card_answer(turn, event)
        elif isinstance(event, CardSkip):
            await self._on_card_skip(turn, event)
        elif isinstance(event, CardYouDecide):
            await self._on_you_decide(turn, event)
        elif isinstance(event, CardBack):
            await self._on_back(turn)
        elif isinstance(event, Command):
            await self._on_command(turn, event.name)
        elif isinstance(event, BriefEdit):
            await self._on_brief_edit(turn, event)
        elif isinstance(event, BriefFeedback):
            await self._on_feedback(turn, event)
        elif isinstance(event, AssumptionDecision):
            await self._on_assumption(turn, event)
        elif isinstance(event, LookupEdit):
            await self._on_lookup(turn, event)
        elif isinstance(event, ContributorSwitch):
            await self._on_switch(turn, event)
        elif isinstance(event, GateDecision):
            await self._on_gate(turn, event)
        elif isinstance(event, (UploadAdded, LinkAdded)):
            turn.notice("upload_not_ready", self.catalog.t("welcome.upload_not_ready", turn.lang))
        await self._commit(turn)

    async def _load(self, tenant: str, session_id: str, event: InboundEvent) -> _Turn:
        async with self.store.begin(tenant) as uow:
            session = await uow.sessions.get(session_id)
            rec = await uow.briefs.get(session_id)
            questions = await uow.questions.list(session_id)
            transcript: list[TurnLine] = []
            after = 0
            for _ in range(5):
                page = await uow.events.list_history(session_id, after_idx=after, limit=200)
                for item in page.items:
                    line = self._line(item)
                    if line:
                        transcript.append(line)
                if page.next_after_idx is None:
                    break
                after = page.next_after_idx
        lang = session.locale
        slots = SlotStore(self.catalog, clock=self.clock)
        slots.load(self._slot_records(rec.brief))
        return _Turn(
            tenant=tenant, session=session, rec=rec, brief=rec.brief.model_copy(deep=True), slots=slots,
            questions=questions, event=event, lang=lang, state=session.state,
            transcript=transcript[-self.config.gate.history_slice_turns :],
        )  # fmt: skip

    @staticmethod
    def _line(item: StoredEvent) -> TurnLine | None:
        text = item.payload.get("text")
        if not isinstance(text, str) or not text:
            return None
        if item.direction == "in" and item.type == "user_message":
            return TurnLine(role="user", text=text)
        if item.direction == "out" and item.type == "assistant_message":
            return TurnLine(role="assistant", text=text)
        return None

    async def _commit(self, turn: _Turn) -> None:
        sid = turn.session.id
        new_hash = brief_hash(turn.brief)
        changed = new_hash != turn.rec.hash
        async with self.store.begin(turn.tenant) as uow:
            version = turn.rec.version
            if changed:
                turn.brief.version = turn.rec.version + 1
                saved = await uow.briefs.save_version(
                    sid, turn.brief, change_set_id=turn.change_set.id if turn.change_set else None
                )
                version = saved.version
            if turn.change_set is not None:
                await uow.change_sets.save(sid, turn.change_set)
            for card, why in turn.asked:
                await uow.questions.record_asked(sid, card, why)
            for card_id, outcome, answer in turn.answered:
                await uow.questions.record_answer(sid, card_id, outcome, answer)  # type: ignore[arg-type]
            session = await uow.sessions.get(sid)
            session.state = turn.state
            session.locale = turn.lang
            session.brief_version = version
            session.updated_at = self.clock.now()
            idea = turn.brief.idea.get("summary")
            if idea is not None and isinstance(idea.value, str):
                session.title = idea.value[:80]
            if turn.approval is not None:
                session.approved_version = turn.approval[0]
                session.approved_hash = new_hash
            await uow.sessions.save(session)
            if turn.approval is not None:
                await uow.briefs.mark_baseline(sid, turn.approval[0], turn.approval[1])
            for ev in turn.out:
                payload = ev.model_dump(mode="json")
                await uow.events.append_outbound(
                    sid, str(payload["type"]), payload, turn_id=turn.turn_id
                )
        if turn.handoff_views is not None and turn.approval is not None:
            await self.handoff_port.hand_off(
                session_id=sid,
                version=turn.approval[0],
                brief_hash=new_hash,
                views=turn.handoff_views,
            )

    # ------------------------------------------------------------------ helpers
    def _t(self, turn: _Turn, key: str, **values: str | int) -> str:
        return self.catalog.t(key, turn.lang, **values)

    def _go(self, turn: _Turn, trigger: str, fallback: SessionState) -> None:
        try:
            turn.state = state_mod.next_state(turn.state, trigger)
        except InvalidTransition:
            log.warning("compass_state_fallback", state=turn.state, trigger=trigger, to=fallback)
            turn.state = fallback

    def _plan(self, turn: _Turn) -> DiscoveryPlan:
        return turn.brief.meta.plan or DiscoveryPlan()

    def _ctx(self, turn: _Turn) -> TurnContext:
        views = [
            SlotView(slot_id=sid, value=rec.value, state=rec.status, confidence=rec.confidence)
            for sid, rec in turn.slots.all().items()
        ]
        return TurnContext(
            session_id=turn.session.id, tenant_id=turn.tenant, language=turn.lang, mode=turn.session.mode,
            depth=self._plan(turn).depth, profile=turn.brief.meta.situation_profile, slots=views,
            recent_turns=turn.transcript[-self.config.gate.history_slice_turns :],
            pending_card=turn.pending_card(), question_n=self._question_n(turn),
        )  # fmt: skip

    def _question_n(self, turn: _Turn) -> int:
        return sum(1 for c in turn.asked_cards() if c.frame not in (WELCOME_FRAME, VARIANTS_FRAME))

    def _rebuild(self, turn: _Turn) -> None:
        brief = apply_slots(turn.brief, turn.slots, self.catalog)
        self._enrich_intents(brief)
        brief = assumptions_mod.sync_assumptions(brief, turn.slots.all(), self.catalog)
        brief.readiness = compute_readiness(brief, self.catalog)
        turn.brief = brief

    def _slot_records(self, brief: Brief) -> list[Any]:
        """Slot records from the Brief with each record's last contributor restored (enterprise conflicts)."""
        records = slots_from_brief(brief, self.catalog, self.clock)
        for rec in records:
            if rec.contributor_id is None:
                rec.contributor_id = next(
                    (p.contributor_id for p in reversed(rec.provenance) if p.contributor_id), None
                )
        return records

    def _enrich_intents(self, brief: Brief) -> None:
        """A feature's intent (the job it does) comes from the family data or the hypothesis when the user
        only named the feature; custom features stay blank and the blueprint probe asks about them."""
        intents: dict[str, str] = {}
        for family in self.catalog.families.values():
            for feature in family.typical_features:
                intents[feature.name.en.lower()] = feature.intent
                intents[feature.name.hi.lower()] = feature.intent
        hyp = brief.meta.hypothesis
        if hyp is not None:
            for hf in hyp.likely_features:
                if hf.intent:
                    intents.setdefault(hf.name.lower(), hf.intent)
        described = self.catalog.slots.get("scope.features")
        for option in described.fallback_card.options if described else ():
            for label in (option.label.en, option.label.hi):
                intents.setdefault(
                    label.lower(), label
                )  # the label already says what people can do
        for rec in brief.scope:
            if not rec.intent:
                rec.intent = intents.get(rec.name.lower(), "")

    def _resync_slots(self, turn: _Turn) -> None:
        slots = SlotStore(self.catalog, clock=self.clock)
        slots.load(self._slot_records(turn.brief))
        turn.slots = slots

    def _prov(self, turn: _Turn, kind: str, quote: str = "") -> Provenance:
        contributor = getattr(turn.event, "contributor_id", None)
        return Provenance(
            type=cast(ProvenanceType, kind),
            ref=turn.turn_id,
            quote=quote[:300],
            date=self.clock.now(),
            contributor_id=contributor,
        )  # type: ignore[arg-type]

    def _label(self, turn: _Turn, slot_id: str) -> str:
        sd = self.catalog.slots.get(slot_id)
        return sd.label.get(turn.lang) if sd else slot_id

    def _corpus(self, turn: _Turn) -> str:
        return " ".join(line.text for line in turn.transcript if line.role == "user")

    def _blockers(
        self, turn: _Turn, *, supplied_version: int | None = None, supplied_hash: str | None = None
    ) -> list[gate_mod.Blocker]:
        brief = turn.brief
        cur_hash = brief_hash(brief)
        cur_version = turn.rec.version + (1 if cur_hash != turn.rec.hash else 0)
        roles = {c.id: c.role for c in turn.session.contributors}
        by_role = {roles[cid]: h for cid, h in brief.meta.approvals.items() if cid in roles}
        return gate_mod.gate_blockers(
            brief,
            must_missing=must_slots_missing(turn.slots, self.catalog, mode=turn.session.mode),
            probes=brief.meta.probes,
            blocking_questions=sum(1 for q in brief.open_questions if q.blocking),
            mode=turn.session.mode,
            approvals=by_role,
            required_roles=self.config.gate.required_approver_roles,
            supplied_version=supplied_version,
            supplied_hash=supplied_hash,
            current_hash=cur_hash,
            current_version=cur_version,
        )

    def _blocker_lines(self, turn: _Turn, blockers: Sequence[gate_mod.Blocker]) -> list[str]:
        return [self.catalog.t(b.key, turn.lang, **b.values) for b in blockers]

    # ------------------------------------------------------------------ user messages
    async def _on_user_message(self, turn: _Turn, ev: UserMessage) -> None:
        assert self.ports is not None
        scr = screen(ev.text)
        text = scr.text
        turn.original_text = text
        turn.lang = (
            ev.lang_hint
            if ev.lang_hint in self.config.supported_languages
            else detect_language(text, turn.lang)
        )
        for flag in scr.flags:
            if flag == "secret":
                turn.notice("secret_redacted", self._t(turn, "error.generic"), warn=True)
        if _is_chat_language_switch(text):
            target = _chat_language_target(text)
            if target in self.config.supported_languages:
                turn.lang = target
        if turn.state == "PAUSED":  # a message while paused is an implicit resume, never silence
            turn.state = turn.session.resume_state or "INTERVIEWING"
            turn.say(self._t(turn, "notice.paused_resumed"))
        route = route_text(
            text, has_pending_card=turn.pending_card() is not None, language=turn.lang
        )
        if route is not None:
            await self._on_route(turn, route.kind)
            return
        if scr.unsafe:
            turn.say(self._t(turn, "fallback.unsafe_decline"))
            return
        if scr.declined is not None:
            await self._decline(turn, scr.declined)
            return
        if scr.tier_b:
            labels = [
                self.catalog.capabilities[i].label.get(turn.lang)
                for i in scr.tier_b
                if i in self.catalog.capabilities
            ]
            if labels:
                turn.notice("tier_b", self._t(turn, "notice.tier_b", items=", ".join(labels)))
        has_draft = turn.slots.filled("idea.summary")
        if not has_draft and not _idea_like(text):
            self._ask_idea_card(turn)
            return
        if turn.state == "BRIEF_REVIEW":
            blockers = self._blockers(turn)
            verdict = gate_mod.classify_yes(
                text,
                gate_open=not blockers,
                language=turn.lang,
                confidence_min=self.config.gate.typed_yes_min_confidence,
            )
            if verdict == "yes":
                brief = turn.brief
                await self._on_gate(
                    turn,
                    GateDecision(event_id=turn.turn_id, contributor_id=getattr(ev, "contributor_id", None), decision="approve", version=turn.rec.version, brief_hash=brief_hash(brief), section_hashes=section_hashes(brief)),
                )  # fmt: skip
                return
        ctx = self._ctx(turn)
        understood = await self.ports.understander.understand(ctx, text, input_mode=ev.input_mode)
        if understood.language in self.config.supported_languages and not text.isascii():
            turn.lang = understood.language
        corrected = any(
            u.basis in ("user_text", "voice") and not u.remove for u in understood.slot_updates
        )
        if (
            understood.intent == "change_request"
            and has_draft
            and not corrected
            and turn.state not in ("WELCOME",)
        ):
            await self._change_from_text(turn, text, ResolvedTarget())
            return
        if understood.intent in ("question", "price_or_time", "tech_choice", "off_topic"):
            ans = await self.ports.answerer.answer(ctx, text)
            turn.say(ans.text)
            await self._reshow_or_advance(turn)
            return
        if understood.intent == "unsafe":
            turn.say(self._t(turn, "fallback.unsafe_decline"))
            return
        first = not has_draft
        applied = self._write_updates(turn, understood.slot_updates, text)
        if first and not turn.slots.filled("idea.summary") and len(text.strip()) >= 3:
            turn.slots.write(
                "idea.summary", text.strip()[:IDEA_CHARS], state="volunteered",
                provenance=self._prov(turn, "user_text", text), confidence=0.6, turn_id=turn.turn_id,
                contributor_id=getattr(ev, "contributor_id", None),
            )  # fmt: skip
        for ref in understood.references:
            self._add_reference(turn, ref, text)
        if applied:
            turn.out.append(
                Notice(
                    code="picked_up",
                    text=self._t(turn, "notice.picked_up", items=", ".join(applied)),
                    chips=applied,
                )
            )
        if first:
            await self._first_understanding(turn, text)
        else:
            self._rebuild(turn)
        await self._advance(turn)

    def _write_updates(self, turn: _Turn, updates: Sequence[Any], text: str) -> list[str]:
        chips: list[str] = []
        for u in updates:
            if u.slot_id not in self.catalog.slots:
                continue
            if u.slot_id == "locales" and _is_chat_language_switch(text):
                continue
            if u.basis in ("user_text", "voice"):
                state, ptype = "volunteered", u.basis
            else:
                state, ptype = "assumed", "model_knowledge"
            res = turn.slots.write(
                u.slot_id, u.value, state=state, provenance=self._prov(turn, ptype, u.quote),
                confidence=u.confidence, turn_id=turn.turn_id, remove=u.remove,
                contributor_id=getattr(turn.event, "contributor_id", None),
            )  # fmt: skip
            if res.applied and state == "volunteered":
                chips.append(f"{self._label(turn, u.slot_id)}: {_text(u.value)}"[:80])
        return chips

    def _add_reference(self, turn: _Turn, ref: str, text: str) -> None:
        current = turn.slots.get("scope.references")
        items = list(current.value) if current and isinstance(current.value, list) else []
        if any(
            isinstance(i, dict) and str(i.get("label", "")).lower() == ref.lower() for i in items
        ):
            return
        items.append({"label": ref, "borrow": [], "avoid": []})
        turn.slots.write(
            "scope.references", items, state="volunteered", provenance=self._prov(turn, "user_text", ref),
            confidence=0.6, turn_id=turn.turn_id,
        )  # fmt: skip

    async def _decline(self, turn: _Turn, match: Any) -> None:
        turn.notice(
            "declined_category",
            self._t(
                turn, "notice.declined_category", what=match.label, alternative=match.alternative
            ),
        )
        from mde_compass.domain import DeclinedRecord

        turn.brief.declined.append(
            DeclinedRecord(what=match.label, reason=match.reason, alternative=match.alternative, status="confirmed", tier="Locked", provenance=[self._prov(turn, "user_text", turn.original_text)], confidence=0.9)
        )  # fmt: skip

    async def _first_understanding(self, turn: _Turn, text: str) -> None:
        assert self.ports is not None
        self._rebuild(turn)
        ctx = self._ctx(turn)
        situation = await self.ports.situator.situate(ctx, user_text=text)
        profile: SituationProfile = situation.profile
        turn.brief.meta.situation_profile = profile
        depth = (
            "large"
            if turn.session.mode == "enterprise" or profile.size_stakes == "enterprise"
            else "standard"
        )
        if profile.urgency == "just_build":
            depth = "express"
        budget = {
            "express": self.config.questions.express_max,
            "standard": self.config.questions.standard_target,
            "large": self.config.questions.hard_cap,
        }[depth]
        turn.brief.meta.plan = DiscoveryPlan(
            depth=depth, question_budget=budget, minimum_viable=profile.urgency == "just_build"
        )  # type: ignore[arg-type]
        turn.brief.meta.depth_profile = depth
        turn.brief.meta.hypothesis = situation.hypothesis
        hyp = situation.hypothesis
        if hyp.category and not turn.slots.filled("idea.category"):
            turn.slots.write("idea.category", hyp.category, state="assumed", provenance=Provenance(type="model_knowledge", ref="hypothesis", date=self.clock.now()), confidence=hyp.confidence, turn_id=turn.turn_id)  # fmt: skip
        if hyp.subcategory and not turn.slots.filled("idea.subcategory"):
            turn.slots.write("idea.subcategory", hyp.subcategory, state="assumed", provenance=Provenance(type="model_knowledge", ref="hypothesis", date=self.clock.now()), confidence=hyp.confidence, turn_id=turn.turn_id)  # fmt: skip
        self._rebuild(turn)
        self._go(turn, "idea_received", "UNDERSTANDING")
        self._go(turn, "understood", "UNDERSTANDING")
        summary_rec = turn.slots.get("idea.summary")
        items = [PlaybackItem(key="idea.summary", label=self._label(turn, "idea.summary"), value=_text(summary_rec.value if summary_rec else ""), basis="your_words")]  # fmt: skip
        if hyp.category:
            items.append(
                PlaybackItem(
                    key="idea.category",
                    label=self._label(turn, "idea.category"),
                    value=hyp.category,
                    basis="suggestion",
                )
            )
        if hyp.likely_roles:
            items.append(PlaybackItem(key="roles.list", label=self._label(turn, "roles.list"), value=", ".join(hyp.likely_roles[:5]), basis="suggestion"))  # fmt: skip
        if hyp.likely_features:
            items.append(PlaybackItem(key="scope.features", label=self._label(turn, "scope.features"), value=", ".join(f.name for f in hyp.likely_features[:6]), basis="typical" if any(not f.custom for f in hyp.likely_features) else "suggestion"))  # fmt: skip
        missing = must_slots_missing(turn.slots, self.catalog, mode=turn.session.mode)
        turn.out.append(
            Playback(
                title=self._t(turn, "playback.title"),
                items=items,
                unclear=[self._label(turn, s) for s in missing[:5]],
            )
        )
        self._go(turn, "playback_shown", "PLAYBACK")

    # ------------------------------------------------------------------ the interview loop
    def _inferability(self, turn: _Turn) -> dict[str, float]:
        out: dict[str, float] = {}
        for sid, rec in turn.slots.all().items():
            if rec.status == "assumed":
                out[sid] = max(rec.confidence, 0.5)
        return out

    async def _advance(self, turn: _Turn) -> None:
        if turn.state in ("WELCOME", "INTAKE", "UNDERSTANDING", "PLAYBACK", "INTERVIEWING"):
            if await self._ask_next(turn) or await self._ask_missing_must(turn):
                turn.state = "INTERVIEWING"
                return
            self._go(turn, "queue_empty", "SEEDING")
        if turn.state == "SEEDING":
            await self._seed(turn)
            self._go(turn, "research_seeded", "PROBING")
        if turn.state == "PROBING":
            if not await self._probe(turn):
                turn.state = "INTERVIEWING"
                return
            self._go(turn, "probes_done", "BRIEF_REVIEW")
        if turn.state == "BRIEF_REVIEW":
            await self._open_review(turn)

    def _derive_subcategory(self, turn: _Turn) -> None:
        """idea.subcategory is required but no card writes it: assume it from the hypothesis, else from the
        category's label, listed in "We assumed" (never Locked)."""
        if turn.slots.filled("idea.subcategory"):
            return
        hyp = turn.brief.meta.hypothesis
        cat = turn.slots.get("idea.category")
        value = (hyp.subcategory if hyp is not None else "") or (
            _text(cat.value) if cat is not None and cat.value else ""
        )
        if value:
            turn.slots.write(
                "idea.subcategory",
                value,
                state="assumed",
                provenance=Provenance(type="model_knowledge", ref="derived", date=self.clock.now()),
                confidence=0.4,
                turn_id=turn.turn_id,
            )
            self._rebuild(turn)

    async def _ask_missing_must(self, turn: _Turn) -> bool:
        """A required slot with no default that is still empty after its frame was asked (a frame batches
        several slots but one card settles one) gets ONE follow-up card; never more than twice per slot."""
        self._derive_subcategory(turn)
        over_cap = self._question_n(turn) >= self.config.questions.hard_cap
        counts: dict[str, int] = {}
        for card in turn.asked_cards():
            for sid in card.slots:
                counts[sid] = counts.get(sid, 0) + 1
        for sid in must_slots_missing(turn.slots, self.catalog, mode=turn.session.mode):
            sd = self.catalog.slots[sid]
            if sd.default is not None or counts.get(sid, 0) >= 2:
                continue
            if over_cap and not sd.must_confirm:
                continue
            frame = next((f for f in self.catalog.frames.values() if sid in f.slots), None)
            if frame is None:
                continue
            item = voi_mod.QueueItem(
                frame_id=frame.id,
                slots=[sid],
                action="ask",
                score=50.0,
                must_confirm=sd.must_confirm,
                reason="required answer still empty",
            )
            await self._ask_next(turn, forced=item)
            return True
        return False

    async def _reshow_or_advance(self, turn: _Turn) -> None:
        pending = turn.pending_card()
        if pending is not None and turn.state in ("INTERVIEWING", "PLAYBACK"):
            turn.out.append(CardEvent(card=pending))
            return
        await self._advance(turn)

    def _queue(self, turn: _Turn) -> list[voi_mod.QueueItem]:
        asked = [
            c.frame for c in turn.asked_cards() if c.frame not in (WELCOME_FRAME, VARIANTS_FRAME)
        ]
        return voi_mod.plan_queue(
            turn.slots.all(), self.catalog, profile=turn.brief.meta.situation_profile, depth=self._plan(turn).depth,
            asked_frames=asked, inferability=self._inferability(turn), config=self.config.questions, mode=turn.session.mode,
        )  # fmt: skip

    async def _ask_next(self, turn: _Turn, forced: voi_mod.QueueItem | None = None) -> bool:
        queue = self._queue(turn)
        for item in queue:
            if item.action == "assume":
                self._assume_default(turn, item)
        item = forced or next((i for i in queue if i.action in ("ask", "propose")), None)
        if item is None:
            self._rebuild(turn)
            return False
        asked_frames = [c.frame for c in turn.asked_cards()]
        total = voi_mod.estimate_total(queue, asked_frames)
        card = await self._make_card(
            turn,
            item,
            n=self._question_n(turn) + 1,
            of_about=max(total, self._question_n(turn) + 1),
        )
        self._rebuild(turn)
        turn.asked.append(
            (
                card,
                {
                    "frame": item.frame_id,
                    "action": item.action,
                    "score": item.score,
                    "reason": item.reason,
                },
            )
        )
        turn.out.append(
            Progress(
                stage="interview",
                n=card.progress.n if card.progress else 0,
                of_about=card.progress.of_about if card.progress else 0,
            )
        )
        turn.out.append(CardEvent(card=card))
        return True

    def _assume_default(self, turn: _Turn, item: voi_mod.QueueItem) -> None:
        for sid in item.slots:
            sd = self.catalog.slots.get(sid)
            if sd is None or sd.default is None or turn.slots.filled(sid):
                continue
            turn.slots.write(
                sid, cast(JsonValue, sd.default), state="assumed", provenance=Provenance(type="pattern", ref=f"catalog:{sid}", date=self.clock.now()),
                confidence=0.5, turn_id=turn.turn_id,
            )  # fmt: skip

    async def _make_card(
        self, turn: _Turn, item: voi_mod.QueueItem, *, n: int, of_about: int
    ) -> Card:
        assert self.ports is not None
        hyp = turn.brief.meta.hypothesis
        frame = self.catalog.frames.get(item.frame_id)
        if (
            frame is not None
            and frame.card_type in ("choose_one", "pick_many")
            and all(
                self.catalog.slots[sid].value_schema.kind == "text"
                for sid in item.slots
                if sid in self.catalog.slots
            )
        ):
            # A choice card cannot be written for text-only slots: confirm the whole frame instead.
            item = replace(item, slots=list(frame.slots))
        hints: list[str] = []
        if hyp is not None:
            if item.frame_id == "features":
                hints = [f.name for f in hyp.likely_features if not f.custom]
            elif item.frame_id == "roles":
                hints = list(hyp.likely_roles)
            elif item.frame_id == "business_model":
                hints = list(hyp.business_model_candidates)
        if not hints:
            family_ids = list(hyp.likely_families) if hyp is not None else []
            cat = turn.slots.get("idea.category")
            if (
                cat is not None
                and isinstance(cat.value, str)
                and cat.value in self.catalog.families
            ):
                family_ids.insert(0, cat.value)
            hints = cards_mod.family_hints(
                self.catalog, item.slots, family_ids[:2], language=turn.lang
            )
        user_text = self._corpus(turn) + " " + turn.original_text
        evidence = [line.text for line in turn.transcript if line.role == "user"][-3:]
        card_id = f"Q-{len(turn.asked_cards()) + 1:03d}"
        selected = self._selected_labels(turn, item.slots)
        req = cards_mod.request_for(
            item,
            self.catalog,
            ctx_language=turn.lang,
            hints=hints,
            evidence=evidence,
            avoid_labels=selected,
        )
        ctx = self._ctx(turn)
        for _ in range(self.config.questions.card_validation_retries):
            draft = await self.ports.card_writer.write(ctx, req)
            if not cards_mod.validate_card(
                draft,
                req,
                self.catalog,
                language=turn.lang,
                user_text=user_text,
                config=self.config.questions,
            ):
                return cards_mod.assemble_card(
                    draft,
                    req,
                    card_id=card_id,
                    n=n,
                    of_about=of_about,
                    language=turn.lang,
                    catalog=self.catalog,
                )
        return cards_mod.fallback_card(
            item.frame_id, self.catalog, language=turn.lang, card_id=card_id, n=n, of_about=of_about
        )

    # ------------------------------------------------------------------ seeding, probes, review
    async def _seed(self, turn: _Turn) -> None:
        assert self.ports is not None
        self._rebuild(turn)
        seed = await self.ports.seeder.seed(self._ctx(turn), turn.brief)
        kept = [r for r in turn.brief.research_requests if r.user_added]
        fresh: list[ResearchRequest] = []
        for r in seed.requests:
            fresh.append(r.model_copy(update={"id": turn.brief.ids.allocate("RR")}))
        turn.brief.research_requests = [*fresh, *kept]
        turn.brief.search_hints = seed.hints
        turn.out.append(LookupPreview(requests=turn.brief.research_requests))

    async def _run_probes(self, turn: _Turn, kinds: Sequence[ProbeKind]) -> dict[str, Any]:
        assert self.ports is not None
        ctx = self._ctx(turn)
        results = await asyncio.gather(
            *(self.ports.prober.probe(ctx, k, turn.brief) for k in kinds)
        )
        return dict(zip(kinds, results, strict=True))

    async def _probe(self, turn: _Turn) -> bool:
        """Run the three canaries; assume-fixes are applied (shown for approval), ask-fixes become ONE card.
        Returns True when probing is done (passed or flagged degraded), False when a card was asked."""
        kinds: list[ProbeKind] = ["research", "blueprint", "design"]
        for _ in range(self.config.gate.probe_repair_rounds + 1):
            results = await self._run_probes(turn, kinds)
            for kind, res in results.items():
                turn.brief.meta.probes[kind] = res.passed
                turn.out.append(
                    ProbeResultEvent(
                        kind=cast(ProbeKind, kind),
                        passed=res.passed,
                        gaps=[g.question for g in res.gaps],
                        round=turn.brief.meta.probe_rounds + 1,
                    )
                )
            failing = [k for k, r in results.items() if not r.passed]
            if not failing:
                turn.brief.meta.degraded = False
                return True
            if turn.brief.meta.probe_rounds >= self.config.gate.probe_repair_rounds:
                turn.brief.meta.degraded = True
                turn.notice("degraded_probes", self._t(turn, "notice.degraded_probes"), warn=True)
                return True
            turn.brief.meta.probe_rounds += 1
            ask_gap = None
            for kind in failing:
                for gap in results[kind].gaps:
                    if (
                        gap.fix == "assume"
                        and gap.slot_id
                        and gap.suggested_value is not None
                        and not turn.slots.filled(gap.slot_id)
                    ):
                        turn.slots.write(gap.slot_id, gap.suggested_value, state="assumed", provenance=Provenance(type="model_knowledge", ref=f"probe:{kind}", date=self.clock.now()), confidence=0.5, turn_id=turn.turn_id)  # fmt: skip
                    elif ask_gap is None and gap.slot_id:
                        ask_gap = gap
            self._rebuild(turn)
            if ask_gap is not None:
                frame = next(
                    (f for f in self.catalog.frames.values() if ask_gap.slot_id in f.slots), None
                )
                asks = sum(1 for c in turn.asked_cards() if ask_gap.slot_id in c.slots)
                if (
                    frame is not None
                    and asks < 2
                    and self._question_n(turn) < self.config.questions.hard_cap
                ):
                    item = voi_mod.QueueItem(
                        frame_id=frame.id,
                        slots=[ask_gap.slot_id],
                        action="ask",
                        score=99.0,
                        must_confirm=False,
                        reason="handoff probe gap",
                    )
                    await self._ask_next(turn, forced=item)
                    return False
        return True

    async def _open_review(self, turn: _Turn) -> None:
        assert self.ports is not None
        self._rebuild(turn)
        compiled = await self.ports.compiler.compile(self._ctx(turn), turn.brief)
        turn.brief.summary = compiled.summary
        turn.brief.pitch = compiled.pitch
        blockers = self._blockers(turn)
        lines = self._blocker_lines(turn, blockers)
        h = brief_hash(turn.brief)
        version = turn.rec.version + (1 if h != turn.rec.hash else 0)
        turn.say(self._t(turn, "brief.ready" if not lines else "notice.gate_blocked"))
        turn.out.append(
            BriefReady(
                version=version,
                brief_hash=h,
                section_hashes=section_hashes(turn.brief),
                approvable=not lines,
                blockers=lines,
            )
        )

    # ------------------------------------------------------------------ card answers
    def _find_question(self, turn: _Turn, card_id: str) -> Card | None:
        for card in turn.asked_cards():
            if card.id == card_id:
                return card
        return None

    async def _on_card_answer(self, turn: _Turn, ev: CardAnswer) -> None:
        card = self._find_question(turn, ev.card_id)
        if card is None:
            raise NotFound(f"card {ev.card_id}")
        if card.frame == WELCOME_FRAME:
            await self._on_welcome(turn, str(ev.value))
            return
        if card.frame == VARIANTS_FRAME:
            await self._on_variant_pick(turn, card, ev)
            return
        writes = cards_mod.answer_to_writes(card, ev.value, ev.custom_text, self.catalog)
        if not writes and card.type == "pick_many" and not ev.custom_text:
            await self._assume_checklist(turn, card)
            turn.answered.append((card.id, "answered", {"value": ev.value, "slots": []}))  # type: ignore[arg-type]
            self._rebuild(turn)
            await self._advance(turn)
            return
        applied = self._apply_card_writes(turn, writes, state="confirmed", from_tap=True)
        turn.answered.append(
            (
                card.id,
                "answered",
                {"value": ev.value, "custom": ev.custom_text, "slots": [w.slot_id for w in writes]},
            )
        )  # type: ignore[arg-type]
        if applied:
            turn.out.append(
                Notice(
                    code="picked_up",
                    text=self._t(turn, "notice.picked_up", items=", ".join(applied)),
                    chips=applied,
                )
            )
        self._rebuild(turn)
        await self._advance(turn)

    async def _assume_checklist(self, turn: _Turn, card: Card) -> None:
        """An empty checklist on a required slot is not a decision: assume the typical items (listed in
        "We assumed" for approval) instead of asking the same card again."""
        required = {
            sid
            for sid in card.slots
            if sid in self.catalog.slots and self.catalog.slots[sid].level == "must"
        }
        if not required or any(turn.slots.filled(sid) for sid in required):
            return
        typical = [o for o in card.options if o.basis == "typical"] or list(card.options)
        picked = [o.id for o in typical[:3]]
        writes = cards_mod.answer_to_writes(card, cast(JsonValue, picked), None, self.catalog)
        if writes:
            self._apply_card_writes(turn, writes, state="assumed", from_tap=True)
            turn.notice(
                "assumed",
                self._t(
                    turn, "card.assumed_collapsed", value=", ".join(_text(w.value) for w in writes)
                ),
            )

    def _apply_card_writes(
        self, turn: _Turn, writes: Sequence[Any], *, state: str, from_tap: bool
    ) -> list[str]:
        chips: list[str] = []
        for w in writes:
            kind = w.provenance_type if state == "confirmed" else "assumed"
            value = (
                expand_structure(w.value, lambda key: self._t(turn, key))
                if w.slot_id == "apps.list"
                else self._label_codes(w.slot_id, w.value, turn.lang)
            )
            res = turn.slots.write(
                w.slot_id, value, state=state,  # type: ignore[arg-type]
                provenance=self._prov(turn, kind, w.custom_text or ""), confidence=0.9 if state == "confirmed" else 0.5,
                turn_id=turn.turn_id, custom_text=w.custom_text, contributor_id=getattr(turn.event, "contributor_id", None),
            )  # fmt: skip
            if res.applied and state == "confirmed":
                chips.append(f"{self._label(turn, w.slot_id)}: {_text(value)}"[:80])
        return chips

    async def _on_card_skip(self, turn: _Turn, ev: CardSkip) -> None:
        card = self._find_question(turn, ev.card_id)
        if card is None:
            raise NotFound(f"card {ev.card_id}")
        if card.must_confirm:
            turn.notice("must_confirm", self._t(turn, "fallback.not_sure_rephrase"), warn=True)
            turn.out.append(CardEvent(card=card))
            return
        turn.answered.append((card.id, "skipped", None))
        await self._advance(turn)

    async def _on_you_decide(self, turn: _Turn, ev: CardYouDecide) -> None:
        card = self._find_question(turn, ev.card_id)
        if card is None:
            raise NotFound(f"card {ev.card_id}")
        if card.must_confirm:
            turn.notice("must_confirm", self._t(turn, "fallback.not_sure_rephrase"), warn=True)
            turn.out.append(CardEvent(card=card))
            return
        writes = cards_mod.you_decide_writes(card) or self._default_writes(card)
        self._apply_card_writes(turn, writes, state="assumed", from_tap=True)
        picked = ", ".join(_text(w.value) for w in writes)
        turn.answered.append((card.id, "you_decide", {"slots": [w.slot_id for w in writes]}))  # type: ignore[arg-type]
        turn.notice("assumed", self._t(turn, "card.assumed_collapsed", value=picked))
        self._rebuild(turn)
        await self._advance(turn)

    def _label_codes(self, slot_id: str, value: JsonValue, lang: str) -> JsonValue:
        """For list-type slots a card option's value may be an internal code (``account_profile``): the Brief
        stores the human label instead. Enum slots keep their codes (the schema validates them)."""
        sd = self.catalog.slots.get(slot_id)
        if sd is None or sd.value_schema.kind not in ("object_list", "text_list"):
            return value
        labels = {o.value: o.label.get(lang) for o in sd.fallback_card.options if o.value}

        def convert(v: JsonValue) -> JsonValue:
            if isinstance(v, str) and "_" in v and v in labels:
                return labels[v]
            if isinstance(v, dict):
                name = v.get("name")
                if isinstance(name, str) and "_" in name and name in labels:
                    return {**v, "name": labels[name]}
            return v

        if isinstance(value, list):
            return [convert(x) for x in value]
        return convert(value)

    def _selected_labels(self, turn: _Turn, slot_ids: Sequence[str]) -> list[str]:
        """Names already chosen for list slots, so a follow-up card offers something new."""
        out: list[str] = []
        for sid in slot_ids:
            rec = turn.slots.get(sid)
            if rec is None or not isinstance(rec.value, list):
                continue
            for item in rec.value:
                name = item.get("name") if isinstance(item, dict) else item
                if isinstance(name, str) and name.strip():
                    out.append(name)
        return out

    def _ask_idea_card(self, turn: _Turn) -> None:
        card = cards_mod.fallback_card(
            "idea_in_your_words",
            self.catalog,
            language=turn.lang,
            card_id=f"Q-{len(turn.asked_cards()) + 1:03d}",
            n=1,
            of_about=self._plan(turn).question_budget,
        )
        turn.asked.append(
            (card, {"frame": "idea_in_your_words", "action": "ask", "reason": "idea"})
        )
        turn.out.append(CardEvent(card=card))

    def _default_writes(self, card: Card) -> list[Any]:
        """Writes for the pattern defaults of the card's slots (listed as assumptions, never Locked)."""
        out: list[Any] = []
        for sid in card.slots:
            sd = self.catalog.slots.get(sid)
            if sd is not None and sd.default is not None:
                out.append(
                    cards_mod.CardWrite(
                        slot_id=sid,
                        value=cast(JsonValue, sd.default),
                        provenance_type="assumed",
                        custom_text=None,
                        needs_confirmation=False,
                    )
                )
        return out

    async def _on_welcome(self, turn: _Turn, choice: str) -> None:
        turn.answered.append(("Q-001", "answered", choice))
        key = {
            "upload": "welcome.upload_not_ready",
            "like_app": "welcome.like_app_prompt",
            "just_build": "welcome.just_build_prompt",
        }.get(choice)
        if choice == "describe" or key is None:
            card = cards_mod.fallback_card("idea_in_your_words", self.catalog, language=turn.lang, card_id=f"Q-{len(turn.asked_cards()) + 1:03d}", n=1, of_about=self._plan(turn).question_budget)  # fmt: skip
            turn.asked.append(
                (card, {"frame": "idea_in_your_words", "action": "ask", "reason": "welcome"})
            )
            turn.out.append(CardEvent(card=card))
            return
        turn.say(self._t(turn, key))
        if choice == "just_build":
            turn.brief.meta.plan = DiscoveryPlan(
                depth="express",
                question_budget=self.config.questions.express_max,
                minimum_viable=True,
            )

    async def _on_back(self, turn: _Turn) -> None:
        cards = [c for c in turn.asked_cards() if c.frame not in (WELCOME_FRAME, VARIANTS_FRAME)]
        if len(cards) < 1:
            turn.notice("nothing_to_undo", self._t(turn, "notice.nothing_to_undo"))
            return
        answered = {q.card.id for q in turn.questions if q.outcome != "open"} | {
            cid for cid, _, _ in turn.answered
        }
        target = next((c for c in reversed(cards) if c.id in answered), cards[-1])
        turn.out.append(CardEvent(card=target))
        turn.state = "INTERVIEWING"

    # ------------------------------------------------------------------ commands
    async def _on_route(self, turn: _Turn, kind: str) -> None:
        mapping = {
            "you_decide": "you_decide",
            "finish_with_defaults": "finish_with_defaults",
            "pause": "pause",
            "start_over": "start_over",
            "back": "back",
            "undo": "undo",
            "show_what_you_know": "show_what_you_know",
            "stop": "pause",
        }
        if kind == "greeting":
            turn.out.append(CardEvent(card=self._welcome_card(turn.lang)))
            return
        if kind == "you_decide":
            pending = turn.pending_card()
            if pending is not None:
                await self._on_you_decide(
                    turn, CardYouDecide(event_id=turn.turn_id, card_id=pending.id)
                )
            return
        if kind == "none_of_these":
            turn.say(self._t(turn, "fallback.not_sure_rephrase"))
            return
        name = mapping.get(kind)
        if name:
            await self._on_command(turn, name)

    async def _on_command(self, turn: _Turn, name: str) -> None:
        if name == "undo":
            await self._undo(turn)
        elif name == "back":
            await self._on_back(turn)
        elif name == "pause":
            if turn.state != "PAUSED":
                turn.session.resume_state = turn.state
                self._go(turn, "pause", "PAUSED")
            turn.notice("paused", self._t(turn, "notice.paused"))
        elif name == "resume":
            if turn.state == "PAUSED":
                back = turn.session.resume_state or "INTERVIEWING"
                turn.state = back
            await self._recap(turn)
        elif name == "start_over":
            turn.say(self._t(turn, "notice.started_over"))
            turn.state = "ARCHIVED"
        elif name == "finish_with_defaults":
            await self._finish_with_defaults(turn)
        elif name == "show_what_you_know":
            items = [PlaybackItem(key=sid, label=self._label(turn, sid), value=rec.value, basis="your_words" if rec.status in ("volunteered", "confirmed") else "suggestion") for sid, rec in turn.slots.all().items() if rec.value not in (None, "", [], {})]  # fmt: skip
            turn.out.append(Playback(title=self._t(turn, "playback.title"), items=items))

    async def _recap(self, turn: _Turn) -> None:
        app = turn.brief.idea.get("summary")
        pending = turn.pending_card()
        turn.notice(
            "resumed",
            self._t(
                turn,
                "notice.resumed_recap",
                app=_text(app.value)[:60] if app else "",
                question=pending.question if pending else "",
            ),
        )
        if pending is not None:
            turn.out.append(CardEvent(card=pending))

    async def _finish_with_defaults(self, turn: _Turn) -> None:
        queue = self._queue(turn)
        for item in queue:
            if item.action in ("ask", "propose", "assume"):
                if item.must_confirm:
                    continue
                self._assume_default(turn, item)
        turn.slots_defaulted = True  # type: ignore[attr-defined]
        turn.notice("finish_defaults", self._t(turn, "notice.finish_defaults"))
        self._rebuild(turn)
        self._go(turn, "queue_empty", "SEEDING")
        await self._advance(turn)

    # ------------------------------------------------------------------ changes (every one is a change set)
    def _section_label(self, turn: _Turn, path: str) -> str:
        key = SECTION_STRING.get(path.split(".")[0])
        return self._t(turn, key) if key else path

    async def _apply_change(
        self,
        turn: _Turn,
        ops: Sequence[PatchOp],
        *,
        source: ChangeSource,
        reason: str = "",
        what: str = "",
    ) -> bool:
        res = patch_mod.apply_ops(
            turn.brief, list(ops), self.catalog, now=self.clock.now(), turn_id=turn.turn_id
        )
        if res.errors:
            turn.notice("change_failed", self._t(turn, "change.nothing_changed"), warn=True)
            return False
        where = self._section_label(turn, ops[0].path) if ops else ""
        summary = self._t(turn, "change.updated", where=where, what=what or ops[0].path)
        n = len(turn.questions) + 0  # unique per session via the store's change-set count below
        change_id = f"CHG-{len(await self._change_sets(turn)) + 1:03d}"
        cs = patch_mod.build_change_set(
            res,
            id=change_id,
            base_version=turn.rec.version,
            source=source,
            reason=reason,
            summary=summary,
            turn_id=turn.turn_id,
        )  # type: ignore[arg-type]
        before = turn.rec
        turn.brief = res.brief
        self._resync_slots(turn)
        self._rebuild(turn)
        voided = changed_sections(before.section_hashes, section_hashes(turn.brief))
        cs = cs.model_copy(update={"voided_sections": voided})
        if any(op.path.startswith(RESEED_PATH_PREFIXES) for op in ops) and turn.state != "WELCOME":
            await self._seed(turn)
            cs = cs.model_copy(update={"reseeded_research": True})
        kinds: list[ProbeKind] = [
            cast(ProbeKind, k) for k, secs in PROBES_BY_SECTION.items() if secs & set(voided)
        ]
        if kinds and turn.brief.meta.probes:
            results = await self._run_probes(turn, kinds)
            for k, r in results.items():
                turn.brief.meta.probes[k] = r.passed
                turn.out.append(
                    ProbeResultEvent(
                        kind=cast(ProbeKind, k), passed=r.passed, gaps=[g.question for g in r.gaps]
                    )
                )
        turn.change_set = cs
        turn.out.append(
            BriefPatch(
                change_set_id=cs.id,
                from_version=cs.base_version,
                to_version=cs.new_version,
                summary=summary,
                ops=res.ops,
                voided_sections=voided,
            )
        )
        turn.say(summary)
        if turn.state in ("APPROVED",):
            turn.notice(
                "changed_since_approval",
                self._t(turn, "notice.changed_since_approval", sections=", ".join(voided)),
            )
            self._go(turn, "reopen", "BRIEF_REVIEW")
        if turn.state == "BRIEF_REVIEW":
            blockers = self._blockers(turn)
            h = brief_hash(turn.brief)
            turn.out.append(
                BriefReady(
                    version=cs.new_version,
                    brief_hash=h,
                    section_hashes=section_hashes(turn.brief),
                    approvable=not blockers,
                    blockers=self._blocker_lines(turn, blockers),
                )
            )
        _ = n
        return True

    async def _change_sets(self, turn: _Turn) -> list[ChangeSet]:
        async with self.store.begin(turn.tenant) as uow:
            return await uow.change_sets.list(turn.session.id)

    async def _undo(self, turn: _Turn) -> None:
        sets = await self._change_sets(turn)
        undone = {c.undoes for c in sets if c.undoes}
        target = next((c for c in reversed(sets) if c.id not in undone and c.undoes is None), None)
        if target is None:
            await self._on_back(turn)
            return
        res = patch_mod.apply_ops(
            turn.brief, target.inverse_ops, self.catalog, now=self.clock.now(), turn_id=turn.turn_id
        )
        if res.errors:
            turn.notice("undo_failed", self._t(turn, "change.nothing_changed"), warn=True)
            return
        cs = patch_mod.build_change_set(
            res,
            id=f"CHG-{len(sets) + 1:03d}",
            base_version=turn.rec.version,
            source="undo",
            reason="undo",
            summary=self._t(turn, "notice.undone", what=target.summary),
            turn_id=turn.turn_id,
        )
        cs = cs.model_copy(update={"undoes": target.id})
        before = turn.rec
        turn.brief = res.brief
        self._resync_slots(turn)
        self._rebuild(turn)
        cs = cs.model_copy(
            update={
                "voided_sections": changed_sections(
                    before.section_hashes, section_hashes(turn.brief)
                )
            }
        )
        turn.change_set = cs
        turn.out.append(
            BriefPatch(
                change_set_id=cs.id,
                from_version=cs.base_version,
                to_version=cs.new_version,
                summary=cs.summary,
                ops=res.ops,
                voided_sections=cs.voided_sections,
                undoable=False,
            )
        )
        turn.say(cs.summary)

    async def _on_brief_edit(self, turn: _Turn, ev: BriefEdit) -> None:
        ops = [
            PatchOp(
                op="set",
                path=ev.item_id,
                value=ev.value,
                provenance=[self._prov(turn, "user_text", _text(ev.value))],
            )
        ]
        await self._apply_change(turn, ops, source="ui_edit", what=ev.item_id)

    async def _on_assumption(self, turn: _Turn, ev: AssumptionDecision) -> None:
        asm = next((a for a in turn.brief.assumptions if a.id == ev.id), None)
        if asm is None:
            raise NotFound(f"assumption {ev.id}")
        if ev.decision == "accept":
            ops = [
                PatchOp(
                    op="set",
                    path=f"assumptions.{ev.id}.decision",
                    value="accepted",
                    provenance=[self._prov(turn, "user_tap", asm.claim)],
                )
            ]
            if await self._apply_change(turn, ops, source="assumption", what=asm.claim):
                turn.notice(
                    "assumption_accepted",
                    self._t(turn, "notice.assumption_accepted", claim=asm.claim),
                )
            return
        slot = self.catalog.slots.get(asm.slot_id or "")
        if ev.value is not None and slot is not None:
            ops = [
                PatchOp(op="set", path=slot.brief_path, value=ev.value, provenance=[self._prov(turn, "user_text", _text(ev.value))]),
                PatchOp(op="set", path=f"assumptions.{ev.id}.decision", value="changed"),
            ]  # fmt: skip
            await self._apply_change(turn, ops, source="assumption", what=asm.claim)
            return
        await self._variants(turn, asm.slot_id, _text(asm.claim), "dont_want_assumption")

    async def _on_lookup(self, turn: _Turn, ev: LookupEdit) -> None:
        if ev.action == "remove" and ev.request_id:
            req = next((r for r in turn.brief.research_requests if r.id == ev.request_id), None)
            ops = [PatchOp(op="remove", path=f"research_requests.{ev.request_id}")]
            if await self._apply_change(
                turn, ops, source="lookup", what=req.topic if req else ev.request_id
            ):
                turn.notice(
                    "lookup_removed",
                    self._t(
                        turn, "notice.lookup_removed", text=req.topic if req else ev.request_id
                    ),
                )
            return
        if ev.action == "add" and ev.text.strip():
            geo = turn.slots.get("context.geo")
            cat = turn.slots.get("idea.category")
            words = [w for w in ev.text.replace(",", " ").split() if len(w) >= 3][:6]
            req = ResearchRequest(
                id=turn.brief.ids.allocate("RR"), topic=ev.text.strip()[:200], question=ev.text.strip()[:300], decision_unlocked="you asked for it",
                entity=_text(cat.value) if cat and cat.value else ev.text.strip()[:60], geography=_text(geo.value) if geo and geo.value else "global",
                terms=words or [ev.text.strip()[:30]], shown_to_user_as=ev.text.strip()[:120], priority="high", user_added=True,
            )  # fmt: skip
            ops = [PatchOp(op="add", path="research_requests", value=req.model_dump(mode="json"))]
            if await self._apply_change(turn, ops, source="lookup", what=req.topic):
                turn.notice("lookup_added", self._t(turn, "notice.lookup_added", text=req.topic))

    async def _on_feedback(self, turn: _Turn, ev: BriefFeedback) -> None:
        target = self._resolve_target(turn, ev.target)
        if ev.reason == "dont_want_assumption":
            asm = next(
                (a for a in turn.brief.assumptions if a.id == target.id.split(".")[-1]), None
            )
            if asm is not None:
                await self._variants(turn, asm.slot_id, asm.claim, ev.reason)
                return
        await self._change_from_text(turn, ev.text or ev.reason, target, reason=ev.reason)

    def _resolve_target(self, turn: _Turn, raw: str) -> ResolvedTarget:
        head = raw.split(".")[0]
        if raw in SECTION_STRING or raw in (
            "idea",
            "who",
            "features",
            "assumed",
            "look",
            "access",
            "words",
        ):
            return ResolvedTarget(kind="section", id=raw)
        if head == "assumptions":
            return ResolvedTarget(kind="assumption", id=raw)
        if head == "scope":
            return ResolvedTarget(kind="feature", id=raw)
        if raw in self.catalog.slots:
            return ResolvedTarget(kind="slot", id=raw)
        return ResolvedTarget(kind="row" if "." in raw else "unknown", id=raw)

    async def _change_from_text(
        self, turn: _Turn, text: str, target: ResolvedTarget, *, reason: str = ""
    ) -> None:
        assert self.ports is not None
        history = []
        from mde_compass.engine.ports import HistoryLine

        for i, line in enumerate(turn.transcript):
            history.append(HistoryLine(turn_id=f"t{i}", role=line.role, text=line.text))
        proposal = await self.ports.change_interpreter.interpret(
            self._ctx(turn),
            ChangeInput(text=text, target=target, history_slice=history, reason=reason),
            turn.brief,
        )
        if not proposal.understood or not proposal.ops:
            question = proposal.clarification or self._t(turn, "change.target_unknown")
            if proposal.options:
                card = Card(
                    id=f"Q-{len(turn.asked_cards()) + 1:03d}", frame=VARIANTS_FRAME, slots=[], type="choose_one", question=question,
                    options=[CardOption(id=chr(65 + i), label=o) for i, o in enumerate(proposal.options[:3])], language=turn.lang,
                )  # fmt: skip
                turn.asked.append((card, {"frame": VARIANTS_FRAME, "reason": "clarify"}))
                turn.out.append(CardEvent(card=card))
            else:
                turn.say(question)
            return
        if proposal.touches_locked:
            turn.notice("touches_locked", ", ".join(proposal.touches_locked), warn=False)
        await self._apply_change(
            turn,
            proposal.ops,
            source="chat" if not reason else "feedback",
            reason=reason,
            what=text[:60],
        )

    async def _variants(self, turn: _Turn, slot_id: str | None, current: str, reason: str) -> None:
        assert self.ports is not None
        slot = self.catalog.slots.get(slot_id or "")
        if slot is None:
            turn.say(self._t(turn, "change.target_unknown"))
            return
        cur = turn.slots.get(slot.id)
        vs = await self.ports.variant_maker.variants(
            self._ctx(turn), path=slot.brief_path, current=cur.value if cur else None, reason=reason
        )
        opts = [
            CardOption(id=chr(65 + i), label=v.label, basis=v.basis, recommended=i == 0, reason=v.reason, sets={slot.id: v.value})
            for i, v in enumerate(vs.variants[:3])
        ]  # fmt: skip
        card = Card(
            id=f"Q-{len(turn.asked_cards()) + 1:03d}", frame=VARIANTS_FRAME, slots=[slot.id], type="choose_one",
            question=self._t(turn, "change.variants_question", current=current[:80]), options=opts, language=turn.lang,
            progress=CardProgress(n=0, of_about=0),
        )  # fmt: skip
        turn.asked.append((card, {"frame": VARIANTS_FRAME, "reason": reason}))
        turn.out.append(CardEvent(card=card))

    async def _on_variant_pick(self, turn: _Turn, card: Card, ev: CardAnswer) -> None:
        turn.answered.append((card.id, "answered", ev.value))
        opt = next((o for o in card.options if o.id == ev.value), None)
        if ev.custom_text and card.slots:
            slot = self.catalog.slots[card.slots[0]]
            ops = [
                PatchOp(
                    op="set",
                    path=slot.brief_path,
                    value=ev.custom_text,
                    provenance=[self._prov(turn, "user_text", ev.custom_text)],
                )
            ]
        elif opt is not None and opt.sets:
            ops = []
            for sid, value in opt.sets.items():
                slot = self.catalog.slots.get(sid)
                if slot is not None:
                    ops.append(
                        PatchOp(
                            op="set",
                            path=slot.brief_path,
                            value=value,
                            provenance=[self._prov(turn, "user_tap", opt.label)],
                        )
                    )
        else:
            turn.say(self._t(turn, "change.nothing_changed"))
            return
        if ops:
            await self._apply_change(turn, ops, source="feedback", what=opt.label if opt else "")

    async def _on_switch(self, turn: _Turn, ev: ContributorSwitch) -> None:
        who = next((c for c in turn.session.contributors if c.id == ev.contributor_id), None)
        if who is None:
            raise NotFound(f"contributor {ev.contributor_id}")
        turn.notice("speaking_as", self._t(turn, "notice.speaking_as", name=who.name or who.id))

    # ------------------------------------------------------------------ Gate 1
    async def _on_gate(self, turn: _Turn, ev: GateDecision) -> None:
        if ev.decision == "request_changes":
            turn.notice("changes_requested", self._t(turn, "change.target_unknown"))
            return
        if turn.state not in ("BRIEF_REVIEW", "APPROVED"):
            turn.notice("gate_blocked", self._t(turn, "notice.gate_blocked"), warn=True)
            return
        blockers = self._blockers(
            turn, supplied_version=ev.version, supplied_hash=ev.brief_hash or None
        )
        if ev.section_hashes and ev.section_hashes != section_hashes(turn.brief):
            blockers = [
                *blockers,
                gate_mod.Blocker(code="hash_mismatch", key="blocker.hash_mismatch", values={}),
            ]
        who = ev.contributor_id or turn.session.user_id
        roles = {c.id: c.role for c in turn.session.contributors}
        if turn.session.mode == "enterprise":
            if roles.get(who) == "approver" and not [
                b for b in blockers if b.code != "approvers_missing"
            ]:
                turn.brief.meta.approvals[who] = brief_hash(turn.brief)
                blockers = self._blockers(
                    turn, supplied_version=ev.version, supplied_hash=ev.brief_hash or None
                )
            elif roles.get(who) != "approver":
                turn.notice(
                    "not_an_approver",
                    self._t(
                        turn,
                        "blocker.approvers_missing",
                        roles=", ".join(self.config.gate.required_approver_roles),
                    ),
                    warn=True,
                )
                return
        if blockers:
            lines = self._blocker_lines(turn, blockers)
            only_approvals = all(b.code == "approvers_missing" for b in blockers)
            turn.notice(
                "gate_waiting" if only_approvals else "gate_blocked",
                lines[0] if only_approvals else self._t(turn, "notice.gate_blocked"),
                warn=not only_approvals,
            )
            turn.out.append(
                BriefReady(
                    version=turn.rec.version,
                    brief_hash=brief_hash(turn.brief),
                    section_hashes=section_hashes(turn.brief),
                    approvable=False,
                    blockers=lines,
                )
            )
            return
        version = turn.rec.version + (1 if brief_hash(turn.brief) != turn.rec.hash else 0)
        label = "B-G1" if turn.session.approved_version is None else f"B-G1-v{version}"
        self._go(turn, "gate_approved", "APPROVED")
        turn.state = "APPROVED"
        turn.approval = (version, label)
        turn.handoff_views = build_handoff(turn.brief)
        h = brief_hash(turn.brief)
        turn.out.append(BriefApproved(version=version, brief_hash=h, baseline=label))
        turn.say(self._t(turn, "notice.approved", version=version))

    async def gate(
        self, caller: Caller, session_id: str, event: InboundEvent
    ) -> dict[str, JsonValue]:
        result = await self.handle_event(caller, session_id, event)
        session = await self.get_session(caller, session_id)
        async with self.store.begin(caller.tenant_id) as uow:
            rec = await uow.briefs.get(session_id)
        blockers: list[str] = []
        for ev in result.events:
            if ev.type == "brief_ready":
                raw = ev.payload.get("blockers")
                blockers = [str(b) for b in raw] if isinstance(raw, list) else []
        return {
            "accepted": session.state == "APPROVED" and result.accepted,
            "state": session.state, "version": rec.version, "brief_hash": rec.hash,
            "baseline": rec.baseline_label, "blockers": list(blockers),
        }  # fmt: skip

    # ------------------------------------------------------------------ queries
    async def list_outbound(
        self, caller: Caller, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        async with self.store.begin(caller.tenant_id) as uow:
            await uow.sessions.get(session_id)
            return await uow.events.list_outbound(session_id, after_seq=after_seq, limit=limit)

    async def get_brief(
        self, caller: Caller, session_id: str, version: int | None = None
    ) -> dict[str, JsonValue]:
        async with self.store.begin(caller.tenant_id) as uow:
            rec = await uow.briefs.get(session_id, version)
        data = rec.brief.model_dump(mode="json")
        data["_hash"] = rec.hash
        data["_section_hashes"] = rec.section_hashes
        return data

    async def brief_view(
        self, caller: Caller, session_id: str, *, version: int | None = None
    ) -> dict[str, JsonValue]:
        async with self.store.begin(caller.tenant_id) as uow:
            session = await uow.sessions.get(session_id)
            rec = await uow.briefs.get(session_id, version)
            prev = await uow.briefs.get(session_id, rec.version - 1) if rec.version > 1 else None
            baseline = await uow.briefs.get_baseline(session_id)
        slots = SlotStore(self.catalog, clock=self.clock)
        slots.load(self._slot_records(rec.brief))
        turn_like = _Turn(
            tenant=caller.tenant_id,
            session=session,
            rec=rec,
            brief=rec.brief,
            slots=slots,
            questions=[],
            event=None,
            lang=session.locale,
            state=session.state,
        )
        lines = (
            self._blocker_lines(turn_like, self._blockers(turn_like))
            if session.state in ("BRIEF_REVIEW", "APPROVED")
            else []
        )
        changed = (
            [
                e.path
                for e in diff_briefs(
                    prev.brief, rec.brief, from_version=prev.version, to_version=rec.version
                ).entries
            ]
            if prev
            else []
        )
        view = build_view_model(
            rec.brief, catalog=self.catalog, language=session.locale, session=session, blockers=lines,
            reviewed_sections=(), changed_paths=changed, baseline=baseline, brief_hash=rec.hash, section_hashes=rec.section_hashes,
        )  # fmt: skip
        return view.model_dump(mode="json")

    async def brief_versions(self, caller: Caller, session_id: str) -> list[dict[str, JsonValue]]:
        async with self.store.begin(caller.tenant_id) as uow:
            versions = await uow.briefs.list_versions(session_id)
        return [
            {"version": v.version, "hash": v.hash, "baseline": v.baseline_label, "change_set_id": v.change_set_id, "created_at": v.created_at.isoformat() if v.created_at else None}
            for v in versions
        ]  # fmt: skip

    async def brief_diff(
        self, caller: Caller, session_id: str, *, from_version: int, to_version: int
    ) -> dict[str, JsonValue]:
        async with self.store.begin(caller.tenant_id) as uow:
            a = await uow.briefs.get(session_id, from_version)
            b = await uow.briefs.get(session_id, to_version)
        return diff_briefs(
            a.brief, b.brief, from_version=from_version, to_version=to_version
        ).model_dump(mode="json")

    async def history(
        self,
        caller: Caller,
        session_id: str,
        *,
        after_idx: int = 0,
        limit: int = 50,
        slot: str | None = None,
        q: str | None = None,
    ) -> dict[str, JsonValue]:
        turn_ids: list[str] | None = None
        highlighted: list[str] = []
        async with self.store.begin(caller.tenant_id) as uow:
            if slot:
                rec = await uow.briefs.get(session_id)
                prov = provenance_for(rec.brief, slot)
                highlighted = prov.turn_ids if prov else []
            page = await uow.events.list_history(
                session_id, after_idx=after_idx, limit=limit, q=q, turn_ids=turn_ids
            )
        out: dict[str, JsonValue] = {
            "items": [i.model_dump(mode="json") for i in page.items],
            "next_after_idx": page.next_after_idx,
            "highlighted_turn_ids": cast(JsonValue, highlighted),
        }
        return out

    async def provenance(
        self, caller: Caller, session_id: str, item_id: str
    ) -> dict[str, JsonValue]:
        async with self.store.begin(caller.tenant_id) as uow:
            rec = await uow.briefs.get(session_id)
        prov = provenance_for(rec.brief, item_id)
        if prov is None:
            raise NotFound(f"item {item_id}")
        return prov.model_dump(mode="json")

    async def handoff(self, caller: Caller, session_id: str) -> dict[str, JsonValue]:
        async with self.store.begin(caller.tenant_id) as uow:
            session = await uow.sessions.get(session_id)
            if session.state != "APPROVED" or session.approved_version is None:
                raise GateError("the Brief is not approved yet")
            rec = await uow.briefs.get(session_id, session.approved_version)
        return {"version": rec.version, "brief_hash": rec.hash, "views": build_handoff(rec.brief)}  # type: ignore[dict-item]

    async def budget(self) -> dict[str, JsonValue]:
        return dict(self.budget_snapshot())

    def budget_snapshot(self) -> dict[str, Any]:
        snap = dict(self.budget_guard.snapshot())
        snap["mode"] = self.mode_label
        return snap

    async def calls(
        self, caller: Caller, session_id: str, *, limit: int = 30
    ) -> list[dict[str, JsonValue]]:
        async with self.store.begin(caller.tenant_id) as uow:
            await uow.sessions.get(session_id)
            entries = await uow.call_log.list(session_id, limit=limit)
        return [
            {"call_id": e.call_id, "spec_id": e.spec_id, "outcome": e.outcome, "tokens_in": e.tokens_in, "tokens_out": e.tokens_out, "cost_usd": f"{e.cost_micros / 1_000_000:.4f}", "latency_ms": e.latency_ms}
            for e in entries
        ]  # fmt: skip


__all__ = ["MdeError", "Service", "datetime"]
