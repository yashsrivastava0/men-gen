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
    assert "state.batches.push(p)" in JS and "state.batch = p;" in JS
    assert 'p.ui_mode === "answer_all"' in JS, "the layout follows the batch, as the server sets it"


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


def test_the_answer_all_form_is_built_from_the_batch_and_sent_with_one_button() -> None:
    form = function_body("renderBatchForm")
    assert "batch-form" in form and '"qgroup"' in form and '"qgroup-title"' in form
    assert "send-all" in form and 't("ui.interview.form_send")' in form
    assert "I.buildAnswers(cards, draft)" in form and "I.missingRequired(cards, draft)" in form
    assert "window.compassSend.batchAnswer(batch.batch_id, answers)" in form
    controls = function_body("formControls")
    for token in ('"radio"', '"checkbox"', '"textarea"', "I.examplePlaceholder(card, t)"):
        assert token in controls, token
    item = function_body("formItem")
    assert 't("ui.you_decide")' in item and 't("ui.skip")' in item, "per-question toggles"
    assert '"you_decide"' in item and '"skip"' in item and "must_confirm" in item
    assert "g.title" in form
    # the screen decides between the single card and the form in the card handler
    handler = JS.split("    card: function (p) {", 1)[1].split("    playback:", 1)[0]
    assert "I.useForm(state.uiMode" in handler and "renderBatchForm(" in handler
    assert ".batch-form" in CSS and ".qitem" in CSS and ".toggle" in CSS


def test_the_layout_toggle_and_ask_more_live_near_the_composer() -> None:
    bar = re.search(r'<div id="interview-bar".*?</div>', HTML, flags=re.S)
    assert bar, "the interview bar is in the fragment"
    assert 'id="btn-mode-toggle"' in bar.group(0) and 'id="btn-ask-more"' in bar.group(0)
    assert HTML.index('id="interview-bar"') < HTML.index('id="composer"'), "just above the composer"
    toggle = function_body("toggleMode")
    assert "I.modeCommand(next)" in toggle and 'type: "command"' in toggle
    assert 'name: "ask_more"' in function_body("askMore")
    render = function_body("renderInterviewBar")
    assert (
        't("ui.interview.mode_answer_all")' in render
        and 't("ui.interview.mode_one_at_a_time")' in render
    )
    assert "answeredCount()" in render and "isApproved()" in render, (
        "Ask me more: answered, not approved"
    )
    assert '$("btn-mode-toggle")' in JS and '$("btn-ask-more")' in JS
    strings = load_catalog().strings["en"]
    assert strings["ui.interview.mode_answer_all"] == "Answer all at once"
    assert strings["ui.interview.mode_one_at_a_time"] == "Show one at a time"
    assert strings["ui.interview.ask_more"] == "Ask me more"


def test_depth_chips_send_the_budget_commands_before_the_first_batch() -> None:
    chips = function_body("depthChips")
    assert "I.BUDGETS.forEach" in chips and '"depth-chip"' in chips and '"aria-pressed"' in chips
    assert "I.budgetCommand(id)" in function_body("chooseDepth")
    assert "depthChips()" in function_body("startButtons"), "in the welcome block"
    assert (
        "depthChips()"
        in JS.split("    playback: function (p) {", 1)[1].split("    question_batch:", 1)[0]
    ), "and on the playback card"
    assert "state.progress.of_about" in function_body("depthKnown"), "the budget known so far"
    for name in ("quick", "standard", "thorough"):
        assert f'"{name}"' in HELPER


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
