/* Compass interview helpers: the pure, DOM-free logic of the AI-planned interview screens (build prompt 02,
   section 2.7). Loaded by the shell before compass.js (the registry entry's "helper" file) and exposed as
   window.compassInterview. Nothing here touches the page, the network or storage, so every function can be
   run under Node (apps/web/tests/test_interview_helpers.py does exactly that).

   Shapes it works on (all from the Compass HTTP API):
     batch   question_batch event: {batch_id, mode, ui_mode, groups: [{id, title, cards: [card]}], total, answered,
             progress?: {tier, target, hard_cap, committed, closing}}   (progress is optional: absent on older servers)
     card    a Card (id, type, question, options, group_id, examples, must_confirm, ...)
     draft   {card_id: entry}; an entry is what the answer-all form has collected for one question
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

  /** The form is used for a batch question when the layout is "answer all", the question is a member of a known
      batch (not a follow-up), and at least two questions of that batch still wait. */
  function useForm(uiMode, loc, pendingCount) {
    return uiMode === "answer_all" && !!loc && !loc.followup && pendingCount >= 2;
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
    return { action: "answer", choice: null, picks: [], fields: {}, touched: false, confirmed: false, other: "", text: "" };
  }

  /** What an entry says for its card: {value, custom}, or null while it is blank. */
  function entryValue(card, e) {
    var type = card.type;
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

  function addressed(card, e) {
    if (!card.must_confirm && (e.action === "skip" || e.action === "you_decide")) { return true; }
    return entryValue(card, e) !== null;
  }
  function entryOf(draft, card) { return draft[card.id] || emptyEntry(); }

  /** Cards that must be answered (risky slots: no skip, no "you decide") and are still blank. */
  function missingRequired(cards, draft) {
    return cards.filter(function (c) { return c.must_confirm && entryValue(c, entryOf(draft, c)) === null; });
  }
  /** Cards that will be sent as skipped because the person left them blank. */
  function blankCards(cards, draft) {
    return cards.filter(function (c) {
      var e = entryOf(draft, c);
      return !c.must_confirm && e.action === "answer" && entryValue(c, e) === null;
    });
  }
  function handledCount(cards, draft) {
    var n = 0;
    cards.forEach(function (c) { if (addressed(c, entryOf(draft, c))) { n += 1; } });
    return n;
  }

  /** The `answers` of a batch_answer event. Blank questions are sent as skipped (never left dangling) except
      those that must be confirmed, which the form refuses to send blank. */
  function buildAnswers(cards, draft) {
    var out = [];
    cards.forEach(function (card) {
      var e = entryOf(draft, card);
      if (!card.must_confirm && (e.action === "skip" || e.action === "you_decide")) {
        out.push({ card_id: card.id, action: e.action, value: null, custom_text: null });
        return;
      }
      var v = entryValue(card, e);
      if (v) { out.push({ card_id: card.id, action: "answer", value: v.value, custom_text: v.custom }); return; }
      if (!card.must_confirm) { out.push({ card_id: card.id, action: "skip", value: null, custom_text: null }); }
    });
    return out.slice(0, MAX_ANSWERS);
  }

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
    emptyEntry: emptyEntry, entryValue: entryValue, missingRequired: missingRequired, blankCards: blankCards,
    handledCount: handledCount, buildAnswers: buildAnswers,
    qaStatus: qaStatus, qaCanEdit: qaCanEdit, qaIndent: qaIndent, qaAnswerText: qaAnswerText,
    qaEditModel: qaEditModel, qaEditPayload: qaEditPayload, qaFactLine: qaFactLine,
    changedRows: changedRows, pulseKey: pulseKey, ackHasNotice: ackHasNotice
  };
})(window);
