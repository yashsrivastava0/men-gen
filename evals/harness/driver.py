"""``Driver``: one user's conversation with one session, whatever carries it.

``FlowDriver`` talks to ``FlowService`` in process (what the scenario suite uses). ``HttpDriver`` runs
the same conversations over ``apps/context-api`` (``create_app`` behind an ASGI transport: HTTP in
and out, the SSE log read back with ``follow=false``). A conversation written against ``Driver``
runs unchanged over either; ``tests/e2e`` runs its journeys over both.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import TypeAdapter

from evals.harness.stack import Stack
from mde_contracts.brief import Brief
from mde_contracts.common import GateKind, Mode, Principal
from mde_contracts.events import (
    GateDecision,
    GateEvent,
    OptionSelected,
    OutboundEvent,
    UiAction,
    UiActionName,
    UploadAdded,
    UserMessage,
)
from mde_contracts.gates import ApprovalRecord
from mde_contracts.records import JobRecord, ResearchNoteRecord, SessionRecord, SpecVersionRecord
from mde_flow.models import SessionSnapshot
from mde_flow.testing import Peek

E = TypeVar("E", bound=OutboundEvent)
HashRef = Literal["current", "stale", "wrong"]


class DriverError(AssertionError):
    """The conversation asked for something the session cannot do right now."""


@runtime_checkable
class Driver(Protocol):
    """What a conversation needs from a transport. Introspection methods may raise
    ``NotImplementedError`` on a transport that cannot see inside (the HTTP one)."""

    session_id: str
    who: Principal

    async def start(
        self, *, mode: Mode = "guided", policy_id: str | None = None
    ) -> list[OutboundEvent]: ...

    async def say(self, text: str, *, event_id: str | None = None) -> list[OutboundEvent]: ...

    async def tap(
        self,
        option: str | Sequence[str] | None = None,
        *,
        card: str | None = None,
        other_text: str | None = None,
        you_decide: bool = False,
        skipped: bool = False,
        event_id: str | None = None,
    ) -> list[OutboundEvent]: ...

    async def upload(
        self,
        filename: str,
        text: str | None = None,
        *,
        media_type: str = "text/markdown",
        event_id: str | None = None,
    ) -> list[OutboundEvent]: ...

    async def decide(
        self,
        gate: GateKind,
        decision: str = "approve",
        *,
        hash: HashRef = "current",
        comment: str | None = None,
        role: str | None = None,
        as_user: str | None = None,
        event_id: str | None = None,
    ) -> list[OutboundEvent]: ...

    async def ui(
        self,
        action: UiActionName,
        target: str | None = None,
        value: bool | None = None,
        *,
        event_id: str | None = None,
    ) -> list[OutboundEvent]: ...

    async def answer_with_recommended(
        self, text: str = ..., limit: int = ...
    ) -> list[OutboundEvent]: ...

    async def run_plan(self) -> bool: ...

    async def start_plan(self, hold: str) -> None: ...

    async def finish_plan(self) -> list[OutboundEvent]: ...

    async def snapshot(self) -> SessionSnapshot: ...

    async def events(self) -> list[OutboundEvent]: ...

    async def session_record(self) -> SessionRecord: ...

    async def approvals(self) -> list[ApprovalRecord]: ...

    async def research_note(self) -> ResearchNoteRecord | None: ...

    async def specs(self) -> list[SpecVersionRecord]: ...

    async def briefs(self) -> list[Brief]: ...

    async def jobs(self) -> list[JobRecord]: ...


@dataclass
class FlowDriver:
    """``Driver`` over an in-process :class:`Stack` (``FlowService`` + real engines)."""

    stack: Stack
    who: Principal
    session_id: str = ""
    seen: int = 0  # highest outbound seq already handed out by ``fresh``
    log: list[OutboundEvent] = field(default_factory=lambda: [])  # every event seen, in order
    plan_task: asyncio.Task[bool] | None = None  # a plan job running in the background

    # -- construction ------------------------------------------------------------------------
    @classmethod
    def for_user(
        cls,
        stack: Stack,
        *,
        user_id: str = "user-1",
        tenant_id: str = "tenant-a",
        roles: Sequence[str] = (),
    ) -> FlowDriver:
        return cls(stack, stack.principal(user_id, tenant_id, roles))

    async def start(
        self, *, mode: Mode = "guided", policy_id: str | None = None
    ) -> list[OutboundEvent]:
        rec, events = await self.stack.flow.create_session(self.who, mode, policy_id)
        self.session_id = rec.id
        self.seen = 0
        self.log = []
        self._take(events)
        return list(events)

    # -- plumbing ----------------------------------------------------------------------------
    def _eid(self, explicit: str | None) -> str:
        return explicit or self.stack.ids("evt")

    def _take(self, events: Sequence[OutboundEvent]) -> None:
        for ev in events:
            if ev.seq > self.seen:
                self.seen = ev.seq
                self.log.append(ev)

    async def _send(
        self,
        event: UserMessage | OptionSelected | UploadAdded | GateDecision | UiAction,
        who: Principal | None = None,
    ) -> list[OutboundEvent]:
        out = await self.stack.flow.handle_event(who or self.who, self.session_id, event)
        self._take(out)
        return list(out)

    async def fresh(self) -> list[OutboundEvent]:
        """Outbound events appended since the last call (job events arrive outside requests)."""
        new = await self.stack.flow.list_events(self.who, self.session_id, after_seq=self.seen)
        self._take(new)
        return list(new)

    # -- sending -----------------------------------------------------------------------------
    async def say(self, text: str, *, event_id: str | None = None) -> list[OutboundEvent]:
        return await self._send(UserMessage(event_id=self._eid(event_id), text=text))

    async def tap(
        self,
        option: str | Sequence[str] | None = None,
        *,
        card: str | None = None,
        other_text: str | None = None,
        you_decide: bool = False,
        skipped: bool = False,
        event_id: str | None = None,
    ) -> list[OutboundEvent]:
        snap = await self.snapshot()
        pending = snap.pending_card
        if pending is None:
            raise DriverError(f"no pending card to tap (state {snap.state})")
        if card is not None and pending.card.id != card:
            raise DriverError(f"the pending card is {pending.card.id}, not {card}")
        wanted = [option] if isinstance(option, str) else list(option or [])
        ids: list[str] = []
        for pick in wanted:
            match = next((o.id for o in pending.card.options if pick in (o.id, o.label)), None)
            if match is None:
                have = [o.id for o in pending.card.options]
                raise DriverError(f"{pick!r} is not an option of {pending.card.id} ({have})")
            ids.append(match)
        return await self._send(
            OptionSelected(
                event_id=self._eid(event_id),
                card_instance_id=pending.instance_id,
                option_ids=ids,
                other_text=other_text,
                you_decide=you_decide,
                skipped=skipped,
            )
        )

    async def tap_recommended(self, text: str = "Blue Basket") -> list[OutboundEvent]:
        """What a user who agrees with every suggestion would do on the pending card."""
        snap = await self.snapshot()
        pending = snap.pending_card
        if pending is None:
            raise DriverError(f"no pending card to answer (state {snap.state})")
        c = pending.card
        if c.type == "multi":
            return await self.tap([o.id for o in c.options if o.recommended])
        if c.type in ("text", "upload"):
            return await self.tap(None, other_text=text)
        pick = next((o for o in c.options if o.recommended), c.options[0])
        return await self.tap(pick.id)

    async def upload(
        self,
        filename: str,
        text: str | None = None,
        *,
        media_type: str = "text/markdown",
        event_id: str | None = None,
    ) -> list[OutboundEvent]:
        return await self._send(
            UploadAdded(
                event_id=self._eid(event_id),
                upload_id=self.stack.ids("upl"),
                filename=filename,
                media_type=media_type,
                size_bytes=len(text or ""),
                text=text,
            )
        )

    def _gate_events(self, gate: GateKind) -> list[GateEvent]:
        return [e for e in self.log if isinstance(e, GateEvent) and e.gate == gate]

    def last_gate(self, gate: GateKind | None = None) -> GateEvent | None:
        for ev in reversed(self.log):
            if isinstance(ev, GateEvent) and (gate is None or ev.gate == gate):
                return ev
        return None

    async def decide(
        self,
        gate: GateKind,
        decision: str = "approve",
        *,
        hash: HashRef = "current",
        comment: str | None = None,
        role: str | None = None,
        as_user: str | None = None,
        event_id: str | None = None,
    ) -> list[OutboundEvent]:
        shown = self._gate_events(gate)
        if not shown:
            raise DriverError(f"no {gate} gate has been shown yet")
        latest = shown[-1]
        version, digest = latest.version, latest.hash
        if hash == "stale":
            older = [g for g in shown if (g.version, g.hash) != (latest.version, latest.hash)]
            if older:
                version, digest = older[0].version, older[0].hash
            else:  # nothing older: the version before the current one, with a made-up hash
                version, digest = max(latest.version - 1, 0), _fake_hash("stale")
        elif hash == "wrong":
            digest = _fake_hash("wrong")
        who = self.who
        if as_user is not None:
            roles = [role] if role else []
            who = self.stack.principal(as_user, self.who.tenant_id, roles)
        event = GateDecision.model_validate(
            {
                "event_id": self._eid(event_id),
                "gate": gate,
                "decision": decision,
                "version": version,
                "hash": digest,
                "comment": comment,
                "role": role,
            }
        )
        return await self._send(event, who)

    async def ui(
        self,
        action: UiActionName,
        target: str | None = None,
        value: bool | None = None,
        *,
        event_id: str | None = None,
    ) -> list[OutboundEvent]:
        return await self._send(
            UiAction(event_id=self._eid(event_id), action=action, target=target, value=value)
        )

    async def run_plan(self) -> bool:
        ran = await self.stack.flow.run_next_job()
        await self.fresh()
        return ran

    async def run_plan_events(self) -> list[OutboundEvent]:
        """Run the queued plan job inline and return the events it produced."""
        await self.stack.flow.run_next_job()
        return await self.fresh()

    async def start_plan(self, hold: str) -> None:
        """Start the queued plan job in the background and return once its ``hold`` engine
        (research or build) is inside: the run is now genuinely in progress."""
        gate = self.stack.research_hold if hold == "research" else self.stack.builder_hold
        if gate is None or hold not in ("research", "build"):
            raise DriverError(f"unknown hold {hold!r}: use research or build")
        if self.plan_task is not None:
            raise DriverError("a plan job is already running in the background")
        gate.hold.arm()
        self.plan_task = asyncio.ensure_future(self.stack.flow.run_next_job())
        try:
            await asyncio.wait_for(gate.hold.inside.wait(), timeout=10)
        except TimeoutError:
            gate.hold.armed = False
            raise DriverError(f"the plan job never reached the {hold} engine") from None

    async def finish_plan(self) -> list[OutboundEvent]:
        """Release any hold and wait for the background plan job to end."""
        if self.plan_task is None:
            raise DriverError("no plan job is running in the background")
        for gate in (self.stack.research_hold, self.stack.builder_hold):
            if gate is not None:
                gate.hold.release.set()
        task, self.plan_task = self.plan_task, None
        await asyncio.wait_for(task, timeout=60)
        return await self.fresh()

    # -- reading -----------------------------------------------------------------------------
    async def snapshot(self) -> SessionSnapshot:
        return await self.stack.flow.get_snapshot(self.who, self.session_id)

    async def events(self) -> list[OutboundEvent]:
        return list(await self.stack.flow.list_events(self.who, self.session_id))

    async def session_record(self) -> SessionRecord:
        return await Peek(self.stack.store).session(self.session_id)

    async def approvals(self) -> list[ApprovalRecord]:
        return await Peek(self.stack.store).approvals(self.session_id)

    async def research_note(self) -> ResearchNoteRecord | None:
        async with self.stack.store.begin(self.who.tenant_id) as uow:
            return await uow.research.latest(self.session_id)

    async def specs(self) -> list[SpecVersionRecord]:
        return await Peek(self.stack.store).specs(self.session_id)

    async def briefs(self) -> list[Brief]:
        return [b.brief for b in await Peek(self.stack.store).briefs(self.session_id)]

    async def jobs(self) -> list[JobRecord]:
        return await Peek(self.stack.store).jobs(self.session_id)

    async def answer_with_recommended(
        self, text: str = "Blue Basket", limit: int = 60
    ) -> list[OutboundEvent]:
        """Tap the recommended option of every pending card until the interview is over."""
        out: list[OutboundEvent] = []
        for _ in range(limit):
            snap = await self.snapshot()
            if snap.state != "DISCOVERING" or snap.pending_card is None:
                return out
            out.extend(await self.tap_recommended(text))
        raise DriverError(f"the interview did not finish within {limit} cards")

    # -- helpers used by journeys --------------------------------------------------------------
    def texts(self, events: Sequence[OutboundEvent] | None = None) -> list[str]:
        from mde_contracts.events import AssistantMessage, Notice

        pool = self.log if events is None else events
        return [e.text for e in pool if isinstance(e, AssistantMessage | Notice)]

    def of_type(self, kind: type[E]) -> list[E]:
        return [e for e in self.log if isinstance(e, kind)]


def _fake_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


# --------------------------------------------------------------------------- the HTTP variant


class HttpError(DriverError):
    """The API answered a request with a failure status (``code`` is the API's stable code)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass
class HttpDriver(FlowDriver):
    """The same conversations over ``apps/context-api``: every message goes through HTTP (an
    ``httpx.AsyncClient`` on an ASGI transport, so no socket), the state and the events are read
    back over HTTP (``GET /v1/sessions/{id}``, the stream with ``follow=false``), and the app is the
    real ``create_app`` over the SAME :class:`Stack` as :class:`FlowDriver` uses: the real engines
    and flow behind it, the in-memory store or PostgreSQL, the mock gateway, the scripted holds.

    Three things stay off the wire on purpose:

    * the plan job is run inline (``run_plan``, ``start_plan``, ``finish_plan``), exactly as the
      worker would run it; ``approve Gate 1`` over HTTP only enqueues it;
    * the introspection methods that have no route (``approvals``, ``specs``, ``briefs``,
      ``jobs``, ``research_note``, ``session_record``) read the shared store, as in ``FlowDriver``;
    * ``who`` becomes the three identity headers; ``decide(as_user=...)`` is another caller.
    """

    client: httpx.AsyncClient | None = None
    token: str = ""

    @classmethod
    def for_stack(
        cls,
        stack: Stack,
        *,
        user_id: str = "user-1",
        tenant_id: str = "tenant-a",
        roles: Sequence[str] = (),
    ) -> HttpDriver:
        """A driver for one user over a fresh ``create_app`` around ``stack``."""
        from mde_context_api.composition import AppState
        from mde_context_api.main import create_app
        from mde_context_api.policies import PolicyCatalog
        from mde_context_api.profiles import OrgProfiles
        from mde_context_api.testing import BASE_URL, TEST_TOKEN, make_settings

        settings = make_settings(stack.projects_dir)

        async def ready() -> None:
            return None

        state = AppState(
            settings=settings,
            store=stack.store,
            flow=stack.flow,
            gateway=stack.client,
            policies=PolicyCatalog(
                guided_default=settings.default_guided_policy,
                enterprise_default=settings.default_enterprise_policy,
            ),
            org_profiles=OrgProfiles(stack.store),
            ping=ready,
            builder_kind="real",
        )
        app = create_app(state=state)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=BASE_URL
        )
        return cls(
            stack, stack.principal(user_id, tenant_id, roles), client=client, token=TEST_TOKEN
        )

    # -- the wire ----------------------------------------------------------------------------
    def _headers(self, who: Principal | None = None) -> dict[str, str]:
        caller = who or self.who
        out = {
            "Authorization": f"Bearer {self.token}",
            "X-Tenant-Id": caller.tenant_id,
            "X-User-Id": caller.user_id,
        }
        if caller.roles:
            out["X-Roles"] = ",".join(caller.roles)
        return out

    async def _request(
        self,
        method: str,
        path: str,
        *,
        who: Principal | None = None,
        json_body: object | None = None,
        params: Mapping[str, str | int | bool] | None = None,
        accept: tuple[int, ...] = (200, 201, 202),
    ) -> httpx.Response:
        assert self.client is not None, "build an HttpDriver with HttpDriver.for_stack(stack)"
        response = await self.client.request(
            method, path, headers=self._headers(who), json=json_body, params=params
        )
        if response.status_code not in accept:
            body = response.json() if response.content else {}
            raise HttpError(
                response.status_code, str(body.get("code", "")), str(body.get("message", ""))
            )
        return response

    def _parse(self, items: Sequence[object]) -> list[OutboundEvent]:
        return [_OUTBOUND.validate_python(item) for item in items]

    async def _stored(self, after: int = 0) -> list[OutboundEvent]:
        """Every outbound event after ``after``, read through the stream (``follow=false``)."""
        response = await self._request(
            "GET",
            f"/v1/sessions/{self.session_id}/stream",
            params={"after": after, "follow": False},
        )
        frames = [f for f in _sse_frames(response.text) if f.get("event") != "stream_error"]
        return self._parse([json.loads(f["data"]) for f in frames])

    # -- Driver, over HTTP -----------------------------------------------------------------
    async def start(
        self, *, mode: Mode = "guided", policy_id: str | None = None
    ) -> list[OutboundEvent]:
        body: dict[str, object] = {"mode": mode}
        if policy_id is not None:
            body["policy_id"] = policy_id
        response = await self._request("POST", "/v1/sessions", json_body=body)
        data = response.json()
        self.session_id = str(data["session"]["session_id"])
        self.seen = 0
        self.log = []
        events = self._parse(data["events"])
        self._take(events)
        return list(events)

    async def _send(
        self,
        event: UserMessage | OptionSelected | UploadAdded | GateDecision | UiAction,
        who: Principal | None = None,
    ) -> list[OutboundEvent]:
        response = await self._request(
            "POST",
            f"/v1/sessions/{self.session_id}/events",
            who=who,
            json_body=event.model_dump(mode="json"),
        )
        out = self._parse(response.json()["events"])
        self._take(out)
        return list(out)

    async def fresh(self) -> list[OutboundEvent]:
        new = await self._stored(self.seen)
        self._take(new)
        return list(new)

    async def snapshot(self) -> SessionSnapshot:
        response = await self._request("GET", f"/v1/sessions/{self.session_id}")
        return SessionSnapshot.model_validate_json(response.content)

    async def events(self) -> list[OutboundEvent]:
        return await self._stored(0)

    # -- documents over HTTP (what a client would fetch) -------------------------------------
    async def spec_text(self, version: int | None = None) -> tuple[str, dict[str, str]]:
        """``GET /spec``: the file byte for byte, and its ``X-Spec-*`` headers (lower-cased)."""
        params = {"version": version} if version is not None else None
        response = await self._request("GET", f"/v1/sessions/{self.session_id}/spec", params=params)
        headers = {k.lower(): v for k, v in response.headers.items() if k.lower().startswith("x-")}
        return response.content.decode("utf-8"), headers

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()


_OUTBOUND: TypeAdapter[OutboundEvent] = TypeAdapter(OutboundEvent)


def _sse_frames(text: str) -> list[dict[str, str]]:
    """``{"id", "event", "data"}`` per frame of a ``text/event-stream`` body (comments skipped)."""
    frames: list[dict[str, str]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        frame: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            frame[name] = value.removeprefix(" ")
        if "data" in frame:
            frames.append(frame)
    return frames
