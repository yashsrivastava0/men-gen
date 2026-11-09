"""The pure helpers of the interview screens (``console/agents/compass/interview.js``), run under Node.

The file is DOM-free by design: grouping a batch, the progress line, the answer-all payload, the Q&A editor
model and the Brief highlight keys are plain functions on plain data. Each test hands a small JavaScript
program to ``node -e`` and compares its JSON output; the tests are skipped when Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, get_args

import pytest

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.events import CommandName

CONSOLE = Path(__file__).resolve().parents[1] / "console"
HELPER = CONSOLE / "agents" / "compass" / "interview.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

PRELUDE = """
global.window = {};
require(process.argv[1]);
const I = window.compassInterview;
const S = JSON.parse(process.argv[2]);
const t = (key, vars) => (S[key] === undefined ? "?" + key : S[key]).replace(/\\{(\\w+)\\}/g, (m, k) => (vars && vars[k] !== undefined ? String(vars[k]) : m));
const card = (id, type, extra) => Object.assign({ id: id, type: type, question: "Q " + id, options: [], must_confirm: false }, extra || {});
const opts = (...ids) => ids.map((id) => ({ id: id, label: "Label " + id }));
const batch = (groups, extra) => Object.assign({ batch_id: "B1", mode: "initial", ui_mode: "one_at_a_time", groups: groups, total: 0 }, extra || {});
"""


def run_js(body: str) -> Any:
    """Run ``body`` (a JS function body that returns a value) after the prelude; return its JSON."""
    strings = load_catalog().strings["en"]
    program = PRELUDE + f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));"
    out = subprocess.run(
        [NODE or "node", "-e", program, str(HELPER), json.dumps(strings)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


GROUPED = """
const b = batch([
  { id: "g1", title: "How it works", cards: [card("A", "choose_one"), card("B", "choose_one")] },
  { id: "g2", title: "Who orders", cards: [card("C", "open_prompt"), card("D", "pick_many"), card("E", "open_prompt")] },
  { id: "g3", title: "Look and feel", cards: [card("F", "choose_one"), card("G", "open_prompt")] }
], { total: 7 });
"""


# ---- grouping, progress ---------------------------------------------------------------------------------


def test_locate_finds_the_group_and_position_of_a_card() -> None:
    got = run_js(
        GROUPED
        + """
        const loc = I.locate([b], { id: "D" });
        return { g: loc.groupIndex, of: loc.groupCount, title: loc.groupTitle, id: loc.groupId,
                 pos: loc.position, total: loc.total, followup: loc.followup, same: loc.batch === b,
                 missing: I.locate([b], { id: "ZZ" }), none: I.locate([], { id: "A" }) };
        """
    )
    assert got == {
        "g": 2, "of": 3, "title": "Who orders", "id": "g2", "pos": 4, "total": 7,
        "followup": False, "same": True, "missing": None, "none": None,
    }  # fmt: skip


def test_a_follow_up_borrows_the_group_of_the_question_it_refines() -> None:
    got = run_js(
        GROUPED
        + """
        const f1 = { id: "F1", parent_card_id: "C", origin: "followup" };
        const f2 = { id: "F2", parent_card_id: "F1", origin: "followup" };   // not in any batch either
        const direct = I.locate([b], f1);
        const loop = I.locate([b], { id: "L1", parent_card_id: "L2" });      // a parent that is unknown
        return { title: direct.groupTitle, g: direct.groupIndex, followup: direct.followup, loop: loop };
        """
    )
    assert got == {"title": "Who orders", "g": 2, "followup": True, "loop": None}


def test_the_newest_batch_wins_when_a_card_id_repeats() -> None:
    got = run_js(
        """
        const old = batch([{ id: "g1", title: "Old", cards: [card("X", "choose_one")] }], { batch_id: "B1" });
        const fresh = batch([{ id: "n1", title: "New one" , cards: [card("Y", "choose_one")] },
                             { id: "n2", title: "New two", cards: [card("X", "choose_one")] }], { batch_id: "B2" });
        const loc = I.locate([old, fresh], { id: "X" });
        return [loc.batch.batch_id, loc.groupTitle, loc.groupIndex, loc.groupCount];
        """
    )
    assert got == ["B2", "New two", 2, 2]


def test_the_progress_line_reads_group_and_question_of_about() -> None:
    got = run_js(
        GROUPED
        + """
        const loc = I.locate([b], { id: "D" });
        const f = I.locate([b], { id: "F1", parent_card_id: "D" });
        return {
          line: I.progressLine(loc, { n: 4, of_about: 9 }, t),
          followup: I.progressLine(f, { n: 8, of_about: 9 }, t),
          groupOnly: I.progressLine(loc, null, t),
          questionOnly: I.progressLine(null, { n: 2, of_about: 5 }, t),
          nothing: I.progressLine(null, null, t),
          zero: I.progressLine(null, { n: 0, of_about: 0 }, t),
        };
        """
    )
    assert got == {
        "line": "Group 2 of 3 · question 4 of about 9",
        "followup": "Follow-up · question 8 of about 9",
        "groupOnly": "Group 2 of 3",
        "questionOnly": "Question 2 of about 5",
        "nothing": "",
        "zero": "",
    }


def test_example_placeholder_uses_the_planner_examples() -> None:
    got = run_js(
        """
        return [
          I.examplePlaceholder({ examples: ["Mon to Fri", "weekends only", "third"] }, t),
          I.examplePlaceholder({ examples: ["  ", ""] }, t),
          I.examplePlaceholder({}, t),
          I.examplePlaceholder(null, t),
        ];
        """
    )
    assert got == ["For example: Mon to Fri; weekends only", "", "", ""]


# ---- which cards the form still shows -------------------------------------------------------------------


def test_pending_groups_drop_answered_questions_and_empty_groups() -> None:
    got = run_js(
        GROUPED
        + """
        const groups = I.pendingGroups(b, { A: true, B: true, E: true });
        const limited = I.pendingGroups(b, {}, 3);
        return { titles: groups.map((g) => g.title), ids: I.flatCards(groups).map((c) => c.id),
                 limited: I.flatCards(limited).map((c) => c.id), all: I.flatCards(I.pendingGroups(b, {})).length,
                 none: I.pendingGroups(null, {}) };
        """
    )
    assert got == {
        "titles": ["Who orders", "Look and feel"],
        "ids": ["C", "D", "F", "G"],
        "limited": ["A", "B", "C"],
        "all": 7,
        "none": [],
    }


def test_the_form_is_the_layout_for_every_planned_question_even_a_batch_of_one() -> None:
    got = run_js(
        GROUPED
        + """
        const loc = I.locate([b], { id: "C" });
        const fu = I.locate([b], { id: "F1", parent_card_id: "C" });
        return [I.useForm(loc, 3), I.useForm(loc, 1), I.useForm(loc, 0), I.useForm(null, 5), I.useForm(fu, 5)];
        """
    )
    # a single question of a batch still gets the form; nothing waiting, no batch or a follow-up card does not
    assert got == [True, True, False, False, False]


# ---- the answer-all payload -----------------------------------------------------------------------------

ANSWER = """
const entry = (patch) => Object.assign(I.emptyEntry(), patch || {});
const one = (c, e) => I.buildAnswers([c], { [c.id]: e })[0];
"""


def test_choice_questions_become_an_option_id_or_a_custom_text() -> None:
    got = run_js(
        ANSWER
        + """
        const c = card("A", "choose_one", { options: opts("x", "y") });
        const l = card("B", "look_signals", { options: opts("x", "y") });
        return [
          one(c, entry({ choice: "y" })),
          one(c, entry({ choice: I.OTHER, other: "  Something odd  " })),
          one(c, entry({ choice: I.OTHER, other: "   " })),    // "Something else" ticked, nothing typed: blank
          one(l, entry({ choice: "x" })),
        ];
        """
    )
    assert got == [
        {"card_id": "A", "action": "answer", "value": "y", "custom_text": None},
        {"card_id": "A", "action": "answer", "value": None, "custom_text": "Something odd"},
        {"card_id": "A", "action": "skip", "value": None, "custom_text": None},
        {"card_id": "B", "action": "answer", "value": "x", "custom_text": None},
    ]


def test_many_and_priority_questions_keep_their_picked_ids() -> None:
    got = run_js(
        ANSWER
        + """
        const m = card("M", "pick_many", { options: opts("a", "b", "c") });
        const p = card("P", "priority", { options: opts("a", "b", "c") });
        return [
          one(m, entry({ picks: ["a", "c"] })),
          one(m, entry({ picks: ["b"], other: "and cake" })),
          one(m, entry({ other: "only this" })),                // nothing ticked, a typed answer: custom text
          one(p, entry({ picks: ["c", "a"] })),                 // priority keeps the order they were ticked in
          one(m, entry()),
        ];
        """
    )
    assert got == [
        {"card_id": "M", "action": "answer", "value": ["a", "c"], "custom_text": None},
        {"card_id": "M", "action": "answer", "value": ["b"], "custom_text": "and cake"},
        {"card_id": "M", "action": "answer", "value": None, "custom_text": "only this"},
        {"card_id": "P", "action": "answer", "value": ["c", "a"], "custom_text": None},
        {"card_id": "M", "action": "skip", "value": None, "custom_text": None},
    ]


def test_text_form_and_confirm_questions() -> None:
    got = run_js(
        ANSWER
        + """
        const o = card("O", "open_prompt");
        const u = card("U", "upload_or_link");
        const f = card("F", "quick_form", { options: opts("n", "m") });
        const k = card("K", "confirm", { options: opts("n", "m") });
        return [
          one(o, entry({ text: "  Lunch for offices  " })),
          one(u, entry({ text: "https://example.com/menu" })),
          one(f, entry({ fields: { n: "Asha", m: "  " } })),     // blank fields are dropped
          one(f, entry({ fields: { n: " ", m: "" } })),            // nothing typed: blank
          one(k, entry()),                                        // a confirm card is not answered by default
          one(k, entry({ confirmed: true })),                     // "Looks right" keeps the suggestions
          one(k, entry({ touched: true, fields: { n: "Edited" } })),
        ];
        """
    )
    assert got == [
        {"card_id": "O", "action": "answer", "value": "Lunch for offices", "custom_text": None},
        {"card_id": "U", "action": "answer", "value": None, "custom_text": "https://example.com/menu"},
        {"card_id": "F", "action": "answer", "value": {"n": "Asha"}, "custom_text": None},
        {"card_id": "F", "action": "skip", "value": None, "custom_text": None},
        {"card_id": "K", "action": "skip", "value": None, "custom_text": None},
        {"card_id": "K", "action": "answer", "value": {"n": "Label n", "m": "Label m"}, "custom_text": None},
        {"card_id": "K", "action": "answer", "value": {"n": "Edited", "m": "Label m"}, "custom_text": None},
    ]  # fmt: skip


def test_skip_and_you_decide_win_over_typed_text_and_blanks_become_skips() -> None:
    got = run_js(
        ANSWER
        + """
        const a = card("A", "open_prompt");
        const b = card("B", "open_prompt");
        const c = card("C", "open_prompt");
        const cards = [a, b, c];
        const draft = { A: entry({ action: "you_decide", text: "ignored" }), B: entry({ action: "skip" }), C: entry() };
        return { answers: I.buildAnswers(cards, draft), handled: I.handledCount(cards, draft),
                 blank: I.blankCards(cards, draft).map((x) => x.id) };
        """
    )
    assert got["answers"] == [
        {"card_id": "A", "action": "you_decide", "value": None, "custom_text": None},
        {"card_id": "B", "action": "skip", "value": None, "custom_text": None},
        {"card_id": "C", "action": "skip", "value": None, "custom_text": None},
    ]
    assert got["handled"] == 2 and got["blank"] == ["C"]


def test_a_question_that_must_be_confirmed_is_never_skipped() -> None:
    got = run_js(
        ANSWER
        + """
        const r = card("R", "open_prompt", { must_confirm: true });
        const ok = card("S", "open_prompt");
        const draftBlank = { R: entry({ action: "skip" }), S: entry({ text: "fine" }) };
        const draftTyped = { R: entry({ text: "confirmed" }), S: entry({ text: "fine" }) };
        return {
          missing: I.missingRequired([r, ok], draftBlank).map((c) => c.id),
          none: I.missingRequired([r, ok], draftTyped).map((c) => c.id),
          blankCount: I.blankCards([r, ok], draftBlank).length,
          sent: I.buildAnswers([r, ok], draftTyped).map((a) => a.card_id + ":" + a.action),
          omitted: I.buildAnswers([r, ok], draftBlank).map((a) => a.card_id + ":" + a.action),
        };
        """
    )
    assert got == {
        "missing": ["R"],
        "none": [],
        "blankCount": 0,
        "sent": ["R:answer", "S:answer"],
        "omitted": ["S:answer"],
    }


def test_an_answer_all_payload_never_exceeds_the_server_limit() -> None:
    got = run_js(
        ANSWER
        + """
        const cards = Array.from({ length: 15 }, (_, i) => card("C" + i, "open_prompt"));
        return [I.MAX_ANSWERS, I.buildAnswers(cards, {}).length];
        """
    )
    assert got == [12, 12]


# ---- the Questions-and-answers list ---------------------------------------------------------------------

QA = """
const item = (extra) => Object.assign({ card_id: "Q-1", status: "answered", type: "open_prompt", question: "Q", options: [],
  answer: "", value: null, custom_text: null, origin: "planner", depth: 0, fallback: false, facts: [] }, extra || {});
"""


def test_status_editing_and_indent() -> None:
    got = run_js(
        QA
        + """
        return {
          status: ["answered", "skipped", "you_decide", "open", "dropped", undefined].map((s) => I.qaStatus(item({ status: s }))),
          canEdit: ["answered", "skipped", "you_decide", "open"].map((s) => I.qaCanEdit(item({ status: s }), false)),
          approved: I.qaCanEdit(item(), true),
          indent: [0, 1, 2, 3, 9, undefined].map((d) => I.qaIndent(item({ depth: d }))),
        };
        """
    )
    assert got == {
        "status": ["answered", "skipped", "you_decide", "open", "open", "open"],
        "canEdit": [True, True, True, False],
        "approved": False,
        "indent": [0, 1, 2, 3, 3, 0],
    }


def test_the_answer_in_plain_words() -> None:
    got = run_js(
        QA
        + """
        const o = opts("a", "b");
        return [
          I.qaAnswerText(item({ answer: "WhatsApp" }), t),
          I.qaAnswerText(item({ type: "pick_many", options: o, value: ["a", "b"] }), t),
          I.qaAnswerText(item({ type: "quick_form", options: o, value: { a: "Asha", b: "  " } }), t),
          I.qaAnswerText(item({ type: "confirm", options: o, value: { a: "Pune", b: "Hindi" } }), t),
          I.qaAnswerText(item({ type: "choose_one", options: o, value: "b" }), t),
          I.qaAnswerText(item({ type: "choose_one", options: o, value: null, custom_text: "Something odd" }), t),
          I.qaAnswerText(item({ status: "skipped" }), t),
          I.qaAnswerText(item({ status: "you_decide" }), t),
          I.qaAnswerText(item({ status: "open" }), t),
        ];
        """
    )
    assert got == [
        "WhatsApp",
        "Label a | Label b",
        "Label a: Asha",
        "Pune | Hindi",
        "Label b",
        "Something odd",
        "You skipped this question.",
        "You left this one to us.",
        "Not answered yet.",
    ]


def test_the_editor_starts_from_the_current_answer_by_question_kind() -> None:
    got = run_js(
        QA
        + """
        const o = opts("a", "b", "c");
        return {
          single: I.qaEditModel(item({ type: "choose_one", options: o, value: "b" })),
          singleOther: I.qaEditModel(item({ type: "choose_one", options: o, value: null, custom_text: "odd" })),
          multi: I.qaEditModel(item({ type: "pick_many", options: o, value: ["c", "zzz", "a"], custom_text: "plus" })),
          ordered: I.qaEditModel(item({ type: "priority", options: o, value: ["c", "a"] })).ordered,
          fields: I.qaEditModel(item({ type: "confirm", options: opts("n", "m"), value: { n: "Pune" } })).fields,
          form: I.qaEditModel(item({ type: "quick_form", options: opts("n", "m"), value: null })).fields,
          text: I.qaEditModel(item({ type: "open_prompt", value: "typed" })),
          textCustom: I.qaEditModel(item({ type: "open_prompt", value: null, custom_text: "said" })).text,
          noOptions: I.qaEditModel(item({ type: "choose_one", options: [], value: "x" })).kind,
        };
        """
    )
    assert got["single"] == {
        "kind": "single",
        "options": [{"id": i, "label": f"Label {i}"} for i in "abc"],
        "choice": "b",
        "other": "",
    }
    assert (got["singleOther"]["choice"], got["singleOther"]["other"]) == ("__other__", "odd")
    assert got["multi"]["picks"] == ["c", "a"] and got["multi"]["other"] == "plus"
    assert got["ordered"] is True
    assert got["fields"] == {
        "n": "Pune",
        "m": "Label m",
    }  # a confirm field the answer lacks starts as the label
    assert got["form"] == {"n": "", "m": ""}
    assert got["text"] == {"kind": "text", "text": "typed"} and got["textCustom"] == "said"
    assert got["noOptions"] == "text"


def test_the_edit_payload_matches_the_card_answer_shapes() -> None:
    got = run_js(
        QA
        + """
        const o = opts("a", "b");
        const pay = (it, patch) => I.qaEditPayload(it, Object.assign(I.qaEditModel(it), patch));
        const single = item({ type: "choose_one", options: o, value: "a" });
        const multi = item({ type: "pick_many", options: o, value: ["a"] });
        const form = item({ type: "quick_form", options: o, value: { a: "x" } });
        return [
          pay(single, { choice: "b" }),
          pay(single, { choice: "__other__", other: " odd " }),
          pay(single, { choice: "__other__", other: "" }),
          pay(single, { choice: null }),
          pay(multi, { picks: ["a", "b"], other: "" }),
          pay(multi, { picks: [], other: "just text" }),
          pay(multi, { picks: [], other: "" }),
          pay(form, { fields: { a: " y ", b: "" } }),
          pay(form, { fields: { a: "", b: "" } }),
          pay(item({ type: "open_prompt", value: "old" }), { text: "  new  " }),
          pay(item({ type: "open_prompt", value: "old" }), { text: "   " }),
          pay(item({ type: "upload_or_link", value: null }), { text: "https://x.example" }),
        ];
        """
    )
    assert got == [
        {"value": "b", "custom": None},
        {"value": None, "custom": "odd"},
        None,
        None,
        {"value": ["a", "b"], "custom": None},
        {"value": None, "custom": "just text"},
        None,
        {"value": {"a": "y"}, "custom": None},
        None,
        {"value": "new", "custom": None},
        None,
        {"value": None, "custom": "https://x.example"},
    ]


def test_fact_lines_mark_the_ones_that_no_longer_count() -> None:
    got = run_js(
        """
        return ["active", "superseded", "disputed", "not_used", undefined, "odd"].map((s) =>
          I.qaFactLine({ statement: "S", status: s }, t));
        """
    )
    assert got == [
        {"text": "S", "inactive": False, "tag": ""},
        {"text": "S", "inactive": True, "tag": "replaced"},
        {"text": "S", "inactive": True, "tag": "disputed"},
        {"text": "S", "inactive": True, "tag": "not used"},
        {"text": "S", "inactive": False, "tag": ""},
        {"text": "S", "inactive": True, "tag": "odd"},
    ]


# ---- Brief rows, acknowledgements, commands -------------------------------------------------------------


def test_changed_rows_and_the_pulse_key() -> None:
    got = run_js(
        """
        const view = { brief_hash: "H1", sections: [
          { id: "idea", rows: [{ id: "idea.summary", changed: false }, { id: "idea.category", changed: true }] },
          { id: "empty", rows: [] },
          { id: "details", rows: [{ id: "domain_facts.FCT-001", changed: true }] },
        ] };
        const rows = I.changedRows(view);
        return { ids: rows.map((r) => r.section.id + "/" + r.row.id), key: I.pulseKey(view, rows[0].row),
                 none: I.changedRows(null), noKey: I.pulseKey(null, { id: "x" }) };
        """
    )
    assert got == {
        "ids": ["idea/idea.category", "details/domain_facts.FCT-001"],
        "key": "H1:idea.category",
        "none": [],
        "noKey": ":x",
    }


def test_ack_notice_detection() -> None:
    got = run_js(
        """
        const ack = { events: [{ payload: { type: "assistant_message" } },
                               { payload: { type: "notice", code: "edit_unavailable", text: "no" } }] };
        return [I.ackHasNotice(ack, "edit_unavailable"), I.ackHasNotice(ack, "pivot"),
                I.ackHasNotice({ events: [] }, "x"), I.ackHasNotice(null, "x"), I.ackHasNotice({}, "x")];
        """
    )
    assert got == [True, False, False, False, False]


def test_budget_and_layout_commands_are_server_commands() -> None:
    got = run_js(
        """
        return {
          budgets: I.BUDGETS.map((b) => [b.id, b.command]),
          budget: ["quick", "standard", "thorough", "nope"].map((b) => I.budgetCommand(b)),
          modes: [I.modeCommand("answer_all"), I.modeCommand("one_at_a_time"), I.modeCommand("x")],
          other: [I.otherMode("answer_all"), I.otherMode("one_at_a_time"), I.otherMode(undefined)],
        };
        """
    )
    assert got["budgets"] == [
        ["quick", "budget_quick"],
        ["standard", "budget_standard"],
        ["thorough", "budget_thorough"],
    ]
    assert got["budget"] == ["budget_quick", "budget_standard", "budget_thorough", None]
    assert got["modes"] == ["mode_answer_all", "mode_one_at_a_time", None]
    assert got["other"] == ["one_at_a_time", "answer_all", "answer_all"]
    sent = {c for c in got["budget"] + got["modes"] if c}
    assert sent <= set(get_args(CommandName)), "the helpers only name commands the server knows"


# ---- Compass v2: the tier first --------------------------------------------------------------------------


def test_a_budget_command_maps_back_to_its_tier() -> None:
    got = run_js(
        """
        return ["budget_quick", "budget_standard", "budget_thorough", "ask_more", undefined].map((n) => I.budgetOfCommand(n));
        """
    )
    assert got == ["quick", "standard", "thorough", None, None]


def test_the_stored_log_says_which_tier_was_chosen_and_what_was_answered() -> None:
    got = run_js(
        """
        const inn = (type, payload) => ({ direction: "in", type: type, payload: payload });
        const out = (type, payload) => ({ direction: "out", type: type, payload: payload });
        const fresh = I.replaySummary([out("assistant_message", {}), out("card", {})]);
        const tierOnly = I.replaySummary([inn("command", { name: "budget_thorough" })]);
        const changed = I.replaySummary([inn("command", { name: "budget_quick" }), inn("command", { name: "budget_standard" })]);
        const full = I.replaySummary([
          inn("command", { name: "budget_quick" }), inn("user_message", { text: "idea" }),
          inn("card_answer", { card_id: "Q-1" }), inn("card_skip", { card_id: "Q-2" }),
          inn("batch_answer", { batch_id: "BAT-1", answers: [{ card_id: "Q-3" }, { card_id: "Q-4" }] }),
        ]);
        const layout = I.replaySummary([inn("command", { name: "mode_answer_all" })]);
        return { fresh: fresh, tierOnly: tierOnly, changed: changed.tier, full: full, layout: layout.started, none: I.replaySummary(null) };
        """
    )
    assert got["fresh"] == {"tier": None, "answered": {}, "submitted": {}, "started": False}
    assert got["tierOnly"]["tier"] == "thorough" and got["tierOnly"]["started"] is False
    assert got["changed"] == "standard", "the last choice wins"
    assert got["full"]["tier"] == "quick" and got["full"]["started"] is True
    assert got["full"]["answered"] == {"Q-1": True, "Q-2": True, "Q-3": True, "Q-4": True}
    assert got["full"]["submitted"] == {"BAT-1": True}
    assert got["layout"] is False, "a layout command is not the idea"
    assert got["none"]["started"] is False


def test_the_idea_box_waits_for_the_tier_only_in_a_new_project() -> None:
    got = run_js(
        """
        const f = (hasSession, awaiting, tier) => I.tierRequired({ hasSession: hasSession, awaiting: awaiting, tier: tier });
        return [f(true, true, null), f(true, true, "quick"), f(true, false, null), f(false, true, null), I.tierRequired(null)];
        """
    )
    assert got == [True, False, False, False, False]


# ---- Compass v2: numbers, drafts, batches -----------------------------------------------------------------


def test_the_form_numbers_come_from_the_optional_progress_and_hide_when_it_is_absent() -> None:
    got = run_js(
        """
        const pr = (extra) => Object.assign({ tier: "quick", target: 7, hard_cap: 10, committed: 6, closing: false }, extra || {});
        const nums = I.batchNumbers({ progress: pr() }, 3);          // questions 4, 5, 6 of about 7
        const past = I.batchNumbers({ progress: pr({ committed: 9 }) }, 1);   // past the target: no "of about"
        const closing = I.batchNumbers({ progress: pr({ committed: 8, closing: true }) }, 1);
        return {
          nums: nums, labels: [0, 1, 2].map((i) => I.questionLabel(nums, i, t)),
          past: I.questionLabel(past, 0, t), closingFlag: closing.closing,
          absent: [I.batchNumbers({}, 3), I.batchNumbers(null, 3), I.batchNumbers({ progress: { committed: 0 } }, 3)],
          noLabel: I.questionLabel(null, 0, t),
          leaked: JSON.stringify(nums).indexOf("hard") >= 0,
        };
        """
    )
    assert got["nums"] == {"first": 4, "last": 6, "target": 7, "closing": False, "tier": "quick"}
    assert got["labels"] == [
        "Question 4 of about 7",
        "Question 5 of about 7",
        "Question 6 of about 7",
    ]
    assert got["past"] == "Question 9", "past the target the page stops promising a total"
    assert got["closingFlag"] is True
    assert got["absent"] == [None, None, None] and got["noLabel"] == ""
    assert got["leaked"] is False, "the hard cap never leaves the helper"


def test_the_closing_question_is_recognised_from_the_batch_or_its_card() -> None:
    got = run_js(
        """
        const closingCard = card("Z", "priority", { origin: "closing" });
        const b1 = batch([{ id: "g", title: "", cards: [closingCard] }]);
        const b2 = batch([{ id: "g", title: "", cards: [card("A", "choose_one")] }], { progress: { closing: true, committed: 5 } });
        const b3 = batch([{ id: "g", title: "", cards: [card("A", "choose_one"), closingCard] }]);
        return [I.isClosingBatch(b1), I.isClosingBatch(b2), I.isClosingBatch(b3), I.isClosingBatch(null),
                I.isClosingCard(closingCard), I.isClosingCard(card("A", "choose_one")), I.isClosingCard(null)];
        """
    )
    assert got == [True, True, False, False, True, False, False]


def test_a_quick_form_without_fields_reads_like_an_open_question() -> None:
    got = run_js(
        ANSWER
        + """
        const empty = card("F", "quick_form", { options: [] });
        const withFields = card("G", "quick_form", { options: opts("n") });
        const confirmEmpty = card("K", "confirm", { options: [] });
        return {
          type: [I.effectiveType(empty), I.effectiveType(withFields), I.effectiveType(confirmEmpty), I.effectiveType(card("O", "open_prompt"))],
          typed: one(empty, entry({ text: "  Opening hours and delivery area  " })),
          blank: one(empty, entry()),
          fields: one(withFields, entry({ fields: { n: "Asha" } })),
        };
        """
    )
    assert got["type"] == ["open_prompt", "quick_form", "open_prompt", "open_prompt"]
    assert got["typed"] == {
        "card_id": "F",
        "action": "answer",
        "value": "Opening hours and delivery area",
        "custom_text": None,
    }
    assert got["blank"]["action"] == "skip", "blank is skipped, never sent as {}"
    assert got["fields"]["value"] == {"n": "Asha"}


def test_select_all_none_of_these_and_a_typed_answer_work_together() -> None:
    got = run_js(
        ANSWER
        + """
        const m = card("M", "pick_many", { options: opts("a", "b", "c") });
        const e = entry();
        I.togglePick(m, e, "c", true); I.togglePick(m, e, "a", true);
        const ticked = e.picks.slice();                         // option order, not tick order
        I.toggleSelectAll(m, e);                                // all three
        const all = e.picks.slice(); const allFlag = I.allSelected(m, e);
        I.toggleSelectAll(m, e);                                // pressed again: cleared
        const cleared = e.picks.slice();
        I.togglePick(m, e, "b", true); e.other = "and cake";
        const withText = one(m, e);                             // picks and a typed answer together
        I.toggleNone(m, e);                                     // None of these clears the ticks, keeps the text
        const none = one(m, e); const afterNone = e.picks.slice();
        I.togglePick(m, e, "a", true);                          // ticking anything ends "none"
        const back = one(m, e);
        const single = card("S", "choose_one", { options: opts("x", "y") });
        const es = entry(); I.toggleNone(single, es);
        return { ticked: ticked, all: all, allFlag: allFlag, cleared: cleared, withText: withText, none: none,
                 afterNone: afterNone, back: back, noneOnSingle: es.none, canAll: [I.canSelectAll(m), I.canSelectAll(single),
                 I.canSelectAll(card("P", "priority", { options: opts("a", "b") }))] };
        """
    )
    assert got["ticked"] == ["a", "c"] and got["all"] == ["a", "b", "c"] and got["allFlag"] is True
    assert got["cleared"] == []
    assert got["withText"] == {
        "card_id": "M",
        "action": "answer",
        "value": ["b"],
        "custom_text": "and cake",
    }
    assert got["none"] == {
        "card_id": "M",
        "action": "none",
        "value": None,
        "custom_text": "and cake",
    }
    assert got["afterNone"] == []
    assert got["back"]["action"] == "answer" and got["back"]["value"] == ["a"]
    assert got["noneOnSingle"] is False, "only a pick-several question has None of these"
    assert got["canAll"] == [True, False, False]


def test_none_of_these_alone_is_an_answer_and_counts_as_handled() -> None:
    got = run_js(
        ANSWER
        + """
        const m = card("M", "pick_many", { options: opts("a", "b") });
        const r = card("R", "pick_many", { options: opts("a", "b"), must_confirm: true });
        const e = entry({ none: true });
        const cards = [m, r];
        const draft = { M: e, R: entry({ none: true }) };
        return { item: one(m, e), handled: I.handledCount(cards, draft), blank: I.blankCards(cards, draft).length,
                 missing: I.missingRequired(cards, draft).length };
        """
    )
    assert got["item"] == {"card_id": "M", "action": "none", "value": None, "custom_text": None}
    assert got["handled"] == 2 and got["blank"] == 0 and got["missing"] == 0


def test_the_priority_picks_keep_their_rank_and_can_be_moved() -> None:
    got = run_js(
        ANSWER
        + """
        const p = card("P", "priority", { options: opts("a", "b", "c", "d") });
        const e = entry();
        I.togglePick(p, e, "c", true); I.togglePick(p, e, "a", true); I.togglePick(p, e, "d", true);
        const ranked = e.picks.slice();
        I.movePick(e, "d", -1); const up = e.picks.slice();
        I.movePick(e, "c", -1); I.movePick(e, "c", -1); const edge = e.picks.slice();   // already first: stays
        I.movePick(e, "zzz", 1);
        I.togglePick(p, e, "a", false); const dropped = e.picks.slice();
        e.other = "must work offline";
        return { ranked: ranked, up: up, edge: edge, dropped: dropped, item: one(p, e) };
        """
    )
    assert got["ranked"] == ["c", "a", "d"]
    assert got["up"] == ["c", "d", "a"]
    assert got["edge"] == ["c", "d", "a"]
    assert got["dropped"] == ["c", "d"]
    assert got["item"]["value"] == ["c", "d"] and got["item"]["custom_text"] == "must work offline"


def test_a_blank_question_is_summarised_before_sending_and_sent_as_skipped() -> None:
    got = run_js(
        ANSWER
        + """
        const a = card("A", "open_prompt"); const b = card("B", "open_prompt"); const c = card("C", "choose_one", { options: opts("x") });
        const d = card("D", "pick_many", { options: opts("x", "y") }); const r = card("R", "open_prompt", { must_confirm: true });
        const cards = [a, b, c, d, r];
        const draft = { A: entry({ text: "hello" }), B: entry({ action: "you_decide" }), C: entry(), D: entry({ none: true }), R: entry() };
        const sum = I.summarizeBlanks(cards, draft);
        const ids = (l) => l.map((x) => x.id);
        const allBlank = I.summarizeBlanks([a, b], {});
        return { answered: ids(sum.answered), decide: ids(sum.decide), none: ids(sum.none), blank: ids(sum.blank),
                 required: ids(sum.required), total: sum.total, allBlank: ids(allBlank.blank),
                 sent: I.buildAnswers([a, b, c], draft).map((x) => x.card_id + ":" + x.action) };
        """
    )
    assert got["answered"] == ["A"] and got["decide"] == ["B"] and got["none"] == ["D"]
    assert got["blank"] == ["C"] and got["required"] == ["R"] and got["total"] == 5
    assert got["allBlank"] == ["A", "B"]
    assert got["sent"] == ["A:answer", "B:you_decide", "C:skip"]


def test_the_batch_payload_carries_decide_rest_only_when_asked_and_leaves_blanks_out() -> None:
    got = run_js(
        ANSWER
        + """
        const a = card("A", "open_prompt"); const b = card("B", "open_prompt"); const c = card("C", "choose_one", { options: opts("x") });
        const draft = { A: entry({ text: "hello" }), B: entry(), C: entry({ action: "you_decide" }) };
        return { plain: I.buildBatchPayload("BAT-1", [a, b, c], draft), rest: I.buildBatchPayload("BAT-1", [a, b, c], draft, { decideRest: true }),
                 many: I.buildBatchPayload("BAT-1", Array.from({ length: 14 }, (_, i) => card("X" + i, "open_prompt")), {}, { decideRest: false }).answers.length };
        """
    )
    assert got["plain"] == {
        "type": "batch_answer",
        "batch_id": "BAT-1",
        "answers": [
            {"card_id": "A", "action": "answer", "value": "hello", "custom_text": None},
            {"card_id": "B", "action": "skip", "value": None, "custom_text": None},
            {"card_id": "C", "action": "you_decide", "value": None, "custom_text": None},
        ],
    }
    assert "decide_rest" not in got["plain"], (
        "an older server never sees the field unless it is asked for"
    )
    assert got["rest"]["decide_rest"] is True
    assert [a["card_id"] for a in got["rest"]["answers"]] == ["A", "C"], (
        "blank B is decided by the server"
    )
    assert got["many"] == 12


def test_a_new_batch_keeps_what_was_typed_where_the_question_still_exists() -> None:
    got = run_js(
        ANSWER
        + """
        const old = { A: entry({ text: "kept text" }), B: entry({ choice: "gone", other: "typed" }),
                      M: entry({ picks: ["a", "zzz"], none: true, other: "extra" }), X: entry({ text: "dropped question" }) };
        const cards = [card("A", "open_prompt"), card("B", "choose_one", { options: opts("x") }),
                       card("M", "choose_one", { options: opts("a") }), card("NEW", "open_prompt")];
        const r = I.carryDraft(old, cards);
        return { kept: r.kept, a: r.draft.A.text, bChoice: r.draft.B.choice, bOther: r.draft.B.other,
                 mPicks: r.draft.M.picks, mNone: r.draft.M.none, mOther: r.draft.M.other, fresh: r.draft.NEW.text,
                 hasX: "X" in r.draft, none: I.carryDraft(null, cards).kept };
        """
    )
    assert got["kept"] == 3 and got["a"] == "kept text"
    assert got["bChoice"] is None and got["bOther"] == "typed", (
        "a vanished option is dropped, the text stays"
    )
    assert got["mPicks"] == ["a"] or got["mPicks"] == []
    assert got["mNone"] is False, "a question that cannot say none no longer does"
    assert (
        got["mOther"] == "extra"
        and got["fresh"] == ""
        and got["hasX"] is False
        and got["none"] == 0
    )


def test_a_batch_sent_again_replaces_its_copy_and_events_are_drawn_once() -> None:
    got = run_js(
        """
        const b1 = batch([{ id: "g", title: "", cards: [card("A", "open_prompt"), card("B", "open_prompt")] }], { batch_id: "B1" });
        const b1again = batch([{ id: "g", title: "", cards: [card("A", "open_prompt"), card("B", "open_prompt")] }], { batch_id: "B1" });
        const b1fewer = batch([{ id: "g", title: "", cards: [card("B", "open_prompt")] }], { batch_id: "B1" });
        const b2 = batch([{ id: "g", title: "", cards: [card("C", "open_prompt")] }], { batch_id: "B2" });
        const list = [];
        I.upsertBatch(list, b1, 3); I.upsertBatch(list, b2, 3); I.upsertBatch(list, b1again, 3);
        const many = []; for (let i = 0; i < 6; i++) { I.upsertBatch(many, batch([], { batch_id: "N" + i }), 3); }
        const seen = {};
        const first = I.registerEvent(seen, { seq: 4 }); const second = I.registerEvent(seen, { seq: 4 });
        return { ids: list.map((b) => b.batch_id), replaced: list[0] === b1again,
                 same: [I.sameQuestions(b1, b1again), I.sameQuestions(b1, b1fewer), I.sameQuestions(b1, b2), I.sameQuestions(null, b1)],
                 capped: many.map((b) => b.batch_id), events: [first, second, I.registerEvent(seen, { seq: 5 }), I.registerEvent(seen, {}), I.registerEvent(seen, null)],
                 delays: [0, 1, 2, 3, 4, 9].map((n) => I.reconnectDelay(n)) };
        """
    )
    assert got["ids"] == ["B1", "B2"] and got["replaced"] is True
    assert got["same"] == [True, False, False, False]
    assert got["capped"] == ["N3", "N4", "N5"]
    assert got["events"] == [True, False, True, True, True]
    assert got["delays"] == [1, 2, 4, 8, 15, 15]


def test_the_helper_exposes_only_what_the_screen_uses() -> None:
    names = run_js("return Object.keys(I).sort();")
    assert {
        "locate", "progressLine", "buildAnswers", "pendingGroups", "qaEditPayload", "changedRows",
    } <= set(names)  # fmt: skip
