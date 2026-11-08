r"""Scenario runner: plays a YAML conversation against the REAL product, offline.

What runs: the real Discover, Research and Spec Builder engines behind the real ``Gateway`` (mock
mode), the real ``FlowService`` over the in-memory store (or Postgres), a git hand-off repo in a
temp directory, a fake clock and sequential ids. Only the provider is scripted, and only where a
scenario says so: every call id without a script entry runs the call spec's own mock handler.

Scenario file (one per row of the situation catalog; ``tests/scenarios/SIT-nnn-<slug>.yaml``)::

    id: SIT-004-vague-idea             # file stem
    situation: "..."                   # first column of the spec section 9 row (checked 1:1)
    mode: guided                       # guided | enterprise
    policy: enterprise-it-services-firm  # optional policy id (default: the mode's default)
    policies:                          # optional extra policies defined inline (inheritance resolved)
      - {id: guided-no-research, mode: guided, inherits: "guided-default@1", research: {enabled: false}}
    variants:                          # optional: same file, several sessions; each variant overrides
      hindi: {steps: [...]}            #   any top-level key (steps, policy, provider_script, ...)
    policies:                          # optional extra policies defined inline (inheritance resolved)
      - {id: guided-no-research, mode: guided, inherits: "guided-default@1", research: {enabled: false}}
    variants:                          # optional: same file, several sessions; each variant overrides
      hindi: {steps: [...]}            #   any top-level key (steps, policy, provider_script, ...)
    integration: true                  # optional: also run on PostgreSQL (-m integration)
    known_issue: "BUG-n: ..."          # optional: run as xfail(strict=True) with this reason
    gateway:                           # optional gateway/budget knobs
      session_cap_usd: "0.0005"        #   tiny per-session cap (the real budget guard refuses)
      research_cap_usd: ...            #   tiny research cap
      max_attempts: 2                  #   provider retry attempts
    provider_script:                   # scripted provider output by call id (a FIFO per id)
      C1.understand:
        - {intent: idea, confidence: 0.3, language: en}        # C1Output fields (validated)
        - {error: timeout, times: 4}                           # a provider failure
        - {raw: "not json", times: 2}                          # malformed output (repair, fallback)
        - {mock: true}                                         # this call runs the mock handler
        - {error: provider_500, times: 40, optional: true}     # may stay unused (breaker opens)
      S1.web_search:
        - {text: "...", citations: [{url: "https://x.example/a"}]}
    steps:                             # played in order, one action each, then ``expect``
      - user: "text"                   # (alias: say) a typed message
        repeat_tail: {text: " more.", times: 40}   # append that text N times (a long paste)
        event_id: evt-fixed            # optional: send with this id (duplicate-event tests)
      - tap: {card: Q-type-01, option: A}   # option id/label or a list; you_decide: true;
                                            # skip: true; other_text: "..."; recommended: true
                                            # (the option a user who agrees with everything takes)
      - upload: {filename: prd.md, text: "...", media_type: text/markdown}
      - decide: {gate: brief, decision: approve, hash: current}   # hash: current|stale|wrong;
                                            # also: as: <role> (another user holding that role),
                                            # comment: "...", role: ..., via is implied (tap)
      - ui: {action: stop, target: ..., value: ...}
      - wait_for_plan: true            # run the queued plan job to completion, inline
      - start_plan: {hold: research}   # start the job in the background; return once the engine
                                       #   (research | build) is inside: the run is in progress
      - finish_plan: true              # release the hold and wait for the job to end
      - answer_recommended: {limit: 60}  # tap the recommended option until the interview is over
      - advance_clock: 3600            # seconds (inactivity, a gate left open)
      - new_session: true              # a fresh session for the same user (after "start over")
      - new_session: true              # a fresh session for the same user (after "start over")
      - check: true                    # no action: only ``expect`` (look at the world now)
    final: {...}                       # same keys as ``expect``, checked after the last step

``expect`` keys (all optional; lists match as subsets unless noted)::

    state, plan_phase, origin, language           # session snapshot
    events: [{type: card, card_id: Q-x}, ...]     # in order, as a subsequence of THIS step's events;
                                                  # fields: type, card_id, text_contains, code, gate,
                                                  # phase, step, version, buttons, single_button
    no_events: [spec_ready, ...]                  # types that must NOT appear in this step
    card_ids: [Q-a, Q-b]                          # cards shown in this step, exactly, in order
    pending_card: Q-x | none
    text_contains / text_absent: [..]             # assistant messages + notices of this step
    text_regex_absent: ['\d']                     # regexes that must match none of them
    notices / notices_absent: [code, ..]
    slots: {id: {value: .., contains: .., state: .., source: .., empty: true}}
    gate: {gate: brief, status: open, required_roles: [..], approved_roles: [..], buttons: [..],
           single_button: true, diff_contains: ".."} | none      # gate event of THIS step
    gate_open: {gate: brief, status: open|approved|void}         # snapshot gate (now)
    approvals: [{gate, decision, role, via, version}, ..]        # subset of ALL approvals so far
    approvals_count: n
    spec_ready: n                                 # cumulative spec_ready events
    llm_calls: {C1.understand: 1, total: 0}       # calls made in THIS step by call id
    llm_calls_total: {C1.understand: 1}           # calls made so far (final: use this)
    brief_version, spec_version, handed_off_version, research_version, queued_messages: n | none
    research: {status: ok, reason: policy}        # latest Research Note
    spec: {screens_min: n, text_contains: "..", text_absent: ".."}   # latest spec record
    handoff_commits: n                            # commits in the project repo of the session
    plan: {research_added_min, backlog_min, compliance_min, excluded: [RA-..],
           diff_contains, diff_absent, research_note_contains,
           research_status: ok|unavailable|...}                   # latest Plan view
    reminders: {scheduled: n, roles: [..], cancelled: n}          # Enterprise reminder port
    inputs_contain / inputs_absent: {call_id: [needle, ..]}       # what reached a gateway call
    llm_failed / llm_repaired: n                  # gateway calls that failed / needed a repair
    absent_everywhere: ["literal"]                # in no event, record, Brief, spec or approval
    frontend_notified: n
    job: none | queued | running
    clock_job_state: ..                           # state of the newest job

Failures print a readable transcript (events so far, redacted). Never secrets.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from evals.harness.driver import DriverError, FlowDriver
from evals.harness.stack import (
    ScriptError,
    ScriptItem,
    Stack,
    StackOptions,
    StoreFactory,
    build_stack,
    script_item,
)
from mde_content.policies import resolve_policy
from mde_contracts.common import Mode
from mde_contracts.events import (
    AssistantMessage,
    CardEvent,
    GateEvent,
    Notice,
    OutboundEvent,
    PlanReady,
    ProgressEvent,
    SpecReady,
    StateChanged,
)
from mde_contracts.policy import Policy
from mde_core.redact import redact_text
from mde_flow.testing import Peek

SCENARIO_DIR = Path(__file__).resolve().parent
ACTIONS = (
    "user",
    "say",
    "tap",
    "upload",
    "decide",
    "ui",
    "wait_for_plan",
    "start_plan",
    "finish_plan",
    "answer_recommended",
    "advance_clock",
    "new_session",
    "check",
)


class ScenarioFailed(AssertionError):
    """A scenario expectation did not hold (the message carries the transcript)."""


# --------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class Scenario:
    id: str
    path: Path
    situation: str
    mode: str
    policy: str | None
    integration: bool
    known_issue: str | None
    gateway: Mapping[str, Any]
    script: Mapping[str, Sequence[Any]]
    steps: Sequence[Mapping[str, Any]]
    final: Mapping[str, Any]
    policies: Sequence[Mapping[str, Any]] = ()
    variant: str | None = None

    @property
    def param_id(self) -> str:
        return self.id if self.variant is None else f"{self.id}[{self.variant}]"


def _validate_steps(path: Path, steps: Sequence[Mapping[str, Any]], label: str) -> None:
    for n, step in enumerate(steps, 1):
        actions = [k for k in step if k in ACTIONS]
        if len(actions) != 1:
            raise ScriptError(
                f"{path.name} {label} step {n}: exactly one action of {ACTIONS}, got {actions}"
            )


def _build(path: Path, data: Mapping[str, Any], variant: str | None) -> Scenario:
    steps = cast("list[dict[str, Any]]", data.get("steps") or [])
    if not steps:
        raise ScriptError(f"{path.name}: no steps" + (f" in variant {variant}" if variant else ""))
    _validate_steps(path, steps, variant or "")
    return Scenario(
        id=str(data["id"]),
        path=path,
        situation=str(data["situation"]),
        mode=str(data["mode"]),
        policy=data.get("policy"),
        integration=bool(data.get("integration", False)),
        known_issue=data.get("known_issue"),
        gateway=data.get("gateway") or {},
        script=data.get("provider_script") or {},
        steps=steps,
        final=data.get("final") or {},
        policies=data.get("policies") or (),
        variant=variant,
    )


def load_scenario(path: Path) -> list[Scenario]:
    """The scenario file as one ``Scenario`` per variant (one when it has no ``variants``)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ScriptError(f"{path.name}: the file must be a mapping")
    data = cast("dict[str, Any]", raw)
    for key in ("id", "situation", "mode"):
        if key not in data:
            raise ScriptError(f"{path.name}: missing key {key!r}")
    if data["id"] != path.stem:
        raise ScriptError(f"{path.name}: id {data['id']!r} must equal the file stem")
    if data["mode"] not in ("guided", "enterprise"):
        raise ScriptError(f"{path.name}: mode must be guided or enterprise")
    variants = cast("dict[str, dict[str, Any]]", data.get("variants") or {})
    if not variants:
        return [_build(path, data, None)]
    base = {k: v for k, v in data.items() if k != "variants"}
    return [_build(path, {**base, **override}, name) for name, override in variants.items()]


def scenario_files() -> list[Path]:
    return sorted(SCENARIO_DIR.glob("SIT-*.yaml"))


def build_script(raw: Mapping[str, Sequence[Any]]) -> dict[str, list[ScriptItem]]:
    out: dict[str, list[ScriptItem]] = {}
    for call_id, entries in raw.items():
        items: list[ScriptItem] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ScriptError(f"{call_id}: each script entry must be a mapping, got {entry!r}")
            items.extend(script_item(call_id, cast("Mapping[str, Any]", entry)))
        out[call_id] = items
    return out


def inline_policies(raw: Sequence[Mapping[str, Any]]) -> list[Policy]:
    """Policies defined inside a scenario file, inheritance resolved like packaged ones."""
    return [resolve_policy(Policy.model_validate(dict(p))) for p in raw]


def stack_options(sc: Scenario, store_factory: StoreFactory | None = None) -> StackOptions:
    gw = sc.gateway
    return StackOptions(
        script=build_script(sc.script),
        store_factory=store_factory,
        extra_policies=inline_policies(sc.policies),
        session_cap_usd=gw.get("session_cap_usd"),
        research_cap_usd=gw.get("research_cap_usd"),
        global_cap_usd=gw.get("global_cap_usd"),
        max_attempts=int(gw.get("max_attempts", 4)),
    )


# --------------------------------------------------------------------------- transcript


def _clip(text: str, n: int = 160) -> str:
    safe = redact_text(text)[0].replace("\n", " ")
    return safe if len(safe) <= n else safe[: n - 3] + "..."


def describe_event(ev: OutboundEvent) -> str:
    if isinstance(ev, AssistantMessage):
        return f"assistant: {_clip(ev.text)}" + (f"  chips={len(ev.chips)}" if ev.chips else "")
    if isinstance(ev, CardEvent):
        opts = ",".join(o.id + ("*" if o.recommended else "") for o in ev.card.card.options)
        return f"card {ev.card.card.id} [{opts}] {_clip(ev.card.card.text, 70)}"
    if isinstance(ev, GateEvent):
        extra = (
            f" roles={ev.required_roles} approved={ev.approved_roles}" if ev.required_roles else ""
        )
        diff = f" diff={[_clip(line, 60) for line in ev.diff_lines[:4]]}" if ev.diff_lines else ""
        return f"gate {ev.gate} v{ev.version} {ev.hash[:8]} buttons={ev.buttons}{extra}{diff}"
    if isinstance(ev, Notice):
        return f"notice {ev.code}: {_clip(ev.text, 90)}"
    if isinstance(ev, ProgressEvent):
        return f"progress {ev.phase}/{ev.step}"
    if isinstance(ev, StateChanged):
        return f"state {ev.from_state} -> {ev.to_state} phase={ev.plan_phase}"
    if isinstance(ev, PlanReady):
        return f"plan_ready v{ev.version}"
    if isinstance(ev, SpecReady):
        return f"SPEC_READY v{ev.version} {ev.hash[:8]} {ev.git_sha[:8]}"
    return ev.type  # pragma: no cover


def describe_step(step: Mapping[str, Any]) -> str:
    for action in ACTIONS:
        if action in step:
            value = step[action]
            if isinstance(value, str):
                return f"{action} {_clip(value, 100)!r}"
            return f"{action} {_clip(str(value), 120)}"
    return "?"  # pragma: no cover


@dataclass
class Transcript:
    lines: list[str] = field(default_factory=lambda: [])

    def add(self, text: str) -> None:
        self.lines.append(text)

    def add_events(self, events: Sequence[OutboundEvent]) -> None:
        """One line per event; a run of progress events is folded into one line."""
        run: list[ProgressEvent] = []

        def flush() -> None:
            if run:
                steps = ", ".join(e.step for e in run)
                self.add(f"   < progress {run[0].phase}: {steps}")
                run.clear()

        for ev in events:
            if isinstance(ev, ProgressEvent):
                run.append(ev)
                continue
            flush()
            self.add("   < " + describe_event(ev))
        flush()

    def render(self) -> str:
        return "\n".join(self.lines)


# --------------------------------------------------------------------------- observation


@dataclass
class Observation:
    stack: Stack
    driver: FlowDriver
    new_events: list[OutboundEvent]
    calls_step: dict[str, int]
    calls_total: dict[str, int]


def _match_event(ev: OutboundEvent, want: Mapping[str, Any]) -> bool:
    for key, expected in want.items():
        if key == "type":
            if ev.type != expected:
                return False
        elif key == "card_id":
            if not isinstance(ev, CardEvent) or ev.card.card.id != expected:
                return False
        elif key == "text_contains":
            text = getattr(ev, "text", None)
            if not isinstance(text, str) or str(expected).lower() not in text.lower():
                return False
        elif key == "buttons":
            if not isinstance(ev, GateEvent) or ev.buttons != expected:
                return False
        else:
            if getattr(ev, key, object()) != expected:
                return False
    return True


def _texts(events: Sequence[OutboundEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, AssistantMessage | Notice)]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


async def _check(expect: Mapping[str, Any], obs: Observation) -> list[str]:
    """Evaluate one ``expect`` block; returns the list of failed expectations."""
    d, stack = obs.driver, obs.stack
    fails: list[str] = []
    snap = await d.snapshot()
    rec = await d.session_record()

    def eq(label: str, actual: Any, wanted: Any) -> None:
        if actual != wanted:
            fails.append(f"{label}: expected {wanted!r}, got {actual!r}")

    for key, actual in (
        ("state", snap.state),
        ("plan_phase", snap.plan_phase),
        ("origin", snap.origin),
        ("language", snap.language),
        ("brief_version", snap.brief_version),
        ("spec_version", snap.spec_version),
        ("handed_off_version", snap.handed_off_version),
        ("research_version", snap.research_version),
        ("queued_messages", snap.queued_messages),
    ):
        if key in expect:
            eq(key, actual, None if expect[key] == "none" else expect[key])

    if "pending_card" in expect:
        actual_card = snap.pending_card.card.id if snap.pending_card else None
        wanted = None if expect["pending_card"] in (None, "none") else expect["pending_card"]
        eq("pending_card", actual_card, wanted)

    events = obs.new_events
    if "events" in expect:
        pos = 0
        for want in expect["events"]:
            hit = next((i for i in range(pos, len(events)) if _match_event(events[i], want)), None)
            if hit is None:
                fails.append(f"events: no event matching {dict(want)} (after position {pos})")
            else:
                pos = hit + 1
    for kind in _as_list(expect.get("no_events")):
        if any(e.type == kind for e in events):
            fails.append(f"no_events: a {kind} event was produced")
    if "card_ids" in expect:
        eq(
            "card_ids",
            [e.card.card.id for e in events if isinstance(e, CardEvent)],
            expect["card_ids"],
        )
    texts = _texts(events)
    blob = "\n".join(texts).lower()
    for needle in _as_list(expect.get("text_contains")):
        if str(needle).lower() not in blob:
            fails.append(f"text_contains: {needle!r} not in this step's messages")
    for pattern in _as_list(expect.get("text_regex_absent")):
        if re.search(str(pattern), blob):
            fails.append(f"text_regex_absent: /{pattern}/ matched this step's messages")
    for needle in _as_list(expect.get("text_absent")):
        if str(needle).lower() in blob:
            fails.append(f"text_absent: {needle!r} found in this step's messages")
    codes = [e.code for e in events if isinstance(e, Notice)]
    for code in _as_list(expect.get("notices")):
        if code not in codes:
            fails.append(f"notices: {code!r} not among {codes}")
    for code in _as_list(expect.get("notices_absent")):
        if code in codes:
            fails.append(f"notices_absent: {code!r} was shown")

    for slot_id, want in cast("Mapping[str, Mapping[str, Any]]", expect.get("slots", {})).items():
        record = rec.discovery.slots.get(slot_id)
        if want.get("empty"):
            if record is not None and record.state != "empty":
                fails.append(
                    f"slots[{slot_id}]: expected empty, got {record.state}={record.value!r}"
                )
            continue
        if record is None:
            fails.append(f"slots[{slot_id}]: not set (expected {dict(want)})")
            continue
        if "value" in want:
            eq(f"slots[{slot_id}].value", record.value, want["value"])
        if "contains" in want:
            value = record.value
            hay = value if isinstance(value, list) else str(value)
            if want["contains"] not in hay:
                fails.append(f"slots[{slot_id}]: {value!r} does not contain {want['contains']!r}")
        if "state" in want:
            eq(f"slots[{slot_id}].state", record.state, want["state"])
        if "source" in want:
            eq(f"slots[{slot_id}].source", record.source, want["source"])

    if "gate" in expect:
        gates = [e for e in events if isinstance(e, GateEvent)]
        want_gate = expect["gate"]
        if want_gate in (None, "none"):
            if gates:
                fails.append(
                    f"gate: expected no gate event, got {[describe_event(g) for g in gates]}"
                )
        elif not gates:
            fails.append("gate: expected a gate event, none in this step")
        else:
            g = gates[-1]
            checks: Mapping[str, Any] = {
                "gate": g.gate,
                "required_roles": g.required_roles,
                "approved_roles": g.approved_roles,
                "buttons": g.buttons,
                "single_button": g.single_button,
                "version": g.version,
            }
            for k, v in cast("Mapping[str, Any]", want_gate).items():
                if k == "diff_contains":
                    if not any(str(v).lower() in line.lower() for line in g.diff_lines):
                        fails.append(f"gate.diff_contains: {v!r} not in {g.diff_lines}")
                elif k == "diff_empty":
                    eq("gate.diff_empty", not g.diff_lines, bool(v))
                elif k in checks:
                    eq(f"gate.{k}", checks[k], v)
                else:
                    fails.append(f"gate: unknown key {k!r}")
    if "gate_open" in expect:
        want_open = expect["gate_open"]
        if want_open in (None, "none"):
            eq("gate_open", snap.gate, None)
        elif snap.gate is None:
            fails.append("gate_open: no gate is open")
        else:
            for k, v in cast("Mapping[str, Any]", want_open).items():
                actual_open = {"gate": snap.gate.gate, "status": snap.gate.status.status,
                               "version": snap.gate.version}  # fmt: skip
                eq(f"gate_open.{k}", actual_open.get(k), v)

    if "approvals" in expect or "approvals_count" in expect:
        approvals = await d.approvals()
        if "approvals_count" in expect:
            eq("approvals_count", len(approvals), expect["approvals_count"])
        for want_a in _as_list(expect.get("approvals")):
            if not any(
                all(getattr(a, k) == v for k, v in cast("Mapping[str, Any]", want_a).items())
                for a in approvals
            ):
                got = [(a.gate, a.decision, a.role, a.via, a.version) for a in approvals]
                fails.append(f"approvals: nothing matches {dict(want_a)}; have {got}")

    if "spec_ready" in expect:
        everything = await d.events()
        eq(
            "spec_ready",
            sum(1 for e in everything if isinstance(e, SpecReady)),
            expect["spec_ready"],
        )
    if "frontend_notified" in expect:
        eq("frontend_notified", len(stack.frontend.notifications), expect["frontend_notified"])

    if "llm_calls" in expect:
        for call_id, n in cast("Mapping[str, int]", expect["llm_calls"]).items():
            actual_n = (
                sum(obs.calls_step.values())
                if call_id == "total"
                else obs.calls_step.get(call_id, 0)
            )
            eq(f"llm_calls[{call_id}]", actual_n, n)
    if "llm_calls_total" in expect:
        for call_id, n in cast("Mapping[str, int]", expect["llm_calls_total"]).items():
            actual_n = (
                sum(obs.calls_total.values())
                if call_id == "total"
                else obs.calls_total.get(call_id, 0)
            )
            eq(f"llm_calls_total[{call_id}]", actual_n, n)

    if "research" in expect:
        note = await d.research_note()
        if note is None:
            fails.append("research: no Research Note stored")
        else:
            for k, v in cast("Mapping[str, Any]", expect["research"]).items():
                actual_r = {"status": note.note.status, "reason": note.note.unavailable_reason}.get(
                    k
                )
                eq(f"research.{k}", actual_r, v)
    if "spec" in expect:
        specs = await d.specs()
        if not specs:
            fails.append("spec: no spec version stored")
        else:
            latest = specs[-1]
            for k, v in cast("Mapping[str, Any]", expect["spec"]).items():
                if k == "screens_min":
                    if len(latest.spec.screens) < int(v):
                        fails.append(f"spec.screens_min: {len(latest.spec.screens)} < {v}")
                elif k == "text_contains":
                    if str(v) not in latest.spec_text:
                        fails.append(f"spec.text_contains: {v!r} not in the spec text")
                elif k == "text_absent":
                    if str(v) in latest.spec_text:
                        fails.append(f"spec.text_absent: {v!r} is in the spec text")
                elif k == "status":
                    eq("spec.status", latest.status, v)
                elif k == "versions":
                    eq("spec.versions", len(specs), v)
                else:
                    fails.append(f"spec: unknown key {k!r}")
    if "handoff_commits" in expect:
        eq(
            "handoff_commits",
            _git_commits(stack, rec.tenant_id, rec.app_id),
            expect["handoff_commits"],
        )
    if "job" in expect:
        jobs = await d.jobs()
        active = [j for j in jobs if j.state in ("queued", "running")]
        actual_job = active[-1].state if active else "none"
        eq("job", actual_job, expect["job"])
    if "last_job_state" in expect:
        jobs = await d.jobs()
        eq("last_job_state", jobs[-1].state if jobs else None, expect["last_job_state"])
    if "reminders" in expect:
        sched = stack.reminders.scheduled
        want_r = cast("Mapping[str, Any]", expect["reminders"])
        if "scheduled" in want_r:
            eq("reminders.scheduled", len(sched), want_r["scheduled"])
        if "roles" in want_r:
            eq(
                "reminders.roles",
                sorted({r for s in sched for r in s.roles}),
                sorted(want_r["roles"]),
            )
        if "cancelled" in want_r:
            eq("reminders.cancelled", len(stack.reminders.cancelled), want_r["cancelled"])
    for call_id, needles in cast("Mapping[str, Any]", expect.get("inputs_contain", {})).items():
        dumps = [i.model_dump_json() for i in stack.client.inputs_for(call_id)]
        for needle in _as_list(needles):
            if not any(str(needle).lower() in dumped.lower() for dumped in dumps):
                fails.append(
                    f"inputs_contain[{call_id}]: {needle!r} not in any of {len(dumps)} inputs"
                )
    for call_id, needles in cast("Mapping[str, Any]", expect.get("inputs_absent", {})).items():
        dumps = [i.model_dump_json() for i in stack.client.inputs_for(call_id)]
        for needle in _as_list(needles):
            if any(str(needle).lower() in dumped.lower() for dumped in dumps):
                fails.append(f"inputs_absent[{call_id}]: {needle!r} reached the call")
    if "sessions" in expect:
        everyone = await Peek(stack.store).sessions()
        want_s = cast("Mapping[str, Any]", expect["sessions"])
        if "total" in want_s:
            eq("sessions.total", len(everyone), want_s["total"])
        if "archived" in want_s:
            eq(
                "sessions.archived",
                sum(1 for s in everyone if s.state == "ARCHIVED"),
                want_s["archived"],
            )
    if "llm_failed" in expect:
        eq("llm_failed", len(stack.calls.failed()), expect["llm_failed"])
    if "llm_repaired" in expect:
        eq("llm_repaired", len(stack.calls.repaired()), expect["llm_repaired"])
    if "plan" in expect:
        specs = await d.specs()
        plan = specs[-1].plan if specs else None
        if plan is None:
            fails.append("plan: no Plan view stored")
        else:
            for k, v in cast("Mapping[str, Any]", expect["plan"]).items():
                if k == "research_added_min":
                    if len(plan.research_added) < int(v):
                        fails.append(f"plan.research_added_min: {len(plan.research_added)} < {v}")
                elif k == "backlog_min":
                    if len(plan.backlog) < int(v):
                        fails.append(f"plan.backlog_min: {len(plan.backlog)} < {v}")
                elif k == "research_note_contains":
                    if str(v).lower() not in (plan.research_note or "").lower():
                        fails.append(
                            f"plan.research_note_contains: {v!r} not in {plan.research_note!r}"
                        )
                elif k == "compliance_min":
                    if len(plan.compliance) < int(v):
                        fails.append(f"plan.compliance_min: {len(plan.compliance)} < {v}")
                elif k == "excluded":
                    got = sorted(a.id for a in plan.research_added if not a.included)
                    eq("plan.excluded", got, sorted(v))
                elif k == "diff_contains":
                    lines = plan.diff_since_gate1.lines if plan.diff_since_gate1 else []
                    if not any(str(v).lower() in ln.lower() for ln in lines):
                        fails.append(f"plan.diff_contains: {v!r} not in {lines}")
                elif k == "diff_absent":
                    lines = plan.diff_since_gate1.lines if plan.diff_since_gate1 else []
                    if any(str(v).lower() in ln.lower() for ln in lines):
                        fails.append(f"plan.diff_absent: {v!r} found in {lines}")
                else:
                    eq(f"plan.{k}", getattr(plan, k, None), v)
    needles_everywhere = _as_list(expect.get("absent_everywhere"))
    if needles_everywhere:
        everywhere = await _everything(d)
        for needle in needles_everywhere:
            if str(needle) in everywhere:
                fails.append("absent_everywhere: a forbidden literal is stored or was emitted")
    return fails


async def _everything(d: FlowDriver) -> str:
    """Every event, record and artifact of the session as one string (for absence checks)."""
    parts: list[str] = [e.model_dump_json() for e in await d.events()]
    parts.append((await d.session_record()).model_dump_json())
    parts += [b.model_dump_json() for b in await d.briefs()]
    parts += [s.model_dump_json() for s in await d.specs()]
    parts += [a.model_dump_json() for a in await d.approvals()]
    note = await d.research_note()
    if note is not None:
        parts.append(note.model_dump_json())
    return "\n".join(parts)


def _git_commits(stack: Stack, tenant_id: str, app_id: str) -> int:
    repo_dir = stack.repo.repo_dir(tenant_id, app_id)
    if not (repo_dir / ".git").exists():
        return 0
    out = subprocess.run(
        [shutil.which("git") or "git", "-C", str(repo_dir), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(out.stdout.strip() or 0) if out.returncode == 0 else 0


# --------------------------------------------------------------------------- playing


async def _act(
    step: Mapping[str, Any], d: FlowDriver, stack: Stack, mode: Mode, policy: str | None
) -> list[OutboundEvent]:
    event_id = step.get("event_id")
    if "user" in step or "say" in step:
        text = str(step.get("user", step.get("say")))
        tail = cast("Mapping[str, Any] | None", step.get("repeat_tail"))
        if tail:  # a long paste without a page of YAML
            text += str(tail["text"]) * int(tail["times"])
        return await d.say(text, event_id=event_id)
    if "tap" in step:
        spec = cast("Mapping[str, Any]", step["tap"] or {})
        if spec.get("recommended"):  # what a user who agrees with every suggestion does
            snap = await d.snapshot()
            if spec.get("card") and (
                snap.pending_card is None or snap.pending_card.card.id != spec["card"]
            ):
                have = snap.pending_card.card.id if snap.pending_card else None
                raise DriverError(f"the pending card is {have}, not {spec['card']}")
            return await d.tap_recommended(str(spec.get("other_text", "Blue Basket")))
        return await d.tap(
            spec.get("option"),
            card=spec.get("card"),
            other_text=spec.get("other_text"),
            you_decide=bool(spec.get("you_decide", False)),
            skipped=bool(spec.get("skip", False)),
            event_id=event_id,
        )
    if "upload" in step:
        up = cast("Mapping[str, Any]", step["upload"])
        return await d.upload(
            str(up.get("filename", "notes.md")),
            up.get("text"),
            media_type=str(up.get("media_type", "text/markdown")),
            event_id=event_id,
        )
    if "decide" in step:
        dec = cast("Mapping[str, Any]", step["decide"])
        who = dec.get("as")
        return await d.decide(
            dec["gate"],
            str(dec.get("decision", "approve")),
            hash=dec.get("hash", "current"),
            comment=dec.get("comment"),
            role=dec.get("role", who),
            as_user=f"u-{who}" if who else None,
            event_id=event_id,
        )
    if "ui" in step:
        ui = cast("Mapping[str, Any]", step["ui"])
        return await d.ui(ui["action"], ui.get("target"), ui.get("value"), event_id=event_id)
    if "wait_for_plan" in step:
        return await d.run_plan_events()
    if "start_plan" in step:
        hold = str(cast("Mapping[str, Any]", step["start_plan"] or {}).get("hold", "research"))
        await d.start_plan(hold)
        return await d.fresh()
    if "finish_plan" in step:
        return await d.finish_plan()
    if "answer_recommended" in step:
        opts = cast("Mapping[str, Any]", step["answer_recommended"] or {})
        return await d.answer_with_recommended(
            str(opts.get("text", "Blue Basket")), int(opts.get("limit", 60))
        )
    if "advance_clock" in step:
        stack.clock.advance(float(step["advance_clock"]))
        return []
    if "new_session" in step:
        return await d.start(mode=mode, policy_id=policy)
    return []  # check


@dataclass
class Outcome:
    scenario: Scenario
    transcript: Transcript
    failures: list[str]
    stack: Stack
    driver: FlowDriver


async def play(sc: Scenario, stack: Stack) -> Outcome:
    """Run every step; collect failures instead of stopping at the first one."""
    d = FlowDriver.for_user(stack)
    t = Transcript()
    failures: list[str] = []
    policy = sc.policy
    greeting = await d.start(mode=cast("Mode", sc.mode), policy_id=policy)
    t.add(f"== {sc.id} ({sc.mode}{', ' + policy if policy else ''})")
    for ev in greeting:
        t.add("   < " + describe_event(ev))
    for n, step in enumerate(sc.steps, 1):
        before = dict(stack.calls.counts())
        t.add(f"[{n}] > {describe_step(step)}")
        try:
            returned = await _act(step, d, stack, cast("Mode", sc.mode), policy)
        except DriverError as exc:
            failures.append(f"step {n}: {exc}")
            t.add(f"    !! {exc}")
            continue
        fresh = await d.fresh()
        seen = {e.event_id for e in returned}
        new = [*returned, *[e for e in fresh if e.event_id not in seen]]
        t.add_events(new)
        after = dict(stack.calls.counts())
        step_calls = {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)}
        obs = Observation(stack, d, new, step_calls, after)
        expect = cast("Mapping[str, Any]", step.get("expect") or {})
        problems = await _check(expect, obs)
        snap = await d.snapshot()
        t.add(f"    . state={snap.state} phase={snap.plan_phase} calls={step_calls or '-'}")
        for p in problems:
            failures.append(f"step {n} ({describe_step(step)}): {p}")
            t.add(f"    !! {p}")
    if sc.final:
        after = dict(stack.calls.counts())
        obs = Observation(stack, d, [], {}, after)
        for p in await _check(sc.final, obs):
            failures.append(f"final: {p}")
            t.add(f"    !! final: {p}")
    leftover = stack.provider.pending()
    if leftover:
        msg = f"scripted provider outputs never used (the scenario does not exercise them): {leftover}"
        failures.append(msg)
        t.add(f"    !! {msg}")
    return Outcome(sc, t, failures, stack, d)


def assert_outcome(outcome: Outcome) -> None:
    if outcome.failures:
        body = "\n".join(f"  - {f}" for f in outcome.failures)
        raise ScenarioFailed(
            f"{outcome.scenario.id}: {len(outcome.failures)} expectation(s) failed\n{body}\n"
            f"--- transcript ---\n{outcome.transcript.render()}"
        )


async def run_scenario(
    sc: Scenario, projects_dir: Path, store_factory: StoreFactory | None = None
) -> Outcome:
    stack = build_stack(projects_dir, stack_options(sc, store_factory))
    outcome = await play(sc, stack)
    assert_outcome(outcome)
    return outcome
