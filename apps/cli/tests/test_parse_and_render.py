"""What typing means (cards, gates, commands), and how events, cards and the two pages look."""

from __future__ import annotations

from datetime import date

import pytest

from mde_cli.chat import (
    Command,
    GateInput,
    Hint,
    Say,
    Tap,
    parse_card_input,
    parse_command,
    parse_gate_input,
)
from mde_cli.render import (
    approvals_line,
    card_kind,
    render_card,
    render_event,
    render_gate,
    render_gate_snapshot,
    render_plan,
    short_hash,
    status_approvals,
)
from mde_cli.strings import LANGUAGES, normalise_language, say
from mde_contracts.brief import AssumedItem
from mde_contracts.cards import Card, CardInstance, CardOption, CardProgress
from mde_contracts.events import (
    AssistantMessage,
    BriefPage,
    GateEvent,
    Notice,
    PlanPage,
    PlanReady,
    ProgressEvent,
    SpecReady,
    StateChanged,
)
from mde_contracts.gates import ApprovalRecord, GateStatus
from mde_contracts.plan import (
    ApproverStatus,
    EnterpriseExtras,
    IntegrationStatus,
    PlanDiff,
    ResearchAdditionView,
    RoleView,
)
from mde_contracts.research import CompliancePointer, SourceRef
from mde_contracts.spec import BacklogItem
from mde_flow.models import GateSnapshot
from mde_flow.testing import make_brief, make_plan, make_spec


def instance(card_type: str = "choice3", n: int = 3, **extra: object) -> CardInstance:
    options = [
        CardOption(
            id=chr(65 + i),
            label=f"Option {i + 1}",
            recommended=(i == 1),
            description="a short description" if i == 0 else None,
            preview="theme:clean-bright" if i == 2 else None,
        )
        for i in range(n)
    ]
    card = Card(
        id="Q-test-01",
        slot="idea.archetype",
        type=card_type,  # type: ignore[arg-type]
        text="What are you building?",
        options=options,
        **extra,  # type: ignore[arg-type]
    )
    return CardInstance(
        instance_id="crd_1", card=card, progress=CardProgress(asked=2, cap=10), language="en"
    )


# --------------------------------------------------------------------------- commands


@pytest.mark.parametrize("word", ["stop", "pause", "resume", "restart", "finish", "quit", "help"])
def test_the_bare_words_are_commands(word: str) -> None:
    assert parse_command(word) == Command(word)
    assert parse_command(f"  {word.upper()}  ") == Command(word)


def test_other_commands_need_a_slash_and_arguments_are_kept() -> None:
    assert parse_command("plan") is None  # a plain word, not a command
    assert parse_command("undo") is None
    assert parse_command("/plan") == Command("plan")
    assert parse_command("/upload  my notes.md ") == Command("upload", "my notes.md")
    assert parse_command("/STATUS") == Command("status")
    assert parse_command("an online shop") is None


# --------------------------------------------------------------------------- card input


def test_a_number_taps_that_option() -> None:
    card = instance()
    assert parse_card_input(card, "2") == Tap(("B",))
    assert parse_card_input(card, " 3 ") == Tap(("C",))


def test_a_number_out_of_range_or_two_numbers_on_a_single_choice_is_a_hint() -> None:
    card = instance()
    for bad in ("0", "4", "99"):
        result = parse_card_input(card, bad)
        assert isinstance(result, Hint) and "1 to 3" in result.text
    two = parse_card_input(card, "1,2")
    assert isinstance(two, Hint) and "single number" in two.text


def test_a_multi_card_takes_several_numbers_once_each() -> None:
    card = instance("multi", 4)
    assert parse_card_input(card, "1,3") == Tap(("A", "C"))
    assert parse_card_input(card, "4 2 2") == Tap(("D", "B"))
    assert isinstance(parse_card_input(card, "1,9"), Hint)


def test_d_s_and_r() -> None:
    card = instance()
    assert parse_card_input(card, "d") == Tap(you_decide=True)
    assert parse_card_input(card, "S") == Tap(skipped=True)
    assert parse_card_input(card, "r") == Tap(("B",))  # the suggested one
    multi = instance("multi", 3)
    assert parse_card_input(multi, "r") == Tap(("B",))
    plain = CardInstance(
        instance_id="crd_2",
        card=Card(id="Q-x", slot="idea.archetype", type="choice3", text="?", options=[]),
    )
    assert parse_card_input(plain, "r") == Tap(you_decide=True)  # nothing is suggested


def test_free_text_on_a_choice_card_is_a_message_and_on_a_text_card_the_answer() -> None:
    assert parse_card_input(instance(), "something else entirely") == Say("something else entirely")
    text = instance("text", 0)
    assert parse_card_input(text, "Blue Basket") == Tap(other_text="Blue Basket")
    assert parse_card_input(text, "42") == Tap(other_text="42")  # a number is text here
    hint = parse_card_input(text, "r")
    assert isinstance(hint, Hint)  # no suggestion to take on a text card
    assert parse_card_input(text, "d") == Tap(you_decide=True)


def test_card_kind() -> None:
    assert card_kind(instance("choice3")) == "choice"
    assert card_kind(instance("visual3")) == "choice"
    assert card_kind(instance("confirm", 2)) == "choice"
    assert card_kind(instance("multi")) == "multi"
    assert card_kind(instance("upload", 0)) == "text"


# --------------------------------------------------------------------------- gate input


def test_gate_input() -> None:
    assert parse_gate_input("1") == GateInput("approve")
    assert parse_gate_input("2") == GateInput("change", None)
    assert parse_gate_input("2 make it blue") == GateInput("change", "make it blue")
    assert parse_gate_input("2\tmake it blue") == GateInput("change", "make it blue")
    assert parse_gate_input("yes please") == GateInput("say", "yes please")
    assert parse_gate_input("21") == GateInput("say", "21")


# --------------------------------------------------------------------------- strings


def test_the_hints_exist_in_every_language_and_fall_back_to_english() -> None:
    for language in LANGUAGES:
        assert say("card_hint_choice", language)
        assert "{count}" not in say("bad_number", language, count=4)
    assert "devanagari" not in say("gate_hint", "hi").lower()
    assert any(ord(ch) > 0x900 for ch in say("gate_hint", "hi"))  # real Devanagari
    assert say("gate_hint", "xx") == say("gate_hint", "en")
    assert say("gate_hint", None) == say("gate_hint", "en")
    assert say("no_such_key") == "no_such_key"
    assert normalise_language("hi-Latn") == "hi-Latn" and normalise_language("auto") == "en"


# --------------------------------------------------------------------------- rendering


def texts(lines: list) -> list[str]:  # type: ignore[type-arg]
    return [line.text for line in lines]


def test_a_card_is_numbered_with_the_suggestion_marked_and_a_hint() -> None:
    lines = texts(render_card(instance()))
    assert lines[1] == "Question 2 of 10"
    assert "  1) Option 1" in lines and "  2) Option 2  (suggested)" in lines
    assert "       a short description" in lines
    assert "       look: clean-bright" in lines
    assert lines[-1].startswith("  Type a number to choose")
    multi = texts(render_card(instance("multi")))
    assert multi[-1].startswith("  Type numbers separated by commas")
    typed = texts(render_card(instance("text", 0)))
    assert typed[-1].startswith("  Type your answer")
    assert not any(line.startswith("  1)") for line in typed)


def test_a_card_in_hinglish_has_hinglish_hints_and_a_help_line() -> None:
    card = instance(help="So I can pick sensible defaults.", kind="compliance")
    lines = texts(render_card(card, language="hi-Latn"))
    assert lines[1] == "Question 2 of 10 (compliance)"
    assert "  Why I ask: So I can pick sensible defaults." in lines
    assert lines[-1] == "  " + say("card_hint_choice", "hi-Latn")


def test_every_event_kind_has_a_line() -> None:
    base = {"event_id": "evt_1", "session_id": "ses_1", "seq": 1}
    hello = AssistantMessage(**base, text="Hi!\nWhat would you like?", chips=["A", "B"])
    assert texts(render_event(hello)) == [
        "Assistant: Hi!",
        "           What would you like?",
        "  You could say: A | B",
    ]
    assert texts(
        render_event(
            ProgressEvent(**base, phase="research", step="s", label="Looking", done=2, total=4)
        )
    ) == ["  [research 2/4] Looking"]
    assert texts(render_event(ProgressEvent(**base, phase="build", step="s", label="Writing"))) == [
        "  [build] Writing"
    ]
    assert texts(
        render_event(
            StateChanged(
                **base, from_state="DISCOVERING", to_state="PLANNING", plan_phase="research"
            )
        )
    ) == ["-- now: PLANNING (research)"]
    assert texts(
        render_event(StateChanged(**base, from_state="PLANNING", to_state="PLAN_REVIEW"))
    ) == ["-- now: PLAN_REVIEW"]
    assert texts(render_event(Notice(**base, code="x", text="careful", level="warn"))) == [
        "Warning: careful"
    ]
    assert texts(render_event(Notice(**base, code="x", text="fyi"))) == ["Note: fyi"]
    assert texts(render_event(PlanReady(**base, version=2, hash="a" * 64))) == [
        "Plan ready (version 2, hash aaaaaaaaaaaa)."
    ]
    ready = SpecReady(
        **base,
        app_id="app_1",
        version=3,
        hash="b" * 64,
        git_sha="c" * 40,
        path="APP_SPEC.md",
        repo_dir="data/p",
    )
    shown = texts(render_event(ready))
    assert "Spec ready." in shown and f"  hash:    {'b' * 64}" in shown
    assert (
        "  path:    APP_SPEC.md" in shown
        and "  version: 3" in shown
        and f"  commit:  {'c' * 40}" in shown
    )


def test_the_brief_page_has_rows_assumptions_and_two_buttons() -> None:
    brief = make_brief().model_copy(
        update={
            "assumptions": [
                AssumedItem(
                    slot_id="auth.method",
                    label="Login",
                    value_text="email",
                    reason="most apps use it",
                ),
                AssumedItem(
                    slot_id="design.direction",
                    label="Look",
                    value_text="bold",
                    reason="you said so",
                    included=False,
                ),
            ]
        }
    )
    event = GateEvent(
        event_id="evt_2", session_id="ses_1", seq=5, gate="brief", version=1, hash=brief.hash,
        page=BriefPage(brief=brief), buttons=["Looks right", "Change something"],
        diff_lines=["The look changed"], required_roles=["requester", "delivery_lead"], approved_roles=["requester"],
    )  # fmt: skip
    lines = texts(render_gate(event))
    page = "\n".join(lines)
    assert f"=== Brief, version 1 (hash {short_hash(brief.hash)}) ===" in page
    assert "In short: A fitness tracking app" in page
    assert "[x] workouts" in page or "[x] Workouts" in page
    assert "What I assumed" in page and "Login: email" in page and "(switched off)" in page
    assert "What changed since you last looked:" in page and "  - The look changed" in page
    assert "Approvals: requester [approved], delivery_lead [waiting]" in page
    assert "  1) Looks right" in lines and "  2) Change something" in lines
    assert lines[-1] == "  " + say("gate_hint", None)


def test_the_plan_page_shows_screens_research_with_sources_mocks_roles_and_approvers() -> None:
    spec = make_spec()
    plan = make_plan(spec, spec_hash="d" * 64)
    plan = plan.model_copy(
        update={
            "research_status": "ok",
            "research_note": "Research went fine.",
            "research_added": [
                ResearchAdditionView(
                    id="RA-001", title="Guest checkout", kind="pattern", why="Seen in most apps",
                    sources=[
                        SourceRef(url="https://example.com/a", title="A page", retrieved_at=date(2026, 11, 8)),
                        SourceRef(url="https://example.org/b", retrieved_at=date(2026, 11, 8)),
                    ],
                    included=True, verified=True,
                ),
                ResearchAdditionView(id="RA-002", title="Dark mode", kind="feature", why="Common", included=False),
            ],
            "compliance": [CompliancePointer(flag="gdpr", title="GDPR basics", url="https://example.eu/gdpr")],
            "integrations": [
                IntegrationStatus(id="INT-pay", name="Payments", mode="mock", needs_dev_build=True),
                IntegrationStatus(id="INT-sms", name="Sign-in codes", mode="real"),
            ],
            "roles": [RoleView(id="ROLE-customer", name="Customer", sees=["Home", "Cart"])],
            "diff_since_gate1": PlanDiff(since="gate1", lines=["2 screens added"]),
            "assumptions": [AssumedItem(slot_id="auth.method", label="Login", value_text="email", reason="r")],
            "backlog": [BacklogItem(title="Loyalty points", reason="phase 2")],
            "enterprise": EnterpriseExtras(
                approvers=[ApproverStatus(role="requester", user_id="u1", decision="approve"), ApproverStatus(role="delivery_lead")],
                comments=["Looks fine to me"],
            ),
        }
    )  # fmt: skip
    page = "\n".join(texts(render_plan(plan)))
    assert f"=== Plan, version 1 (hash {short_hash('d' * 64)}) ===" in page
    assert f"Screens: {len(spec.screens)} in total" in page and "  Main (" in page
    assert "Research: ok" in page and "Research went fine." in page
    assert "[x] Guest checkout (pattern, verified): Seen in most apps" in page
    assert (
        "source: https://example.com/a (A page)" in page and "source: https://example.org/b" in page
    )
    assert "[ ] Dark mode (feature, unverified)" in page
    assert "GDPR basics: https://example.eu/gdpr" in page
    assert "Payments: mock, needs a development build" in page and "Sign-in codes: real" in page
    assert "Customer: Home, Cart" in page
    assert "Since the Brief you approved:" in page and "- 2 screens added" in page
    assert "Kept for later:" in page and "Loyalty points (phase 2)" in page
    assert "requester (u1): approve" in page and "delivery_lead: waiting" in page
    assert "Comments:" in page and "Looks fine to me" in page
    assert "corner radius" in page


def test_a_gate_snapshot_renders_like_the_event_and_shows_the_approver_status() -> None:
    brief = make_brief()
    approval = ApprovalRecord(
        id="apr_1", session_id="ses_1", gate="brief", version=1, hash=brief.hash,
        approver_id="u1", role="requester", decision="approve",
    )  # fmt: skip
    status = GateStatus(
        gate="brief", version=1, hash=brief.hash, required_roles=["requester", "delivery_lead"], approvals=[approval]
    )  # fmt: skip
    gate = GateSnapshot(
        gate="brief", version=1, hash=brief.hash, status=status, buttons=["Send for approval", "Change something"], page=BriefPage(brief=brief)
    )  # fmt: skip
    lines = texts(render_gate_snapshot(gate, language="hi"))
    assert "Approvals: requester [approved], delivery_lead [waiting]" in lines
    assert "  1) Send for approval" in lines and lines[-1] == "  " + say("gate_hint", "hi")
    assert status_approvals(status) == "Approvals: requester [approved], delivery_lead [waiting]"
    plan_gate = GateEvent(
        event_id="evt_3", session_id="ses_1", seq=9, gate="plan", version=1, hash="e" * 64,
        page=PlanPage(plan=make_plan(make_spec(), spec_hash="e" * 64)), buttons=["Looks right"],
    )  # fmt: skip
    assert any(t.startswith("=== Plan") for t in texts(render_gate(plan_gate)))


def test_the_approvals_line_is_silent_for_a_one_person_gate() -> None:
    assert approvals_line(["requester"], []) is None
    assert approvals_line([], []) is None
    assert approvals_line(["requester"], ["requester"]) == "Approvals: requester [approved]"
