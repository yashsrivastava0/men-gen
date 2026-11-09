"""The interview screens (build prompt 02, section 2.7): what the console must contain, checked in the source
and through the served API. No browser: the elements are looked up by the ids and classes the code builds,
and the behaviour of the pure helpers is tested in ``test_interview_helpers.py``."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from mde_compass.api.settings import ApiSettings
from mde_compass.api.testing import ScriptedService
from mde_compass.app import create_app
from mde_compass.catalog.loader import load_catalog
from mde_compass.views.build import build_view_model
from mde_compass.views.testing import make_session, rich_brief

HEADERS = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
CONSOLE = Path(__file__).resolve().parents[1] / "console"
README = (CONSOLE.parent / "README.md").read_text(encoding="utf-8")
HTML = (CONSOLE / "agents" / "compass" / "compass.html").read_text(encoding="utf-8")
JS = (CONSOLE / "agents" / "compass" / "compass.js").read_text(encoding="utf-8")
HELPER = (CONSOLE / "agents" / "compass" / "interview.js").read_text(encoding="utf-8")
CSS = (CONSOLE / "agents" / "compass" / "compass.css").read_text(encoding="utf-8")
BODY = JS.split("CONTRACT*/", 1)[1]


def function_body(name: str, source: str = JS) -> str:
    """The text of ``function name(...) { ... }`` up to the next top-level ``  function`` of the same indent."""
    start = source.index(f"function {name}(")
    nxt = re.search(r"\n  (?:function |var |// ----)", source[start + 10 :])
    return source[start : start + 10 + (nxt.start() if nxt else len(source))]


def media_blocks(query: str) -> list[str]:
    """The bodies of every ``@media <query> { ... }`` block of the stylesheet (balanced braces)."""
    blocks = []
    for m in re.finditer(re.escape(f"@media {query}") + r"\s*\{", CSS):
        depth, i = 1, m.end()
        while depth and i < len(CSS):
            depth += {"{": 1, "}": -1}.get(CSS[i], 0)
            i += 1
        blocks.append(CSS[m.end() : i - 1])
    return blocks


def element(tag_id: str) -> str:
    """The opening tag of the fragment element with this id."""
    m = re.search(rf'<[a-z]+\b[^>]*\bid="{re.escape(tag_id)}"[^>]*>', HTML)
    assert m, f"#{tag_id} is not in the fragment"
    return m.group(0)


# ---- mock banner ----------------------------------------------------------------------------------------


def test_the_mock_banner_is_in_the_fragment_and_cannot_be_dismissed() -> None:
    tag = element("mock-banner")
    assert 'role="status"' in tag and "hidden" in tag, "hidden until the server says mock"
    block = re.search(r'<div id="mock-banner".*?</div>', HTML, flags=re.S)
    assert block and "<button" not in block.group(0), "no close button"
    assert (
        load_catalog().strings["en"]["ui.interview.mock_banner"]
        == "MOCK MODE: questions are canned"
    )
    assert "MOCK MODE: questions are canned" in block.group(0), (
        "readable even before the strings load"
    )
    # the only code that shows or hides it is renderMockBanner, driven by GET /budget
    assert JS.count("mock-banner") == 1 and '$("mock-banner")' in function_body("renderMockBanner")
    render = function_body("renderMockBanner")
    assert 'mode === "mock"' in render and 't("ui.interview.mock_banner")' in render
    assert "renderMockBanner();" in function_body("loadBudget")


def test_the_mock_banner_stays_visible_on_a_phone() -> None:
    rule = re.search(r"\.mock-banner\s*\{([^}]*)\}", CSS)
    assert rule, ".mock-banner is styled"
    css = rule.group(1)
    assert "position: sticky" in css and "var(--warn)" in css and "font-weight: 700" in css
    assert not any(".mock-banner" in block for block in media_blocks("(max-width: 900px)")), (
        "no phone rule hides or shrinks it"
    )
    assert ".mock-banner" not in re.sub(r"\.mock-banner\s*\{[^}]*\}", "", CSS)


async def test_the_banner_follows_what_the_budget_endpoint_says(settings: ApiSettings) -> None:
    for mode in ("mock", "live"):
        app = create_app(ScriptedService(mode=mode), settings=settings)  # pyright: ignore[reportArgumentType]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=HEADERS
        ) as c:
            r = await c.get("/v1/compass/budget")
        assert r.status_code == 200 and r.json()["mode"] == mode
        assert "cap_usd" in r.json()


# ---- cards: group header, popover, fallback badge ---------------------------------------------------------


def test_every_card_gets_a_why_popover_that_closes_on_escape() -> None:
    body = function_body("whyEl")
    assert 'role: "note"' in body and '"aria-expanded"' in body and '"aria-controls"' in body
    assert 'e.key === "Escape"' in body and "btn.focus()" in body, (
        "Escape closes it and returns the focus"
    )
    assert "card.why_we_ask" in body and "card.unlocks" in body
    assert 't("ui.interview.feeds"' in body and 't("ui.interview.why_button")' in body
    assert "innerHTML" not in body and "text:" in body
    # used by the single card and by every question of the answer-all form
    assert "whyEl(card)" in function_body("renderCard") and "whyEl(card)" in function_body(
        "formItem"
    )
    assert ".why-pop" in CSS and ".why-btn" in CSS
    assert "ui.interview.why_button" in load_catalog().strings["hi"]


def test_cards_show_their_group_title_and_the_progress_line() -> None:
    head = function_body("cardHead")
    assert "I.locate(state.batches, card)" in head and "I.progressLine(" in head
    assert "loc.groupTitle" in head and '"group-chip"' in head and "progress-line" in head
    assert "cardHead(card)" in function_body("renderCard")
    assert "I.upsertBatch(state.batches, p, 40)" in JS and "state.batch = p;" in JS, (
        "a batch that is sent again replaces its earlier copy"
    )


def test_a_fallback_card_shows_an_unobtrusive_badge_with_a_tooltip() -> None:
    body = function_body("fallbackBadge")
    assert "fallback-badge" in body and "title: tip" in body and '"aria-label"' in body
    assert 't("ui.interview.fallback_badge")' in body and 't("ui.interview.fallback_tip")' in body
    assert "card.fallback" in function_body("cardHead") and "card.fallback" in function_body(
        "formItem"
    )
    assert "item.fallback" in function_body("qaItemEl")
    strings = load_catalog().strings["en"]
    assert strings["ui.interview.fallback_badge"] == "Standard question"
    assert strings["ui.interview.fallback_tip"] == (
        "The AI planner did not produce this question; a standard one was used"
    )


# ---- answer-all form --------------------------------------------------------------------------------------


def test_every_planned_batch_is_one_form_that_is_sent_once() -> None:
    build = function_body("buildForm")
    assert "batch-form" in build and '"qgroup"' in build and '"qgroup-title"' in build
    assert "send-all" in build and "sendLabel(f)" in build and "g.title" in build
    assert "ui.v2.form_helper" in build, (
        "a line says nothing is processed before the button is pressed"
    )
    controls = function_body("formControls")
    for token in ('"radio"', '"checkbox"', '"textarea"', "I.examplePlaceholder(card, t)"):
        assert token in controls, token
    item = function_body("formItem")
    assert 't("ui.you_decide")' in item and '"you_decide"' in item and "must_confirm" in item
    # the form is the layout for every question of a batch, a batch of one included
    handler = JS.split("    card: function (p, replay) {", 1)[1].split("    playback:", 1)[0]
    assert "I.useForm(loc, pending.length)" in handler and "showForm(loc.batch)" in handler
    batch_handler = JS.split("    question_batch: function (p, replay) {", 1)[1].split(
        "    notice:", 1
    )[0]
    assert "showForm(p)" in batch_handler and "ensureFormLayout(p)" in batch_handler
    assert "if (replay) { return; }" in batch_handler, (
        "a reopened project draws its form once, at the end"
    )
    assert "uiMode" not in JS and "one_at_a_time" in function_body("ensureFormLayout"), (
        "one-at-a-time survives only as the older server's layout, asked away once"
    )
    assert ".batch-form" in CSS and ".qitem" in CSS and ".toggle" in CSS and ".form-actions" in CSS


def test_nothing_is_sent_or_collapsed_until_the_person_presses_send_and_the_server_answers() -> (
    None
):
    submit = function_body("submitForm")
    assert "if (f.sending || f.confirmed) { return; }" in submit, "double-submit guard"
    assert "setFormBusy(f, true)" in submit and "I.buildBatchPayload(" in submit
    assert "I.missingRequired(f.cards, f.draft)" in submit
    assert "window.compassSend.batchAnswer(f.batchId, payload.answers" in submit
    assert "onAck" in submit and "confirmForm(f)" in submit
    busy = function_body("setFormBusy")
    assert "f.ui.send.disabled = on" in busy and "ui.v2.sending" in busy
    assert '"aria-busy"' in busy and ".qgroup" in busy, "every input waits while the request is out"
    # a failed POST keeps the form: the state, a plain message and a Retry button, and the same event id when unchanged
    assert "f.failed = { key: key, eventId: eventId }" in submit
    assert "f.failed && f.failed.key === key ? f.failed.eventId : randomId()" in submit
    assert "f.ui.error.hidden = false" in submit and "f.ui.retry.focus()" in submit
    assert "submitForm(f, f.lastDecideRest)" in function_body("buildForm"), "the Retry button"
    # the listing of the events the server just sent comes after the form has closed
    confirm = function_body("confirmForm")
    assert "markAnswered(c.id)" in confirm and "state.submitted[f.batchId] = true" in confirm
    assert "if (!f || f.confirmed) { return; }" in confirm, "idempotent"
    assert "collapse" not in submit.lower().replace("collapses", ""), (
        "submitting does not collapse anything"
    )
    # the request layer keeps a form's own event id and never overlaps two events of a session
    post = function_body("postEvent")
    assert "o.onAck" in post and "o.onError" in post
    assert "if (o.eventId) { body.event_id = o.eventId; }" in function_body("sendBatchAnswer")
    assert "ui.v2.retry" in HELPER + JS and "ui.v2.send_failed" in function_body("sendFailureText")
    assert "ui.v2.stale_batch" in function_body("sendFailureText")


def test_the_form_says_which_question_it_is_and_which_one_is_last() -> None:
    build = function_body("buildForm")
    assert "I.questionLabel(f.nums, index, t)" in build and "ui.v2.last_question_note" in build
    assert "f.closing" in build and "closing-note" in build
    show = function_body("showForm")
    assert "I.batchNumbers(batch, cards.length)" in show and "I.isClosingBatch(batch)" in show
    assert 'f.el.scrollIntoView({ block: "start" })' in show
    assert "title.focus" in show or "f.ui.title.focus" in show, "focus follows the new questions"
    strings = load_catalog().strings["en"]
    assert strings["ui.v2.question_n_of"] == "Question {n} of about {total}"
    assert strings["ui.v2.last_question"] == "Last question"
    assert "hard_cap" not in BODY and "hard_cap" not in HELPER, (
        "the hard cap is never read, so it can never be shown"
    )


def test_a_batch_that_changes_while_the_form_is_open_keeps_what_was_typed() -> None:
    show = function_body("showForm")
    assert "I.sameQuestions(old.batch, batch)" in show, "the same batch again changes nothing"
    assert "I.carryDraft(carried, cards)" in show and "ui.v2.updated_kept" in show
    assert "if (old.sending) { confirmForm(old); }" in show, (
        "new questions after a send mean the server has the answers"
    )
    assert "if (state.submitted[batch.batch_id]) { return; }" in show, (
        "a sent batch is never drawn again"
    )
    assert "state.answered[card.id] || state.submitted[loc.batch.batch_id]" in JS, (
        "nor are its cards"
    )
    replay = function_body("replayHistory")
    assert "showForm(last, true)" in replay and "state.submitted = summary.submitted" in replay


def test_an_older_server_that_still_shows_one_at_a_time_is_asked_for_the_form_once() -> None:
    body = function_body("ensureFormLayout")
    assert 'batch.ui_mode !== "one_at_a_time"' in body and "state.layoutAsked" in body
    assert 'I.modeCommand("answer_all")' in body and "quiet: true" in body


# ---- Compass v2: several answers can be true, and a typed answer next to them --------------------------------


def test_a_pick_several_question_has_select_all_none_of_these_and_a_typed_answer() -> None:
    controls = function_body("formControls")
    assert "select-all" in controls and "select-none" in controls
    assert "I.toggleSelectAll(card, entry)" in controls and "I.toggleNone(card, entry)" in controls
    assert "I.canSelectAll(card)" in controls and "I.canNone(card)" in controls
    assert "I.togglePick(card, entry, o.id, input.checked)" in controls
    assert '"aria-pressed"' in controls, "both tools say whether they are on"
    assert "ui.v2.select_all" in controls and "ui.v2.select_none" in controls
    # the typed answer is on screen next to the options, not behind a click, and is sent with the ticks
    assert "I.showsCustom(card)" in controls and "otherRow(null)" in controls
    assert "ui.v2.custom_many" in controls
    # buttons are disabled with the rest of the question when "You decide" is on
    assert 'querySelectorAll("input, textarea, button")' in function_body("formItem")
    assert ".pick-tools" in CSS and ".form-other" in CSS


def test_a_single_choice_can_be_picked_or_typed_and_the_last_action_wins() -> None:
    controls = function_body("formControls")
    assert "entry.choice = I.OTHER" in controls and "radio.checked = true" in controls
    assert "is-idle" in controls and "syncIdle" in controls, "the unused side is shown as unused"
    assert ".form-other.is-idle" in CSS


def test_the_priority_question_ranks_the_ticks_and_keeps_the_typed_answer() -> None:
    controls = function_body("formControls")
    assert (
        'type === "priority"' in controls
        and "rank-list" in controls
        and "I.movePick(entry, id, -1)" in controls
    )
    assert (
        "I.movePick(entry, id, 1)" in controls
        and "ui.v2.rank_up" in controls
        and "ui.v2.rank_remove" in controls
    )
    assert "ui.v2.priority_hint" in controls and "focus()" in controls, (
        "the arrow keeps the keyboard where it was"
    )
    assert not re.search(r'type === "priority"[^;]*select-all', controls), (
        "no select-all on a ranking"
    )
    assert 'return effectiveType(card) === "pick_many" && optionIds(card).length >= 2;' in HELPER
    assert ".rank-row" in CSS


def test_a_form_question_without_fields_reads_like_an_open_question_everywhere() -> None:
    assert "var type = I.effectiveType(card);" in function_body("formControls")
    assert 'I.effectiveType(card) === "open_prompt"' in function_body("cardForm")
    assert 'I.effectiveType(card) === "open_prompt"' in function_body("cardConfirm")
    assert (
        'if ((type === "quick_form" || type === "confirm") && !((card.options || []).length))'
        in HELPER
    )


# ---- Compass v2: "You decide" and "Decide the rest for me" ---------------------------------------------------


def test_every_question_has_one_you_decide_switch_that_disables_its_inputs() -> None:
    item = function_body("formItem")
    assert (
        't("ui.you_decide")' in item
        and 'setAction(entry.action === "you_decide" ? "answer" : "you_decide")' in item
    )
    assert 'c.disabled = action !== "answer"' in item, "its inputs are disabled while it is on"
    assert "ui.v2.you_decide_on" in item and '"aria-pressed"' in item
    assert "card.must_confirm" in item, "a risky question cannot be left to us"
    assert "skip" not in item.lower().replace("skipped", ""), (
        "a blank question is the skip: no second switch"
    )
    assert "I.answerFor(card, e)" in HELPER or "answerFor(card, entryOf(draft, card))" in HELPER


def test_decide_the_rest_asks_in_one_line_then_sends_the_filled_in_answers_with_the_flag() -> None:
    build = function_body("buildForm")
    for token in ("decide-rest", "decide-rest-yes", "decide-confirm", "ui.v2.decide_rest_confirm"):
        assert token in build, token
    assert '"aria-expanded"' in build and 'e.key === "Escape"' in build, (
        "keyboard: Escape closes it"
    )
    assert "toggleDecideConfirm(f, true)" in build and "submitForm(f, true)" in build
    submit = function_body("submitForm")
    assert "{ decideRest: !!decideRest }" in submit and "decideRest: !!decideRest" in submit
    assert "decideRest ? [] : I.missingRequired" in submit
    assert "f.ui.decide.disabled = on" in function_body("setFormBusy")
    confirm = function_body("confirmForm")
    assert "ui.v2.form_done_rest" in confirm and "f.decideRest" in confirm
    # the page never offers the old "finish with defaults" command: decide_rest replaces it in the form
    assert "finish_with_defaults" not in JS.split("CONTRACT*/", 1)[1]
    strings = load_catalog().strings
    assert "We assumed" in strings["en"]["ui.v2.decide_rest_confirm"], (
        "the real heading of the Brief section"
    )
    assert strings["en"]["section.assumed"] == "We assumed"


def test_the_layout_toggle_is_gone_and_ask_more_lives_near_the_composer() -> None:
    bar = re.search(r'<div id="interview-bar".*?</div>', HTML, flags=re.S)
    assert bar, "the interview bar is in the fragment"
    assert 'id="btn-ask-more"' in bar.group(0)
    assert "btn-mode-toggle" not in HTML + JS and "mode-note" not in HTML + JS
    assert HTML.index('id="interview-bar"') < HTML.index('id="composer"'), "just above the composer"
    assert 'name: "ask_more"' in function_body("askMore")
    replay = function_body("replayHistory")
    assert "state.answered = answered" in replay, (
        "a reopened project shows the form only for questions still waiting"
    )
    render = function_body("renderInterviewBar")
    assert "answeredCount()" in render and "isApproved()" in render, (
        "Ask me more: answered, not approved"
    )
    assert '$("btn-ask-more")' in JS
    assert load_catalog().strings["en"]["ui.interview.ask_more"] == "Ask me more"


def test_depth_chips_send_the_budget_commands_before_the_first_batch() -> None:
    chips = function_body("depthChips")
    assert "I.BUDGETS.forEach" in chips and '"depth-chip"' in chips and '"aria-pressed"' in chips
    assert "I.budgetCommand(id)" in function_body("chooseDepth")
    assert "I.BUDGETS.forEach" in function_body("tierCards"), (
        "the welcome block offers the same three tiers"
    )
    assert "tierCards()" in function_body("renderWelcome")
    playback = JS.split("    playback: function (p) {", 1)[1].split("    question_batch:", 1)[0]
    assert "depthChips()" in playback and "!state.depth" in playback, (
        "the compact chips stay on the playback card only for an idea that came without a tier"
    )
    assert "state.progress.of_about" in function_body("depthKnown"), "the budget known so far"
    for name in ("quick", "standard", "thorough"):
        assert f'"{name}"' in HELPER


# ---- Compass v2: the tier first ----------------------------------------------------------------------------


def test_the_welcome_block_asks_for_the_tier_before_the_idea() -> None:
    cards = function_body("tierCards")
    assert 'class: "tier-card"' in cards and '"aria-pressed"' in cards and 'type: "button"' in cards
    for key in ("ui.v2.tier_title", "ui.v2.tier_help", "ui.v2.tier_quick_desc"):
        assert key in JS, key
    assert 'id: "tier-title"' in cards and 'id: "tier-help"' in cards
    assert '"aria-labelledby": "tier-title"' in cards and '"aria-describedby": "tier-help"' in cards
    labels = function_body("depthLabels")
    assert "ui.v2.tier_standard_desc" in labels and "ui.v2.tier_thorough_desc" in labels
    # the choice becomes a chip with a Change link, until the first batch arrives
    welcome = function_body("renderWelcome")
    assert 'class: "tier-chip"' in welcome and "ui.v2.tier_chosen" in welcome
    assert "if (!state.batches.length)" in welcome and "tier-change" in welcome
    assert "ui.v2.tier_change_label" in welcome
    assert (
        "renderWelcome();"
        in JS.split("    question_batch: function (p, replay) {", 1)[1].split("    notice:", 1)[0]
    ), "the Change link goes away when the first batch arrives"
    # the idea buttons wait for the tier too
    assert "disabled: locked" in function_body("startButtons")
    assert ".tier-card" in CSS and ".tier-chip" in CSS and "max-width: 520px" in CSS


def test_the_idea_box_stays_disabled_until_a_tier_is_chosen_and_says_why() -> None:
    gate = function_body("applyComposerGate")
    for token in (
        "composer-input",
        "composer-send",
        "composer-mic",
        "composer-gate",
        "tierLocked()",
    ):
        assert token in gate, token
    assert (
        ".disabled = locked" in gate and 'setAttribute("aria-describedby", "composer-gate")' in gate
    )
    assert "ui.v2.tier_needed" in gate and "ui.v2.composer_locked" in gate
    tag = element("composer-gate")
    assert 'role="status"' in tag and "hidden" in tag
    assert HTML.index('id="composer-gate"') < HTML.index('id="composer"'), (
        "the reason sits above the idea box"
    )
    assert "I.tierRequired(" in function_body("tierLocked")
    submit = JS.split('$("composer").addEventListener("submit"', 1)[1].split(
        "input.addEventListener", 1
    )[0]
    assert "tierLocked()" in submit, "a stray Enter cannot send an idea without a tier"
    assert "applyComposerGate();" in function_body("applyChromeStrings"), (
        "and the copy follows the language"
    )
    assert "applyComposerGate();" in function_body("chooseDepth")


def test_choosing_a_tier_sends_the_budget_command_and_rolls_back_when_it_fails() -> None:
    body = function_body("chooseDepth")
    assert 'type: "command"' in body and "I.budgetCommand(id)" in body and "quiet: true" in body
    assert "state.depth = prev" in body, "a failed save puts the page back"
    assert '$("composer-input").focus()' in body, "the next thing to do is typing the idea"
    # commands queue behind each other, so the tier always reaches the server before the idea
    send = function_body("sendEvent")
    assert "sendQueue" in send and "state.awaitingTier = false" in send


def test_the_server_welcome_card_is_replaced_by_the_welcome_block() -> None:
    assert 'var WELCOME_FRAME = "welcome";' in JS
    handler = JS.split("    card: function (p, replay) {", 1)[1].split("var loc", 1)[0]
    assert "WELCOME_FRAME" in handler and "return;" in handler
    replay = function_body("replayHistory")
    assert "WELCOME_FRAME" in replay and "I.replaySummary(items)" in replay
    assert "state.depth = summary.tier" in replay, (
        "a reopened project shows the tier it already chose"
    )
    assert "showWelcome()" in function_body("openSession")


def test_the_new_tier_numbers_are_in_both_languages() -> None:
    en = load_catalog().strings["en"]
    hi = load_catalog().strings["hi"]
    assert en["ui.interview.depth_quick_hint"] == "About 7 questions"
    assert en["ui.interview.depth_standard_hint"] == "About 12 questions"
    assert en["ui.interview.depth_thorough_hint"] == "25 or more questions"
    for key, number in (("quick", "7"), ("standard", "12"), ("thorough", "25")):
        assert number in hi[f"ui.interview.depth_{key}_hint"], key
    for stale in ("about 5", "about 25 questions"):
        assert stale not in "\n".join(en.values()).lower()


# ---- Brief ------------------------------------------------------------------------------------------------


def test_changed_rows_pulse_once_and_scroll_into_view_on_wide_screens() -> None:
    body = function_body("highlightChanged")
    assert (
        "I.changedRows(vm)" in body
        and "I.pulseKey(vm, c.row)" in body
        and 'classList.add("pulse")' in body
    )
    assert "scrollIntoView" in body and "max-width: 900px" in body, "wide screens only"
    assert "prefers-reduced-motion: reduce" in body, "the scroll is not smooth for reduced motion"
    assert "highlightChanged(host, vm)" in function_body("renderBrief")
    assert "@keyframes row-pulse" in CSS and ".brow.pulse" in CSS
    assert re.search(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\.brow\.pulse\s*\{\s*animation: none",
        CSS,
    )
    assert "scheduleView();" in function_body("processAck"), (
        "the view is refreshed after every turn"
    )


def test_the_details_section_comes_from_the_server_view_model() -> None:
    vm = build_view_model(
        rich_brief(),
        catalog=load_catalog(),
        language="en",
        session=make_session("BRIEF_REVIEW"),  # pyright: ignore[reportArgumentType]
        blockers=[],
    )
    section = next(s for s in vm.sections if s.id == "details")
    assert section.title == "Details we captured" and section.rows
    assert all(r.group and not r.editable for r in section.rows), "grouped by topic, read-only"
    # the generic renderer: server order and titles, group headings, an Edit button only when editable
    assert (
        "vm.sections.forEach(function (s) { host.appendChild(sectionEl(s)); });"
        in function_body("renderBrief")
    )
    assert "text: section.title" in function_body("sectionEl")
    assert 'class: "group-title"' in function_body("sectionEl") and "row.group" in function_body(
        "sectionEl"
    )
    assert "if (row.editable) {" in function_body("rowEl")
    assert "integrations_data" not in JS and "cannot_build" not in JS, "no hard-coded section list"
    assert "repeats" in function_body("rowEl"), (
        "a row whose label is its group heading does not repeat it"
    )


# ---- Questions and answers ---------------------------------------------------------------------------------


def test_the_history_tab_has_the_questions_and_answers_list_and_an_inline_editor() -> None:
    for tag in ("qa-section", "qa-title", "qa-list", "history-chat"):
        assert f'id="{tag}"' in HTML, tag
    assert (
        HTML.index('id="tab-history"')
        < HTML.index('id="qa-list"')
        < HTML.index('id="history-chat"')
    )
    item = function_body("qaItemEl")
    for token in ('"qa-item', "qa-followup", '"qa-status', "item.facts", "I.qaFactLine(f, t)", "I.qaAnswerText(item, t)",
                  "I.qaCanEdit(item, isApproved())", 't("x.edit")', "followup"):  # fmt: skip
        assert token in item, token
    editor = function_body("qaEditorEl")
    assert "qa-editor" in editor and "I.qaEditPayload(item, m)" in editor
    assert "window.compassSend.answerEdit(item.card_id, payload.value, payload.custom)" in editor
    assert 't("ui.interview.qa_saved")' in editor
    for kind in ('"single"', '"multi"', '"fields"'):
        assert kind in HELPER, kind
    assert 'api("GET", "/sessions/" + sid + "/qa")' in function_body("loadQa")
    assert "scheduleQa();" in function_body("processAck"), "refreshed after each turn"
    assert "loadQa();" in function_body("setTab"), "and when the tab opens"
    assert load_catalog().strings["en"]["ui.interview.qa_saved"] == (
        "Updated: your Brief and the next questions will follow"
    )
    assert ".qa-item" in CSS and ".qa-depth-1" in CSS and ".qa-status" in CSS


async def test_the_qa_endpoint_the_list_reads_exists_on_the_served_app(
    client: httpx.AsyncClient,
) -> None:
    sid = (await client.post("/v1/compass/sessions", json={})).json()["id"]
    r = await client.get(f"/v1/compass/sessions/{sid}/qa")
    assert r.status_code == 200 and r.json() == {"items": []}


async def test_the_interview_strings_are_served_in_both_languages(anon: httpx.AsyncClient) -> None:
    for lang in ("en", "hi"):
        strings = (await anon.get(f"/v1/compass/strings/{lang}")).json()["strings"]
        interview = [k for k in strings if k.startswith("ui.interview.")]
        assert len(interview) >= 40, lang
        assert "ui.interview.mock_banner" in interview and "ui.interview.why_button" in interview


def test_the_readme_documents_the_interview_screens() -> None:
    for token in (
        "interview.js",
        "compassInterview",
        "mock-banner",
        "interview-bar",
        "btn-mode-toggle",
        "btn-ask-more",
        "qa-list",
        "history-chat",
        "why-pop",
        "batch-form",
        "depth-chip",
        "fallback-badge",
        "row-pulse",
    ):
        assert token in README, f"apps/web/README.md does not mention {token}"
