"""``ScriptedService``: an in-memory ``CompassService`` with canned but realistic data.

Used by the API and UI tests and by the lead for early UI work (no engine, no store, no gateway). One
session walks: welcome, a typed idea, the eight card types in turn, a playback, "what we will look up",
a ``BriefViewModel`` and Gate 1. Edits, feedback, assumption decisions and lookup edits change the view
model and bump the version, so the UI can be exercised end to end.

Real behaviour it keeps: tenant isolation (another tenant's id is a 404), idempotency by ``event_id``,
per-session ``seq`` on outbound events, hash-bound approval (409 on a stale version or hash).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from pydantic import JsonValue

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.cards import Card, CardOption, CardProgress, CustomInput, YouDecide
from mde_compass.domain.changes import PatchOp
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
    GateDecision,
    InboundEvent,
    LookupEdit,
    LookupPreview,
    Notice,
    Playback,
    PlaybackItem,
    Progress,
    StoredEvent,
    UserMessage,
)
from mde_compass.domain.research import ResearchRequest
from mde_compass.domain.session import Mode, SessionRecord
from mde_compass.engine.ports import Caller, TurnResult
from mde_compass.views.models import (
    BriefViewModel,
    ReadinessChip,
    ViewBadge,
    ViewFooter,
    ViewHeader,
    ViewRow,
    ViewSection,
    ViewValue,
)
from mde_core.errors import Conflict, HashMismatch, NotFound

_T0 = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)


def _badge(kind: str, lang: str) -> ViewBadge:
    cat = load_catalog()
    return ViewBadge(
        kind=kind,  # type: ignore[arg-type]
        text=cat.t(f"badge.{kind}", lang),
        icon=cat.t(f"badge.icon.{kind}", lang),
    )


def _opt(
    oid: str, label: str, basis: str = "suggestion", rec: bool = False, reason: str = ""
) -> CardOption:
    return CardOption(
        id=oid,
        label=label,
        basis=basis,  # type: ignore[arg-type]
        recommended=rec,
        reason=reason,
        sets={},
    )


def script_cards() -> list[Card]:
    """One card of each of the eight types, in a believable order (a food-delivery app idea)."""
    n_total = 8
    common: dict[str, Any] = {
        "custom_input": CustomInput(
            enabled=True, placeholder="Something else: type it", voice=True
        ),
        "you_decide": YouDecide(enabled=True, assumes="A"),
    }

    def card(
        i: int, ctype: str, frame: str, slots: list[str], q: str, why: str, opts: list[CardOption]
    ):
        return Card(
            id=f"Q-{i:03d}",
            frame=frame,
            slots=slots,
            type=ctype,  # type: ignore[arg-type]
            question=q,
            why_we_ask=why,
            options=opts,
            progress=CardProgress(n=i, of_about=n_total),
            **common,
        )

    return [
        card(
            1,
            "choose_one",
            "business_model",
            ["idea.business_model"],
            "How will it make money?",
            "It decides which features and payments we plan for.",
            [
                _opt(
                    "A",
                    "A small commission on each order",
                    "typical",
                    True,
                    "Common for marketplaces",
                ),
                _opt("B", "Delivery fees paid by customers", "typical"),
                _opt("C", "A monthly plan for restaurants", "suggestion"),
            ],
        ),
        card(
            2,
            "pick_many",
            "features",
            ["scope.features"],
            "Which of these should the first version have?",
            "We keep the first version small.",
            [
                CardOption(
                    id="A",
                    label="Order tracking",
                    basis="typical",
                    recommended=True,
                    reason="Typical for delivery apps",
                    ticked=True,
                ),
                CardOption(id="B", label="Ratings and reviews", basis="typical"),
                CardOption(id="C", label="Promo codes", basis="suggestion"),
                CardOption(id="D", label="Scheduled orders", basis="your_words"),
            ],
        ),
        card(
            3,
            "priority",
            "priorities",
            ["scope.priority"],
            "Put these in the order that matters most.",
            "So we know what to build first.",
            [
                _opt("A", "Fast ordering", "suggestion", True, "You said speed matters"),
                _opt("B", "Order tracking", "typical"),
                _opt("C", "Restaurant dashboard", "suggestion"),
            ],
        ),
        card(
            4,
            "confirm",
            "idea_confirm",
            ["idea.category", "context.geo"],
            "Here is what I understood. Edit anything that is off.",
            "Corrections now save time later.",
            [
                _opt("A", "A food delivery marketplace", "your_words", True),
                _opt("B", "In Pune, India", "your_words"),
                _opt("C", "For customers and restaurants", "your_words"),
            ],
        ),
        card(
            5,
            "look_signals",
            "brand_tone",
            ["look.tone_words"],
            "Which look feels closest?",
            "Mood words guide the design, not exact colours.",
            [
                _opt("A", "Warm and friendly", "suggestion", True, "Soft colours, rounded shapes"),
                _opt("B", "Clean and modern", "suggestion", False, "Lots of white space"),
                _opt("C", "Bold and energetic", "suggestion", False, "Strong colours, big type"),
            ],
        ),
        card(
            6,
            "quick_form",
            "scale_launch",
            ["context.scale_launch"],
            "A few quick details about the launch.",
            "Sizes how we plan the first release.",
            [
                _opt("city", "City", "your_words", False, "Pune"),
                _opt("pilot", "How many restaurants at first?", "suggestion", False, "e.g. 20"),
                _opt(
                    "when", "When do you want to launch?", "suggestion", False, "e.g. in 3 months"
                ),
            ],
        ),
        card(
            7,
            "upload_or_link",
            "documents",
            ["data.existing_systems"],
            "Do you have a document or a link to share?",
            "We read it instead of asking again.",
            [],
        ),
        card(
            8,
            "open_prompt",
            "anything_else",
            ["unknowns.research_wanted"],
            "Anything else we should know?",
            "Last chance to add what we missed.",
            [],
        ),
    ]


def _initial_rows(lang: str) -> dict[str, list[dict[str, Any]]]:
    """Section id -> row dicts (value text/list, badge kind, flags). Mutated by edits."""
    return {
        "idea": [
            {
                "id": "idea.summary",
                "label": "In one line",
                "kind": "text",
                "text": "A food delivery marketplace for Pune",
                "badge": "you_said",
                "editable": True,
            },
            {
                "id": "idea.business_model",
                "label": "How it makes money",
                "kind": "text",
                "text": "A small commission on each order",
                "badge": "typical",
                "editable": True,
            },
        ],
        "who": [
            {
                "id": "audience.segments",
                "label": "Who uses it",
                "kind": "chips",
                "items": ["Hungry customers", "Restaurant owners"],
                "badge": "you_said",
                "editable": True,
            },
            {
                "id": "context.geo",
                "label": "Where",
                "kind": "text",
                "text": "Pune, India",
                "badge": "you_said",
                "editable": True,
            },
        ],
        "apps_roles": [
            {
                "id": "apps.APP-001",
                "label": "Customer app",
                "kind": "text",
                "text": "Browse, order and track food",
                "badge": "suggestion",
                "editable": True,
            },
            {
                "id": "roles.ROL-001",
                "label": "Restaurant owner",
                "kind": "text",
                "text": "Manage menu and incoming orders",
                "badge": "suggestion",
                "editable": True,
            },
        ],
        "features": [
            {
                "id": "scope.FTR-001",
                "label": "Order tracking",
                "kind": "text",
                "text": "In the first version",
                "badge": "typical",
                "editable": True,
                "group": "Customer app › Delivery",
                "chip": "In the first version · Must have",
            },
            {
                "id": "scope.FTR-002",
                "label": "Ratings and reviews",
                "kind": "text",
                "text": "Later",
                "badge": "typical",
                "editable": True,
                "group": "Customer app › Community",
                "chip": "Later · Nice to have",
            },
        ],
        "look": [
            {
                "id": "look.tone_words",
                "label": "Tone",
                "kind": "chips",
                "items": ["Warm", "Friendly"],
                "badge": "suggestion",
                "editable": True,
            },
        ],
        "access": [
            {
                "id": "auth.methods",
                "label": "Sign in",
                "kind": "text",
                "text": "Phone number and one-time code",
                "badge": "assumed",
                "editable": True,
            },
        ],
        "assumed": [
            {
                "id": "assumptions.ASM-001",
                "label": "Payments",
                "kind": "text",
                "text": "Customers pay online by card or UPI",
                "badge": "assumed",
                "editable": True,
                "review": True,
            },
            {
                "id": "assumptions.ASM-002",
                "label": "Languages",
                "kind": "text",
                "text": "English and Hindi",
                "badge": "assumed",
                "editable": True,
            },
        ],
        "unknown": [
            {
                "id": "unknowns.u1",
                "label": "Delivery partners",
                "kind": "text",
                "text": "Not decided yet: own riders or partners",
                "badge": "assumed",
                "editable": False,
            },
        ],
    }


@dataclass
class _Session:
    rec: SessionRecord
    events: list[StoredEvent] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    card_i: int = 0
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    version: int = 0
    snapshots: dict[int, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    changed: set[str] = field(default_factory=set)
    reviewed: set[str] = field(default_factory=set)
    high_pending: bool = True
    lookups: list[ResearchRequest] = field(default_factory=list)
    approved_hash: str | None = None
    calls: list[dict[str, JsonValue]] = field(default_factory=list)
    idx: int = 0
    seq: int = 0


def _rr(i: int, topic: str, geo: str = "Pune, India") -> ResearchRequest:
    return ResearchRequest(
        id=f"RR-{i:03d}",
        topic=topic,
        question=f"What should we know about {topic.lower()}?",
        decision_unlocked="Which features to plan",
        entity="food delivery",
        geography=geo,
        terms=["food delivery", "online ordering"],
        shown_to_user_as=topic,
    )


class ScriptedService:
    """Implements ``mde_compass.engine.ports.CompassService`` in memory."""

    def __init__(self, *, spent_usd: str = "0.0000", mode: str = "mock") -> None:
        self._sessions: dict[str, _Session] = {}
        self._n = 0
        self._spent = Decimal(spent_usd)
        self._mode = mode
        self.cards = script_cards()

    # --- helpers -------------------------------------------------------------------------------

    def _get(self, caller: Caller, session_id: str) -> _Session:
        s = self._sessions.get(session_id)
        if s is None or s.rec.tenant_id != caller.tenant_id:
            raise NotFound("Session not found.")
        return s

    def _stamp(self, s: _Session) -> datetime:
        return _T0 + timedelta(seconds=s.idx)

    def _emit(self, s: _Session, model: Any, turn_id: str | None) -> StoredEvent:
        s.idx += 1
        s.seq += 1
        payload = model.model_dump(mode="json")
        ev = StoredEvent(
            idx=s.idx,
            direction="out",
            type=payload["type"],
            event_id=None,
            turn_id=turn_id,
            seq=s.seq,
            payload=payload,
            at=self._stamp(s),
        )
        s.events.append(ev)
        s.rec.outbound_seq = s.seq
        return ev

    def _record_in(self, s: _Session, event: InboundEvent) -> None:
        s.idx += 1
        s.events.append(
            StoredEvent(
                idx=s.idx,
                direction="in",
                type=event.type,
                event_id=event.event_id,
                turn_id=event.event_id,
                seq=None,
                payload=event.model_dump(mode="json"),
                at=self._stamp(s),
            )
        )

    def _hash(self, s: _Session) -> str:
        blob = json.dumps(s.rows, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def _section_hashes(self, s: _Session) -> dict[str, str]:
        return {
            k: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()
            for k, v in s.rows.items()
        }

    def _blockers(self, s: _Session) -> list[str]:
        cat = load_catalog()
        lang = s.rec.locale
        if s.high_pending:
            return [cat.t("blocker.high_impact_assumption", lang, count=1)]
        return []

    def _view(self, s: _Session, lang: str, version: int | None = None) -> BriefViewModel:
        cat = load_catalog()
        rows_by_section = s.snapshots.get(version, s.rows) if version else s.rows
        sections: list[ViewSection] = []
        order = [
            "idea",
            "who",
            "apps_roles",
            "features",
            "look",
            "access",
            "integrations_data",
            "limits",
            "words",
            "assumed",
            "unknown",
            "lookup",
            "sources",
            "cannot_build",
        ]
        for sid in order:
            rows: list[ViewRow] = []
            for r in rows_by_section.get(sid, []):
                if r["kind"] == "chips":
                    val = ViewValue(kind="chips", items=list(r.get("items", [])))
                elif r["kind"] == "list":
                    val = ViewValue(kind="list", items=list(r.get("items", [])))
                else:
                    val = ViewValue(kind="text", text=r.get("text", ""))
                rows.append(
                    ViewRow(
                        id=r["id"],
                        label=r["label"],
                        value=val,
                        badges=[_badge(r["badge"], lang)],
                        tier="Locked" if r["badge"] == "you_said" else "Default",
                        editable=bool(r.get("editable", True)),
                        history_available=True,
                        needs_review=bool(r.get("review")) and s.high_pending,
                        changed=r["id"] in s.changed,
                        group=r.get("group", ""),
                        status_chip=r.get("chip", ""),
                        description="",
                    )
                )
            if sid == "lookup":
                rows = [
                    ViewRow(
                        id=f"research_requests.{rr.id}",
                        label=rr.topic,
                        value=ViewValue(kind="text", text=rr.question),
                        badges=[_badge("suggestion", lang)],
                        editable=False,
                        history_available=False,
                    )
                    for rr in s.lookups
                ]
            sections.append(
                ViewSection(
                    id=sid,  # type: ignore[arg-type]
                    title=cat.t(f"section.{sid}", lang),
                    rows=rows,
                    reviewed=sid in s.reviewed,
                    empty_text="Nothing here yet." if not rows else "",
                    count=len(rows),
                )
            )
        blockers = self._blockers(s)
        approved = s.approved_hash is not None
        status = "approved" if approved else ("in_review" if s.version else "draft")
        ready = not blockers
        readiness = [
            ReadinessChip(
                consumer=c,  # type: ignore[arg-type]
                label=cat.t("readiness.ready_for", lang, consumer=cat.t(f"readiness.{c}", lang))
                if ready
                else cat.t("readiness.gaps", lang, consumer=cat.t(f"readiness.{c}", lang), count=1),
                ready=ready,
                gaps=[] if ready else ["One assumption to confirm"],
            )
            for c in ("research", "blueprint", "design")
        ]
        return BriefViewModel(
            language=lang,
            session_id=s.rec.id,
            header=ViewHeader(
                title="Pune Food Delivery",
                idea_line=s.rows["idea"][0]["text"],
                version=s.version,
                status=status,  # type: ignore[arg-type]
                status_label=cat.t(f"status.{status}", lang),
                readiness=readiness,
                reviewed=len(s.reviewed),
                of_total=len(order),
            ),
            sections=sections,
            footer=ViewFooter(
                primary_label=cat.t("ui.looks_right", lang),
                secondary_label=cat.t("ui.change_something", lang),
                approvable=ready and not approved,
                blockers=blockers,
            ),
            brief_hash=self._hash(s),
            section_hashes=self._section_hashes(s),
            changed_since_approval=sorted(s.changed) if approved else [],
        )

    def _bump(self, s: _Session, summary: str, turn_id: str | None, ops: list[PatchOp]) -> None:
        prev = s.version
        s.version += 1
        s.snapshots[s.version] = copy.deepcopy(s.rows)
        s.rec.brief_version = s.version
        self._emit(
            s,
            BriefPatch(
                change_set_id=f"CHG-{s.version:03d}",
                from_version=prev,
                to_version=s.version,
                summary=summary,
                ops=ops,
            ),
            turn_id,
        )
        self._emit_ready(s, turn_id)

    def _emit_ready(self, s: _Session, turn_id: str | None) -> None:
        self._emit(
            s,
            BriefReady(
                version=s.version,
                brief_hash=self._hash(s),
                section_hashes=self._section_hashes(s),
                approvable=not self._blockers(s),
                blockers=self._blockers(s),
            ),
            turn_id,
        )

    def _log_call(self, s: _Session, spec_id: str) -> None:
        n = len(s.calls) + 1
        s.calls.append(
            {
                "call_id": f"call_{n:04d}",
                "spec_id": spec_id,
                "outcome": "ok",
                "tokens_in": 420 + n,
                "tokens_out": 96 + n,
                "cost_usd": "0.0000",
                "latency_ms": 12 + n,
            }
        )
        s.rec.llm_calls = len(s.calls)

    def _next_card(self, s: _Session, turn_id: str) -> None:
        if s.card_i < len(self.cards):
            card = self.cards[s.card_i]
            s.card_i += 1
            self._emit(
                s,
                Progress(
                    stage="interview",
                    n=card.progress.n if card.progress else 0,
                    of_about=len(self.cards),
                ),
                turn_id,
            )
            self._emit(s, CardEvent(card=card), turn_id)
            s.rec.state = "INTERVIEWING"
            self._log_call(s, "cmp.write_card")
            return
        self._finish_interview(s, turn_id)

    def _finish_interview(self, s: _Session, turn_id: str) -> None:
        s.rec.state = "BRIEF_REVIEW"
        s.lookups = [
            _rr(1, "Food delivery rules in India"),
            _rr(2, "Popular delivery apps in Pune"),
            _rr(3, "Restaurant commission norms"),
        ]
        self._emit(
            s,
            Playback(
                title="Here is what I understood",
                items=[
                    PlaybackItem(
                        key="idea.summary",
                        label="Idea",
                        value="A food delivery marketplace",
                        basis="your_words",
                    ),
                    PlaybackItem(
                        key="context.geo", label="Where", value="Pune, India", basis="your_words"
                    ),
                ],
                unclear=["Delivery partners"],
            ),
            turn_id,
        )
        self._emit(s, LookupPreview(requests=s.lookups), turn_id)
        self._emit(s, AssistantMessage(text="Your Brief is ready to review on the right."), turn_id)
        self._bump(s, "Brief drafted", turn_id, [])
        self._log_call(s, "cmp.compile_text")

    # --- CompassService ------------------------------------------------------------------------

    async def create_session(
        self, caller: Caller, *, mode: Mode, locale: str, user_label: str
    ) -> SessionRecord:
        self._n += 1
        sid = f"ses_{self._n:026d}"
        rec = SessionRecord(
            id=sid,
            tenant_id=caller.tenant_id,
            user_id=caller.user_id,
            mode=mode,
            locale=locale,
            user_label=user_label,
            title=user_label or "New project",
            created_at=_T0,
            updated_at=_T0,
        )
        s = _Session(rec=rec, rows=_initial_rows(locale))
        self._sessions[sid] = s
        cat = load_catalog()
        self._emit(
            s,
            AssistantMessage(
                text=cat.t("welcome.greeting", locale),
                language=locale,
                chips=[
                    cat.t("welcome.start_describe", locale),
                    cat.t("welcome.start_just_build", locale),
                ],
            ),
            None,
        )
        return rec

    async def list_sessions(self, caller: Caller) -> list[SessionRecord]:
        return [s.rec for s in self._sessions.values() if s.rec.tenant_id == caller.tenant_id]

    async def get_session(self, caller: Caller, session_id: str) -> SessionRecord:
        return self._get(caller, session_id).rec

    async def handle_event(
        self, caller: Caller, session_id: str, event: InboundEvent
    ) -> TurnResult:
        s = self._get(caller, session_id)
        if event.event_id in s.seen:
            return TurnResult(accepted=True, duplicate=True, events=[])
        s.seen.add(event.event_id)
        self._record_in(s, event)
        before_out = len(s.events)
        turn = event.event_id
        if isinstance(event, GateDecision):
            await self._gate(s, event, turn)
        elif isinstance(event, UserMessage):
            if s.card_i == 0:
                self._emit(
                    s,
                    AssistantMessage(
                        text="Got it. I picked up your idea.", chips=["Pune", "food delivery"]
                    ),
                    turn,
                )
                self._emit(
                    s,
                    Notice(
                        code="picked_up",
                        text="I picked up: Pune, food delivery",
                        chips=["Pune", "food delivery"],
                    ),
                    turn,
                )
            self._next_card(s, turn)
        elif isinstance(event, CardAnswer | CardSkip | CardYouDecide):
            self._next_card(s, turn)
        elif isinstance(event, CardBack):
            s.card_i = max(0, s.card_i - 2)
            self._next_card(s, turn)
        elif isinstance(event, Command):
            self._emit(s, Notice(code=f"command_{event.name}", text=f"Done: {event.name}."), turn)
        elif isinstance(event, BriefEdit):
            self._apply_edit(s, event, turn)
        elif isinstance(event, BriefFeedback):
            self._emit(s, AssistantMessage(text=f"Thanks. I will rework {event.target}."), turn)
            self._log_call(s, "cmp.interpret_change")
            self._bump(s, f"Updated: {event.target}", turn, [])
        elif isinstance(event, AssumptionDecision):
            for rows in s.rows.values():
                for r in rows:
                    if r["id"] == f"assumptions.{event.id}":
                        r["badge"] = "you_said" if event.decision == "accept" else "assumed"
                        if event.decision == "change" and isinstance(event.value, str):
                            r["text"] = event.value
                        r.pop("review", None)
                        s.changed.add(r["id"])
            s.high_pending = False
            self._bump(s, f"Updated: assumption {event.id}", turn, [])
        elif isinstance(event, LookupEdit):
            self._edit_lookup(s, event, turn)
        else:
            self._emit(s, Notice(code="ack", text="Noted."), turn)
        s.rec.updated_at = self._stamp(s)
        return TurnResult(accepted=True, duplicate=False, events=s.events[before_out:])

    def _apply_edit(self, s: _Session, event: BriefEdit, turn: str) -> None:
        for rows in s.rows.values():
            for r in rows:
                if r["id"] == event.item_id:
                    if r["kind"] in ("chips", "list") and isinstance(event.value, list):
                        r["items"] = [str(v) for v in event.value]
                    else:
                        r["text"] = str(event.value)
                    r["badge"] = "you_said"
                    s.changed.add(r["id"])
                    self._bump(
                        s,
                        f"Updated: {r['label']}",
                        turn,
                        [PatchOp(op="set", path=event.item_id, value=event.value)],
                    )
                    return
        raise NotFound("That item does not exist in this Brief.")

    def _edit_lookup(self, s: _Session, event: LookupEdit, turn: str) -> None:
        if event.action == "remove":
            s.lookups = [r for r in s.lookups if r.id != event.request_id]
        elif event.text.strip():
            s.lookups.append(_rr(len(s.lookups) + 10, event.text.strip()[:80]))
        self._emit(s, LookupPreview(requests=s.lookups), turn)
        self._bump(s, "Updated: What we will look up", turn, [])

    async def _gate(
        self, s: _Session, event: GateDecision, turn: str | None
    ) -> dict[str, JsonValue]:
        cat = load_catalog()
        lang = s.rec.locale
        if event.decision == "request_changes":
            self._emit(s, AssistantMessage(text="What would you like to change?"), turn)
            return {
                "accepted": True,
                "state": s.rec.state,
                "version": s.version,
                "brief_hash": self._hash(s),
                "baseline": None,
                "blockers": [],
            }
        if event.version != s.version or (event.brief_hash and event.brief_hash != self._hash(s)):
            raise HashMismatch(cat.t("blocker.hash_mismatch", lang))
        blockers = self._blockers(s)
        if blockers:
            raise Conflict(blockers[0])
        s.approved_hash = self._hash(s)
        s.rec.state = "APPROVED"
        s.rec.approved_version = s.version
        s.rec.approved_hash = s.approved_hash
        s.changed.clear()
        self._emit(s, BriefApproved(version=s.version, brief_hash=s.approved_hash), turn)
        return {
            "accepted": True,
            "state": "APPROVED",
            "version": s.version,
            "brief_hash": s.approved_hash,
            "baseline": "B-G1",
            "blockers": [],
        }

    async def gate(
        self, caller: Caller, session_id: str, event: InboundEvent
    ) -> dict[str, JsonValue]:
        s = self._get(caller, session_id)
        if not isinstance(event, GateDecision):
            raise Conflict("Not a gate decision.")
        if event.event_id in s.seen:
            return {
                "accepted": True,
                "state": s.rec.state,
                "version": s.version,
                "brief_hash": s.approved_hash or self._hash(s),
                "baseline": None,
                "blockers": [],
            }
        s.seen.add(event.event_id)
        self._record_in(s, event)
        return await self._gate(s, event, event.event_id)

    async def list_outbound(
        self, caller: Caller, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        s = self._get(caller, session_id)
        return [e for e in s.events if e.direction == "out" and (e.seq or 0) > after_seq][:limit]

    async def get_brief(
        self, caller: Caller, session_id: str, version: int | None = None
    ) -> dict[str, JsonValue]:
        s = self._get(caller, session_id)
        if version is not None and version not in s.snapshots:
            raise NotFound("That version does not exist.")
        rows = s.snapshots.get(version, s.rows) if version else s.rows
        return {
            "meta": {"session_id": session_id, "mode": s.rec.mode, "schema_version": 1},
            "version": version or s.version,
            "sections": {
                k: [{"id": r["id"], "label": r["label"]} for r in v] for k, v in rows.items()
            },
        }

    async def brief_view(
        self,
        caller: Caller,
        session_id: str,
        *,
        version: int | None = None,
        language: str | None = None,
    ) -> dict[str, JsonValue]:
        s = self._get(caller, session_id)
        if version is not None and version not in s.snapshots:
            raise NotFound("That version does not exist.")
        view = self._view(s, language or s.rec.locale, version)
        if version is None and s.version:
            s.reviewed.add("idea")
        return view.model_dump(mode="json")

    async def brief_versions(self, caller: Caller, session_id: str) -> list[dict[str, JsonValue]]:
        s = self._get(caller, session_id)
        return [
            {
                "version": v,
                "hash": hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest(),
                "baseline": "B-G1" if s.rec.approved_version == v else None,
                "change_set_id": f"CHG-{v:03d}",
                "created_at": _T0.isoformat(),
            }
            for v, snap in sorted(s.snapshots.items())
        ]

    async def brief_diff(
        self, caller: Caller, session_id: str, *, from_version: int, to_version: int
    ) -> dict[str, JsonValue]:
        s = self._get(caller, session_id)
        for v in (from_version, to_version):
            if v not in s.snapshots:
                raise NotFound("That version does not exist.")
        a, b = s.snapshots[from_version], s.snapshots[to_version]
        entries: list[JsonValue] = []
        sections: list[str] = []
        for sid in b:
            ra = {r["id"]: r for r in a.get(sid, [])}
            for r in b[sid]:
                old = ra.get(r["id"])
                if old is None:
                    entries.append(
                        {"path": r["id"], "before": None, "after": r.get("text"), "kind": "added"}
                    )
                elif old != r:
                    entries.append(
                        {
                            "path": r["id"],
                            "before": old.get("text"),
                            "after": r.get("text"),
                            "kind": "changed",
                        }
                    )
                else:
                    continue
                if sid not in sections:
                    sections.append(sid)
        return cast(
            dict[str, JsonValue],
            {
                "from_version": from_version,
                "to_version": to_version,
                "sections_changed": sections,
                "entries": entries,
            },
        )

    async def qa_list(self, caller: Caller, session_id: str) -> dict[str, JsonValue]:
        self._get(caller, session_id)
        return {"items": []}

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
        s = self._get(caller, session_id)
        items = [e for e in s.events if e.idx > after_idx]
        if q:
            needle = q.lower()
            items = [e for e in items if needle in json.dumps(e.payload).lower()]
        highlighted: list[str] = []
        if slot:
            highlighted = [e.event_id for e in s.events if e.direction == "in" and e.event_id][:2]
        page = items[:limit]
        nxt = page[-1].idx if len(items) > limit and page else None
        return cast(
            dict[str, JsonValue],
            {
                "items": [e.model_dump(mode="json") for e in page],
                "next_after_idx": nxt,
                "highlighted_turn_ids": highlighted,
            },
        )

    async def provenance(
        self, caller: Caller, session_id: str, item_id: str
    ) -> dict[str, JsonValue]:
        s = self._get(caller, session_id)
        for rows in s.rows.values():
            for r in rows:
                if r["id"] == item_id:
                    inbound = [e.event_id for e in s.events if e.direction == "in" and e.event_id]
                    return cast(
                        dict[str, JsonValue],
                        {
                            "item_id": item_id,
                            "value": r.get("text") or r.get("items"),
                            "tier": "Locked" if r["badge"] == "you_said" else "Default",
                            "status": "confirmed" if r["badge"] == "you_said" else "assumed",
                            "provenance": [
                                {
                                    "type": "user_text" if r["badge"] == "you_said" else "assumed",
                                    "ref": inbound[0] if inbound else "",
                                    "quote": "a food delivery app in Pune",
                                    "date": _T0.isoformat(),
                                    "who": "you",
                                }
                            ],
                            "value_history": [],
                            "turn_ids": inbound[:1],
                        },
                    )
        raise NotFound("No such item.")

    async def handoff(self, caller: Caller, session_id: str) -> dict[str, JsonValue]:
        s = self._get(caller, session_id)
        if s.approved_hash is None:
            raise Conflict("The Brief is not approved yet.")
        views: dict[str, dict[str, JsonValue]] = {
            name: {"idea": s.rows["idea"][0]["text"], "geography": "Pune, India"}
            for name in ("research", "blueprint", "design", "verify")
        }
        return cast(
            dict[str, JsonValue],
            {"version": s.version, "brief_hash": s.approved_hash, "views": views},
        )

    async def budget(self) -> dict[str, JsonValue]:
        cap = Decimal("3.00")
        return {
            "mode": self._mode,
            "cap_usd": str(cap),
            "hard_cap_usd": str(cap),
            "effective_stop_usd": "2.85",
            "per_session_cap_usd": "0.50",
            "spent_usd": str(self._spent),
            "remaining_usd": str(cap - self._spent),
            "calls": sum(len(s.calls) for s in self._sessions.values()),
        }

    async def ingest_upload(
        self, caller: Caller, session_id: str, filename: str, data: bytes
    ) -> TurnResult:
        s = self._get(caller, session_id)
        before = len(s.events)
        self._emit(
            s,
            AssistantMessage(text=f"I read {filename}: {len(data)} bytes.", language="en"),
            None,
        )
        return TurnResult(accepted=True, events=s.events[before:])

    async def calls(
        self, caller: Caller, session_id: str, *, limit: int = 30
    ) -> list[dict[str, JsonValue]]:
        s = self._get(caller, session_id)
        return list(reversed(s.calls))[:limit]
