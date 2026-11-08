"""Property tests (hypothesis stateful) over ``FlowService`` + ``InMemoryStore`` + scripted engines.

Random sequences of messages, taps, gate decisions (right and stale hashes, with and without the
right role), changes, stop, pause/resume, restart, queued messages, light and heavy post-handoff
changes, split approvers, duplicate events, interrupted runs and engine failures. After every step
the invariants of the brief hold:

* ``spec_ready`` exists only for a spec whose current hash was approved by every required role
  (or carries the requester's recorded light-change approval) and only after the commit;
* approved versions are never mutated; versions only move forward;
* approvals never survive a version change (a gate's first event for a new hash shows nobody
  approved);
* two active runs never coexist and a PLANNING session has its run;
* outbound ``seq`` is consecutive per session; duplicate inbound ids never duplicate effects.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
from pydantic import TypeAdapter

from mde_contracts.common import Principal
from mde_contracts.events import (
    GateDecision,
    GateEvent,
    InboundEvent,
    OptionSelected,
    OutboundEvent,
    SpecReady,
    UiAction,
    UiActionName,
    UserMessage,
)
from mde_contracts.gates import ApprovalRecord
from mde_contracts.records import (
    BriefVersionRecord,
    JobRecord,
    SessionRecord,
    SpecVersionRecord,
)
from mde_core.errors import GateError, InvalidTransition, MdeError, NotFound
from mde_core.hashing import sha256_hex
from mde_flow import gates, machine
from mde_flow.errors import Forbidden, HandoffFailed
from mde_flow.testing.builders import enterprise_policy
from mde_flow.testing.fakes import FakeCommit
from mde_flow.testing.kit import FlowKit, make_flow_kit

ALLOWED_ERRORS = (Forbidden, GateError, HandoffFailed, NotFound)  # a stranger: not found
STATS: Counter[str] = Counter()  # what the random walks actually reached (asserted at the end)

TEXTS = [
    "a shop for plants",
    "yes",
    "ok",
    "go ahead",
    "build it",
    "ok maybe",
    "looks fine but set design.direction=bold-dark",
    "set design.direction=bold-dark",
    "set design.direction=warm-friendly",
    "set auth.method=sso",
    "set idea.archetype=booking",
    "set pay.model=card",
    "feature+ chat",
    "feature- chat",
    "add screen faq",
    "what is this?",
    "stop",
    "pause",
    "hello there",
    "start over",
]
ACTIONS: list[UiActionName] = [
    "stop",
    "pause",
    "resume",
    "finish_with_defaults",
    "toggle_assumption",
    "toggle_feature",
    "toggle_research_item",
    "edit_row",
    "undo",
]
TARGETS = ["scope.features", "design.direction", "chat", "RA-001", "RA-002", "RA-999", "look", None]

OWNER_G = Principal(tenant_id="t", user_id="gu", roles=[])
STRANGER = Principal(tenant_id="t", user_id="sam", roles=[])
OWNER_E = Principal(tenant_id="t", user_id="eo", roles=[])
PAT = Principal(tenant_id="t", user_id="pat", roles=["po"])
DANA = Principal(tenant_id="t", user_id="dana", roles=["dl"])
EVE = Principal(tenant_id="t", user_id="eve", roles=["janitor"])
# the attackers of the security review: someone who merely claims "requester", and an owner who
# also carries both approver roles (one person must never be two approvers)
MALLORY = Principal(tenant_id="t", user_id="mallory", roles=["requester", "po"])
OWNER_E_ALL = Principal(tenant_id="t", user_id="eo", roles=["po", "dl", "requester"])
OWNER_G_CLAIMS = Principal(tenant_id="t", user_id="gu", roles=["requester"])
PAT_DL = Principal(
    tenant_id="t", user_id="pam", roles=["po", "dl"]
)  # two approver roles, one person
ATTACKERS: dict[str, list[Principal]] = {
    "g": [MALLORY, OWNER_G_CLAIMS],
    "e": [MALLORY, OWNER_E_ALL, PAT_DL],
}
WHO: dict[str, list[Principal]] = {
    "g": [OWNER_G, OWNER_G, OWNER_G_CLAIMS, STRANGER, MALLORY],
    "e": [OWNER_E, OWNER_E_ALL, PAT, PAT, DANA, DANA, EVE, MALLORY],
}
# Statuses only move forward. A rule can span several service calls, so a version may legitimately
# go draft -> handed_off between two observations; anything else that goes back is a bug.
ALLOWED_STATUS_MOVES = {
    "draft": {"approved", "handed_off", "superseded"},
    "approved": {"handed_off", "superseded"},
    "handed_off": {"superseded"},
    "superseded": set(),
}


@dataclass
class View:
    rec: SessionRecord
    events: list[OutboundEvent]
    approvals: list[ApprovalRecord]
    specs: list[SpecVersionRecord]
    briefs: list[BriefVersionRecord]
    jobs: list[JobRecord]


_OUTBOUND: TypeAdapter[OutboundEvent] = TypeAdapter(OutboundEvent)


class FlowMachine(RuleBasedStateMachine):
    """One Guided and one Enterprise (two required plan approvers) session, driven at random."""

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.kit: FlowKit = make_flow_kit(
            policies=[
                enterprise_policy(
                    policy_id="ent", plan_approvers=("po", "dl"), heavy_if=("auth", "pay", "scope")
                )
            ]
        )
        self.sessions: dict[str, str] = {}
        self.history: list[tuple[str, Principal, InboundEvent]] = []
        self.frozen_specs: dict[tuple[str, int], tuple[str, str]] = {}
        self.spec_status: dict[tuple[str, int], str] = {}
        self.frozen_briefs: dict[tuple[str, int], str] = {}
        self.commit_checks = 0
        self.event_cache: dict[str, dict[int, OutboundEvent]] = {}
        self.kit.fake_repo.on_commit = self._check_commit_is_approved  # type: ignore[assignment]

    # ----------------------------------------------------------------- plumbing

    def run(self, coro: Awaitable[Any]) -> Any:
        return self.loop.run_until_complete(coro)

    def teardown(self) -> None:
        self.loop.close()

    def _check_commit_is_approved(self, commit: FakeCommit) -> None:
        """Called by the project repo at the moment ``commit_spec`` writes: the approval must
        already be recorded (so nothing is committed or announced before Gate 2)."""
        sid = next(s.id for s in self.kit.memory.all_sessions() if s.app_id == commit.app_id)
        assert self._approved_for_handoff(sid, commit.version, commit.spec_hash), (
            f"commit_spec called without approval for v{commit.version}"
        )
        self.commit_checks += 1

    def _roles(self, sid: str) -> list[str]:
        rec = next(s for s in self.kit.memory.all_sessions() if s.id == sid)
        return gates.required_roles(self.kit.policies(rec.policy_id), "plan")

    def _approved_for_handoff(self, sid: str, version: int, digest: str) -> bool:
        approvals = [a for a in self.kit.memory.all_approvals(sid) if a.gate == "plan"]
        if gates.is_complete(self._roles(sid), approvals, version=version, hash=digest):
            return True
        if self._roles(sid) != ["requester"]:
            return False  # the requester's own light approval never stands in for other approvers
        owner = next(s.owner_id for s in self.kit.memory.all_sessions() if s.id == sid)
        return any(
            a.version == version
            and a.hash == digest
            and a.decision == "approve"
            and a.comment == gates.LIGHT_CHANGE_COMMENT
            and a.approver_id == owner
            for a in approvals
        )

    def eid(self) -> str:
        return self.kit.ids("evt")

    def send(self, which: str, who: Principal, event: InboundEvent) -> list[OutboundEvent]:
        self.history.append((which, who, event))
        try:
            return self.run(self.kit.service.handle_event(who, self.sessions[which], event))  # type: ignore[no-any-return]
        except ALLOWED_ERRORS:
            return []
        except InvalidTransition as exc:  # pragma: no cover - a real bug if it ever happens
            raise AssertionError(f"InvalidTransition escaped to the caller: {exc}") from exc

    # ----------------------------------------------------------------- rules

    @initialize(ff_g=st.sampled_from([0, 1, 2]), ff_e=st.sampled_from([0, 1, 2]))
    def start(self, ff_g: int, ff_e: int) -> None:
        """Create both sessions; some examples fast-forward a session to Gate 2 or hand-off so the
        deep states (multi-approver gates, post-handoff changes) are exercised often."""
        for which, who, mode, policy in (
            ("g", OWNER_G, "guided", None),
            ("e", OWNER_E, "enterprise", "ent"),
        ):
            rec, _ = self.run(self.kit.service.create_session(who, mode, policy))  # type: ignore[arg-type]
            self.sessions[which] = rec.id
        for which, ff in (("g", ff_g), ("e", ff_e)):
            target = {1: "PLAN_REVIEW", 2: "HANDED_OFF"}.get(ff)
            for _ in range(40):
                if target is None or self.state_of(which) == target:
                    break
                self.natural_step(which, heavy=False)

    def state_of(self, which: str) -> str:
        who = OWNER_G if which == "g" else OWNER_E
        return self.run(self.kit.service.get_snapshot(who, self.sessions[which])).state  # type: ignore[no-any-return]

    @rule(which=st.sampled_from("ge"), text=st.sampled_from(TEXTS))
    def say(self, which: str, text: str) -> None:
        who = OWNER_G if which == "g" else OWNER_E
        self.send(which, who, UserMessage(event_id=self.eid(), text=text))

    @rule(
        which=st.sampled_from("ge"), option=st.sampled_from(["A", "B", "C"]), decide=st.booleans()
    )
    def tap(self, which: str, option: str, decide: bool) -> None:
        who = OWNER_G if which == "g" else OWNER_E
        snap = self.run(self.kit.service.get_snapshot(who, self.sessions[which]))
        if snap.pending_card is None:
            return
        event = OptionSelected(
            event_id=self.eid(),
            card_instance_id=snap.pending_card.instance_id,
            option_ids=[] if decide else [option],
            you_decide=decide,
        )
        self.send(which, who, event)

    @rule(
        which=st.sampled_from("ge"),
        gate=st.sampled_from(["brief", "plan"]),
        decision=st.sampled_from(["approve", "approve", "approve", "change", "reject"]),
        pick=st.sampled_from(["current", "current", "stale", "wrong_hash", "future"]),
        who_index=st.integers(0, 5),
        comment=st.sampled_from([None, "set design.direction=bold-dark", "feature+ chat", "hmm"]),
    )
    def gate_decision(
        self, which: str, gate: str, decision: str, pick: str, who_index: int, comment: str | None
    ) -> None:
        pool = WHO[which]
        who = pool[who_index % len(pool)]
        shown = [
            e
            for e in self.view(self.sessions[which]).events
            if isinstance(e, GateEvent) and e.gate == gate
        ]
        version, digest = (shown[-1].version, shown[-1].hash) if shown else (1, "0" * 64)
        if pick == "stale" and len(shown) > 1:
            version, digest = shown[0].version, shown[0].hash
        elif pick == "wrong_hash":
            digest = "f" * 64
        elif pick == "future":
            version += 1
        event = GateDecision.model_validate(
            {
                "event_id": self.eid(),
                "gate": gate,
                "decision": decision,
                "version": version,
                "hash": digest,
                "comment": comment,
            }
        )
        self.send(which, who, event)

    @rule(
        gate=st.sampled_from(["brief", "plan"]),
        decision=st.sampled_from(["change", "reject"]),
        who_index=st.integers(0, 2),
        comment=st.sampled_from([None, "set design.direction=bold-dark", "feature+ chat"]),
    )
    def approver_pushes_back(
        self, gate: str, decision: str, who_index: int, comment: str | None
    ) -> None:
        """An authorised approver asks for a change on the gate that is open right now (the
        random rule above rarely lines up the role, the open gate and the current hash)."""
        shown = [
            e
            for e in self.view(self.sessions["e"]).events
            if isinstance(e, GateEvent) and e.gate == gate
        ]
        if not shown:
            return
        who = [OWNER_E, PAT, DANA][who_index]
        event = GateDecision.model_validate(
            {
                "event_id": self.eid(),
                "gate": gate,
                "decision": decision,
                "version": shown[-1].version,
                "hash": shown[-1].hash,
                "comment": comment,
            }
        )
        self.send("e", who, event)

    @rule(
        which=st.sampled_from("ge"),
        gate=st.sampled_from(["brief", "plan"]),
        who_index=st.integers(0, 2),
        twice=st.booleans(),
    )
    def attacker_decides_the_open_gate(
        self, which: str, gate: str, who_index: int, twice: bool
    ) -> None:
        """The attackers of the security review press approve on whatever gate is open, with the
        current version and hash (the random rule above rarely lines everything up): someone who
        only claims ``requester``, an owner who holds every role, one person holding two roles."""
        shown = [
            e
            for e in self.view(self.sessions[which]).events
            if isinstance(e, GateEvent) and e.gate == gate
        ]
        if not shown:
            return
        pool = ATTACKERS[which]
        who = pool[who_index % len(pool)]
        for _ in range(2 if twice else 1):
            event = GateDecision.model_validate(
                {
                    "event_id": self.eid(),
                    "gate": gate,
                    "decision": "approve",
                    "version": shown[-1].version,
                    "hash": shown[-1].hash,
                }
            )
            self.send(which, who, event)

    @rule()
    def the_owner_holding_every_role_presses_approve(self) -> None:
        """The requester carries both approver roles too and approves on the open plan gate: the
        requester only ever stands in for the requester."""
        shown = [
            e
            for e in self.view(self.sessions["e"]).events
            if isinstance(e, GateEvent) and e.gate == "plan"
        ]
        if not shown:
            return
        event = GateDecision.model_validate(
            {
                "event_id": self.eid(),
                "gate": "plan",
                "decision": "approve",
                "version": shown[-1].version,
                "hash": shown[-1].hash,
            }
        )
        self.send("e", OWNER_E_ALL, event)

    @rule(roll=st.integers(0, 3))
    def one_person_holding_both_approver_roles(self, roll: int) -> None:
        """One person who carries both approver roles presses approve on the open plan gate, again
        and again: only one of the two roles may ever be theirs (drawn often so the walks reach it)."""
        shown = [
            e
            for e in self.view(self.sessions["e"]).events
            if isinstance(e, GateEvent) and e.gate == "plan"
        ]
        if not shown:
            return
        for _ in range(1 + roll % 3):
            event = GateDecision.model_validate(
                {
                    "event_id": self.eid(),
                    "gate": "plan",
                    "decision": "approve",
                    "version": shown[-1].version,
                    "hash": shown[-1].hash,
                }
            )
            self.send("e", PAT_DL, event)

    @rule(
        which=st.sampled_from("ge"),
        action=st.sampled_from(ACTIONS),
        tgt=st.sampled_from(TARGETS),
        value=st.sampled_from([True, False, None]),
    )
    def ui_action(
        self, which: str, action: UiActionName, tgt: str | None, value: bool | None
    ) -> None:
        who = OWNER_G if which == "g" else OWNER_E
        event = UiAction(event_id=self.eid(), action=action, target=tgt, value=value)
        self.send(which, who, event)

    @rule(which=st.sampled_from("ge"), roll=st.integers(0, 49))
    def restart_rarely(self, which: str, roll: int) -> None:
        # archive is irreversible, so keep it rare (1 draw in 50); the roll is a drawn value, not
        # derived from ids, so the walks do not depend on how many ids a turn happens to use
        if roll == 0:
            who = OWNER_G if which == "g" else OWNER_E
            self.send(which, who, UiAction(event_id=self.eid(), action="restart"))

    @rule()
    def run_job(self) -> None:
        self.run(self.kit.service.run_next_job())

    @rule(
        which=st.sampled_from("ge"),
        interrupt=st.sampled_from(["stop", "message", "archetype", "toggle"]),
    )
    def interrupted_run(self, which: str, interrupt: str) -> None:
        """Hold the next job inside the builder, interrupt it, release it."""
        who = OWNER_G if which == "g" else OWNER_E

        async def go() -> None:
            hold = self.kit.builder.pause_inside()
            run = asyncio.create_task(self.kit.service.run_next_job())
            waiter = asyncio.create_task(hold.entered.wait())
            await asyncio.wait({run, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if not run.done():
                event: InboundEvent
                if interrupt == "stop":
                    event = UiAction(event_id=self.eid(), action="stop")
                elif interrupt == "archetype":
                    event = UserMessage(event_id=self.eid(), text="set idea.archetype=booking")
                elif interrupt == "toggle":
                    event = UiAction(
                        event_id=self.eid(), action="toggle_feature", target="chat", value=True
                    )
                else:
                    event = UserMessage(event_id=self.eid(), text="set design.direction=bold-dark")
                self.history.append((which, who, event))
                with contextlib.suppress(*ALLOWED_ERRORS):
                    await self.kit.service.handle_event(who, self.sessions[which], event)
                hold.release()
                await run
            waiter.cancel()
            self.kit.builder.hold = None

        self.run(go())

    @rule(which=st.sampled_from("ge"), first=st.integers(0, 40), second=st.integers(0, 40))
    def concurrent_events(self, which: str, first: int, second: int) -> None:
        """Two events racing on one session serialise (row lock + advisory lock)."""
        who = OWNER_G if which == "g" else OWNER_E
        events: list[InboundEvent] = [
            UserMessage(event_id=self.eid(), text=TEXTS[first % len(TEXTS)]),
            UserMessage(event_id=self.eid(), text=TEXTS[second % len(TEXTS)]),
        ]
        for ev in events:
            self.history.append((which, who, ev))

        async def both() -> list[object]:
            calls: list[Callable[[], Awaitable[list[OutboundEvent]]]] = [
                lambda ev=ev: self.kit.service.handle_event(who, self.sessions[which], ev)  # type: ignore[misc]
                for ev in events
            ]
            return await asyncio.gather(*(c() for c in calls), return_exceptions=True)

        for res in self.run(both()):
            if isinstance(res, BaseException) and not isinstance(res, ALLOWED_ERRORS):
                raise res

    @rule(days=st.sampled_from([0, 1, 20]))
    def advance_clock(self, days: int) -> None:
        self.kit.clock.advance(days * 86400)

    @rule(ok=st.sampled_from([True, True, False]))
    def budget(self, ok: bool) -> None:
        """The budget gate's answer flips (GAP-5): a run that would start pauses the session
        instead, a run in flight halts between phases, ``resume`` starts again once it is back.
        Every invariant below must hold through all of it."""
        self.kit.budget.allow(ok)

    @rule(
        research=st.sampled_from(["ok", "ok", "ok", "raise", "unavailable"]),
        build=st.sampled_from(["ok", "ok", "ok", "ok", "raise", "repair", "invalid_hash"]),
        ask=st.booleans(),
        handoff_fails=st.booleans(),
    )
    def script_engines(self, research: str, build: str, ask: bool, handoff_fails: bool) -> None:
        self.kit.research.modes.append(research)  # type: ignore[arg-type]
        self.kit.builder.modes.append(build)  # type: ignore[arg-type]
        self.kit.builder.ask_slot = "pay.model" if ask else None
        if handoff_fails:
            self.kit.fake_repo.fail_next = 1

    @rule(which=st.sampled_from("ge"), mode=st.sampled_from(["raise", "invalid_hash"]))
    def failing_build(self, which: str, mode: str) -> None:
        """Take a session to a started run and make its build fail (the user must land back at
        the gate they came from with a notice, the job ended for good)."""
        for _ in range(12):
            if self.state_of(which) == "PLANNING":
                break
            self.natural_step(which, heavy=False)
        if self.state_of(which) != "PLANNING":
            return
        self.kit.builder.modes.insert(0, mode)  # type: ignore[arg-type]
        self.run(self.kit.service.run_next_job())

    @rule(which=st.sampled_from("ge"), steps=st.integers(1, 8), heavy=st.booleans())
    def advance(self, which: str, steps: int, heavy: bool) -> None:
        """Walk the happy path for a few steps so the deep states are reached often."""
        for _ in range(steps):
            self.natural_step(which, heavy)

    def natural_step(self, which: str, heavy: bool) -> None:
        owner = OWNER_G if which == "g" else OWNER_E
        snap = self.run(self.kit.service.get_snapshot(owner, self.sessions[which]))
        state = snap.state
        if state == "DISCOVERING":
            if snap.pending_card is not None:
                option = OptionSelected(
                    event_id=self.eid(),
                    card_instance_id=snap.pending_card.instance_id,
                    option_ids=["A"],
                )
                self.send(which, owner, option)
            else:
                self.send(which, owner, UserMessage(event_id=self.eid(), text="a plant shop"))
        elif state == "PLANNING":
            self.run(self.kit.service.run_next_job())
        elif state == "PAUSED":
            self.send(which, owner, UiAction(event_id=self.eid(), action="resume"))
        elif state in {"BRIEF_REVIEW", "PLAN_REVIEW"} and snap.gate is not None:
            gate = snap.gate
            roles = gate.status.required_roles
            approved = {a.role for a in gate.status.approvals if a.decision == "approve"}
            pending = [r for r in roles if r not in approved]
            who = owner
            if which == "e" and gate.gate == "plan" and pending:
                who = PAT if pending[0] == "po" else DANA
            event = GateDecision.model_validate(
                {
                    "event_id": self.eid(), "gate": gate.gate, "decision": "approve",
                    "version": gate.version, "hash": gate.hash,
                }
            )  # fmt: skip
            self.send(which, who, event)
        elif state == "HANDED_OFF":
            text = "set auth.method=sso" if heavy else "set design.direction=bold-dark"
            self.send(which, owner, UserMessage(event_id=self.eid(), text=text))

    @precondition(lambda self: bool(self.history))
    @rule(index=st.integers(0, 10_000))
    def duplicate_event(self, index: int) -> None:
        """Replaying a seen inbound id changes nothing and returns the original replies."""
        which, who, event = self.history[index % len(self.history)]
        before = self._effects()
        with contextlib.suppress(*ALLOWED_ERRORS):  # a forbidden caller is refused first
            self.run(self.kit.service.handle_event(who, self.sessions[which], event))
        assert self._effects() == before, "a duplicate inbound event changed something"

    def _effects(self) -> tuple[object, ...]:
        s = self.kit.memory
        return (
            *tuple(
                (
                    sid,
                    s.outbound_count(sid),
                    len(s.all_approvals(sid)),
                    len(s.all_jobs(sid)),
                    tuple((v.version, v.status) for v in s.all_specs(sid)),
                )
                for sid in sorted(self.sessions.values())
            ),
            len(self.kit.fake_repo.commits),
            len(self.kit.frontend.notifications),
        )

    # ----------------------------------------------------------------- invariants

    def view(self, sid: str) -> View:
        """Everything stored for one session, parsed once per step (events incrementally)."""
        store = self.kit.memory
        raw = store.data.outbound.get(sid, {})
        cached = self.event_cache.setdefault(sid, {})
        for seq in [q for q in cached if q not in raw]:
            del cached[seq]  # a rolled-back unit of work removed events
        for seq, text in raw.items():
            if seq not in cached:
                cached[seq] = _OUTBOUND.validate_json(text)
        rec = next(r for r in store.all_sessions() if r.id == sid)
        return View(
            rec=rec,
            events=[cached[q] for q in sorted(cached)],
            approvals=store.all_approvals(sid),
            specs=store.all_specs(sid),
            briefs=store.all_briefs(sid),
            jobs=store.all_jobs(sid),
        )

    @invariant()
    def everything_holds(self) -> None:
        views = {which: self.view(sid) for which, sid in self.sessions.items()}
        ready_total = 0
        for which, v in views.items():
            ready_total += self.check_hand_off(v)
            self.check_immutability(v)
            self.check_approvals_do_not_survive(v)
            self.check_runs(v)
            self.check_log(v)
            self.check_structure(v)
            self.check_gate_status(which, v)
            self.check_separation_of_duties(v)
            self.record(v)
        notified = [n.event_id for n in self.kit.frontend.notifications]
        assert len(notified) == len(set(notified)) == ready_total  # exactly once each

    def check_hand_off(self, v: View) -> int:
        """spec_ready only with full approval of its hash, after the commit; returns the count."""
        specs = {s.version: s for s in v.specs}
        count = 0
        for ev in v.events:
            if not isinstance(ev, SpecReady):
                continue
            count += 1
            rec = specs[ev.version]
            assert rec.hash == ev.hash and sha256_hex(rec.spec_text) == rec.hash
            assert rec.git_sha == ev.git_sha
            assert rec.status in {"handed_off", "superseded"}
            assert self._approved_for_handoff(v.rec.id, ev.version, ev.hash), (
                f"spec_ready v{ev.version} without approval of its hash"
            )
            assert any(
                c.version == ev.version and c.spec_hash == ev.hash
                for c in self.kit.fake_repo.commits
            ), "spec_ready without a commit"
        for rec in v.specs:  # anything past 'draft' was approved or replaced
            if rec.status in {"approved", "handed_off"}:
                assert self._approved_for_handoff(v.rec.id, rec.version, rec.hash)
        return count

    def check_immutability(self, v: View) -> None:
        """Approved versions are never mutated and statuses only move forward."""
        sid = v.rec.id
        for rec in v.specs:
            key = (sid, rec.version)
            assert sha256_hex(rec.spec_text) == rec.hash, "spec hash no longer matches its text"
            prev = self.spec_status.get(key)
            if prev is not None and rec.status != prev:
                assert rec.status in ALLOWED_STATUS_MOVES[prev], f"{prev} -> {rec.status}"
            self.spec_status[key] = rec.status
            if rec.status != "draft":  # approved at some point: content is frozen
                snapshot = (rec.hash, rec.spec_text)
                assert self.frozen_specs.setdefault(key, snapshot) == snapshot
        for b in v.briefs:
            assert b.brief.compute_hash() == b.brief.hash
            if b.brief.status == "approved":
                key = (sid, b.brief.version)
                assert self.frozen_briefs.setdefault(key, b.brief.hash) == b.brief.hash

    def check_approvals_do_not_survive(self, v: View) -> None:
        """Approvals never survive a version change: a gate's first event for a new hash shows
        nobody approved, and nobody is shown who did not approve exactly that hash."""
        seen: set[tuple[str, str]] = set()
        for ev in v.events:
            if not isinstance(ev, GateEvent):
                continue
            at_hash = {
                a.role
                for a in v.approvals
                if (a.gate, a.hash, a.version, a.decision)
                == (ev.gate, ev.hash, ev.version, "approve")
            }
            assert set(ev.approved_roles) <= at_hash
            if (ev.gate, ev.hash) not in seen:
                assert ev.approved_roles == [], "a new version opened with carried-over approvals"
                seen.add((ev.gate, ev.hash))
            if ev.single_button:
                assert ev.diff_lines, "a re-opened gate shows its diff"
            assert ev.buttons[0] in {"Looks right", "Send for approval"}

    def check_gate_status(self, which: str, v: View) -> None:
        who = OWNER_G if which == "g" else OWNER_E
        snap = self.run(self.kit.service.get_snapshot(who, v.rec.id))
        if snap.gate is None:
            return
        assert all(
            a.hash == snap.gate.hash and a.version == snap.gate.version
            for a in snap.gate.status.approvals
        )
        if snap.gate.status.status == "approved":
            assert gates.is_complete(
                snap.gate.status.required_roles, v.approvals,
                version=snap.gate.version, hash=snap.gate.hash,
            )  # fmt: skip

    def check_separation_of_duties(self, v: View) -> None:
        """Only the owner is ever the requester; where several roles must approve, one person never
        stands behind two of them and the owner never approves as anything but the requester."""
        owner = v.rec.owner_id
        roles = self._roles(v.rec.id)
        for a in v.approvals:
            if a.role == gates.REQUESTER:
                assert a.approver_id == owner, f"{a.approver_id} approved as the requester"
            if a.gate == "plan" and len(roles) > 1 and a.decision == "approve":
                assert a.approver_id != owner or a.role == gates.REQUESTER, (
                    "the owner approved as another role"
                )
        if len(roles) > 1:
            held: dict[tuple[str, int, str, str], set[str]] = {}
            for a in v.approvals:
                if a.gate == "plan" and a.decision == "approve" and a.role is not None:
                    held.setdefault((a.approver_id, a.version, a.hash, "plan"), set()).add(a.role)
            for key, got in held.items():
                assert len(got) == 1, f"{key[0]} approved for {sorted(got)} on one hash"
            for ev in v.events:
                if isinstance(ev, SpecReady):
                    behind = {
                        a.role: a.approver_id
                        for a in v.approvals
                        if (a.gate, a.version, a.hash, a.decision)
                        == ("plan", ev.version, ev.hash, "approve")
                        and a.role in roles
                    }
                    assert set(behind) == set(roles)
                    assert len(set(behind.values())) == len(roles), "one person, two approvers"

    def check_runs(self, v: View) -> None:
        """At most one active run; PLANNING has its (uncancelled) run; nothing outlives its state."""
        active = [j for j in v.jobs if j.state in {"queued", "running"}]
        assert len(active) <= 1, "two active runs coexist"
        if v.rec.state == "PLANNING":
            assert len(active) == 1 and not active[0].cancel_requested
        elif active:
            assert active[0].cancel_requested, "a run outlived its state without being cancelled"

    def check_log(self, v: View) -> None:
        assert [e.seq for e in v.events] == list(range(1, len(v.events) + 1))
        assert all(e.session_id == v.rec.id for e in v.events)
        ids = [e.event_id for e in v.events]
        assert len(ids) == len(set(ids))

    def check_structure(self, v: View) -> None:
        rec = v.rec
        machine.check(machine.state_of(rec))
        if rec.state in {"PLAN_REVIEW", "HANDED_OFF"}:
            assert rec.spec_version is not None and rec.approved_brief_version is not None
        if rec.state == "HANDED_OFF":
            assert rec.handed_off_version is not None
        if rec.state not in {"PLANNING", "DISCOVERING", "PAUSED"}:
            assert rec.plan_phase is None

    def record(self, v: View) -> None:
        """What the walks reached (asserted at the end, so the invariants are not vacuous)."""
        STATS[f"state:{v.rec.mode}:{v.rec.state}"] += 1
        for job in v.jobs:
            STATS[f"job:{job.state}"] += 1
            if job.error == "failed":  # a failure the flow handled: the store ends it cancelled
                STATS["job:failed"] += 1
        for a in v.approvals:
            kind = "light" if a.comment == gates.LIGHT_CHANGE_COMMENT else "normal"
            STATS[f"approval:{a.decision}:{kind}"] += 1
        ready = sum(isinstance(e, SpecReady) for e in v.events)
        STATS["spec_ready"] = max(STATS["spec_ready"], ready)
        STATS["max_spec_versions"] = max(STATS["max_spec_versions"], len(v.specs))


SETTINGS = settings(
    max_examples=70,
    stateful_step_count=30,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.data_too_large,
        HealthCheck.large_base_example,
    ],
)


def test_flow_invariants_hold_over_random_event_sequences() -> None:
    STATS.clear()
    run_state_machine_as_test(FlowMachine, settings=SETTINGS)
    # the walks must actually get somewhere, or the invariants above prove nothing
    assert STATS["state:guided:HANDED_OFF"] > 0 and STATS["state:enterprise:HANDED_OFF"] > 0
    assert STATS["state:guided:PLAN_REVIEW"] > 0 and STATS["state:enterprise:PLAN_REVIEW"] > 0
    assert STATS["spec_ready"] >= 1 and STATS["max_spec_versions"] >= 2
    assert STATS["job:cancelled"] > 0 and STATS["job:failed"] > 0
    assert STATS["approval:approve:light"] > 0  # a light post-handoff revision happened
    assert STATS["approval:reject:normal"] > 0 or STATS["approval:change:normal"] > 0
    assert STATS["state:guided:PAUSED"] + STATS["state:enterprise:PAUSED"] > 0
    assert STATS["state:guided:ARCHIVED"] + STATS["state:enterprise:ARCHIVED"] > 0


# ----------------------------------------------------------------------------- pure properties

from hypothesis import given  # noqa: E402


@given(
    st.lists(
        st.tuples(
            st.sampled_from(["po", "dl", "requester"]),
            st.sampled_from(["approve", "reject", "change"]),
            st.sampled_from(["h1", "h2"]),
            st.integers(1, 2),
        ),
        max_size=12,
    )
)
def test_completeness_matches_a_brute_force_oracle(
    history: list[tuple[str, str, str, int]],
) -> None:
    """``is_complete`` == every required role's latest decision on exactly this (version, hash)
    is an approval, whatever happened on other hashes."""
    from mde_contracts.gates import ApprovalRecord

    records = [
        ApprovalRecord.model_validate(
            {
                "id": f"apr_{i}",
                "session_id": "s",
                "gate": "plan",
                "version": ver,
                "hash": h,
                "approver_id": role,
                "role": role,
                "decision": dec,
            }
        )  # fmt: skip
        for i, (role, dec, h, ver) in enumerate(history)
    ]
    roles = ["po", "dl"]
    for version, digest in [(1, "h1"), (2, "h2"), (1, "h2")]:
        latest: dict[str, str] = {}
        for role, dec, h, ver in history:
            if (ver, h) == (version, digest):
                latest[role] = dec
        expected = all(latest.get(r) == "approve" for r in roles)
        assert gates.is_complete(roles, records, version=version, hash=digest) is expected


@given(st.data())
def test_random_walks_over_the_machine_stay_structurally_valid(data: st.DataObject) -> None:
    ms = machine.MachineState()
    for _ in range(data.draw(st.integers(1, 40))):
        legal = sorted(machine.legal_transitions(ms))
        if not legal:
            break
        name = data.draw(st.sampled_from(legal))
        kwargs: dict[str, Any] = {}
        if name in {"change_plan", "change_after_handoff"}:
            kwargs["start_phase"] = data.draw(st.sampled_from(["research", "build"]))
        if name == "plan_ready" and ms.origin == "HANDED_OFF":
            kwargs["light"] = data.draw(st.booleans())
        ms = machine.apply(ms, name, **kwargs)
        machine.check(ms)
    for name in machine.ALL_TRANSITIONS:
        if name not in machine.legal_transitions(ms):
            with pytest.raises(MdeError):
                machine.apply(ms, name)
