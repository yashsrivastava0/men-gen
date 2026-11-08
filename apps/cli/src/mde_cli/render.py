"""Outbound events, cards and the two gate pages as plain text.

Everything here is a pure function from a typed model to a list of ``(text, style)`` lines, so the
transcript is easy to test and the terminal layer (``console.Out``) stays dumb. The wording is the
API's own (assistant messages, questions, gate buttons); the CLI adds only the numbering and a few
hint lines (``strings.yaml``). Output is ASCII apart from what the API sends.

A transcript line shape (stable; ``docs/packages/cli.md``):

    Assistant: ...                      a reply
    Question 2 of 10                    a card, then   1) option   2) option  ...
    [research 2/4] Looking at ...       a progress update
    === Brief, version 1 ... ===        a gate page, then   1) Looks right   2) Change something
    Spec ready.                         spec_ready summary: path, version, hash, commit
"""

from __future__ import annotations

from dataclasses import dataclass

from mde_cli.strings import say
from mde_contracts.brief import Brief
from mde_contracts.cards import CardInstance
from mde_contracts.events import (
    AssistantMessage,
    BriefPage,
    CardEvent,
    GateEvent,
    Notice,
    OutboundEvent,
    PlanPage,
    PlanReady,
    ProgressEvent,
    SpecReady,
    StateChanged,
)
from mde_contracts.gates import GateStatus
from mde_contracts.plan import PlanView
from mde_flow.models import GateSnapshot

ASSISTANT = "Assistant: "
SHORT_HASH = 12


@dataclass(frozen=True)
class Line:
    text: str
    style: str | None = None


def short_hash(value: str, size: int = SHORT_HASH) -> str:
    return value[:size]


def _para(text: str, first: str, rest: str, style: str | None = None) -> list[Line]:
    rows = text.splitlines() or [""]
    out = [Line(f"{first}{rows[0]}", style)]
    out.extend(Line(f"{rest}{row}" if row else "", style) for row in rows[1:])
    return out


# --------------------------------------------------------------------------- one event


def render_event(event: OutboundEvent, *, language: str | None = None) -> list[Line]:
    """The lines for one outbound event."""
    if isinstance(event, AssistantMessage):
        return render_assistant(event)
    if isinstance(event, CardEvent):
        return render_card(event.card, language=language or event.card.language)
    if isinstance(event, ProgressEvent):
        counter = f" {event.done}/{event.total}" if event.total else ""
        return [Line(f"  [{event.phase}{counter}] {event.label}", "dim")]
    if isinstance(event, GateEvent):
        return render_gate(event)
    if isinstance(event, PlanReady):
        return [
            Line(f"Plan ready (version {event.version}, hash {short_hash(event.hash)}).", "bold")
        ]
    if isinstance(event, SpecReady):
        return render_spec_ready(event)
    if isinstance(event, StateChanged):
        phase = f" ({event.plan_phase})" if event.plan_phase else ""
        return [Line(f"-- now: {event.to_state}{phase}", "dim")]
    if isinstance(event, Notice):
        label = "Warning" if event.level == "warn" else "Note"
        return [Line(f"{label}: {event.text}", "yellow" if event.level == "warn" else None)]
    return []  # pragma: no cover - the union above is exhaustive


def render_assistant(event: AssistantMessage) -> list[Line]:
    out = _para(event.text, ASSISTANT, " " * len(ASSISTANT))
    if event.chips:
        out.append(Line("  You could say: " + " | ".join(event.chips), "dim"))
    return out


def render_spec_ready(event: SpecReady) -> list[Line]:
    return [
        Line(""),
        Line("Spec ready.", "bold green"),
        Line(f"  path:    {event.path}"),
        Line(f"  repo:    {event.repo_dir}"),
        Line(f"  version: {event.version}"),
        Line(f"  hash:    {event.hash}"),
        Line(f"  commit:  {event.git_sha}"),
    ]


# --------------------------------------------------------------------------- cards


def card_kind(card: CardInstance) -> str:
    """``choice``, ``multi`` or ``text``: how the CLI takes an answer to this card."""
    kind = card.card.type
    if kind == "multi":
        return "multi"
    if kind in ("text", "upload"):
        return "text"
    return "choice"


def render_card(instance: CardInstance, *, language: str | None = None) -> list[Line]:
    card = instance.card
    lang = language or instance.language
    heading = "Question"
    if instance.progress is not None:
        heading += f" {instance.progress.asked} of {instance.progress.cap}"
    if card.kind != "standard":
        heading += f" ({card.kind})"
    out = [Line(""), Line(heading, "bold")]
    out.extend(_para(card.text, "  ", "  "))
    for number, option in enumerate(card.options, start=1):
        mark = "  (suggested)" if option.recommended else ""
        out.append(Line(f"  {number}) {option.label}{mark}"))
        if option.description:
            out.append(Line(f"       {option.description}", "dim"))
        if option.preview and option.preview.startswith("theme:"):
            out.append(Line(f"       look: {option.preview.removeprefix('theme:')}", "dim"))
    if card.help:
        out.append(Line(f"  Why I ask: {card.help}", "dim"))
    key = {"choice": "card_hint_choice", "multi": "card_hint_multi", "text": "card_hint_text"}[
        card_kind(instance)
    ]
    out.append(Line("  " + say(key, lang), "dim"))
    return out


# --------------------------------------------------------------------------- gates


def approvals_line(required: list[str], approved: list[str]) -> str | None:
    """``Approvals: requester [approved], delivery_lead [waiting]`` (nothing for a plain
    one-person gate)."""
    if len(required) < 2 and not approved:
        return None
    parts = [f"{role} [{'approved' if role in approved else 'waiting'}]" for role in required]
    return "Approvals: " + ", ".join(parts) if parts else None


def status_approvals(status: GateStatus) -> str | None:
    """The same line from a gate's derived status (the snapshot)."""
    approved = [a.role for a in status.approvals if a.decision == "approve" and a.role]
    return approvals_line(list(status.required_roles), approved)


def render_gate(event: GateEvent, *, language: str | None = None) -> list[Line]:
    page = event.page
    out: list[Line] = [Line("")]
    if isinstance(page, BriefPage):
        out.extend(render_brief(page.brief))
    else:
        assert isinstance(page, PlanPage)
        out.extend(render_plan(page.plan))
    if event.diff_lines:
        out.append(Line("What changed since you last looked:", "bold"))
        out.extend(Line(f"  - {row}") for row in event.diff_lines)
    status = approvals_line(event.required_roles, event.approved_roles)
    if status:
        out.append(Line(status, "dim"))
    out.extend(render_buttons(event.buttons, language))
    return out


def render_gate_snapshot(gate: GateSnapshot, *, language: str | None = None) -> list[Line]:
    """The open gate as the session snapshot describes it (to resume a session)."""
    out: list[Line] = [Line("")]
    page = gate.page
    out.extend(render_brief(page.brief) if isinstance(page, BriefPage) else render_plan(page.plan))
    status = status_approvals(gate.status)
    if status:
        out.append(Line(status, "dim"))
    out.extend(render_buttons(gate.buttons, language))
    return out


def render_buttons(buttons: list[str], language: str | None = None) -> list[Line]:
    out = [Line(f"  {number}) {label}", "bold") for number, label in enumerate(buttons, start=1)]
    out.append(Line("  " + say("gate_hint", language), "dim"))
    return out


def render_brief(brief: Brief) -> list[Line]:
    out = [
        Line(f"=== Brief, version {brief.version} (hash {short_hash(brief.hash)}) ===", "bold"),
        Line(""),
    ]
    out.extend(_para(brief.app_summary, "In short: ", "          "))
    out.append(Line(""))
    for row in brief.rows:
        out.extend(_para(row.text, f"  {row.title}: ", "      "))
        if row.features:
            out.append(
                Line(
                    "      "
                    + "  ".join(f"[{'x' if f.on else ' '}] {f.label}" for f in row.features)
                )
            )
        if row.swatch:
            out.append(Line("      colours: " + " ".join(row.swatch), "dim"))
    if brief.assumptions:
        out.append(Line(""))
        out.append(Line("What I assumed (tell me if any of these is wrong):", "bold"))
        for item in brief.assumptions:
            off = "" if item.included else " (switched off)"
            out.append(Line(f"  - {item.label}: {item.value_text}{off}"))
            out.append(Line(f"      because {item.reason}", "dim"))
    out.append(Line(""))
    return out


def render_plan(plan: PlanView) -> list[Line]:
    out = [
        Line(
            f"=== Plan, version {plan.spec_version} (hash {short_hash(plan.spec_hash)}) ===", "bold"
        ),
        Line(""),
    ]
    out.extend(_para(plan.summary, "In short: ", "          "))
    out.append(Line(""))
    out.append(Line(f"Screens: {plan.screen_count} in total", "bold"))
    for group in plan.screen_map:
        titles = ", ".join(group.screen_titles)
        out.append(Line(f"  {group.flow_name} ({group.count}): {titles}"))
    out.append(Line(""))
    look = plan.look
    swatches = ", ".join(f"{name} {value}" for name, value in look.swatches_light.items())
    out.append(Line(f"Look: {look.direction}; corner radius {look.radius}", "bold"))
    if swatches:
        out.append(Line(f"  colours (light): {swatches}", "dim"))
    if plan.data:
        out.append(Line(""))
        out.append(Line("The information the app keeps, in plain words:", "bold"))
        for entity in plan.data:
            out.append(Line(f"  - {entity.plain_name}: {', '.join(entity.fields_plain)}"))
    out.append(Line(""))
    out.append(Line(f"Research: {plan.research_status}", "bold"))
    if plan.research_note:
        out.append(Line(f"  {plan.research_note}"))
    if plan.research_added:
        out.append(Line("  What research added:"))
        for item in plan.research_added:
            mark = "x" if item.included else " "
            check = "verified" if item.verified else "unverified"
            out.append(Line(f"  - [{mark}] {item.title} ({item.kind}, {check}): {item.why}"))
            for source in item.sources:
                title = f" ({source.title})" if source.title else ""
                out.append(Line(f"        source: {source.url}{title}", "dim"))
    if plan.compliance:
        out.append(Line(""))
        out.append(Line("Rules to check (pointers only, not legal advice):", "bold"))
        out.extend(Line(f"  - {p.title}: {p.url}") for p in plan.compliance)
    if plan.integrations:
        out.append(Line(""))
        out.append(Line("Connections to other services (real or mocked):", "bold"))
        for integ in plan.integrations:
            dev = ", needs a development build" if integ.needs_dev_build else ""
            out.append(Line(f"  - {integ.name}: {integ.mode}{dev}"))
    if plan.roles:
        out.append(Line(""))
        out.append(Line("Who sees what:", "bold"))
        for role in plan.roles:
            out.append(Line(f"  - {role.name}: {', '.join(role.sees)}"))
    if plan.diff_since_gate1 and plan.diff_since_gate1.lines:
        out.append(Line(""))
        out.append(Line("Since the Brief you approved:", "bold"))
        out.extend(Line(f"  - {row}") for row in plan.diff_since_gate1.lines)
    if plan.assumptions:
        out.append(Line(""))
        out.append(Line("What I assumed:", "bold"))
        out.extend(Line(f"  - {a.label}: {a.value_text}") for a in plan.assumptions)
    if plan.backlog:
        out.append(Line(""))
        out.append(Line("Kept for later:", "bold"))
        out.extend(Line(f"  - {item.title} ({item.reason})") for item in plan.backlog)
    if plan.enterprise is not None:
        extras = plan.enterprise
        if extras.approvers:
            out.append(Line(""))
            out.append(Line("Approvers:", "bold"))
            for approver in extras.approvers:
                who = f" ({approver.user_id})" if approver.user_id else ""
                out.append(Line(f"  - {approver.role}{who}: {approver.decision or 'waiting'}"))
        if extras.comments:
            out.append(Line("Comments:", "bold"))
            out.extend(Line(f"  - {c}") for c in extras.comments)
    out.append(Line(""))
    return out
