/* Compass interview helpers: the pure, DOM-free logic of the AI-planned interview screens (build prompt 02,
   section 2.7). Loaded by the shell before compass.js (the registry entry's "helper" file) and exposed as
   window.compassInterview. Nothing here touches the page, the network or storage, so every function can be
   run under Node (apps/web/tests/test_interview_helpers.py does exactly that).

   Shapes it works on (all from the Compass HTTP API):
     batch   question_batch event: {batch_id, mode, ui_mode, groups: [{id, title, cards: [card]}], total, answered,
             progress?: {tier, target, committed, closing}}   (optional: absent on older servers; the page never reads the cap)
     card    a Card (id, type, question, options, group_id, examples, must_confirm, ...)
     draft   {card_id: entry}; an entry is what the batch form has collected for one question
     qa      one item of GET /sessions/{id}/qa
   User and model text only ever passes through these functions as plain strings. */
(function (root) {
  "use strict";

  var OTHER = "__other__";          // the "Something else" radio of a choose-one question
  var MAX_ANSWERS = 12;             // BatchAnswer.answers max_length on the server
  var RADIO_TYPES = { choose_one: true, look_signals: true };
  var CHECK_TYPES = { pick_many: true, priority: true };
  var FIELD_TYPES = { confirm: true, quick_form: true };

  /** Interview depth: the three chips and the command each one sends. */
  var BUDGETS = [
    { id: "quick", command: "budget_quick" },
    { id: "standard", command: "budget_standard" },
    { id: "thorough", command: "budget_thorough" }
  ];
  var MODE_COMMANDS = { answer_all: "mode_answer_all", one_at_a_time: "mode_one_at_a_time" };

  function budgetCommand(id) {
    for (var i = 0; i < BUDGETS.length; i++) { if (BUDGETS[i].id === id) { return BUDGETS[i].command; } }
    return null;
  }
  /** "budget_quick" -> "quick"; null for any other command name. */
  function budgetOfCommand(name) {
    for (var i = 0; i < BUDGETS.length; i++) { if (BUDGETS[i].command === name) { return BUDGETS[i].id; } }
    return null;
  }

  // ---- the first choice: how many questions (the tier) ----------------------------------------------------

  /** What the stored log says about where a session stands, read once when a project is reopened: the tier
      the person already chose, the questions answered, the batches submitted and whether the idea was given. */
  function replaySummary(items) {
    var out = { tier: null, answered: {}, submitted: {}, started: false };
    (items || []).forEach(function (ev) {
      if (!ev || ev.direction !== "in") { return; }
      var p = ev.payload || {};
      if (ev.type === "command") {
        var b = budgetOfCommand(p.name);
        if (b) { out.tier = b; }
        if (!b && p.name !== "mode_answer_all" && p.name !== "mode_one_at_a_time") { out.started = true; }
        return;
      }
      out.started = true;
      if (ev.type === "card_answer" || ev.type === "card_skip" || ev.type === "card_you_decide") {
        if (p.card_id) { out.answered[p.card_id] = true; }
      } else if (ev.type === "batch_answer") {
        if (p.batch_id) { out.submitted[p.batch_id] = true; }
        (p.answers || []).forEach(function (a) { out.answered[a.card_id] = true; });
      }
    });
    return out;
  }

  /** The idea box and the start buttons wait for the tier while a new project has not had its first idea yet. */
  function tierRequired(ctx) { return !!(ctx && ctx.hasSession && ctx.awaiting && !ctx.tier); }
  function modeCommand(mode) { return MODE_COMMANDS[mode] || null; }
  function otherMode(mode) { return mode === "answer_all" ? "one_at_a_time" : "answer_all"; }

  // ---- where a card sits in the interview -------------------------------------------------------------

  /** Find a card in the remembered batches (newest first). A follow-up is not part of any batch, so it
      borrows the group of the question it refines. Returns null when the card belongs to no known batch. */
  function locate(batches, card, hops) {
    if (!card || !batches || !batches.length) { return null; }
    var b, g, c, before;
    for (b = batches.length - 1; b >= 0; b--) {
      var groups = batches[b].groups || [];
      before = 0;
      for (g = 0; g < groups.length; g++) {
        var cards = groups[g].cards || [];
        for (c = 0; c < cards.length; c++) {
          if (cards[c].id === card.id) {
            return {
              batch: batches[b], groupIndex: g + 1, groupCount: groups.length,
              groupId: groups[g].id, groupTitle: groups[g].title || "",
              position: before + c + 1, total: batches[b].total || countCards(batches[b]), followup: false
            };
          }
        }
        before += cards.length;
      }
    }
    var depth = hops || 0;
    if (card.parent_card_id && depth < 4) {
      var parent = locate(batches, { id: card.parent_card_id }, depth + 1);
      if (parent) { parent.followup = true; return parent; }
    }
    return null;
  }
  function countCards(batch) {
    var n = 0;
    (batch.groups || []).forEach(function (g) { n += (g.cards || []).length; });
    return n;
  }

  /** The compact line under a card's group title: "Group 2 of 3 · question 4 of about 9". */
  function progressLine(loc, progress, t) {
    var hasProgress = !!(progress && progress.of_about);
    if (loc && hasProgress) {
      var key = loc.followup ? "ui.interview.progress_followup" : "ui.interview.progress_line";
      return t(key, { g: loc.groupIndex, groups: loc.groupCount, n: progress.n, total: progress.of_about });
    }
    if (loc && !loc.followup) { return t("ui.interview.group_only", { g: loc.groupIndex, groups: loc.groupCount }); }
    if (hasProgress) { return t("progress.question_n_of_about", { n: progress.n, total: progress.of_about }); }
    return "";
  }

  /** The "For example: ..." placeholder of a text question, from its planner-written examples. */
  function examplePlaceholder(card, t) {
    var ex = (card && card.examples ? card.examples : []).filter(function (x) { return String(x).trim() !== ""; });
    return ex.length ? t("ui.interview.example", { example: ex.slice(0, 2).join("; ") }) : "";
  }

  /** The batch form is the layout for every planned question, even a batch of one (the closing question, a gap
      question). Only a question that belongs to no known batch (a legacy single `card` event)
      is shown on its own. */
  function useForm(loc, pendingCount) {
    return !!loc && pendingCount >= 1;
  }

  // ---- numbers: "Question 4 of about 7", "Last question" ----------------------------------------------------

  /** Where the questions of the form sit in the interview, from the optional `progress` of the batch event:
      {first, last, target, closing, tier}. `committed` counts every question planned so far (asked plus waiting),
      so the form's last question is number `committed`. `target` is the "about N" of the tier and is dropped once
      the count passes it (the hard cap is never shown, never even read). null when the server sent no progress. */
  function batchNumbers(batch, pendingCount) {
    var pr = batch && batch.progress;
    if (!pr || !(pr.committed > 0)) { return null; }
    var n = Math.max(1, pendingCount || 1);
    var last = pr.committed;
    return {
      first: Math.max(1, last - n + 1), last: last, target: pr.target > 0 && last <= pr.target ? pr.target : 0,
      closing: !!pr.closing, tier: pr.tier || ""
    };
  }
  /** "Question 4 of about 7", or "Question 8" once past the target, or "" when the count is unknown. */
  function questionLabel(nums, index, t) {
    if (!nums) { return ""; }
    var n = nums.first + index;
    return nums.target ? t("ui.v2.question_n_of", { n: n, total: nums.target }) : t("ui.v2.question_n", { n: n });
  }
  function isClosingCard(card) { return !!card && card.origin === "closing"; }
  /** The reserved last question: the batch says so, or its only question is a closing one. */
  function isClosingBatch(batch) {
    if (!batch) { return false; }
    if (batch.progress && batch.progress.closing) { return true; }
    var cards = flatCards(batch.groups || []);
    return cards.length > 0 && cards.every(isClosingCard);
  }

  /** The groups of a batch that still have unanswered questions, at most `limit` questions in all. */
  function pendingGroups(batch, answered, limit) {
    var room = limit || MAX_ANSWERS;
    var out = [];
    ((batch && batch.groups) || []).forEach(function (g) {
      var cards = [];
      (g.cards || []).forEach(function (c) {
        if (!answered[c.id] && room > 0) { cards.push(c); room -= 1; }
      });
      if (cards.length) { out.push({ id: g.id, title: g.title || "", cards: cards }); }
    });
    return out;
  }
  function flatCards(groups) {
    var out = [];
    groups.forEach(function (g) { g.cards.forEach(function (c) { out.push(c); }); });
    return out;
  }

  // ---- the answer-all form: what has been collected, and the BatchAnswer it becomes -------------------------

  function emptyEntry() {
    return { action: "answer", choice: null, picks: [], fields: {}, touched: false, confirmed: false, other: "", text: "", none: false };
  }

  /** A form or confirm question with no fields to fill reads like an open question: the planner can produce a
      quick_form without options, and an empty set of fields would otherwise send `{}`. */
  function effectiveType(card) {
    var type = card && card.type;
    if ((type === "quick_form" || type === "confirm") && !((card.options || []).length)) { return "open_prompt"; }
    return type;
  }
  function optionIds(card) { return ((card && card.options) || []).map(function (o) { return o.id; }); }
  /** Only a "pick several" question has "None of these" and "Select all". */
  function canNone(card) { return effectiveType(card) === "pick_many"; }
  function canSelectAll(card) { return effectiveType(card) === "pick_many" && optionIds(card).length >= 2; }
  /** Option cards always show the typed "something else" field (the server keeps custom_input enabled on them). */
  function showsCustom(card) {
    var type = effectiveType(card);
    return !!(RADIO_TYPES[type] || CHECK_TYPES[type]);
  }

  /** What an entry says for its card: {value, custom}, or null while it is blank. */
  function entryValue(card, e) {
    var type = effectiveType(card);
    var other = String(e.other || "").trim();
    var text = String(e.text || "").trim();
    var out = {};
    if (RADIO_TYPES[type]) {
      if (e.choice === OTHER) { return other ? { value: null, custom: other } : null; }
      return e.choice ? { value: e.choice, custom: null } : null;
    }
    if (CHECK_TYPES[type]) {
      if (e.picks.length) { return { value: e.picks.slice(), custom: other || null }; }
      return other ? { value: null, custom: other } : null;
    }
    if (FIELD_TYPES[type]) {
      var opts = card.options || [];
      if (type === "confirm") {
        if (!(e.confirmed || e.touched)) { return null; }
        opts.forEach(function (o) {
          var v = e.fields[o.id] !== undefined ? e.fields[o.id] : o.label;
          out[o.id] = String(v).trim();
        });
        return { value: out, custom: null };
      }
      var any = false;
      opts.forEach(function (o) {
        var v = String(e.fields[o.id] || "").trim();
        if (v) { out[o.id] = v; any = true; }
      });
      return any ? { value: out, custom: null } : null;
    }
    if (type === "upload_or_link") { return text ? { value: null, custom: text } : null; }
    return text ? { value: text, custom: null } : null;   // open_prompt and anything unknown
  }

  /** "None of these" is its own answer: it counts as handled and carries only the typed text, if any. */
  function isNone(card, e) { return !!e.none && e.action === "answer" && canNone(card); }

  function addressed(card, e) {
    if (!card.must_confirm && (e.action === "skip" || e.action === "you_decide")) { return true; }
    return isNone(card, e) || entryValue(card, e) !== null;
  }
  function entryOf(draft, card) { return draft[card.id] || emptyEntry(); }

  /** Cards that must be answered (risky slots: no skip, no "you decide") and are still blank. */
  function missingRequired(cards, draft) {
    return cards.filter(function (c) {
      var e = entryOf(draft, c);
      return c.must_confirm && !isNone(c, e) && entryValue(c, e) === null;
    });
  }
  /** Cards that will be sent as skipped because the person left them blank. */
  function blankCards(cards, draft) {
    return cards.filter(function (c) {
      var e = entryOf(draft, c);
      return !c.must_confirm && e.action === "answer" && !isNone(c, e) && entryValue(c, e) === null;
    });
  }
  function handledCount(cards, draft) {
    var n = 0;
    cards.forEach(function (c) { if (addressed(c, entryOf(draft, c))) { n += 1; } });
    return n;
  }

  /** The item of a batch_answer for one question, or null while the question is blank. */
  function answerFor(card, e) {
    if (!card.must_confirm && (e.action === "skip" || e.action === "you_decide")) {
      return { card_id: card.id, action: e.action, value: null, custom_text: null };
    }
    if (isNone(card, e)) {   // none of the options; a typed text says what they want instead
      return { card_id: card.id, action: "none", value: null, custom_text: String(e.other || "").trim() || null };
    }
    var v = entryValue(card, e);
    return v ? { card_id: card.id, action: "answer", value: v.value, custom_text: v.custom } : null;
  }

  /** The `answers` of a batch_answer event. Blank questions are sent as skipped (never left dangling) except
      those that must be confirmed, which the form refuses to send blank. */
  function buildAnswers(cards, draft) {
    var out = [];
    cards.forEach(function (card) {
      var item = answerFor(card, entryOf(draft, card));
      if (item) { out.push(item); }
      else if (!card.must_confirm) { out.push({ card_id: card.id, action: "skip", value: null, custom_text: null }); }
    });
    return out.slice(0, MAX_ANSWERS);
  }

  /** The whole batch_answer body (without event_id). With `decideRest` the questions the person left blank are
      not listed at all: the server decides every other open question and shows each choice for approval. */
  function buildBatchPayload(batchId, cards, draft, opts) {
    var decideRest = !!(opts && opts.decideRest);
    var answers;
    if (decideRest) {
      answers = [];
      cards.forEach(function (card) {
        var item = answerFor(card, entryOf(draft, card));
        if (item) { answers.push(item); }
      });
      answers = answers.slice(0, MAX_ANSWERS);
    } else {
      answers = buildAnswers(cards, draft);
    }
    var body = { type: "batch_answer", batch_id: batchId, answers: answers };
    if (decideRest) { body.decide_rest = true; }
    return body;
  }

  /** What the form says before it is sent, as numbers and lists of cards: answered, left to us ("You decide"),
      "None of these", blank (will be skipped, we decide), and the confirmations still missing. */
  function summarizeBlanks(cards, draft) {
    var out = { total: cards.length, answered: [], decide: [], none: [], blank: [], required: [] };
    cards.forEach(function (c) {
      var e = entryOf(draft, c);
      var item = answerFor(c, e);
      if (item === null) { (c.must_confirm ? out.required : out.blank).push(c); }
      else if (item.action === "you_decide") { out.decide.push(c); }
      else if (item.action === "skip") { out.blank.push(c); }
      else if (item.action === "none") { out.none.push(c); }
      else { out.answered.push(c); }
    });
    return out;
  }

  // ---- ticking: select all, none of these, ranking -----------------------------------------------------------

  /** Tick or untick one option. A "pick several" keeps the option order; a priority question keeps the order the
      person ticked them in (that order is the ranking). Ticking anything ends "None of these". */
  function togglePick(card, e, id, on) {
    e.picks = e.picks.filter(function (x) { return x !== id; });
    if (on) {
      e.picks.push(id);
      e.none = false;
      if (effectiveType(card) !== "priority") {
        var order = optionIds(card);
        e.picks.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); });
      }
    }
    return e;
  }
  function allSelected(card, e) {
    var ids = optionIds(card);
    return ids.length > 0 && ids.every(function (id) { return e.picks.indexOf(id) >= 0; });
  }
  /** "Select all": every option ticked, in option order. Pressing it again clears them. */
  function toggleSelectAll(card, e) {
    if (allSelected(card, e)) { e.picks = []; }
    else { e.picks = optionIds(card); e.none = false; }
    return e;
  }
  /** "None of these": nothing in the list fits. Clears the ticks (a typed "something else" stays). Pressing it
      again takes it back. */
  function toggleNone(card, e) {
    if (!canNone(card)) { return e; }
    e.none = !e.none;
    if (e.none) { e.picks = []; }
    return e;
  }
  /** Move a ranked pick one place up (delta -1) or down (+1). */
  function movePick(e, id, delta) {
    var i = e.picks.indexOf(id);
    var j = i + delta;
    if (i < 0 || j < 0 || j >= e.picks.length) { return e; }
    var x = e.picks[i]; e.picks[i] = e.picks[j]; e.picks[j] = x;
    return e;
  }

  // ---- keeping what was typed when the batch changes ----------------------------------------------------------

  /** A copy of an entry that still makes sense for a (possibly changed) question. */
  function sanitizeEntry(card, e) {
    var n = emptyEntry();
    var ids = optionIds(card);
    Object.keys(n).forEach(function (k) { if (e[k] !== undefined) { n[k] = e[k]; } });
    n.picks = (e.picks || []).filter(function (x) { return ids.indexOf(x) >= 0; });
    n.fields = {};
    ids.forEach(function (id) { if (e.fields && e.fields[id] !== undefined) { n.fields[id] = e.fields[id]; } });
    if (n.choice && n.choice !== OTHER && ids.indexOf(n.choice) < 0) { n.choice = null; }
    if (n.none && !canNone(card)) { n.none = false; }
    return n;
  }
  /** The draft for a new set of cards: what was typed or ticked before stays wherever the question still exists. */
  function carryDraft(oldDraft, cards) {
    var out = {};
    var kept = 0;
    cards.forEach(function (c) {
      var old = oldDraft && oldDraft[c.id];
      if (old) { out[c.id] = sanitizeEntry(c, old); kept += 1; } else { out[c.id] = emptyEntry(); }
    });
    return { draft: out, kept: kept };
  }

  /** Remember a batch by its id: a batch that is sent again (after a reconnect or a layout change) replaces its
      earlier copy instead of being listed twice. Newest last, at most `limit` kept. */
  function upsertBatch(batches, batch, limit) {
    for (var i = 0; i < batches.length; i++) {
      if (batches[i].batch_id === batch.batch_id) { batches[i] = batch; return batches; }
    }
    batches.push(batch);
    while (batches.length > (limit || 40)) { batches.shift(); }
    return batches;
  }
  /** Two copies of a batch with the same questions in the same order (nothing on screen needs to change). */
  function sameQuestions(a, b) {
    if (!a || !b || a.batch_id !== b.batch_id) { return false; }
    return flatCards(a.groups || []).map(function (c) { return c.id; }).join("|") ===
      flatCards(b.groups || []).map(function (c) { return c.id; }).join("|");
  }

  // ---- documents ------------------------------------------------------------------------------------------------

  var MAX_UPLOAD_BYTES = 15 * 1024 * 1024;   // the server's limit (intake.max_file_bytes); checked here first
  var UPLOAD_ACCEPT = ".pdf,.docx,.xlsx,.pptx,.csv,.txt,.md,.eml";   // what the picker offers; the server checks the type
  /** Is this file worth sending? A file over the limit or an empty one is refused before any bytes leave the page. */
  function checkUpload(file, limit) {
    if (!file || !(file.size > 0)) { return { ok: false, key: "ui.v2.upload_empty" }; }
    if (file.size > (limit || MAX_UPLOAD_BYTES)) { return { ok: false, key: "ui.v2.upload_too_large" }; }
    return { ok: true, key: "" };
  }
  /** The plain-words string for a failed upload request (null: use the server's own message, else the generic one). */
  function uploadErrorKey(status) {
    if (status === 413) { return "ui.v2.upload_too_large"; }
    if (status === 415) { return "ui.v2.upload_unsupported"; }
    return null;
  }

  /** True the first time an outbound event is seen. The stream can replay events after a reconnect, and an
      event can arrive both from the stream and in the reply to the request: each is drawn once. */
  function registerEvent(seen, ev) {
    if (!ev) { return true; }
    var key = ev.seq !== null && ev.seq !== undefined ? ev.seq : (ev.idx !== null && ev.idx !== undefined ? "i" + ev.idx : null);
    if (key === null) { return true; }
    if (seen[key]) { return false; }
    seen[key] = true;
    return true;
  }
  // ---- waiting: what the page says while the server works ----------------------------------------------------

  /** One staged line per phase: [seconds since the request went out, string key]. The page shows the line with the
      largest `seconds` that has passed, so every phase ends in the "still working" fallbacks. */
  var WAIT = {
    idea: [[0, "ui.typing_read"], [7, "ui.typing_plan"], [20, "ui.typing_check"], [45, "ui.typing_long"], [90, "ui.v2.wait_very_long"]],
    answers: [[0, "ui.v2.wait_answers_read"], [6, "ui.v2.wait_answers_plan"], [20, "ui.typing_check"], [45, "ui.typing_long"], [90, "ui.v2.wait_very_long"]],
    brief: [[0, "ui.v2.wait_brief_write"], [12, "ui.v2.wait_brief_check"], [40, "ui.typing_long"], [90, "ui.v2.wait_very_long"]],
    decide: [[0, "ui.v2.wait_decide"], [10, "ui.v2.wait_brief_write"], [35, "ui.v2.wait_brief_check"], [60, "ui.typing_long"], [100, "ui.v2.wait_very_long"]],
    document: [[0, "ui.v2.wait_document"], [10, "ui.v2.wait_document_points"], [40, "ui.typing_long"], [90, "ui.v2.wait_very_long"]],
    edit: [[0, "ui.v2.wait_edit"], [25, "ui.typing_long"], [90, "ui.v2.wait_very_long"]],
    more: [[0, "ui.v2.wait_more"], [20, "ui.typing_check"], [45, "ui.typing_long"], [90, "ui.v2.wait_very_long"]],
    generic: [[0, "ui.typing"], [8, "ui.v2.wait_working"], [45, "ui.typing_long"], [90, "ui.v2.wait_very_long"]]
  };
  function waitSchedule(phase) { return WAIT[phase] || WAIT.generic; }
  /** The string key to show `elapsed` seconds into a phase. */
  function waitKey(phase, elapsed) {
    var steps = waitSchedule(phase);
    var key = steps[0][1];
    steps.forEach(function (st) { if (elapsed >= st[0]) { key = st[1]; } });
    return key;
  }
  /** What is the server doing for this request? From what was just sent: the first idea, an answer to a batch, the
      closing answer (the Brief is written next), "decide the rest", a document, an edit. ctx: {hasBatch, closing}. */
  function phaseFor(ev, ctx) {
    var c = ctx || {};
    switch (ev && ev.type) {
      case "user_message": return c.hasBatch ? "answers" : "idea";
      case "batch_answer": return ev.decide_rest ? "decide" : (c.closing ? "brief" : "answers");
      case "card_answer": case "card_skip": case "card_you_decide": return c.closing ? "brief" : "answers";
      case "answer_edit": case "brief_edit": case "brief_feedback": case "assumption_decision": case "lookup_edit": return "edit";
      case "upload_added": case "link_added": return "document";
      case "command":
        if (ev.name === "ask_more") { return "more"; }
        if (ev.name === "finish_with_defaults") { return "decide"; }
        if (ev.name === "prepare_questions") { return "idea"; }
        return "generic";
      default: return "generic";
    }
  }
  /** The context phaseFor needs, from the batches the page knows: has the interview started, and is the batch or card
      being answered the reserved closing question? */
  function phaseContext(batches, ev) {
    var closing = false;
    (batches || []).forEach(function (b) {
      if (ev && ev.type === "batch_answer" && b.batch_id === ev.batch_id) { closing = isClosingBatch(b); }
      else if (ev && ev.card_id) {
        flatCards(b.groups || []).forEach(function (c) { if (c.id === ev.card_id && isClosingCard(c)) { closing = true; } });
      }
    });
    return { hasBatch: !!(batches && batches.length), closing: closing };
  }
  /** A `progress` event can name a stage the server has moved on to; the wait line follows it. */
  function phaseForStage(stage) {
    var st = String(stage || "").toLowerCase();
    if (st === "brief" || st === "compile" || st === "compiling" || st === "review") { return "brief"; }
    if (st === "document" || st === "intake" || st === "upload") { return "document"; }
    return null;
  }

  /** Seconds to wait before the next stream reconnect: 1, 2, 4, 8 up to 15. */
  function reconnectDelay(attempt) { return Math.min(15, Math.pow(2, Math.max(0, attempt || 0))); }

  // ---- the Questions-and-answers list (History tab) -----------------------------------------------------------

  function qaStatus(item) {
    var s = item && item.status;
    return s === "answered" || s === "skipped" || s === "you_decide" ? s : "open";
  }
  /** Only answered, skipped or "you decide" questions can be edited, and not once the Brief is approved. */
  function qaCanEdit(item, approved) { return !approved && qaStatus(item) !== "open"; }
  function qaIndent(item) { return Math.max(0, Math.min(3, (item && item.depth) || 0)); }
  function hasOption(opts, id) {
    for (var i = 0; i < opts.length; i++) { if (opts[i].id === id) { return true; } }
    return false;
  }
  function optionLabel(opts, id) {
    for (var i = 0; i < opts.length; i++) { if (opts[i].id === id) { return opts[i].label; } }
    return String(id);
  }

  /** The answer in plain words: the server's own wording, else built from the raw value and the options. */
  function qaAnswerText(item, t) {
    if (item.answer) { return item.answer; }
    var s = qaStatus(item);
    var opts = item.options || [];
    var v = item.value;
    var parts = [];
    if (Array.isArray(v)) { parts = v.map(function (x) { return optionLabel(opts, x); }); }
    else if (v && typeof v === "object") {
      Object.keys(v).forEach(function (k) {
        if (String(v[k]).trim() !== "") { parts.push(item.type === "confirm" ? String(v[k]) : optionLabel(opts, k) + ": " + v[k]); }
      });
    } else if (typeof v === "string" && v.trim() !== "") { parts = [hasOption(opts, v) ? optionLabel(opts, v) : v]; }
    if (item.custom_text) { parts.push(item.custom_text); }
    if (parts.length) { return parts.join(" | "); }
    if (s === "skipped") { return t("ui.interview.qa_skipped_text"); }
    if (s === "you_decide") { return t("ui.interview.qa_decide_text"); }
    return t("ui.interview.qa_open_text");
  }

  /** What the inline editor starts from, by the kind of question. */
  function qaEditModel(item) {
    var opts = item.options || [];
    var v = item.value;
    var custom = item.custom_text || "";
    var type = item.type;
    if (RADIO_TYPES[type] && opts.length) {
      var chosen = typeof v === "string" && hasOption(opts, v) ? v : null;
      var other = custom || (chosen === null && typeof v === "string" ? v : "");
      return { kind: "single", options: opts, choice: chosen !== null ? chosen : (other ? OTHER : null), other: other };
    }
    if (CHECK_TYPES[type] && opts.length) {
      var picks = Array.isArray(v) ? v.filter(function (x) { return hasOption(opts, x); }) : [];
      return { kind: "multi", options: opts, picks: picks, other: custom, ordered: type === "priority" };
    }
    if (FIELD_TYPES[type] && opts.length) {
      var fields = {};
      opts.forEach(function (o) {
        var cur = v && typeof v === "object" && !Array.isArray(v) ? v[o.id] : undefined;
        fields[o.id] = cur !== undefined && cur !== null ? String(cur) : (type === "confirm" ? o.label : "");
      });
      return { kind: "fields", options: opts, fields: fields };
    }
    var text = custom || (typeof v === "string" ? v : "") || "";
    return { kind: "text", text: text };
  }

  /** {value, custom} for answer_edit from the editor's model, or null when nothing has been chosen or typed. */
  function qaEditPayload(item, model) {
    var other = String(model.other || "").trim();
    if (model.kind === "single") {
      if (model.choice === OTHER) { return other ? { value: null, custom: other } : null; }
      return model.choice ? { value: model.choice, custom: null } : null;
    }
    if (model.kind === "multi") {
      if (model.picks.length) { return { value: model.picks.slice(), custom: other || null }; }
      return other ? { value: null, custom: other } : null;
    }
    if (model.kind === "fields") {
      var out = {};
      var any = false;
      Object.keys(model.fields).forEach(function (k) {
        var x = String(model.fields[k]).trim();
        if (x) { out[k] = x; any = true; }
      });
      return any ? { value: out, custom: null } : null;
    }
    var text = String(model.text || "").trim();
    if (!text) { return null; }
    return item.type === "upload_or_link" ? { value: null, custom: text } : { value: text, custom: null };
  }

  var FACT_TAGS = {
    superseded: "ui.interview.fact_superseded", disputed: "ui.interview.fact_disputed",
    not_used: "ui.interview.fact_not_used"
  };
  /** One fact line: its statement, whether it still counts, and a short tag for the ones that do not. */
  function qaFactLine(fact, t) {
    var status = fact && fact.status ? fact.status : "active";
    var key = FACT_TAGS[status];
    return { text: fact.statement || "", inactive: status !== "active", tag: key ? t(key) : (status === "active" ? "" : status) };
  }

  // ---- Brief rows and acknowledgements ------------------------------------------------------------------------

  /** Every row the last turn changed, in view order: [{section, row}]. */
  function changedRows(view) {
    var out = [];
    ((view && view.sections) || []).forEach(function (s) {
      (s.rows || []).forEach(function (r) { if (r.changed) { out.push({ section: s, row: r }); } });
    });
    return out;
  }
  /** A row pulses once per Brief version: the key is the Brief hash plus the row id. */
  function pulseKey(view, row) { return String((view && view.brief_hash) || "") + ":" + row.id; }

  /** Did this EventAck carry a notice with this code (for example edit_unavailable)? */
  function ackHasNotice(ack, code) {
    return !!(ack && (ack.events || []).some(function (ev) {
      var p = ev && ev.payload;
      return !!p && p.type === "notice" && p.code === code;
    }));
  }

  root.compassInterview = {
    OTHER: OTHER, MAX_ANSWERS: MAX_ANSWERS, BUDGETS: BUDGETS,
    budgetCommand: budgetCommand, budgetOfCommand: budgetOfCommand, replaySummary: replaySummary,
    tierRequired: tierRequired, modeCommand: modeCommand, otherMode: otherMode,
    locate: locate, progressLine: progressLine, examplePlaceholder: examplePlaceholder, useForm: useForm,
    pendingGroups: pendingGroups, flatCards: flatCards,
    batchNumbers: batchNumbers, questionLabel: questionLabel, isClosingCard: isClosingCard, isClosingBatch: isClosingBatch,
    emptyEntry: emptyEntry, effectiveType: effectiveType, optionIds: optionIds, canNone: canNone,
    canSelectAll: canSelectAll, showsCustom: showsCustom, entryValue: entryValue, isNone: isNone,
    missingRequired: missingRequired, blankCards: blankCards, handledCount: handledCount, answerFor: answerFor,
    buildAnswers: buildAnswers, buildBatchPayload: buildBatchPayload, summarizeBlanks: summarizeBlanks,
    togglePick: togglePick, allSelected: allSelected, toggleSelectAll: toggleSelectAll, toggleNone: toggleNone,
    movePick: movePick, sanitizeEntry: sanitizeEntry, carryDraft: carryDraft, upsertBatch: upsertBatch,
    sameQuestions: sameQuestions, registerEvent: registerEvent, reconnectDelay: reconnectDelay,
    WAIT: WAIT, waitSchedule: waitSchedule, waitKey: waitKey, phaseFor: phaseFor, phaseContext: phaseContext,
    phaseForStage: phaseForStage, MAX_UPLOAD_BYTES: MAX_UPLOAD_BYTES, UPLOAD_ACCEPT: UPLOAD_ACCEPT,
    checkUpload: checkUpload, uploadErrorKey: uploadErrorKey,
    qaStatus: qaStatus, qaCanEdit: qaCanEdit, qaIndent: qaIndent, qaAnswerText: qaAnswerText,
    qaEditModel: qaEditModel, qaEditPayload: qaEditPayload, qaFactLine: qaFactLine,
    changedRows: changedRows, pulseKey: pulseKey, ackHasNotice: ackHasNotice
  };
})(window);
