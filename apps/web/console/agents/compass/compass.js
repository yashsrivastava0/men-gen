/*CONTRACT
{
  "view_model_keys": [
    "brief_hash", "section_hashes", "changed_since_approval",
    "header", "header.title", "header.idea_line", "header.version", "header.status", "header.status_label",
    "header.readiness", "header.readiness[].label", "header.readiness[].ready",
    "header.readiness[].gaps", "header.reviewed", "header.of_total", "header.degraded",
    "sections", "sections[].id", "sections[].title", "sections[].reviewed", "sections[].empty_text",
    "sections[].rows", "sections[].rows[].id", "sections[].rows[].label",
    "sections[].rows[].value", "sections[].rows[].value.kind", "sections[].rows[].value.text",
    "sections[].rows[].value.items", "sections[].rows[].badges", "sections[].rows[].badges[].kind",
    "sections[].rows[].badges[].text", "sections[].rows[].badges[].icon", "sections[].rows[].tier",
    "sections[].rows[].editable", "sections[].rows[].history_available", "sections[].rows[].needs_review",
    "sections[].rows[].changed", "sections[].rows[].group", "sections[].rows[].status_chip",
    "sections[].rows[].description",
    "footer", "footer.primary_label", "footer.secondary_label", "footer.approvable", "footer.blockers"
  ],
  "card_keys": [
    "id", "type", "question", "why_we_ask", "options", "options[].id", "options[].label",
    "options[].basis", "options[].recommended", "options[].reason", "options[].ticked",
    "custom_input", "custom_input.enabled", "custom_input.placeholder", "custom_input.voice",
    "you_decide", "you_decide.enabled", "skip", "skip.enabled", "progress", "progress.n",
    "progress.of_about", "must_confirm"
  ],
  "stored_event_keys": ["direction", "type", "event_id", "turn_id", "seq", "payload"],
  "card_types": [
    "choose_one", "pick_many", "priority", "confirm", "look_signals", "quick_form", "upload_or_link",
    "open_prompt"
  ],
  "inbound_events": [
    "user_message", "card_answer", "card_skip", "card_you_decide", "card_back", "upload_added", "link_added",
    "command", "brief_edit", "brief_feedback", "assumption_decision", "lookup_edit", "contributor_switch",
    "gate_decision", "batch_answer", "answer_edit"
  ],
  "outbound_events": [
    "assistant_message", "assistant_delta", "card", "question_batch", "playback", "notice", "progress", "brief_patch",
    "brief_ready", "brief_approved", "lookup_preview", "probe_result", "budget_warning", "error"
  ]
}
CONTRACT*/

/* Compass test UI. Vanilla JS, no framework, no network except same-origin. User content is only ever
   written with textContent (helper h / text nodes); this file never assigns markup strings.

   Card answer shapes (value of card_answer):
     choose_one, look_signals -> the option id;   pick_many, priority -> list of option ids (priority: in order);
     confirm, quick_form -> {option_id: text} (options are the rows / fields);   open_prompt -> the text;
     "Something else" on any card -> value null and custom_text = the text.
*/
(function () {
  "use strict";

  var BASE = "/v1/compass";
  var CAP_FALLBACK = "3.00";

  // ---- storage and small helpers -------------------------------------------------------------------
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* storage may be blocked */ } }
  };

  function $(id) { return document.getElementById(id); }

  /** Create an element. attrs: class, text, on (map of listeners), data (dataset), other keys as attributes. */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    var a = attrs || {};
    Object.keys(a).forEach(function (k) {
      var v = a[k];
      if (v === null || v === undefined || v === false) { return; }
      if (k === "class") { el.className = v; }
      else if (k === "text") { el.textContent = String(v); }
      else if (k === "on") { Object.keys(v).forEach(function (ev) { el.addEventListener(ev, v[ev]); }); }
      else if (k === "data") { Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; }); }
      else if (k === "css") { Object.keys(v).forEach(function (p) { el.style[p] = v[p]; }); }
      else if (v === true) { el.setAttribute(k, ""); }
      else { el.setAttribute(k, String(v)); }
    });
    for (var i = 2; i < arguments.length; i++) { appendChild(el, arguments[i]); }
    return el;
  }
  function appendChild(el, c) {
    if (c === null || c === undefined || c === false) { return; }
    if (Array.isArray(c)) { c.forEach(function (x) { appendChild(el, x); }); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  function clear(el) { el.replaceChildren(); }
  function randomId() {
    var a = new Uint8Array(8);
    window.crypto.getRandomValues(a);
    var s = "";
    for (var i = 0; i < a.length; i++) { s += ("0" + a[i].toString(16)).slice(-2); }
    return "ui-" + s;
  }

  // ---- strings (UI chrome from /strings/{lang}; embedded English fallback) -----------------------------
  var FALLBACK = {
    "ui.start_new_project": "Start a new project", "ui.your_projects": "Your projects",
    "ui.mode_guided": "Guided", "ui.mode_company": "Company", "ui.back": "Back", "ui.undo": "Undo",
    "ui.skip": "Skip for now", "ui.you_decide": "You decide", "ui.something_else": "Something else",
    "ui.continue": "Continue", "ui.looks_right": "Looks right", "ui.change_something": "Change something",
    "ui.id_change_this": "I'd change this", "ui.show_chat_history": "Show chat history",
    "ui.where_from": "Where did this come from?", "ui.updated_tag": "Updated",
    "ui.undo_toast": "Change saved. Undo?", "ui.send": "Send", "ui.type_here": "Type your answer here",
    "ui.mic": "Speak your answer", "ui.upload": "Upload a file", "ui.spent_cap": "Spent {spent} of {cap}",
    "ui.brief_tab": "Brief", "ui.language": "Language", "ui.mode": "Mode", "ui.typing": "Compass is thinking",
    "progress.question_n_of_about": "Question {n} of about {total}",
    "card.why_we_ask": "Why we ask: {reason}", "card.assumed_collapsed": "Assumed: {value}",
    "card.custom_placeholder": "Something else: type it, or tap the mic",
    "badge.you_said": "You said", "badge.from_document": "From your document", "badge.assumed": "Assumed",
    "badge.typical": "Typical for apps like this", "badge.suggestion": "Our suggestion",
    "badge.icon.you_said": "◆", "badge.icon.from_document": "■", "badge.icon.assumed": "▲",
    "badge.icon.typical": "●", "badge.icon.suggestion": "○",
    "change.reason.not_what_i_meant": "Not what I meant", "change.reason.too_much": "Too much",
    "change.reason.something_missing": "Something is missing", "change.reason.wrong_tone": "Wrong tone",
    "change.reason.dont_want_assumption": "I don't want this assumption",
    "change.reason.free_text": "Tell us in your own words",
    "change.updated": "Updated: {where} › {what}", "change.changing_chip": "Changing: {where}",
    "notice.picked_up": "I picked up: {items}",
    "lookup.title": "What we will look up for you", "lookup.add_chip": "Add something to look up",
    "lookup.remove_chip": "Remove from the list", "history.title": "Chat history",
    "sources.title": "Sources read", "dev.title": "Developer tools", "dev.mock": "Mock mode",
    "dev.live": "Live mode", "error.generic": "Something went wrong on our side. Please try again.",
    "error.rate_limited": "Too many requests at once. Please wait a moment and try again.",
    "welcome.start_describe": "Describe my idea", "welcome.start_upload": "Upload my documents",
    "welcome.start_like_app": "Start from an app I like", "welcome.start_just_build": "Just build it",
    "status.draft": "Draft", "status.in_review": "In review", "status.approved": "Approved",
    // keys below are UI-only (not in the catalog); English only
    "x.tagline": "Tell me about your app idea. I will ask a few simple questions and write a plan.",
    "x.name": "Project name (optional)", "x.no_projects": "No projects yet.", "x.settings": "Settings",
    "x.theme": "Theme", "x.developer": "Developer", "x.chat": "Chat", "x.brief": "Brief",
    "x.lookup_tab": "What we will look up", "x.sources_tab": "Sources", "x.history_tab": "History",
    "x.edit": "Edit", "x.save": "Save", "x.cancel": "Cancel", "x.accept": "Accept", "x.change": "Change",
    "x.not_decided": "Not decided yet", "x.needs_decision": "Needs your decision",
    "x.reviewed_n_of_m": "Reviewed {n} of {m} sections", "x.version": "Version {n}",
    "x.send_feedback": "Send", "x.no_history": "No messages yet.", "x.search": "Search the chat",
    "x.upload_unavailable": "That file could not be read. You can describe it in your own words or add a link.",
    "x.add_link": "Add link", "x.link_placeholder": "https://", "x.move_up": "Move up", "x.move_down": "Move down",
    "x.lookup_add_placeholder": "Something else to look up", "x.add": "Add", "x.open": "Open",
    "x.edit_hint": "One item per line", "x.approved_toast": "Approved. Thank you.",
    "x.changed_since_approval": "Changed since approval: {sections}", "x.you": "You", "x.assistant": "Compass",
    "x.session_state": "State", "x.close": "Close", "x.events": "Events", "x.state": "State and plan",
    "x.calls": "Model calls", "x.slots": "Slot table", "x.no_calls": "No model calls yet.",
    "x.speaking_as": "Speaking as (contributor id)", "x.stream_on": "Stream connected",
    "x.stream_off": "Stream reconnecting", "x.provenance_none": "No details recorded for this item.",
    "x.value_history": "Earlier values", "x.dev_none": "Nothing yet.", "x.brief_empty": "Your Brief will appear here as we talk.",
    "x.step_check": "Check", "x.back_to_chat": "Back to chat", "x.tier": "Tier"
  };

  var state = {
    cfg: { token: "dev-token", tenant: "demo", user: "demo-user", roles: "" },
    lang: "en", mode: "guided", strings: {}, theme: null,
    sessionId: null, session: null, lastSeq: 0, seen: {}, devEvents: [], devTab: "events", devOpen: false,
    view: null, viewJson: "", lookups: [], progress: null, tab: "brief", streamAbort: null, streamOk: false,
    activeCard: null, typingCount: 0, loadingView: false, viewTimer: null, budget: null, calls: [],
    ui: { panel: null, edit: null, prov: {} }, deltaEl: null, lastProbe: [], contributor: "", batch: null
  };

  function t(key, vars) {
    var s = state.strings[key];
    if (s === undefined) { s = FALLBACK[key]; }
    if (s === undefined) { s = key; }
    if (vars) { s = s.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] !== undefined ? String(vars[k]) : m; }); }
    return s;
  }

  // ---- API -------------------------------------------------------------------------------------------
  function authHeaders() {
    var hd = {
      "Authorization": "Bearer " + state.cfg.token, "X-Tenant-Id": state.cfg.tenant, "X-User-Id": state.cfg.user
    };
    if (state.cfg.roles) { hd["X-Roles"] = state.cfg.roles; }
    return hd;
  }
  function ApiProblem(status, code, message) {
    var e = new Error(message || code);
    e.status = status; e.code = code; e.apiMessage = message;
    return e;
  }
  function api(method, path, body) {
    var headers = authHeaders();
    var init = { method: method, headers: headers };
    if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    return fetch(BASE + path, init).then(function (res) {
      return res.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        if (!res.ok) {
          throw ApiProblem(res.status, data && data.code ? data.code : "http_" + res.status,
            data && data.message ? data.message : "");
        }
        return data;
      });
    });
  }
  function friendly(err) {
    if (err && err.status === 429) { return t("error.rate_limited"); }
    if (err && err.apiMessage) { return err.apiMessage; }
    return t("error.generic");
  }

  // ---- chat rendering --------------------------------------------------------------------------------
  function log() { return $("chat-log"); }
  function scrollChat() { var l = log(); l.scrollTop = l.scrollHeight; }
  function addMsg(kind, text, chips, extraClass) {
    var el = h("div", { class: "msg msg-" + kind + (extraClass ? " " + extraClass : "") });
    el.appendChild(document.createTextNode(text));
    if (chips && chips.length) { el.appendChild(chipList(chips)); }
    log().appendChild(el);
    scrollChat();
    return el;
  }
  function chipList(items) {
    return h("ul", { class: "chips" }, items.map(function (c) { return h("li", { class: "chip", text: c }); }));
  }
  function setTyping(on) {
    state.typingCount = Math.max(0, state.typingCount + (on ? 1 : -1));
    $("typing").hidden = state.typingCount === 0;
    if (state.typingCount) { scrollChat(); }
  }

  function basisKind(basis) {
    return { your_document: "from_document", typical: "typical", suggestion: "suggestion", your_words: "you_said" }[basis] || "suggestion";
  }
  function basisLabel(basis) {
    var k = basisKind(basis);
    return h("span", { class: "basis" },
      h("span", { "aria-hidden": "true", text: t("badge.icon." + k) }), h("span", { text: t("badge." + k) }));
  }

  function collapseActive(summary) {
    var a = state.activeCard;
    if (!a) { return; }
    clear(a.el);
    a.el.className = "qcard done";
    a.el.appendChild(h("div", { class: "small muted", text: summary || a.card.question }));
    state.activeCard = null;
    $("chat-actions").replaceChildren();
  }

  function cardSummary(card, value, custom) {
    if (custom) { return custom; }
    var opts = card.options || [];
    function label(id) {
      for (var i = 0; i < opts.length; i++) { if (opts[i].id === id) { return opts[i].label; } }
      return String(id);
    }
    if (Array.isArray(value)) { return value.map(label).join(", "); }
    if (value && typeof value === "object") {
      var keys = Object.keys(value).filter(function (k) { return String(value[k]).trim() !== ""; });
      if (card.type === "confirm") { return keys.map(function (k) { return value[k]; }).join("; "); }
      return keys.map(function (k) { return label(k) + ": " + value[k]; }).join("; ");
    }
    if (typeof value === "string") {
      for (var i = 0; i < opts.length; i++) { if (opts[i].id === value) { return opts[i].label; } }
      return value;
    }
    return "";
  }

  function answer(card, value, custom) {
    var summary = cardSummary(card, value, custom);
    addMsg("user", summary || t("ui.continue"));
    collapseActive(summary ? card.question + " — " + summary : card.question);
    var body = { type: "card_answer", card_id: card.id, value: value === undefined ? null : value };
    if (custom) { body.custom_text = custom; }
    sendEvent(body);
  }
  // "Answer all at once": one event with the answers of a whole batch form (phase 5 renders the form).
  function sendBatchAnswer(batchId, answers) {
    return sendEvent({ type: "batch_answer", batch_id: batchId, answers: answers });
  }
  // Edit an earlier answer from the Questions-and-answers list (phase 5): a new answer version and a replan.
  function sendAnswerEdit(cardId, value, custom) {
    var body = { type: "answer_edit", card_id: cardId, value: value === undefined ? null : value };
    if (custom) { body.custom_text = custom; }
    return sendEvent(body);
  }
  window.compassSend = { batchAnswer: sendBatchAnswer, answerEdit: sendAnswerEdit };
  function micSupported() { return !!(window.SpeechRecognition || window.webkitSpeechRecognition); }
  function startDictation(target, done) {
    var Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Rec) { return; }
    var r = new Rec();
    r.lang = state.lang === "hi" ? "hi-IN" : "en-US";
    r.interimResults = false;
    r.onresult = function (e) {
      var text = "";
      for (var i = 0; i < e.results.length; i++) { text += e.results[i][0].transcript; }
      target.value = (target.value ? target.value + " " : "") + text;
      if (done) { done(); }
    };
    r.start();
  }

  function renderCard(card) {
    var el = h("div", { class: "qcard", role: "group", "aria-label": card.question });
    var body = h("div", { class: "options-wrap" });
    var type = card.type;
    var showCustom = !!(card.custom_input && card.custom_input.enabled) && type !== "open_prompt";
    el.appendChild(h("div", { class: "q", text: card.question }));
    if (card.progress) {
      el.appendChild(h("div", { class: "hint",
        text: t("progress.question_n_of_about", { n: card.progress.n, total: card.progress.of_about }) }));
    }
    if (card.why_we_ask) {
      el.appendChild(h("details", null, h("summary", { text: t("card.why_we_ask", { reason: "" }).replace(/[:\s]+$/, "") }),
        h("div", { text: card.why_we_ask })));
    }
    el.appendChild(body);
    var builders = {
      choose_one: cardChooseOne, pick_many: cardPickMany, priority: cardPriority, confirm: cardConfirm,
      look_signals: cardLook, quick_form: cardForm, upload_or_link: cardUpload, open_prompt: cardOpen
    };
    (builders[type] || cardOpen)(card, body);
    if (showCustom) {
      var input = h("input", { type: "text", "aria-label": t("ui.something_else"),
        placeholder: (card.custom_input.placeholder || t("card.custom_placeholder")) });
      var go = h("button", { class: "btn", type: "button", text: t("ui.send"), on: { click: function () {
        var v = input.value.trim();
        if (v) { answer(card, null, v); }
      } } });
      var row = h("div", { class: "custom-row" }, input, go);
      if (card.custom_input.voice && micSupported()) {
        row.appendChild(h("button", { class: "btn btn-quiet", type: "button", "aria-label": t("ui.mic"), text: "🎤",
          on: { click: function () { startDictation(input); } } }));
      }
      input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); go.click(); } });
      el.appendChild(row);
    }
    return el;
  }

  function optionButton(card, o, role, onClick) {
    var btn = h("button", { class: "option", type: "button", role: role, "aria-checked": role ? "false" : null, on: { click: onClick } },
      h("span", { class: "option-main" },
        h("span", { class: "option-label" }, o.label, o.recommended ? h("span", { class: "rec", text: "★ Recommended" }) : null),
        basisLabel(o.basis),
        o.reason ? h("span", { class: "option-reason", text: o.reason }) : null));
    return btn;
  }
  function cardChooseOne(card, body) {
    var list = h("div", { class: "options", role: "radiogroup", "aria-label": card.question });
    card.options.forEach(function (o) {
      list.appendChild(optionButton(card, o, "radio", function () { answer(card, o.id); }));
    });
    body.appendChild(list);
  }
  function cardPickMany(card, body) {
    var picked = {};
    card.options.forEach(function (o) { picked[o.id] = !!o.ticked; });
    var list = h("div", { class: "options", role: "group", "aria-label": card.question });
    card.options.forEach(function (o) {
      var btn = optionButton(card, o, "checkbox", function () {
        picked[o.id] = !picked[o.id];
        btn.setAttribute("aria-checked", picked[o.id] ? "true" : "false");
      });
      btn.setAttribute("aria-checked", picked[o.id] ? "true" : "false");
      list.appendChild(btn);
    });
    body.appendChild(list);
    body.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary", type: "button", text: t("ui.continue"),
      on: { click: function () {
        answer(card, card.options.filter(function (o) { return picked[o.id]; }).map(function (o) { return o.id; }));
      } } })));
  }
  function cardPriority(card, body) {
    var order = card.options.slice();
    var list = h("div", { class: "options", role: "list" });
    function paint() {
      clear(list);
      order.forEach(function (o, i) {
        list.appendChild(h("div", { class: "rank", role: "listitem" },
          h("span", { class: "rank-n", text: String(i + 1) }),
          h("span", { class: "option-label", text: o.label }),
          h("button", { class: "btn btn-quiet", type: "button", "aria-label": t("x.move_up") + ": " + o.label, text: "↑",
            disabled: i === 0, on: { click: function () { swap(i, i - 1); } } }),
          h("button", { class: "btn btn-quiet", type: "button", "aria-label": t("x.move_down") + ": " + o.label, text: "↓",
            disabled: i === order.length - 1, on: { click: function () { swap(i, i + 1); } } })));
      });
    }
    function swap(a, b) { var x = order[a]; order[a] = order[b]; order[b] = x; paint(); }
    paint();
    body.appendChild(list);
    body.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary", type: "button", text: t("ui.continue"),
      on: { click: function () { answer(card, order.map(function (o) { return o.id; })); } } })));
  }
  function textFields(card, body, withHint) {
    var inputs = {};
    card.options.forEach(function (o) {
      var input = h("input", { type: "text", value: withHint ? "" : o.label, placeholder: withHint ? (o.reason || "") : "" });
      input.value = withHint ? "" : o.label;
      inputs[o.id] = input;
      body.appendChild(h("label", { class: "field" }, h("span", { text: o.label }), input));
    });
    body.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary", type: "button", text: t("ui.continue"),
      on: { click: function () {
        var out = {};
        Object.keys(inputs).forEach(function (k) { out[k] = inputs[k].value.trim(); });
        answer(card, out);
      } } })));
  }
  function cardConfirm(card, body) {
    card.options.forEach(function (o) {
      var input = h("input", { type: "text", "aria-label": o.label });
      input.value = o.label;
      input.dataset.id = o.id;
      body.appendChild(h("div", { class: "field" }, input, basisLabel(o.basis)));
    });
    body.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary", type: "button", text: t("ui.looks_right"),
      on: { click: function () {
        var out = {};
        body.querySelectorAll("input").forEach(function (i) { out[i.dataset.id] = i.value.trim(); });
        answer(card, out);
      } } })));
  }
  function cardForm(card, body) { textFields(card, body, true); }
  function cardLook(card, body) {
    var grid = h("div", { class: "look-grid", role: "group", "aria-label": card.question });
    card.options.forEach(function (o, i) {
      grid.appendChild(h("button", { class: "look", type: "button", "aria-pressed": "false", on: { click: function () { answer(card, o.id); } } },
        h("span", { class: "swatch swatch-" + (i % 3), "aria-hidden": "true" }),
        h("span", { class: "option-label", text: o.label }),
        o.reason ? h("span", { class: "option-reason", text: o.reason }) : null,
        basisLabel(o.basis)));
    });
    body.appendChild(grid);
  }
  function cardUpload(card, body) {
    var note = h("p", { class: "hint", role: "status" });
    var file = h("input", { type: "file", class: "sr-only", "aria-label": t("ui.upload"), tabindex: "-1" });
    file.addEventListener("change", function () {
      if (file.files && file.files.length) { uploadFile(file.files[0], note); }
    });
    var link = h("input", { type: "url", placeholder: t("x.link_placeholder"), "aria-label": t("x.add_link") });
    body.appendChild(h("div", { class: "row-actions" },
      h("button", { class: "btn", type: "button", text: t("ui.upload"), on: { click: function () { file.click(); } } }), file));
    body.appendChild(h("div", { class: "custom-row" }, link, h("button", { class: "btn", type: "button", text: t("x.add_link"), on: { click: function () {
      var u = link.value.trim();
      if (u) { addMsg("user", u); collapseActive(card.question + " — " + u); sendEvent({ type: "link_added", url: u }); }
    } } })));
    body.appendChild(note);
  }
  function cardOpen(card, body) {
    var ta = h("textarea", { rows: "3", "aria-label": card.question, placeholder: (card.custom_input && card.custom_input.placeholder) || t("ui.type_here") });
    var send = h("button", { class: "btn btn-primary", type: "button", text: t("ui.send"), on: { click: function () {
      var v = ta.value.trim();
      if (v) { answer(card, v); }
    } } });
    var row = h("div", { class: "row-actions" }, send);
    if (micSupported()) {
      row.appendChild(h("button", { class: "btn btn-quiet", type: "button", "aria-label": t("ui.mic"), text: "🎤", on: { click: function () { startDictation(ta); } } }));
    }
    body.appendChild(ta);
    body.appendChild(row);
  }

  function renderChatActions(card) {
    var bar = $("chat-actions");
    clear(bar);
    function act(label, fn) { bar.appendChild(h("button", { class: "btn", type: "button", text: label, on: { click: fn } })); }
    act(t("ui.back"), function () { sendEvent({ type: "card_back" }); });
    act(t("ui.undo"), function () { sendEvent({ type: "command", name: "undo" }); });
    if (card && !card.must_confirm) {
      if (card.skip && card.skip.enabled) {
        act(t("ui.skip"), function () { collapseActive(card.question + " — " + t("ui.skip")); sendEvent({ type: "card_skip", card_id: card.id }); });
      }
      if (card.you_decide && card.you_decide.enabled) {
        act(t("ui.you_decide"), function () {
          addMsg("system", t("ui.you_decide"));
          collapseActive(card.question + " — " + t("ui.you_decide"));
          sendEvent({ type: "card_you_decide", card_id: card.id });
        });
      }
    }
  }

  // ---- outbound events ---------------------------------------------------------------------------------
  var OUT = {
    assistant_message: function (p, replay) {
      state.deltaEl = null;
      addMsg("assistant", p.text, p.chips || []);
      if (!replay && state.session && state.session.state === "WELCOME") { /* start buttons stay available */ }
    },
    assistant_delta: function (p) {
      if (!state.deltaEl) { state.deltaEl = addMsg("assistant", ""); }
      state.deltaEl.appendChild(document.createTextNode(p.text));
      scrollChat();
    },
    card: function (p) {
      collapseActive();
      var el = renderCard(p.card);
      log().appendChild(el);
      state.activeCard = { card: p.card, el: el };
      renderChatActions(p.card);
      scrollChat();
    },
    playback: function (p) {
      var el = h("div", { class: "qcard" }, h("div", { class: "q", text: p.title }));
      var inputs = {};
      (p.items || []).forEach(function (it) {
        var input = h("input", { type: "text", "aria-label": it.label });
        input.value = it.value === null || it.value === undefined ? "" : (typeof it.value === "string" ? it.value : JSON.stringify(it.value));
        input.dataset.original = input.value;
        inputs[it.key] = input;
        el.appendChild(h("label", { class: "field" }, h("span", { text: it.label }), input, basisLabel(it.basis)));
      });
      if (p.unclear && p.unclear.length) { el.appendChild(h("div", { class: "hint", text: "?" }, " ", p.unclear.join(", "))); }
      el.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary", type: "button", text: t("x.save"), on: { click: function () {
        Object.keys(inputs).forEach(function (k) {
          if (inputs[k].value !== inputs[k].dataset.original) {
            sendEvent({ type: "brief_edit", item_id: k, value: inputs[k].value });
            inputs[k].dataset.original = inputs[k].value;
          }
        });
      } } })));
      log().appendChild(el);
      scrollChat();
    },
    question_batch: function (p) { state.batch = p; },
    notice: function (p) {
      addMsg("system", p.text, p.chips || [], p.severity === "warning" ? "msg-warning" : "");
    },
    progress: function (p) {
      state.progress = p;
      var bar = $("progressbar");
      if (p.n && p.of_about) {
        bar.hidden = false;
        clear(bar);
        bar.appendChild(h("div", { text: t("progress.question_n_of_about", { n: p.n, total: p.of_about }) }));
        bar.appendChild(h("div", { class: "progress-track" }, h("div", { class: "progress-fill", css: { width: Math.min(100, Math.round(100 * p.n / p.of_about)) + "%" } })));
      }
    },
    brief_patch: function (p, replay) {
      addMsg("system", p.summary || t("change.updated", { where: "", what: "" }));
      if (!replay && p.undoable) { toast(p.summary || t("ui.undo_toast"), t("ui.undo"), function () { sendEvent({ type: "command", name: "undo" }); }); }
      scheduleView();
    },
    brief_ready: function () { scheduleView(); },
    brief_approved: function (p) {
      addMsg("system", t("x.approved_toast") + " (v" + p.version + ")");
      scheduleView();
    },
    lookup_preview: function (p) { state.lookups = p.requests || []; renderLookup(); scheduleView(); },
    probe_result: function (p) { state.lastProbe.push(p); if (state.lastProbe.length > 6) { state.lastProbe.shift(); } renderDev(); },
    budget_warning: function (p) {
      addMsg("system", (p.note || "") + " " + t("ui.spent_cap", { spent: "$" + p.spent_usd, cap: "$" + p.cap_usd }), [], "msg-warning");
      loadBudget();
    },
    error: function (p) {
      var el = addMsg("error", p.message || t("error.generic"));
      el.setAttribute("role", "alert");
    }
  };

  function onStored(ev, replay) {
    if (!ev || typeof ev !== "object") { return; }
    if (ev.direction === "out" && ev.seq !== null && ev.seq !== undefined) {
      if (state.seen[ev.seq]) { return; }
      state.seen[ev.seq] = true;
      state.lastSeq = Math.max(state.lastSeq, ev.seq);
    }
    state.devEvents.push(ev);
    if (state.devEvents.length > 300) { state.devEvents.shift(); }
    if (ev.direction === "out") {
      var fn = OUT[ev.payload && ev.payload.type ? ev.payload.type : ev.type];
      if (fn) { fn(ev.payload, !!replay); }
    }
    if (state.devOpen) { renderDev(); }
  }

  function processAck(ack) {
    (ack.events || []).forEach(function (ev) { onStored(ev, false); });
    refreshSideData();
  }

  var IN_FLIGHT = 0;
  function sendEvent(body) {
    if (!state.sessionId) { return Promise.resolve(); }
    var ev = Object.assign({ event_id: randomId() }, body);
    if (state.contributor && !ev.contributor_id) { ev.contributor_id = state.contributor; }
    IN_FLIGHT += 1;
    setTyping(true);
    return api("POST", "/sessions/" + state.sessionId + "/events", ev).then(processAck).catch(function (err) {
      var el = addMsg("error", friendly(err));
      el.setAttribute("role", "alert");
    }).then(function () { IN_FLIGHT -= 1; setTyping(false); });
  }

  // ---- upload: one multipart request; the reply is an EventAck, handled like any other event -------------
  function uploadFile(file, note) {
    if (!state.sessionId || !file) { return Promise.resolve(); }
    var fd = new FormData();
    fd.append("file", file);
    IN_FLIGHT += 1;
    setTyping(true);
    if (note) { note.textContent = file.name; }
    addMsg("user", file.name);
    return fetch(BASE + "/sessions/" + state.sessionId + "/uploads", { method: "POST", headers: authHeaders(), body: fd })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) { throw ApiProblem(res.status, data && data.code, data && data.message); }
          return data;
        });
      })
      .then(processAck)
      .catch(function (err) {
        var el = addMsg("error", err && err.apiMessage ? err.apiMessage : t("x.upload_unavailable"));
        el.setAttribute("role", "alert");
      })
      .then(function () { IN_FLIGHT -= 1; setTyping(false); });
  }
  function pickFile() {
    var input = h("input", { type: "file", class: "sr-only", tabindex: "-1", "aria-label": t("ui.upload") });
    input.addEventListener("change", function () { if (input.files && input.files.length) { uploadFile(input.files[0], null); } });
    document.body.appendChild(input);
    input.click();
    setTimeout(function () { if (input.parentNode) { input.parentNode.removeChild(input); } }, 60000);
  }

  // ---- stream (fetch + reader: EventSource cannot set headers) ------------------------------------------
  function startStream() {
    stopStream();
    var ctl = new AbortController();
    state.streamAbort = ctl;
    var sid = state.sessionId;
    function connect() {
      if (state.streamAbort !== ctl) { return; }
      var headers = authHeaders();
      headers["Last-Event-ID"] = String(state.lastSeq);
      headers["Accept"] = "text/event-stream";
      fetch(BASE + "/sessions/" + sid + "/stream?after=" + state.lastSeq, { headers: headers, signal: ctl.signal })
        .then(function (res) {
          if (!res.ok || !res.body) { throw new Error("stream " + res.status); }
          state.streamOk = true;
          renderDevHead();
          return pump(res.body.getReader());
        })
        .catch(function () { /* fall through to reconnect */ })
        .then(function () {
          state.streamOk = false;
          renderDevHead();
          if (state.streamAbort === ctl) { window.setTimeout(connect, 3000); }
        });
    }
    function pump(reader) {
      var dec = new TextDecoder();
      var buf = "";
      function step() {
        return reader.read().then(function (r) {
          if (r.done) { return null; }
          buf += dec.decode(r.value, { stream: true });
          var idx;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            handleFrame(buf.slice(0, idx));
            buf = buf.slice(idx + 2);
          }
          return step();
        });
      }
      return step();
    }
    function handleFrame(frame) {
      var data = [];
      frame.split("\n").forEach(function (line) {
        if (line.indexOf("data:") === 0) { data.push(line.slice(5).replace(/^ /, "")); }
      });
      if (!data.length) { return; }
      try { onStored(JSON.parse(data.join("\n")), false); } catch (e) { /* ignore a malformed frame */ }
    }
    connect();
  }
  function stopStream() {
    if (state.streamAbort) { state.streamAbort.abort(); state.streamAbort = null; }
    state.streamOk = false;
  }

  // ---- toasts -------------------------------------------------------------------------------------------
  function toast(text, actionLabel, action) {
    var el = h("div", { class: "toast" }, h("span", { text: text }));
    if (actionLabel) {
      el.appendChild(h("button", { type: "button", text: actionLabel, on: { click: function () { el.remove(); action(); } } }));
    }
    $("toasts").appendChild(el);
    window.setTimeout(function () { el.remove(); }, 10000);
  }

  // ---- brief view ---------------------------------------------------------------------------------------
  function scheduleView() {
    if (state.viewTimer) { return; }
    state.viewTimer = window.setTimeout(function () { state.viewTimer = null; loadView(); }, 60);
  }
  function loadView() {
    if (!state.sessionId) { return Promise.resolve(); }
    return api("GET", "/sessions/" + state.sessionId + "/brief/view?lang=" + encodeURIComponent(state.lang)).then(function (vm) {
      var json = JSON.stringify(vm);
      state.view = vm;
      if (json !== state.viewJson) { state.viewJson = json; renderBrief(); renderSources(); renderDev(); }
    }).catch(function () {
      if (!state.view) { renderBrief(); }
    });
  }

  function sectionRows(id) {
    if (!state.view) { return []; }
    for (var i = 0; i < state.view.sections.length; i++) {
      if (state.view.sections[i].id === id) { return state.view.sections[i].rows; }
    }
    return [];
  }

  function badgeEl(b) {
    return h("span", { class: "badge badge-" + b.kind }, h("span", { "aria-hidden": "true", text: b.icon }), h("span", { text: b.text }));
  }

  function valueEl(v) {
    if (!v || v.kind === "empty") { return h("span", { class: "muted", text: t("x.not_decided") }); }
    if (v.kind === "chips") { return chipList(v.items || []); }
    if (v.kind === "list") { return h("ul", null, (v.items || []).map(function (x) { return h("li", { text: x }); })); }
    return h("span", { text: v.text });
  }

  function whereLabel(section, row) { return section.title + (row ? " › " + row.label : ""); }

  function openPanel(target, label, section, row) {
    state.ui.panel = { target: target, label: label, reason: "not_what_i_meant", text: "" };
    state.ui.edit = null;
    renderBrief();
    var ta = document.querySelector("[data-autofocus]");
    if (ta) { ta.focus(); }
  }

  var REASONS = ["not_what_i_meant", "too_much", "something_missing", "wrong_tone", "dont_want_assumption", "other"];
  function panelEl() {
    var p = state.ui.panel;
    var group = h("div", { class: "reasons", role: "group", "aria-label": t("ui.id_change_this") });
    REASONS.forEach(function (r) {
      var key = r === "other" ? "change.reason.free_text" : "change.reason." + r;
      group.appendChild(h("button", { class: "reason", type: "button", "aria-pressed": p.reason === r ? "true" : "false", text: t(key),
        on: { click: function () { p.reason = r; renderBrief(); } } }));
    });
    var ta = h("textarea", { rows: "2", "aria-label": t("change.reason.free_text"), "data-autofocus": "1" });
    ta.value = p.text;
    ta.addEventListener("input", function () { p.text = ta.value; });
    return h("div", { class: "panel" }, h("h4", { text: t("change.changing_chip", { where: p.label }) }), group, ta,
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary", type: "button", text: t("x.send_feedback"), on: { click: function () {
          var text = p.text.trim();
          addMsg("system", t("change.changing_chip", { where: p.label }), [], "chip chip-changing");
          if (text) { addMsg("user", text); }
          sendEvent({ type: "brief_feedback", target: p.target, reason: p.reason, text: text });
          state.ui.panel = null;
          renderBrief();
          setPane("chat");
        } } }),
        h("button", { class: "btn", type: "button", text: t("x.cancel"), on: { click: function () { state.ui.panel = null; renderBrief(); } } })));
  }

  function editEl(row) {
    var e = state.ui.edit;
    var multi = row.value && (row.value.kind === "chips" || row.value.kind === "list");
    var field = multi ? h("textarea", { rows: "3", "aria-label": row.label, "data-autofocus": "1" }) : h("input", { type: "text", "aria-label": row.label, "data-autofocus": "1" });
    field.value = e.text;
    field.addEventListener("input", function () { e.text = field.value; });
    function save() {
      var value = multi ? e.text.split("\n").map(function (x) { return x.trim(); }).filter(Boolean) : e.text.trim();
      state.ui.edit = null;
      if (row.id.indexOf("assumptions.") === 0) {
        sendEvent({ type: "assumption_decision", id: row.id.split(".")[1], decision: "change", value: value });
      } else {
        sendEvent({ type: "brief_edit", item_id: row.id, value: value });
      }
      renderBrief();
    }
    if (!multi) { field.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); save(); } }); }
    return h("div", { class: "panel" }, field, multi ? h("span", { class: "hint", text: t("x.edit_hint") }) : null,
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary", type: "button", text: t("x.save"), on: { click: save } }),
        h("button", { class: "btn", type: "button", text: t("x.cancel"), on: { click: function () { state.ui.edit = null; renderBrief(); } } })));
  }

  function provEl(id) {
    var d = state.ui.prov[id];
    var box = h("div", { class: "panel", role: "region", "aria-label": t("ui.where_from") });
    if (d === "loading") { box.appendChild(h("span", { class: "muted", text: "…" })); return box; }
    if (!d) { return null; }
    if (d.error) { box.appendChild(h("span", { text: d.error })); return box; }
    box.appendChild(h("div", null, h("strong", { text: t("x.tier") + ": " }), d.tier + " · " + d.status));
    if (!d.provenance || !d.provenance.length) { box.appendChild(h("span", { class: "muted", text: t("x.provenance_none") })); }
    (d.provenance || []).forEach(function (p) {
      var line = [p.type, p.who, p.date ? String(p.date).slice(0, 10) : "", p.ref].filter(Boolean).join(" · ");
      box.appendChild(h("div", null, h("div", { class: "small muted", text: line }), p.quote ? h("div", { class: "quote", text: "“" + p.quote + "”" }) : null));
    });
    if (d.value_history && d.value_history.length) {
      box.appendChild(h("div", { class: "small muted", text: t("x.value_history") }));
      box.appendChild(h("ul", { class: "prov-list" }, d.value_history.map(function (v) {
        return h("li", { text: typeof v.value === "string" ? v.value : JSON.stringify(v.value) });
      })));
    }
    box.appendChild(h("button", { class: "btn btn-link", type: "button", text: t("ui.show_chat_history"), on: { click: function () { openHistoryDrawer(id, d.turn_ids || []); } } }));
    return box;
  }

  function toggleProvenance(id) {
    if (state.ui.prov[id]) { delete state.ui.prov[id]; renderBrief(); return; }
    state.ui.prov[id] = "loading";
    renderBrief();
    api("GET", "/sessions/" + state.sessionId + "/provenance/" + id.split("/").map(encodeURIComponent).join("/")).then(function (d) {
      state.ui.prov[id] = d; renderBrief();
    }).catch(function (e) { state.ui.prov[id] = { error: friendly(e) }; renderBrief(); });
  }

  function rowEl(section, row) {
    var isAssumption = row.id.indexOf("assumptions.") === 0;
    var el = h("div", { class: "brow" + (row.changed ? " changed" : "") + (row.needs_review ? " review" : ""), role: "group", "aria-label": row.label });
    var top = h("div", { class: "brow-top" },
      h("span", { class: "brow-label", text: row.label }),
      h("span", { class: "brow-value" }, valueEl(row.value)));
    el.appendChild(top);
    var meta = h("div", { class: "brow-top" });
    (row.badges || []).forEach(function (b) { meta.appendChild(badgeEl(b)); });
    if (row.status_chip) { meta.appendChild(h("span", { class: "chip", text: row.status_chip })); }
    if (row.changed) { meta.appendChild(h("span", { class: "tag-updated", text: t("ui.updated_tag") })); }
    if (row.needs_review) { meta.appendChild(h("span", { class: "tag-review", text: "⚠ " + t("x.needs_decision") })); }
    el.appendChild(meta);
    if (row.description) { el.appendChild(h("div", { class: "small muted", text: row.description })); }
    var bar = h("div", { class: "row-actions-bar" });
    if (isAssumption) {
      bar.appendChild(h("button", { class: "btn-link", type: "button", text: t("x.accept"), on: { click: function () {
        sendEvent({ type: "assumption_decision", id: row.id.split(".")[1], decision: "accept" });
      } } }));
    }
    if (row.editable) {
      bar.appendChild(h("button", { class: "btn-link", type: "button", text: isAssumption ? t("x.change") : t("x.edit"), on: { click: function () {
        var v = row.value || {};
        state.ui.edit = { id: row.id, text: v.kind === "chips" || v.kind === "list" ? (v.items || []).join("\n") : (v.text || "") };
        state.ui.panel = null;
        renderBrief();
        var f = document.querySelector("[data-autofocus]");
        if (f) { f.focus(); }
      } } }));
    }
    bar.appendChild(h("button", { class: "btn-link", type: "button", text: t("ui.id_change_this"), on: { click: function () { openPanel(row.id, whereLabel(section, row), section, row); } } }));
    if (row.history_available) {
      bar.appendChild(h("button", { class: "btn-link", type: "button", "aria-expanded": state.ui.prov[row.id] ? "true" : "false", text: t("ui.where_from"), on: { click: function () { toggleProvenance(row.id); } } }));
      bar.appendChild(h("button", { class: "btn-link", type: "button", text: t("ui.show_chat_history"), on: { click: function () { openHistoryDrawer(row.id, []); } } }));
    }
    el.appendChild(bar);
    if (state.ui.edit && state.ui.edit.id === row.id) { el.appendChild(editEl(row)); }
    if (state.ui.panel && state.ui.panel.target === row.id) { el.appendChild(panelEl()); }
    var prov = state.ui.prov[row.id] ? provEl(row.id) : null;
    if (prov) { el.appendChild(prov); }
    return el;
  }

  function lookupRowEl(row) {
    var rid = row.id.split(".")[1] || row.id;
    return h("li", { class: "lookup-chip" }, h("span", { text: row.label }),
      h("button", { type: "button", "aria-label": t("lookup.remove_chip") + ": " + row.label, text: "✕", on: { click: function () {
        sendEvent({ type: "lookup_edit", action: "remove", request_id: rid });
      } } }));
  }

  function sectionEl(section) {
    var el = h("section", { class: "section" + (section.reviewed ? " reviewed" : ""), "aria-labelledby": "sec-" + section.id });
    el.appendChild(h("div", { class: "section-head" }, h("h3", { id: "sec-" + section.id, text: section.title }),
      h("button", { class: "btn-link", type: "button", text: t("ui.id_change_this"), on: { click: function () { openPanel(section.id, section.title, section, null); } } })));
    if (state.ui.panel && state.ui.panel.target === section.id) { el.appendChild(panelEl()); }
    var rows = h("div", { class: "rows" });
    if (!section.rows.length) {
      el.appendChild(h("div", { class: "empty", text: section.empty_text || t("x.not_decided") }));
      return el;
    }
    if (section.id === "lookup") {
      var ul = h("ul", { class: "lookup-chips" });
      section.rows.forEach(function (r) { ul.appendChild(lookupRowEl(r)); });
      el.appendChild(ul);
      return el;
    }
    var lastGroup = null;
    section.rows.forEach(function (row) {
      if (row.group && row.group !== lastGroup) { rows.appendChild(h("div", { class: "group-title", text: row.group })); }
      lastGroup = row.group;
      rows.appendChild(rowEl(section, row));
    });
    el.appendChild(rows);
    return el;
  }

  function approvalEl(vm) {
    var f = vm.footer;
    var box = h("div", { class: "approval", role: "region", "aria-label": f.primary_label });
    var err = h("div", { class: "approval-error", role: "alert" });
    var ok = f.approvable && vm.header.status !== "approved";
    var primary = h("button", { class: "btn btn-primary", type: "button", text: f.primary_label, "aria-disabled": ok ? "false" : "true", on: { click: function () {
      if (!ok) { return; }
      err.textContent = "";
      api("POST", "/sessions/" + state.sessionId + "/gate", {
        type: "gate_decision", event_id: randomId(), decision: "approve", version: vm.header.version,
        brief_hash: vm.brief_hash, section_hashes: vm.section_hashes
      }).then(function () { toast(t("x.approved_toast")); return loadView(); }).then(function () { return refreshSession(); })
        .catch(function (e) { err.textContent = friendly(e); loadView(); });
    } } });
    var secondary = h("button", { class: "btn", type: "button", text: f.secondary_label, on: { click: function () {
      openPanel("brief", vm.header.title, null, null);
    } } });
    box.appendChild(h("div", { class: "row-actions" }, primary, secondary));
    if (state.ui.panel && state.ui.panel.target === "brief") { box.appendChild(panelEl()); }
    if (f.blockers && f.blockers.length && !ok) {
      box.appendChild(h("ul", { class: "blockers" }, f.blockers.map(function (b) { return h("li", { text: b }); })));
    }
    if (vm.changed_since_approval && vm.changed_since_approval.length) {
      box.appendChild(h("div", { class: "small muted", text: t("x.changed_since_approval", { sections: vm.changed_since_approval.join(", ") }) }));
    }
    box.appendChild(err);
    return box;
  }

  function renderBrief() {
    var host = $("tab-brief");
    clear(host);
    var vm = state.view;
    var anyRows = !!vm && vm.sections.some(function (s) { return s.rows.length > 0; });
    if (!vm || !anyRows) {
      host.appendChild(h("div", { class: "empty", text: t("x.brief_empty") }));
      if (!vm) { return; }
    }
    var hd = vm.header;
    var head = h("div", { class: "brief-head" },
      h("h2", { text: hd.title || t("ui.brief_tab") }),
      hd.idea_line ? h("p", { text: hd.idea_line }) : null,
      h("div", { class: "brief-meta" },
        h("span", { class: "status-chip", text: hd.status_label }),
        h("span", { text: t("x.version", { n: hd.version }) }),
        h("span", { text: t("x.reviewed_n_of_m", { n: hd.reviewed, m: hd.of_total }) })));
    var chips = h("div", { class: "brief-meta" });
    (hd.readiness || []).forEach(function (r) {
      chips.appendChild(h("span", { class: "ready-chip " + (r.ready ? "ok" : "gap"), title: (r.gaps || []).join("; ") },
        h("span", { "aria-hidden": "true", text: r.ready ? "✓" : "!" }), h("span", { text: r.label })));
    });
    head.appendChild(chips);
    if (hd.degraded) { head.appendChild(h("div", { class: "msg msg-warning", text: "⚠ " + t("notice.degraded_probes") })); }
    host.appendChild(head);
    vm.sections.forEach(function (s) { host.appendChild(sectionEl(s)); });
    host.appendChild(approvalEl(vm));
  }

  // ---- lookup, sources, history tabs ---------------------------------------------------------------------
  function renderLookup() {
    var host = $("tab-lookup");
    clear(host);
    host.appendChild(h("h3", { text: t("lookup.title") }));
    var ul = h("ul", { class: "lookup-chips" });
    state.lookups.forEach(function (rr) {
      ul.appendChild(h("li", { class: "lookup-chip", title: rr.question || "" }, h("span", { text: rr.shown_to_user_as || rr.topic }),
        h("button", { type: "button", "aria-label": t("lookup.remove_chip") + ": " + (rr.shown_to_user_as || rr.topic), text: "✕", on: { click: function () {
          sendEvent({ type: "lookup_edit", action: "remove", request_id: rr.id });
        } } })));
    });
    host.appendChild(state.lookups.length ? ul : h("div", { class: "empty", text: t("x.dev_none") }));
    var input = h("input", { type: "text", placeholder: t("x.lookup_add_placeholder"), "aria-label": t("lookup.add_chip") });
    function add() {
      var v = input.value.trim();
      if (v) { input.value = ""; sendEvent({ type: "lookup_edit", action: "add", text: v }); }
    }
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); add(); } });
    host.appendChild(h("div", { class: "custom-row" }, input, h("button", { class: "btn", type: "button", text: t("x.add"), on: { click: add } })));
  }

  function renderSources() {
    var host = $("tab-sources");
    clear(host);
    host.appendChild(h("h3", { text: t("sources.title") }));
    var rows = sectionRows("sources");
    if (!rows.length) { host.appendChild(h("div", { class: "empty", text: t("x.dev_none") })); }
    var sec = { id: "sources", title: t("sources.title"), rows: [] };
    rows.forEach(function (r) { host.appendChild(rowEl(sec, r)); });
    var note = h("p", { class: "hint", role: "status" });
    var file = h("input", { type: "file", class: "sr-only", tabindex: "-1", "aria-label": t("ui.upload") });
    file.addEventListener("change", function () { if (file.files && file.files.length) { uploadFile(file.files[0], note); } });
    var link = h("input", { type: "url", placeholder: t("x.link_placeholder"), "aria-label": t("x.add_link") });
    host.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn", type: "button", text: t("ui.upload"), on: { click: function () { file.click(); } } }), file));
    host.appendChild(h("div", { class: "custom-row" }, link, h("button", { class: "btn", type: "button", text: t("x.add_link"), on: { click: function () {
      var u = link.value.trim();
      if (u) { link.value = ""; sendEvent({ type: "link_added", url: u }); }
    } } })));
    host.appendChild(note);
  }

  function turnText(ev) {
    var p = ev.payload || {};
    switch (ev.type) {
      case "user_message": return { who: "user", text: p.text || "" };
      case "card_answer": return { who: "user", text: (p.custom_text || (p.value === null || p.value === undefined ? "" : typeof p.value === "string" ? p.value : JSON.stringify(p.value))) || "(answer)" };
      case "card_skip": return { who: "user", text: t("ui.skip") };
      case "card_you_decide": return { who: "user", text: t("ui.you_decide") };
      case "card_back": return { who: "user", text: t("ui.back") };
      case "brief_edit": return { who: "user", text: p.item_id + " → " + (typeof p.value === "string" ? p.value : JSON.stringify(p.value)) };
      case "brief_feedback": return { who: "user", text: (p.target || "") + ": " + (p.text || p.reason || "") };
      case "assistant_message": return { who: "assistant", text: p.text || "" };
      case "card": return { who: "assistant", text: p.card ? p.card.question : "" };
      case "notice": return { who: "system", text: p.text || "" };
      case "brief_patch": return { who: "system", text: p.summary || "" };
      default: return { who: "system", text: ev.type };
    }
  }

  function renderHistoryInto(host, data, highlight) {
    clear(host);
    var items = data.items || [];
    var hl = {};
    (highlight || data.highlighted_turn_ids || []).forEach(function (id) { hl[id] = true; });
    if (!items.length) { host.appendChild(h("div", { class: "empty", text: t("x.no_history") })); return; }
    var first = null;
    items.forEach(function (ev) {
      var tt = turnText(ev);
      var marked = !!(hl[ev.turn_id] || hl[ev.event_id]);
      var el = h("div", { class: "turn turn-" + tt.who + (marked ? " hl" : ""), "aria-current": marked ? "true" : null },
        h("span", { class: "turn-who", text: tt.who === "user" ? t("x.you") : tt.who === "assistant" ? t("x.assistant") : ev.type }),
        h("span", { text: tt.text }));
      if (marked && !first) { first = el; }
      host.appendChild(el);
    });
    if (first) { first.scrollIntoView({ block: "center" }); }
  }

  function loadHistory(params) {
    var q = "?limit=200";
    Object.keys(params || {}).forEach(function (k) { if (params[k]) { q += "&" + k + "=" + encodeURIComponent(params[k]); } });
    return api("GET", "/sessions/" + state.sessionId + "/history" + q);
  }

  function renderHistoryTab() {
    var host = $("tab-history");
    clear(host);
    var search = h("input", { type: "text", placeholder: t("x.search"), "aria-label": t("x.search") });
    var list = h("div", { class: "drawer-body" });
    function run() { loadHistory({ q: search.value.trim() }).then(function (d) { renderHistoryInto(list, d); }).catch(function () {}); }
    search.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); run(); } });
    host.appendChild(search);
    host.appendChild(list);
    run();
  }

  function openHistoryDrawer(slot, turnIds) {
    var drawer = $("history-drawer");
    drawer.hidden = false;
    $("history-drawer-title").textContent = t("history.title");
    var body = $("history-drawer-body");
    clear(body);
    body.appendChild(h("span", { class: "muted", text: "…" }));
    loadHistory({ slot: slot }).then(function (d) {
      var ids = (turnIds && turnIds.length) ? turnIds : (d.highlighted_turn_ids || []);
      renderHistoryInto(body, d, ids);
    }).catch(function (e) { clear(body); body.appendChild(h("div", { text: friendly(e) })); });
    $("history-close").focus();
  }

  // ---- tabs and panes -----------------------------------------------------------------------------------------
  var TABS = [["brief", "x.brief"], ["lookup", "x.lookup_tab"], ["sources", "x.sources_tab"], ["history", "x.history_tab"]];
  function renderTabs() {
    var bar = $("tabs");
    clear(bar);
    TABS.forEach(function (tb) {
      bar.appendChild(h("button", { class: "tab", type: "button", role: "tab", id: "tabbtn-" + tb[0], "aria-selected": state.tab === tb[0] ? "true" : "false",
        "aria-controls": "tab-" + tb[0], text: t(tb[1]), on: { click: function () { setTab(tb[0]); } } }));
    });
    TABS.forEach(function (tb) { $("tab-" + tb[0]).hidden = state.tab !== tb[0]; });
  }
  function setTab(name) {
    state.tab = name;
    renderTabs();
    if (name === "history") { renderHistoryTab(); }
    if (name === "lookup") { renderLookup(); }
    if (name === "sources") { renderSources(); }
  }
  function setPane(name) {
    $("workspace").dataset.pane = name;
    $("pane-chat").setAttribute("aria-selected", name === "chat" ? "true" : "false");
    $("pane-brief").setAttribute("aria-selected", name === "brief" ? "true" : "false");
  }

  // ---- developer drawer ---------------------------------------------------------------------------------------
  function usd(s) { return "$" + (s === undefined || s === null ? "0.00" : String(s)); }
  function renderMeter() {
    var host = $("meter");
    clear(host);
    var b = state.budget;
    var cap = b ? b.cap_usd : CAP_FALLBACK;
    var spent = b ? b.spent_usd : "0.0000";
    var pct = Math.max(0, Math.min(100, Math.round(100 * parseFloat(spent) / parseFloat(cap || "2"))));
    host.appendChild(h("span", { class: "meter-text", text: t("ui.spent_cap", { spent: usd(spent), cap: usd(cap) }) }));
    host.appendChild(h("div", { class: "meter-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": t("ui.spent_cap", { spent: usd(spent), cap: usd(cap) }) },
      h("div", { class: "meter-fill" + (pct >= 95 ? " full" : pct >= 75 ? " hot" : ""), css: { width: pct + "%" } })));
    if (b) { host.title = (b.mode === "live" ? t("dev.live") : t("dev.mock")); }
  }
  function loadBudget() {
    return api("GET", "/budget").then(function (b) { state.budget = b; renderMeter(); renderDevHead(); }).catch(function () { renderMeter(); });
  }
  function loadCalls() {
    if (!state.sessionId) { return Promise.resolve(); }
    return api("GET", "/sessions/" + state.sessionId + "/calls?limit=30").then(function (d) { state.calls = d.calls || []; if (state.devOpen) { renderDev(); } }).catch(function () {});
  }
  function refreshSideData() { loadBudget(); if (state.devOpen) { loadCalls(); } }

  function renderDevHead() {
    var mode = $("dev-mode");
    var live = state.budget && state.budget.mode === "live";
    mode.textContent = (live ? t("dev.live") : t("dev.mock")) + " · " + (state.streamOk ? t("x.stream_on") : t("x.stream_off"));
    mode.className = "pill" + (live ? " live" : "");
    $("dev-title").textContent = t("dev.title");
  }
  var DEV_TABS = [["events", "x.events"], ["state", "x.state"], ["slots", "x.slots"], ["calls", "x.calls"]];
  function renderDev() {
    if (!state.devOpen) { return; }
    renderDevHead();
    var tabs = $("dev-tabs");
    clear(tabs);
    DEV_TABS.forEach(function (d) {
      tabs.appendChild(h("button", { class: "tab", type: "button", role: "tab", "aria-selected": state.devTab === d[0] ? "true" : "false", text: t(d[1]),
        on: { click: function () { state.devTab = d[0]; renderDev(); if (d[0] === "calls") { loadCalls(); } } } }));
    });
    var body = $("dev-body");
    clear(body);
    var b = state.budget;
    var big = h("div", { class: "big-meter" });
    if (b) {
      var pct = Math.max(0, Math.min(100, Math.round(100 * parseFloat(b.spent_usd) / parseFloat(b.cap_usd))));
      big.appendChild(h("span", { class: "meter-text", text: t("ui.spent_cap", { spent: usd(b.spent_usd), cap: usd(b.cap_usd) }) }));
      big.appendChild(h("div", { class: "meter-bar" }, h("div", { class: "meter-fill" + (pct >= 95 ? " full" : pct >= 75 ? " hot" : ""), css: { width: pct + "%" } })));
      body.appendChild(big);
    }
    if (state.devTab === "events") {
      var evs = state.devEvents.slice().reverse();
      if (!evs.length) { body.appendChild(h("div", { class: "empty", text: t("x.dev_none") })); }
      evs.forEach(function (ev) {
        body.appendChild(h("div", { class: "evrow" }, h("div", { class: "evhead", text: "#" + (ev.seq === null || ev.seq === undefined ? "-" : ev.seq) + " " + ev.direction + " " + ev.type }),
          h("div", { class: "mono", text: JSON.stringify(ev.payload) })));
      });
    } else if (state.devTab === "state") {
      body.appendChild(h("div", { class: "mono", text: JSON.stringify({
        session: state.session, progress: state.progress, last_seq: state.lastSeq,
        header: state.view ? state.view.header : null, probes: state.lastProbe
      }, null, 2) }));
      var who = h("input", { type: "text", placeholder: t("x.speaking_as"), "aria-label": t("x.speaking_as") });
      who.value = state.contributor;
      who.addEventListener("change", function () {
        state.contributor = who.value.trim();
        if (state.contributor) { sendEvent({ type: "contributor_switch", contributor_id: state.contributor }); }
      });
      body.appendChild(who);
    } else if (state.devTab === "slots") {
      var table = h("table", { class: "calls" }, h("thead", null, h("tr", null, h("th", { text: "id" }), h("th", { text: t("x.tier") }), h("th", { text: "value" }))));
      var tb = h("tbody");
      if (state.view) {
        state.view.sections.forEach(function (s) {
          s.rows.forEach(function (r) {
            var v = r.value || {};
            tb.appendChild(h("tr", null, h("td", { text: r.id }), h("td", { text: r.tier }), h("td", { text: v.kind === "text" ? v.text : (v.items || []).join(", ") })));
          });
        });
      }
      table.appendChild(tb);
      body.appendChild(table);
    } else {
      if (!state.calls.length) { body.appendChild(h("div", { class: "empty", text: t("x.no_calls") })); }
      else {
        var ct = h("table", { class: "calls" }, h("thead", null, h("tr", null,
          ["call", "spec", "outcome", "in", "out", "cost", "ms"].map(function (x) { return h("th", { text: x }); }))));
        var cb = h("tbody");
        state.calls.forEach(function (c) {
          cb.appendChild(h("tr", null, [c.call_id, c.spec_id, c.outcome, c.tokens_in, c.tokens_out, usd(c.cost_usd), c.latency_ms].map(function (x) { return h("td", { text: String(x) }); })));
        });
        ct.appendChild(cb);
        body.appendChild(ct);
      }
    }
  }
  function setDev(on) {
    state.devOpen = on;
    $("dev-drawer").hidden = !on;
    $("btn-dev").setAttribute("aria-pressed", on ? "true" : "false");
    store.set("compass.dev", on ? "1" : "0");
    if (on) { loadBudget(); loadCalls(); renderDev(); }
  }

  // ---- home ----------------------------------------------------------------------------------------------------
  function showHome() {
    stopStream();
    state.sessionId = null; state.session = null;
    $("workspace").hidden = true;
    $("home").hidden = false;
    $("topbar-session").textContent = "";
    var home = $("home");
    clear(home);
    var name = h("input", { type: "text", id: "new-name", placeholder: t("x.name"), "aria-label": t("x.name"), maxlength: "120" });
    var mode = h("select", { id: "new-mode", "aria-label": t("ui.mode") },
      h("option", { value: "guided", text: t("ui.mode_guided") }), h("option", { value: "enterprise", text: t("ui.mode_company") }));
    mode.value = state.mode;
    mode.addEventListener("change", function () { state.mode = mode.value; store.set("compass.mode", state.mode); });
    var lang = h("select", { id: "new-lang", "aria-label": t("ui.language") },
      h("option", { value: "en", text: "English" }), h("option", { value: "hi", text: "हिन्दी" }));
    lang.value = state.lang;
    lang.addEventListener("change", function () { setLang(lang.value); });
    var start = h("button", { class: "btn btn-primary", type: "button", text: t("ui.start_new_project"), on: { click: function () {
      api("POST", "/sessions", { mode: state.mode, locale: state.lang, user_label: name.value.trim() })
        .then(function (s) { openSession(s.id); })
        .catch(function (e) { homeError.textContent = friendly(e); });
    } } });
    var homeError = h("p", { class: "approval-error", role: "alert" });
    home.appendChild(h("div", { class: "home-hero" }, h("h1", { text: "Compass" }), h("p", { text: t("x.tagline") }),
      h("div", { class: "hero-controls" },
        h("label", { class: "field" }, h("span", { text: t("x.name") }), name),
        h("label", { class: "field" }, h("span", { text: t("ui.mode") }), mode),
        h("label", { class: "field" }, h("span", { text: t("ui.language") }), lang), start), homeError));
    var list = h("div", { class: "projects" });
    home.appendChild(h("div", null, h("h2", { text: t("ui.your_projects") }), list));
    api("GET", "/sessions").then(function (d) {
      if (!d.sessions.length) { list.appendChild(h("p", { class: "muted", text: t("x.no_projects") })); }
      d.sessions.slice().reverse().forEach(function (s) {
        list.appendChild(h("button", { class: "project", type: "button", on: { click: function () { openSession(s.id); } } },
          h("span", null, h("div", { class: "project-title", text: s.title || s.user_label || s.id }),
            h("div", { class: "project-meta", text: s.state + " · v" + s.brief_version + " · " + s.mode })),
          h("span", { class: "btn", text: t("x.open") })));
      });
    }).catch(function (e) { homeError.textContent = friendly(e); });
  }

  function refreshSession() {
    return api("GET", "/sessions/" + state.sessionId).then(function (s) {
      state.session = s;
      $("topbar-session").textContent = (s.title || s.user_label || s.id) + " · " + s.state;
    }).catch(function () {});
  }

  function startButtons() {
    var wrap = h("div", { class: "row-actions" });
    function b(key, fn) { wrap.appendChild(h("button", { class: "btn", type: "button", text: t(key), on: { click: fn } })); }
    b("welcome.start_describe", function () { $("composer-input").focus(); });
    b("welcome.start_upload", function () { pickFile(); });
    b("welcome.start_like_app", function () { $("composer-input").focus(); });
    b("welcome.start_just_build", function () { postUser(t("welcome.start_just_build")); });
    return wrap;
  }

  function openSession(id) {
    stopStream();
    state.sessionId = id; state.lastSeq = 0; state.seen = {}; state.devEvents = []; state.view = null; state.viewJson = "";
    state.lookups = []; state.activeCard = null; state.ui = { panel: null, edit: null, prov: {} }; state.calls = [];
    $("home").hidden = true;
    $("workspace").hidden = false;
    clear(log());
    $("chat-actions").replaceChildren();
    $("progressbar").hidden = true;
    renderTabs(); setTab("brief"); setPane("chat");
    refreshSession().then(function () {
      if (state.session) { state.lang = state.session.locale || state.lang; $("lang-select").value = state.lang; applyChromeStrings(); }
      return loadStrings(state.lang);
    }).then(function () {
      return replayHistory();
    }).then(function () {
      startStream();
      loadView(); loadBudget(); renderLookup(); renderSources();
      if (!state.lastSeq || state.session && state.session.state === "WELCOME") { log().appendChild(startButtons()); }
    });
  }

  /** Rebuild the chat from the stored log (both directions) so a reopened project shows its conversation. */
  function loadAllHistory() {
    var all = [];
    function page(after) {
      return loadHistory(after ? { after: after } : {}).then(function (d) {
        all = all.concat(d.items || []);
        if (d.next_after_idx && all.length < 2000) { return page(d.next_after_idx); }
        return all;
      });
    }
    return page(0);
  }

  function replayHistory() {
    return loadAllHistory().then(function (items) {
      var lastCard = null;
      var answered = {};
      items.forEach(function (ev) {
        if (ev.direction === "in" && (ev.type === "card_answer" || ev.type === "card_skip" || ev.type === "card_you_decide") && ev.payload) { answered[ev.payload.card_id] = true; }
      });
      items.forEach(function (ev) {
        if (ev.direction === "in") {
          if (ev.type === "user_message") { addMsg("user", ev.payload.text || ""); }
          else if (ev.type === "card_answer") { addMsg("user", turnText(ev).text); }
          return;
        }
        if (ev.type === "card" && ev.payload && ev.payload.card && answered[ev.payload.card.id]) {
          var done = h("div", { class: "qcard done" }, h("div", { class: "small muted", text: ev.payload.card.question }));
          log().appendChild(done);
          state.seen[ev.seq] = true; state.lastSeq = Math.max(state.lastSeq, ev.seq);
          state.devEvents.push(ev);
          return;
        }
        onStored(ev, true);
        if (ev.type === "card") { lastCard = ev; }
      });
      return lastCard;
    }).catch(function () { return null; });
  }

  function postUser(text, mode) {
    addMsg("user", text);
    var body = { type: "user_message", text: text };
    if (mode === "voice") { body.input_mode = "voice"; }
    return sendEvent(body);
  }

  // ---- strings, language, theme, settings -----------------------------------------------------------------------
  function loadStrings(lang) {
    return fetch(BASE + "/strings/" + lang).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      state.strings = d && d.strings ? d.strings : {};
      applyChromeStrings();
    }).catch(function () { state.strings = {}; applyChromeStrings(); });
  }
  function applyChromeStrings() {
    document.documentElement.lang = state.lang;
    $("btn-settings").textContent = t("x.settings");
    $("btn-theme").textContent = t("x.theme");
    $("btn-dev").textContent = t("x.developer");
    $("lang-label").textContent = t("ui.language");
    $("pane-chat").textContent = t("x.chat");
    $("pane-brief").textContent = t("x.brief");
    $("composer-input").placeholder = t("ui.type_here");
    $("composer-label").textContent = t("ui.type_here");
    $("composer-send").textContent = t("ui.send");
    $("composer-mic").setAttribute("aria-label", t("ui.mic"));
    $("typing-text").textContent = t("ui.typing");
    $("history-drawer-title").textContent = t("history.title");
    renderTabs(); renderMeter(); renderDevHead();
  }
  function setLang(lang) {
    state.lang = lang;
    store.set("compass.lang", lang);
    $("lang-select").value = lang;
    loadStrings(lang).then(function () {
      if (state.sessionId) { state.viewJson = ""; loadView(); renderLookup(); renderSources(); if (state.tab === "history") { renderHistoryTab(); } }
      else { showHome(); }
    });
  }
  function applyTheme(theme) {
    state.theme = theme;
    if (theme) { document.documentElement.setAttribute("data-theme", theme); } else { document.documentElement.removeAttribute("data-theme"); }
    $("btn-theme").setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }
  function toggleTheme() {
    var dark = state.theme ? state.theme === "dark" : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    applyTheme(next);
    store.set("compass.theme", next);
  }
  function openSettings() {
    $("set-token").value = state.cfg.token; $("set-tenant").value = state.cfg.tenant;
    $("set-user").value = state.cfg.user; $("set-roles").value = state.cfg.roles;
    $("settings-dialog").hidden = false;
    $("set-token").focus();
  }
  function saveSettings(e) {
    e.preventDefault();
    state.cfg = { token: $("set-token").value.trim(), tenant: $("set-tenant").value.trim(), user: $("set-user").value.trim(), roles: $("set-roles").value.trim() };
    store.set("compass.cfg", JSON.stringify(state.cfg));
    $("settings-dialog").hidden = true;
    if (state.sessionId) { openSession(state.sessionId); } else { showHome(); }
    loadBudget();
  }

  // ---- boot --------------------------------------------------------------------------------------------------------
  function boot() {
    try {
      var raw = store.get("compass.cfg");
      if (raw) { var c = JSON.parse(raw); if (c && c.token) { state.cfg = { token: c.token, tenant: c.tenant, user: c.user, roles: c.roles || "" }; } }
    } catch (e) { /* defaults */ }
    state.lang = store.get("compass.lang") || "en";
    state.mode = store.get("compass.mode") || "guided";
    var th = store.get("compass.theme");
    applyTheme(th === "dark" || th === "light" ? th : null);
    $("lang-select").value = state.lang;
    $("lang-select").addEventListener("change", function () { setLang($("lang-select").value); });
    $("brand").addEventListener("click", showHome);
    $("btn-settings").addEventListener("click", openSettings);
    $("settings-form").addEventListener("submit", saveSettings);
    $("settings-cancel").addEventListener("click", function () { $("settings-dialog").hidden = true; });
    $("btn-theme").addEventListener("click", toggleTheme);
    $("btn-dev").addEventListener("click", function () { setDev(!state.devOpen); });
    $("dev-close").addEventListener("click", function () { setDev(false); });
    $("history-close").addEventListener("click", function () { $("history-drawer").hidden = true; });
    $("pane-chat").addEventListener("click", function () { setPane("chat"); });
    $("pane-brief").addEventListener("click", function () { setPane("brief"); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        $("history-drawer").hidden = true; $("settings-dialog").hidden = true;
        if (state.ui.panel || state.ui.edit) { state.ui.panel = null; state.ui.edit = null; renderBrief(); }
      }
    });
    var input = $("composer-input");
    var voiceUsed = false;
    $("composer").addEventListener("submit", function (e) {
      e.preventDefault();
      var v = input.value.trim();
      if (!v || !state.sessionId) { return; }
      input.value = "";
      collapseActive(state.activeCard ? state.activeCard.card.question + " — " + v : "");
      postUser(v, voiceUsed ? "voice" : "text");
      voiceUsed = false;
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
    });
    if (micSupported()) {
      $("composer-mic").hidden = false;
      $("composer-mic").addEventListener("click", function () { voiceUsed = true; startDictation(input); });
    }
    setInterval(function () { if (state.devOpen) { loadBudget(); loadCalls(); } }, 10000);
    applyChromeStrings();
    renderMeter();
    loadStrings(state.lang).then(function () { showHome(); loadBudget(); });
    if (store.get("compass.dev") === "1") { setDev(true); }
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); } else { boot(); }
})();
