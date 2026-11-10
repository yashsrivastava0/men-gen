"""The size of the app and its structure in the console (ADR 0017, S7), checked in the source and through the served
API. No browser: elements are looked up by the ids and classes the code builds. The strings of both languages are
covered by ``test_ui_contract.py`` (every ``ui.v2`` string exists in en, hi and the embedded English, and is used)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from mde_compass.catalog.loader import load_catalog
from mde_compass.catalog.size_bands import load_size_bands
from mde_compass.views.models import StructureViewModel

CONSOLE = Path(__file__).resolve().parents[1] / "console"
HTML = (CONSOLE / "agents" / "compass" / "compass.html").read_text(encoding="utf-8")
JS = (CONSOLE / "agents" / "compass" / "compass.js").read_text(encoding="utf-8")
CSS = (CONSOLE / "agents" / "compass" / "compass.css").read_text(encoding="utf-8")
BODY = JS.split("CONTRACT*/", 1)[1]
P = "/v1/compass"


def function_body(name: str) -> str:
    start = BODY.index(f"function {name}(")
    nxt = re.search(r"\n  (?:function |var |// ----)", BODY[start + 10 :])
    return BODY[start : start + 10 + (nxt.start() if nxt else len(BODY))]


def css_rule(selector: str) -> str:
    m = re.search(rf"(?:^|\n){re.escape(selector)}\s*\{{([^}}]*)\}}", CSS)
    assert m, f"{selector} is styled"
    return m.group(1)


# ---- the tab ------------------------------------------------------------------------------------------------


def test_the_fragment_has_a_hidden_structure_panel_next_to_the_brief() -> None:
    tag = re.search(r'<div id="tab-structure"[^>]*>', HTML)
    assert tag and 'role="tabpanel"' in tag.group(0) and "hidden" in tag.group(0)
    assert (
        HTML.index('id="tab-brief"')
        < HTML.index('id="tab-structure"')
        < HTML.index('id="tab-lookup"')
    )


def test_the_structure_tab_exists_only_when_the_brief_has_a_structure_section() -> None:
    assert '["structure", "ui.v2.structure_tab"]' in BODY
    assert 'id !== "structure" || hasStructureSection()' in function_body("tabVisible")
    assert 'sections.some(function (s) { return s.id === "structure"; })' in function_body(
        "hasStructureSection"
    )
    tabs = function_body("renderTabs")
    assert "tabVisible(tb[0])" in tabs and "hidden = state.tab !== tb[0] || !tabVisible" in tabs
    # the tab appears (or goes) when the view changes, and the structure follows every view
    load = function_body("loadView")
    assert "state.structTab" in load and "renderTabs()" in load and "scheduleStructure()" in load


def test_the_structure_page_is_fetched_in_the_pages_language_and_drawn_only_when_it_changed() -> (
    None
):
    load = function_body("loadStructure")
    assert '"/structure?lang=" + encodeURIComponent(state.lang)' in load
    assert "json !== state.structureJson" in load and "renderStructure()" in load
    assert "sid !== state.sessionId" in load, "a reply for another project is dropped"
    assert 'if (name === "structure") { renderStructure(); loadStructure(); }' in BODY


def test_an_empty_or_undrafted_structure_says_so_instead_of_showing_an_empty_page() -> None:
    render = function_body("renderStructure")
    assert "!vm.drafted" in render and 't("ui.v2.structure_empty")' in render


# ---- what is shown -------------------------------------------------------------------------------------------


def test_the_banner_shows_the_size_the_counts_the_verdict_and_what_is_left_to_accept() -> None:
    banner = function_body("structureBanner")
    for needle in (
        "sizeText(b.label, b.screens, b.open_upper)",
        't("ui.v2.structure_counts"',
        'vm.verdict === "over"',
        'vm.verdict === "under"',
        "vm.unreviewed > 0",
        "vm.review_blocks",
    ):
        assert needle in banner, needle


def test_the_numbers_of_each_size_come_from_the_server_never_from_the_page() -> None:
    assert 'fetch(BASE + "/size-bands")' in function_body("loadBands")
    assert "loadBands()" in function_body("boot")
    assert "tierSize(b.id)" in function_body("tierCards")
    assert "bandOfTier(tierId)" in function_body("tierSize")
    for pattern in (r"\b8\s*(?:to|-)\s*15\b", r"\b20\s*(?:to|-)\s*30\b", r"\b40\s*(?:to|-)\s*60\b"):
        assert not re.search(pattern, BODY), f"the page hardcodes a screen range: {pattern}"
    strings = load_catalog().strings["en"]
    assert "{min}" in strings["ui.v2.size_range"] and "{max}" in strings["ui.v2.size_range"]
    assert not re.search(r"\d", strings["ui.v2.size_range"].replace("{", "").replace("}", ""))


def test_a_module_card_collapses_its_screens_and_every_screen_is_written_with_text_only() -> None:
    card = function_body("moduleCard")
    assert "state.ui.open[m.id]" in card and '"aria-expanded"' in card
    assert "screenLine(s)" in card and "m.screens.forEach" in card
    for fn in ("moduleCard", "screenLine", "flowItem", "entityItem", "matrixEl", "renderStructure"):
        body = function_body(fn)
        assert "innerHTML" not in body and "insertAdjacentHTML" not in body, fn
    assert "state.ui.open" in function_body("freshUi") or "open: {}" in function_body("freshUi")


def test_the_role_by_area_matrix_scrolls_inside_its_own_box() -> None:
    matrix = function_body("matrixEl")
    assert 'tabindex: "0"' in matrix and 'role: "region"' in matrix
    assert 'scope: "col"' in matrix and 'scope: "row"' in matrix
    rule = css_rule(".smatrix")
    assert "overflow-x: auto" in rule and "max-width: 100%" in rule


def test_nothing_in_the_structure_page_can_push_the_page_sideways_on_a_phone() -> None:
    for selector in (".smod", ".sbanner"):
        assert "min-width: 0" in css_rule(selector), selector
    assert "overflow-wrap: anywhere" in css_rule(".smod-name")
    assert "flex-wrap: wrap" in css_rule(".smod-head")
    assert "flex: 1 1 200px" in css_rule(".sinput") and "min-width: 0" in css_rule(".sinput")


# ---- what the person can do ----------------------------------------------------------------------------------


def test_accept_redraft_remove_and_plan_as_send_one_structure_action_each() -> None:
    card = function_body("moduleCard")
    assert 'structAct("accept", { section: "modules", target: m.id })' in card
    assert 'structAct("regenerate", { section: "modules", target: m.id, text: v })' in card
    assert 'removeControl("modules", m.id, m.name)' in card
    assert 'structAct("set_band", { band: to })' in function_body("structureBanner")
    assert "sendStructureAction(action, fields)" in function_body("structAct")
    send = function_body("sendStructureAction")
    assert 'type: "structure_action"' in send and '["section", "target", "text", "band"]' in send


def test_removing_a_record_and_planning_for_another_size_ask_first() -> None:
    remove = function_body("removeControl")
    assert (
        "state.ui.confirm === id" in remove
        and 't("ui.v2.remove_confirm", { name: name })' in remove
    )
    assert 'structAct("remove", { section: section, target: id })' in remove
    banner = function_body("structureBanner")
    assert 'state.ui.confirm = "band:" + id' in banner and 't("ui.v2.plan_as_confirm")' in banner
    for fn in (
        "removeControl",
        "structureBanner",
    ):  # the question is a live alert for a screen reader
        assert 'role: "alert"' in function_body(fn)


def test_a_rename_is_a_plain_brief_edit_of_the_name_path() -> None:
    rename = function_body("renameItem")
    assert 'type: "brief_edit"' in rename and 'item_id: section + "." + id + ".name"' in rename
    assert 'renameControl("modules", m.id, m.name)' in function_body("moduleCard")
    assert 'renameControl("screens", s.id, s.name)' in function_body("screenLine")


def test_screens_flows_and_data_can_be_removed_with_their_dependents() -> None:
    assert 'removeControl("screens", s.id, s.name)' in function_body("screenLine")
    assert 'removeControl("flows", f.id, f.name)' in function_body("flowItem")
    assert 'removeControl("entities", e.id, e.name)' in function_body("entityItem")


def test_the_brief_panels_module_rows_can_be_accepted_and_open_the_structure_tab() -> None:
    row = function_body("rowEl")
    assert 'row.id.indexOf("modules.") === 0' in row
    assert 'sendStructureAction("accept", { section: "modules", target: mid })' in row
    assert 'setTab("structure")' in row and "state.ui.open[mid] = true" in row
    assert 'b.kind === "assumed"' in row, (
        "Accept is offered only while the module is still proposed"
    )


def test_a_notice_can_offer_one_tap_that_sends_its_command_once() -> None:
    notice = re.search(r"notice: function \(p\) \{(.*?)\n    \},", BODY, flags=re.S)
    assert notice
    body = notice.group(1)
    assert "p.command && p.command_label" in body and "text: p.command_label" in body
    assert 'sendEvent({ type: "command", name: p.command })' in body
    assert "btn.disabled = true" in body


def test_the_review_assumed_link_scrolls_to_a_section_that_exists() -> None:
    assert ".bsec[" not in BODY, "the old selector matched nothing"
    assert 'document.getElementById("sec-assumed")' in BODY
    assert 'id: "sec-" + section.id' in function_body("sectionEl")


def test_approve_all_and_the_export_buttons_are_still_there() -> None:
    assert "executeApproveAll" in BODY and "exportButtons(" in BODY
    assert '"/export"' in function_body("fetchFullOutput")


# ---- the served API the page reads ---------------------------------------------------------------------------


async def test_the_size_bands_the_tier_cards_read_are_served_without_a_token(
    anon: httpx.AsyncClient,
) -> None:
    r = await anon.get(f"{P}/size-bands")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"bands", "tier_to_band"}
    assert set(body["tier_to_band"]) == {"quick", "standard", "thorough"}
    ids = {b["id"] for b in body["bands"]}
    assert set(body["tier_to_band"].values()) == ids == set(load_size_bands().bands)
    for b in body["bands"]:
        assert len(b["screens"]) == 2 and isinstance(b["open_upper"], bool)
        assert load_catalog().strings["en"][f"size.{b['id']}"]  # a label for the page to show


async def test_the_structure_page_the_tab_reads_validates_against_the_view_model(
    client: httpx.AsyncClient,
) -> None:
    sid = (await client.post(f"{P}/sessions", json={})).json()["id"]
    r = await client.get(f"{P}/sessions/{sid}/structure?lang=hi")
    assert r.status_code == 200
    vm = StructureViewModel.model_validate(r.json())
    assert vm.drafted and vm.language == "hi" and vm.modules and vm.matrix.rows
    unknown: Any = await client.get(f"{P}/sessions/{sid}/structure?lang=zz")
    assert unknown.status_code == 422


def test_the_session_label_follows_the_brief_status_not_the_raw_session_state() -> None:
    label = function_body("renderSessionLabel")
    assert "vm.header.status_label" in label and "vm.header.title" in label
    assert label.index("s.user_label") < label.index("vm.header.title"), "the project's own name comes first"
    assert "renderSessionLabel()" in function_body("loadView"), (
        "the label is redrawn with every Brief view"
    )
    assert "renderSessionLabel()" in function_body("refreshSession")
