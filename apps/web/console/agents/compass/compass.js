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
    "options[].description",
    "custom_input", "custom_input.enabled", "custom_input.placeholder", "custom_input.voice",
    "you_decide", "you_decide.enabled", "skip", "skip.enabled", "progress", "progress.n",
    "progress.of_about", "must_confirm", "fallback", "unlocks", "examples", "parent_card_id", "origin"
  ],
  "batch_keys": [
    "batch_id", "ui_mode", "groups", "groups[].id", "groups[].title", "groups[].cards", "total",
    "progress", "progress.tier", "progress.target", "progress.committed", "progress.closing"
  ],
  "batch_answer_keys": [
    "batch_id", "answers", "answers[].card_id", "answers[].action", "answers[].value", "answers[].custom_text",
    "decide_rest"
  ],
  "batch_answer_actions": ["answer", "skip", "you_decide", "none"],
  "qa_item_keys": [
    "items", "card_id", "status", "origin", "depth", "parent_card_id", "type", "question", "options",
    "answer", "value", "custom_text", "fallback", "facts", "facts[].statement", "facts[].status"
  ],
  "command_names": [
    "ask_more", "mode_answer_all", "budget_quick", "budget_standard", "budget_thorough"
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
     pick_many / priority also take custom_text next to the picked ids (picks and a typed answer together).

   batch_answer (the batch form, sent once): {batch_id, answers: [{card_id, action: answer|skip|you_decide|none, value,
   custom_text}], decide_rest?: true}. "none" = "None of these" (custom_text may say what they want instead);
   decide_rest asks the server to decide every other open question. Blank questions are sent as skip.
*/
(function () {
  "use strict";

  var AGENT = window.MobigenAgent || {};  // the registry entry the shell mounted (shared/registry.js)
  var BASE = AGENT.base || "/v1/compass";
  var CAP_FALLBACK = "3.00";

  // ---- shared helpers (shared/components.js) -------------------------------------------------------
  var UI = window.MobigenUI;
  var store = UI.store, $ = UI.$, h = UI.h, clear = UI.clear, randomId = UI.randomId, toast = UI.toast;
  var I = window.compassInterview;  // pure interview helpers (interview.js, loaded before this file)

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
    "ui.brief_tab": "Brief", "ui.language": "Language", "ui.mode": "Mode", "ui.typing": "Compass is thinking", "ui.typing_read": "Reading your idea…", "ui.typing_plan": "Choosing the questions that matter for your app…", "ui.typing_check": "Checking the questions are clear and useful…", "ui.typing_long": "Still working. This one is taking a little longer than usual.",
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
    // interview screens (also in the catalog as ui.interview.*; the embedded copy keeps the page usable offline)
    "ui.interview.why_button": "Why am I being asked this?",
    "ui.interview.feeds": "This feeds: {items}",
    "ui.interview.progress_line": "Group {g} of {groups} · question {n} of about {total}",
    "ui.interview.progress_followup": "Follow-up · question {n} of about {total}",
    "ui.interview.group_only": "Group {g} of {groups}",
    "ui.interview.followup_tag": "Follow-up",
    "ui.interview.fallback_badge": "Standard question",
    "ui.interview.fallback_tip": "The AI planner did not produce this question; a standard one was used",
    "ui.interview.example": "For example: {example}",
    "ui.interview.mode_answer_all": "Answer all at once",
    "ui.interview.mode_one_at_a_time": "Show one at a time",
    "ui.interview.mode_note": "This applies to the next questions.",
    "ui.interview.layout_label": "How questions are shown",
    "ui.interview.ask_more": "Ask me more",
    "ui.interview.depth_title": "How deep should I go?",
    "ui.interview.depth_quick": "Quick",
    "ui.interview.depth_standard": "Standard",
    "ui.interview.depth_thorough": "Thorough",
    "ui.interview.depth_quick_hint": "About 7 questions",
    "ui.interview.depth_standard_hint": "About 12 questions",
    "ui.interview.depth_thorough_hint": "25 or more questions",
    "ui.interview.depth_now": "Planned so far: about {total} questions.",
    "ui.interview.form_label": "All the questions of this set",
    "ui.interview.form_send": "Send all answers",
    "ui.interview.form_progress": "{done} of {total} questions handled",
    "ui.interview.form_blank_note": "{count} left blank will be skipped.",
    "ui.interview.form_required": "Please answer this one; it needs your confirmation.",
    "ui.interview.form_link_placeholder": "Paste a link or describe it in a few words",
    "ui.interview.form_sent": "{count} answers sent",
    "ui.interview.priority_hint": "Tick them in order of importance.",
    "ui.interview.qa_title": "Questions and answers",
    "ui.interview.qa_empty": "No questions have been asked yet.",
    "ui.interview.qa_status_answered": "Answered",
    "ui.interview.qa_status_skipped": "Skipped",
    "ui.interview.qa_status_you_decide": "You asked us to decide",
    "ui.interview.qa_status_open": "Waiting for your answer",
    "ui.interview.qa_skipped_text": "You skipped this question.",
    "ui.interview.qa_decide_text": "You left this one to us.",
    "ui.interview.qa_open_text": "Not answered yet.",
    "ui.interview.qa_facts": "What we took from it",
    "ui.interview.qa_saved": "Updated: your Brief and the next questions will follow",
    "ui.interview.qa_edit_empty": "Choose or type an answer first.",
    "ui.interview.qa_saving": "Saving…",
    "ui.interview.fact_superseded": "replaced",
    "ui.interview.fact_disputed": "disputed",
    "ui.interview.fact_not_used": "not used",
    "ui.interview.mock_banner": "MOCK MODE: questions are canned",
    // Compass v2 (ui.v2.*): the tier first, the submit-once form, honest waiting
    "ui.v2.tier_title": "How many questions would you like to answer?",
    "ui.v2.tier_help": "Pick one to unlock the idea box. You can change it until the first questions arrive.",
    "ui.v2.tier_quick_desc": "The fastest way to a first draft. We make sensible choices for the rest.",
    "ui.v2.tier_standard_desc": "A good balance of speed and detail.",
    "ui.v2.tier_thorough_desc": "The most detail. Best for a bigger or business app.",
    "ui.v2.tier_chosen": "Your choice: {name} ({count})",
    "ui.v2.tier_change": "Change",
    "ui.v2.tier_change_label": "Change how many questions",
    "ui.v2.tier_needed": "Choose how many questions you want above, then describe your idea here.",
    "ui.v2.composer_locked": "Choose how many questions first",
    "ui.v2.idea_placeholder": "Describe your app idea here",
    "ui.v2.tier_failed": "We could not save that choice. Please try again.",
    "ui.v2.form_title": "Your questions",
    "ui.v2.form_helper": "Nothing is processed until you press the button below. You can change any answer until then.",
    "ui.v2.question_n_of": "Question {n} of about {total}",
    "ui.v2.question_n": "Question {n}",
    "ui.v2.last_question": "Last question",
    "ui.v2.last_question_note": "This is the last question. Tell us what matters most for the first version; we will write your Brief right after.",
    "ui.v2.send_answers": "Send my answers",
    "ui.v2.send_answer": "Send my answer",
    "ui.v2.send_closing": "Send and write my Brief",
    "ui.v2.sending": "Sending your answers…",
    "ui.v2.send_failed": "We could not send your answers. They are still here. Please check your connection and press Retry.",
    "ui.v2.retry": "Retry",
    "ui.v2.stale_batch": "The questions changed while you were answering. Please check the questions and press Retry.",
    "ui.v2.updated_kept": "These questions were updated. We kept what you had typed wherever the question is still there.",
    "ui.v2.blank_list": "Left blank (question {list}): we will decide those for you.",
    "ui.v2.all_blank": "Nothing answered yet. If you send now, we will decide all of these for you.",
    "ui.v2.line_skipped": "Skipped, we decide",
    "ui.v2.line_none": "None of these",
    "ui.v2.select_all": "Select all",
    "ui.v2.select_none": "None of these",
    "ui.v2.custom_many": "Anything else? Type it here",
    "ui.v2.priority_hint": "Tick the ones that matter most for the first version. The order you tick them in is their rank; use the arrows to change it.",
    "ui.v2.rank_title": "Your top picks, most important first",
    "ui.v2.rank_up": "Move up: {label}",
    "ui.v2.rank_down": "Move down: {label}",
    "ui.v2.rank_remove": "Remove from your picks: {label}",
    "ui.v2.you_decide_on": "We will decide this one for you.",
    "ui.v2.decide_rest": "Decide the rest for me",
    "ui.v2.decide_rest_confirm": "We will decide every question you leave open and list each choice under “We assumed” for your approval. The answers you filled in are kept.",
    "ui.v2.decide_rest_yes": "Yes, decide the rest",
    "ui.v2.cancel": "Cancel",
    "ui.v2.form_done_rest": "{count} answers sent. We decided the rest for you.",
    "ui.v2.wait_answers_read": "Reading your answers…",
    "ui.v2.wait_answers_plan": "Planning your next questions…",
    "ui.v2.wait_brief_write": "Writing your Brief…",
    "ui.v2.wait_brief_check": "Checking the Brief is complete and consistent…",
    "ui.v2.wait_decide": "Deciding the remaining questions for you…",
    "ui.v2.wait_document": "Reading your document…",
    "ui.v2.wait_document_points": "Picking out the points that matter…",
    "ui.v2.wait_edit": "Updating your Brief…",
    "ui.v2.wait_more": "Planning more questions…",
    "ui.v2.wait_working": "Working on it…",
    "ui.v2.wait_very_long": "This is taking longer than usual. We are still working; please keep this page open.",
    "ui.v2.upload_hint": "PDF, Word, Excel, PowerPoint, CSV, text, Markdown or email (.eml), up to 15 MB.",
    "ui.v2.upload_too_large": "That file is larger than 15 MB. Please choose a smaller one, or paste the important parts into the chat.",
    "ui.v2.upload_empty": "That file is empty. Please choose another one.",
    "ui.v2.upload_unsupported": "We cannot read that kind of file. Please use PDF, Word, Excel, PowerPoint, CSV, text, Markdown or email, or describe it in your own words.",
    "ui.v2.upload_failed": "That file could not be read. You can describe it in your own words or add a link.",
    "ui.v2.upload_reading": "Reading your document: {name}…",
    "ui.v2.upload_instead": "Upload a file instead",

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
    "x.step_check": "Check", "x.back_to_chat": "Back to chat", "x.tier": "Tier",
    "x.diagnostics": "Diagnostics", "x.copy_claude": "Copy for Claude"
  };

  var state = {
    cfg: { token: "dev-token", tenant: "demo", user: "demo-user", roles: "" },
    lang: "en", mode: "guided", strings: {}, theme: null,
    sessionId: null, session: null, lastSeq: 0, seen: {}, devEvents: [], devTab: "diagnostics", devOpen: false,
    view: null, viewJson: "", lookups: [], progress: null, tab: "brief", streamAbort: null, streamOk: false,
    activeCard: null, typingCount: 0, loadingView: false, viewTimer: null, budget: null, calls: [],
    ui: { panel: null, edit: null, prov: {} }, deltaEl: null, lastProbe: [], contributor: "", batch: null,
    batches: [], answered: {}, depth: null,
    qa: [], qaEdit: null, qaTimer: null, qaAnswered: 0, pulsed: {},
    awaitingTier: false, tierEditing: false, welcomeEl: null, form: null, submitted: {}, layoutAsked: false,
    networkTrace: [], lastError: null
  };
  var WELCOME_FRAME = "welcome";   // the server's canned first card: the welcome block below replaces it

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
  function recordNetwork(entry) {
    state.networkTrace.push(entry);
    if (state.networkTrace.length > 30) { state.networkTrace.shift(); }
  }
  function api(method, path, body) {
    var headers = authHeaders();
    var init = { method: method, headers: headers };
    var isForm = typeof FormData !== "undefined" && body instanceof FormData;   // an upload: the browser sets the multipart header
    if (isForm) { init.body = body; }
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    var startTime = Date.now();
    return fetch(BASE + path, init).then(function (res) {
      return res.text().then(function (txt) {
        var duration = Date.now() - startTime;
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        recordNetwork({
          time: new Date().toISOString(),
          method: method,
          path: path,
          status: res.status,
          latency: duration,
          body: isForm ? { file: (body.get("file") || {}).name, bytes: (body.get("file") || {}).size } : body,
          response: data || txt
        });
        if (!res.ok) {
          var err = ApiProblem(res.status, data && data.code ? data.code : "http_" + res.status,
            data && data.message ? data.message : "");
          state.lastError = {
            time: new Date().toISOString(),
            method: method,
            path: path,
            status: res.status,
            code: err.code,
            message: err.apiMessage || String(err.message)
          };
          throw err;
        }
        return data;
      });
    }).catch(function (err) {
      if (!state.lastError || state.lastError.path !== path) {
        state.lastError = {
          time: new Date().toISOString(),
          method: method,
          path: path,
          status: err.status || 0,
          code: err.code || "network_error",
          message: err.apiMessage || err.message || "Failed to fetch"
        };
      }
      throw err;
    });
  }
  /** A bug while drawing an answer: shown in the chat and kept for "Copy for Claude Code", never silent. */
  function noteRenderError(e) {
    state.lastError = { time: new Date().toISOString(), method: "UI", path: "render", status: 0, code: "ui_error",
      message: String(e && e.stack ? e.stack : e).slice(0, 600) };
    var el = addMsg("error", t("error.generic"));
    el.setAttribute("role", "alert");
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
  // While a slow turn runs (the first questions can take a minute, the Brief longer) the line under the dots says in plain
  // words what is going on. The phase comes from what was just sent (the idea, an answer, the last answer, "decide the
  // rest", a document, an edit) and the staged lines end in "still working" fallbacks (interview.js WAIT). The line is a
  // polite live region, so a screen reader hears each change once.
  var typingTimers = [];
  var typingPhase = "generic";
  function stopTypingStory() {
    typingTimers.forEach(function (id) { window.clearTimeout(id); });
    typingTimers = [];
    $("typing-text").textContent = t("ui.typing");
  }
  function startTypingStory(phase) {
    stopTypingStory();
    typingPhase = phase || "generic";
    I.waitSchedule(typingPhase).forEach(function (step) {
      function show() { $("typing-text").textContent = t(step[1]); }
      if (step[0] === 0) { show(); } else { typingTimers.push(window.setTimeout(show, step[0] * 1000)); }
    });
  }
  /** The server moved on to another stage (a `progress` event names it): the line follows, unless it already says so. */
  function followStage(stage) {
    var phase = I.phaseForStage(stage);
    if (phase && state.typingCount > 0 && phase !== typingPhase) { startTypingStory(phase); }
  }
  function setTyping(on, phase) {
    var before = state.typingCount;
    state.typingCount = Math.max(0, state.typingCount + (on ? 1 : -1));
    $("typing").hidden = state.typingCount === 0;
    if (on && (before === 0 || (phase && phase !== typingPhase))) { startTypingStory(phase); }
    if (state.typingCount === 0) { stopTypingStory(); }
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
    if (a.form) {   // a form is never collapsed by guesswork: the server has its answers, or it moved on without them
      if (a.form.sending) { confirmForm(a.form); } else { dropForm(a.form); }
      return;
    }
    clear(a.el);
    a.el.className = "qcard done";
    a.el.appendChild(h("div", { class: "small muted", text: summary || a.card.question }));
    state.activeCard = null;
    $("chat-actions").replaceChildren();
  }

  function cardSummary(card, value, custom) {
    var base = cardValueText(card, value);
    if (custom) { return base ? base + ", " + custom : custom; }
    return base;
  }
  function cardValueText(card, value) {
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

  function markAnswered(cardId) { if (cardId) { state.answered[cardId] = true; } }

  function answer(card, value, custom) {
    var summary = cardSummary(card, value, custom);
    markAnswered(card.id);
    addMsg("user", summary || t("ui.continue"));
    collapseActive(summary ? card.question + " — " + summary : card.question);
    var body = { type: "card_answer", card_id: card.id, value: value === undefined ? null : value };
    if (custom) { body.custom_text = custom; }
    sendEvent(body);
  }
  // The batch form: one event with the answers of a whole batch. opts: decideRest (the server decides every other open
  // question), eventId (a retry of the same answers reuses it: the server ignores a repeated id), onError.
  function sendBatchAnswer(batchId, answers, opts) {
    var o = opts || {};
    var body = { type: "batch_answer", batch_id: batchId, answers: answers };
    if (o.decideRest) { body.decide_rest = true; }
    if (o.eventId) { body.event_id = o.eventId; }
    return sendEvent(body, { onError: o.onError, onAck: o.onAck });
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

  // ---- interview pieces shared by the single card and the answer-all form ---------------------------------------
  /** A small badge for a card the planner did not produce: a failure the developer must be able to see. */
  function fallbackBadge() {
    var label = t("ui.interview.fallback_badge");
    var tip = t("ui.interview.fallback_tip");
    return h("span", { class: "fallback-badge", role: "note", tabindex: "0", title: tip, "aria-label": label + ". " + tip, text: label });
  }

  /** "Why am I being asked this?": a button that opens an inline note with why_we_ask and what the answer feeds.
      Escape closes it and puts the focus back on the button. */
  var whySeq = 0;
  function whyEl(card) {
    var unlocks = card.unlocks || [];
    if (!card.why_we_ask && !unlocks.length) { return null; }
    whySeq += 1;
    var popId = "why-pop-" + whySeq;
    var pop = h("div", { class: "why-pop", id: popId, role: "note", hidden: true });
    if (card.why_we_ask) { pop.appendChild(h("p", { text: card.why_we_ask })); }
    if (unlocks.length) { pop.appendChild(h("p", { class: "why-feeds", text: t("ui.interview.feeds", { items: unlocks.join(", ") }) })); }
    var btn = h("button", { class: "why-btn", type: "button", "aria-expanded": "false", "aria-controls": popId,
      text: t("ui.interview.why_button") });
    function setOpen(on) { pop.hidden = !on; btn.setAttribute("aria-expanded", on ? "true" : "false"); }
    btn.addEventListener("click", function () { setOpen(pop.hidden); });
    var wrap = h("div", { class: "why" }, btn, pop);
    wrap.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !pop.hidden) { e.stopPropagation(); setOpen(false); btn.focus(); }
    });
    return wrap;
  }

  /** The header of a card: its group title, a follow-up marker, the fallback badge and the progress line. */
  function cardHead(card) {
    var loc = I.locate(state.batches, card);
    var line = I.progressLine(loc, card.progress, t);
    var head = h("div", { class: "qcard-head" });
    if (loc && loc.groupTitle) { head.appendChild(h("span", { class: "group-chip", text: loc.groupTitle })); }
    if (card.origin === "followup") { head.appendChild(h("span", { class: "followup-tag", text: "↳ " + t("ui.interview.followup_tag") })); }
    if (card.fallback) { head.appendChild(fallbackBadge()); }
    if (line) { head.appendChild(h("span", { class: "hint progress-line", text: line })); }
    return head.childNodes.length ? head : null;
  }

  function renderCard(card) {
    var el = h("div", { class: "qcard", role: "group", "aria-label": card.question });
    var body = h("div", { class: "options-wrap" });
    var type = card.type;
    var showCustom = !!(card.custom_input && card.custom_input.enabled) && type !== "open_prompt";
    var head = cardHead(card);
    if (head) { el.appendChild(head); }
    el.appendChild(h("div", { class: "q", text: card.question }));
    var why = whyEl(card);
    if (why) { el.appendChild(why); }
    el.appendChild(body);
    var builders = {
      choose_one: cardChooseOne, pick_many: cardPickMany, priority: cardPriority, confirm: cardConfirm,
      look_signals: cardLook, quick_form: cardForm, upload_or_link: cardUpload, open_prompt: cardOpen
    };
    (builders[type] || cardOpen)(card, body);
    if (showCustom) {
      var input = h("input", { type: "text", "aria-label": t("ui.something_else"),
        placeholder: I.examplePlaceholder(card, t) || card.custom_input.placeholder || t("card.custom_placeholder") });
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

  function optionBody(o) {
    return h("span", { class: "option-main" },
      h("span", { class: "option-label" }, o.label, o.recommended ? h("span", { class: "rec", text: "★ Recommended" }) : null),
      o.description ? h("span", { class: "option-desc", text: o.description }) : null,
      basisLabel(o.basis),
      o.reason ? h("span", { class: "option-reason", text: o.reason }) : null);
  }
  function optionButton(card, o, role, onClick) {
    return h("button", { class: "option", type: "button", role: role, "aria-checked": role ? "false" : null, on: { click: onClick } },
      optionBody(o));
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
    if (I.effectiveType(card) === "open_prompt") { cardOpen(card, body); return; }
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
  function cardForm(card, body) {
    if (I.effectiveType(card) === "open_prompt") { cardOpen(card, body); return; }   // no fields to fill: an open question
    textFields(card, body, true);
  }
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
    var file = fileInput(function (f) { uploadFile(f, note); });
    var link = h("input", { type: "url", placeholder: t("x.link_placeholder"), "aria-label": t("x.add_link") });
    body.appendChild(h("div", { class: "row-actions" },
      h("button", { class: "btn", type: "button", text: t("ui.upload"), on: { click: function () { file.click(); } } }), file,
      h("span", { class: "hint", text: t("ui.v2.upload_hint") })));
    body.appendChild(h("div", { class: "custom-row" }, link, h("button", { class: "btn", type: "button", text: t("x.add_link"), on: { click: function () {
      var u = link.value.trim();
      if (u) { addMsg("user", u); collapseActive(card.question + " — " + u); sendEvent({ type: "link_added", url: u }); }
    } } })));
    body.appendChild(note);
  }
  function cardOpen(card, body) {
    var ta = h("textarea", { rows: "3", "aria-label": card.question,
      placeholder: I.examplePlaceholder(card, t) || (card.custom_input && card.custom_input.placeholder) || t("ui.type_here") });
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

  // ---- the batch form: every planned batch, even a batch of one, is ONE form that is sent once -------------------
  var formSeq = 0;
  function checkInput(type, name, value) { return h("input", { type: type, name: name, value: value }); }

  /** The inputs of one question of the form, bound to its draft entry (what interview.js turns into an answer). Every
      control starts from the entry, so a form that is drawn again keeps what the person typed. */
  function formControls(card, entry, qid, onChange) {
    var wrap = h("div", { class: "qitem-controls" });
    var type = I.effectiveType(card);
    var opts = card.options || [];
    var placeholder = I.examplePlaceholder(card, t) || (card.custom_input && card.custom_input.placeholder) || "";
    var custom = I.showsCustom(card);

    /** The typed "something else": a text field that is always on screen (no extra click). For a single choice it
        has its own radio, so the last action (pick or type) is the one that counts. */
    function otherRow(radio) {
      var text = h("input", { type: "text", "aria-label": radio ? t("ui.something_else") : t("ui.v2.custom_many"),
        placeholder: placeholder || t("card.custom_placeholder") });
      text.value = entry.other || "";
      var row = h("div", { class: "form-option form-other" });
      function sync() { row.classList.toggle("is-idle", !!radio && !!text.value.trim() && entry.choice !== I.OTHER); }
      text.addEventListener("input", function () {
        entry.other = text.value;
        if (radio && text.value.trim() && entry.choice !== I.OTHER) { entry.choice = I.OTHER; radio.checked = true; }
        sync();
        onChange();
      });
      if (radio) { row.appendChild(h("label", { class: "form-other-label" }, radio, h("span", { text: t("ui.something_else") }))); }
      else { row.appendChild(h("span", { class: "form-other-label", text: t("ui.v2.custom_many") })); }
      row.appendChild(text);
      row.syncIdle = sync;
      sync();
      return row;
    }

    if (type === "choose_one" || type === "look_signals") {
      var group = h("div", { class: "options", role: "radiogroup", "aria-labelledby": qid });
      var others = [];
      opts.forEach(function (o) {
        var input = checkInput("radio", qid, o.id);
        input.checked = entry.choice === o.id;
        input.addEventListener("change", function () {
          entry.choice = o.id;
          others.forEach(function (r) { r.syncIdle(); });
          onChange();
        });
        group.appendChild(h("label", { class: "form-option" }, input, optionBody(o)));
      });
      if (custom) {
        var radio = checkInput("radio", qid, I.OTHER);
        radio.checked = entry.choice === I.OTHER;
        radio.addEventListener("change", function () { entry.choice = I.OTHER; others.forEach(function (r) { r.syncIdle(); }); onChange(); });
        var row = otherRow(radio);
        others.push(row);
        group.appendChild(row);
      }
      wrap.appendChild(group);
    } else if (type === "pick_many" || type === "priority") {
      var boxes = [];
      var byId = {};
      opts.forEach(function (o) { byId[o.id] = o; });
      var allBtn = null, noneBtn = null, rankBox = null, rankList = null;
      /** Everything that shows the picks (ticks, the two tools, the ranking) is drawn from the entry in one place. */
      function paintPicks() {
        boxes.forEach(function (b) { b.input.checked = entry.picks.indexOf(b.id) >= 0; });
        if (allBtn) { allBtn.setAttribute("aria-pressed", I.allSelected(card, entry) ? "true" : "false"); }
        if (noneBtn) { noneBtn.setAttribute("aria-pressed", entry.none ? "true" : "false"); }
        if (rankBox) { paintRank(); }
      }
      function paintRank(focusId, dir) {
        clear(rankList);
        rankBox.hidden = !entry.picks.length;
        entry.picks.forEach(function (id, i) {
          var o = byId[id];
          if (!o) { return; }
          function btn(d, glyph, labelKey, disabled, run) {
            return h("button", { class: "btn btn-quiet", type: "button", text: glyph, disabled: disabled, data: { id: id, dir: d },
              "aria-label": t(labelKey, { label: o.label }), on: { click: run } });
          }
          rankList.appendChild(h("li", { class: "rank-row" },
            h("span", { class: "rank-n", text: String(i + 1) }), h("span", { class: "option-label", text: o.label }),
            btn("up", "↑", "ui.v2.rank_up", i === 0, function () { I.movePick(entry, id, -1); paintRank(id, "up"); onChange(); }),
            btn("down", "↓", "ui.v2.rank_down", i === entry.picks.length - 1, function () { I.movePick(entry, id, 1); paintRank(id, "down"); onChange(); }),
            btn("remove", "✕", "ui.v2.rank_remove", false, function () { I.togglePick(card, entry, id, false); paintPicks(); onChange(); })));
        });
        if (focusId) {   // the arrow that was pressed has been redrawn: keep the keyboard where it was
          var again = rankList.querySelector('button[data-id="' + focusId + '"][data-dir="' + dir + '"]:not(:disabled)') ||
            rankList.querySelector('button[data-id="' + focusId + '"]:not(:disabled)');
          if (again) { again.focus(); }
        }
      }
      if (type === "priority") { wrap.appendChild(h("div", { class: "hint", text: t("ui.v2.priority_hint") })); }
      if (I.canSelectAll(card) || I.canNone(card)) {   // several answers can be true: all of them, or none of them
        var tools = h("div", { class: "pick-tools", role: "group", "aria-label": card.question });
        if (I.canSelectAll(card)) {
          allBtn = h("button", { class: "toggle select-all", type: "button", "aria-pressed": "false", text: t("ui.v2.select_all"),
            on: { click: function () { I.toggleSelectAll(card, entry); paintPicks(); onChange(); } } });
          tools.appendChild(allBtn);
        }
        if (I.canNone(card)) {
          noneBtn = h("button", { class: "toggle select-none", type: "button", "aria-pressed": "false", text: t("ui.v2.select_none"),
            on: { click: function () { I.toggleNone(card, entry); paintPicks(); onChange(); } } });
          tools.appendChild(noneBtn);
        }
        wrap.appendChild(tools);
      }
      var checks = h("div", { class: "options", role: "group", "aria-labelledby": qid });
      opts.forEach(function (o) {
        var input = checkInput("checkbox", qid, o.id);
        boxes.push({ id: o.id, input: input });
        input.addEventListener("change", function () { I.togglePick(card, entry, o.id, input.checked); paintPicks(); onChange(); });
        checks.appendChild(h("label", { class: "form-option" }, input, optionBody(o)));
      });
      if (custom) { checks.appendChild(otherRow(null)); }   // typed next to the ticks: both are sent
      wrap.appendChild(checks);
      if (type === "priority") {
        rankList = h("ol", { class: "rank-list" });
        rankBox = h("div", { class: "rank-box" }, h("div", { class: "hint", text: t("ui.v2.rank_title") }), rankList);
        wrap.appendChild(rankBox);
      }
      paintPicks();
    } else if (type === "confirm" || type === "quick_form") {
      opts.forEach(function (o) {
        var input = h("input", { type: "text", "aria-label": o.label, placeholder: type === "quick_form" ? (o.reason || "") : "" });
        input.value = entry.fields[o.id] !== undefined ? entry.fields[o.id] : (type === "confirm" ? o.label : "");
        input.addEventListener("input", function () { entry.fields[o.id] = input.value; entry.touched = true; onChange(); });
        wrap.appendChild(h("label", { class: "field" }, type === "confirm" ? null : h("span", { text: o.label }), input,
          type === "confirm" ? basisLabel(o.basis) : null));
      });
      if (type === "confirm") {
        var ok = checkInput("checkbox", qid, "ok");
        ok.checked = !!entry.confirmed;
        ok.addEventListener("change", function () { entry.confirmed = ok.checked; onChange(); });
        wrap.appendChild(h("label", { class: "form-option" }, ok, h("span", { class: "option-label", text: t("ui.looks_right") })));
      }
    } else if (type === "upload_or_link") {
      var link = h("input", { type: "text", "aria-label": card.question, placeholder: t("ui.interview.form_link_placeholder") });
      link.value = entry.text || "";
      link.addEventListener("input", function () { entry.text = link.value; onChange(); });
      wrap.appendChild(link);
      var fileNote = h("p", { class: "hint", role: "status" });
      var picker = fileInput(function (f) { uploadFile(f, fileNote); });
      wrap.appendChild(h("div", { class: "row-actions" },
        h("button", { class: "btn", type: "button", text: t("ui.v2.upload_instead"), on: { click: function () { picker.click(); } } }), picker,
        h("span", { class: "hint", text: t("ui.v2.upload_hint") })));
      wrap.appendChild(fileNote);
    } else {
      var area = h("textarea", { rows: "3", "aria-label": card.question, placeholder: placeholder || t("ui.type_here") });
      area.value = entry.text || "";
      area.addEventListener("input", function () { entry.text = area.value; onChange(); });
      wrap.appendChild(area);
    }
    return wrap;
  }

  /** One question of the form: its number, the question, why it is asked, the inputs, and the "You decide" toggle. */
  function formItem(card, entry, onChange, label, closing) {
    formSeq += 1;
    var qid = "fq-" + formSeq;
    var item = h("div", { class: "qitem" + (closing ? " qitem-closing" : ""), role: "group", "aria-labelledby": qid, data: { card: card.id } });
    var head = h("div", { class: "qitem-head" });
    if (label) { head.appendChild(h("span", { class: "qnum", text: label })); }
    head.appendChild(h("span", { class: "q", id: qid, text: card.question }));
    if (card.fallback) { head.appendChild(fallbackBadge()); }
    item.appendChild(head);
    var why = whyEl(card);
    if (why) { item.appendChild(why); }
    var controls = formControls(card, entry, qid, onChange);
    item.appendChild(controls);
    var err = h("div", { class: "approval-error", role: "alert" });
    var youBtn = null;
    var youNote = h("span", { class: "hint you-decide-note", role: "status", "aria-live": "polite" });
    /** "You decide" is the one switch of a question: its inputs are disabled and it is sent as you_decide. */
    function setAction(action) {
      entry.action = action;
      item.classList.toggle("is-off", action !== "answer");
      Array.prototype.forEach.call(controls.querySelectorAll("input, textarea, button"), function (c) { c.disabled = action !== "answer"; });
      if (youBtn) { youBtn.setAttribute("aria-pressed", action === "you_decide" ? "true" : "false"); }
      youNote.textContent = action === "you_decide" ? t("ui.v2.you_decide_on") : "";
      err.textContent = "";
      onChange();
    }
    var toggles = h("div", { class: "qitem-toggles" });
    if (!card.must_confirm && (!card.you_decide || card.you_decide.enabled)) {
      youBtn = h("button", { class: "toggle", type: "button", "aria-pressed": "false", text: t("ui.you_decide"),
        on: { click: function () { setAction(entry.action === "you_decide" ? "answer" : "you_decide"); } } });
      toggles.appendChild(youBtn);
      toggles.appendChild(youNote);
    }
    if (toggles.childNodes.length) { item.appendChild(toggles); }
    item.appendChild(err);
    if (entry.action !== "answer") { setAction(entry.action); }
    return {
      el: item,
      showRequired: function () { err.textContent = t("ui.interview.form_required"); },
      clearRequired: function () { err.textContent = ""; },
      focus: function () { var c = item.querySelector("input, textarea"); if (c) { c.focus(); } }
    };
  }

  function sendLabel(f) {
    if (f.closing) { return t("ui.v2.send_closing"); }
    return f.cards.length === 1 ? t("ui.v2.send_answer") : t("ui.v2.send_answers");
  }
  /** The text under the form that says what will happen to the blank questions, before anything is sent. */
  function blankSummaryText(f) {
    var sum = I.summarizeBlanks(f.cards, f.draft);
    if (!sum.blank.length) { return ""; }
    if (sum.blank.length === f.cards.length) { return t("ui.v2.all_blank"); }
    var numbers = sum.blank.map(function (c) { return String(f.cards.indexOf(c) + 1 + (f.nums ? f.nums.first - 1 : 0)); });
    return t("ui.v2.blank_list", { list: numbers.join(", ") });
  }
  function refreshForm(f) {
    f.ui.count.textContent = t("ui.interview.form_progress", { done: I.handledCount(f.cards, f.draft), total: f.cards.length });
    f.ui.summary.textContent = blankSummaryText(f);
  }

  /** Build the form for the questions that still wait in a batch. `draft` already holds what was typed before (a form
      that is drawn again keeps it). */
  function buildForm(f, note) {
    var el = h("div", { class: "qcard batch-form", role: "group", "aria-label": t("ui.interview.form_label"), "aria-live": "off",
      data: { batch: f.batchId } });
    var title = h("span", { class: "group-chip", tabindex: "-1", text: f.closing ? t("ui.v2.last_question") : t("ui.v2.form_title") });
    var count = h("span", { class: "hint form-count", role: "status", "aria-live": "polite" });
    el.appendChild(h("div", { class: "qcard-head" }, title, count));
    if (f.closing) { el.appendChild(h("p", { class: "closing-note", text: t("ui.v2.last_question_note") })); }
    if (note) { el.appendChild(h("p", { class: "hint form-updated", role: "status", text: note })); }
    var index = 0;
    f.items = {};
    f.groups.forEach(function (g) {
      var box = h("fieldset", { class: "qgroup" });
      if (g.title) { box.appendChild(h("legend", { class: "qgroup-title", text: g.title })); }
      g.cards.forEach(function (c) {
        var label = f.closing && f.cards.length === 1 ? "" : I.questionLabel(f.nums, index, t);
        var it = formItem(c, f.draft[c.id], function () { f.items[c.id].clearRequired(); refreshForm(f); }, label, I.isClosingCard(c));
        f.items[c.id] = it;
        box.appendChild(it.el);
        index += 1;
      });
      el.appendChild(box);
    });
    var summary = h("div", { class: "hint form-summary", role: "status", "aria-live": "polite" });
    var status = h("div", { class: "hint form-status", role: "status", "aria-live": "polite" });
    var errText = h("span", { class: "form-error-text" });
    var retry = h("button", { class: "btn btn-primary form-retry", type: "button", text: t("ui.v2.retry"), on: { click: function () { submitForm(f, f.lastDecideRest); } } });
    var error = h("div", { class: "form-error", role: "alert", hidden: true }, errText, retry);
    var send = h("button", { class: "btn btn-primary send-all", type: "button", text: sendLabel(f), on: { click: function () { submitForm(f, false); } } });
    var decide = h("button", { class: "btn decide-rest", type: "button", "aria-expanded": "false", "aria-controls": "decide-confirm-" + f.batchId,
      text: t("ui.v2.decide_rest"), on: { click: function () { toggleDecideConfirm(f, true); } } });
    var yes = h("button", { class: "btn btn-primary decide-rest-yes", type: "button", text: t("ui.v2.decide_rest_yes"),
      on: { click: function () { toggleDecideConfirm(f, false); submitForm(f, true); } } });
    var cancel = h("button", { class: "btn", type: "button", text: t("ui.v2.cancel"), on: { click: function () { toggleDecideConfirm(f, false); decide.focus(); } } });
    var confirmBox = h("div", { class: "decide-confirm", id: "decide-confirm-" + f.batchId, role: "group", "aria-label": t("ui.v2.decide_rest"), hidden: true },
      h("p", { text: t("ui.v2.decide_rest_confirm") }), h("div", { class: "row-actions" }, yes, cancel));
    confirmBox.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.stopPropagation(); toggleDecideConfirm(f, false); decide.focus(); }
    });
    var bar = h("div", { class: "form-actions" }, summary, status, error, confirmBox,
      h("div", { class: "row-actions" }, send, decide));
    el.appendChild(h("p", { class: "hint form-helper", text: t("ui.v2.form_helper") }));
    el.appendChild(bar);
    f.ui = { count: count, summary: summary, status: status, error: error, errText: errText, retry: retry, send: send, title: title, bar: bar,
      decide: decide, confirm: confirmBox, yes: yes };
    refreshForm(f);
    return el;
  }

  /** "Decide the rest for me" asks first, in one line, what will happen. */
  function toggleDecideConfirm(f, open) {
    f.ui.confirm.hidden = !open;
    f.ui.decide.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { f.ui.yes.focus(); }
  }

  /** The reply to a failed send, in plain words. */
  function sendFailureText(err) {
    var status = err && err.status;
    if (status === 404 || status === 409 || status === 410) { return t("ui.v2.stale_batch"); }
    if (status === 429) { return t("error.rate_limited"); }
    return t("ui.v2.send_failed");
  }

  function setFormBusy(f, on) {
    f.sending = on;
    f.el.classList.toggle("is-sending", on);
    f.el.setAttribute("aria-busy", on ? "true" : "false");
    Array.prototype.forEach.call(f.el.querySelectorAll(".qgroup"), function (g) { g.disabled = on; });
    f.ui.send.disabled = on;
    f.ui.decide.disabled = on;
    f.ui.send.textContent = on ? t("ui.v2.sending") : sendLabel(f);
    f.ui.status.textContent = on ? t("ui.v2.sending") : "";
    if (on) { f.ui.error.hidden = true; }
  }

  /** The one place a form is sent. Nothing leaves the page before the person presses the button; while the request is
      out the button is disabled (no double submit); nothing collapses until the server has the answers; a failed
      request keeps every answer and offers Retry (the same event id when nothing changed, so it is never applied twice). */
  function submitForm(f, decideRest) {
    if (f.sending || f.confirmed) { return; }
    var missing = decideRest ? [] : I.missingRequired(f.cards, f.draft);   // "decide the rest" hands even a required one over
    if (missing.length) {
      missing.forEach(function (c) { f.items[c.id].showRequired(); });
      f.items[missing[0].id].focus();
      return;
    }
    var payload = I.buildBatchPayload(f.batchId, f.cards, f.draft, { decideRest: !!decideRest });
    var key = JSON.stringify(payload);
    var eventId = f.failed && f.failed.key === key ? f.failed.eventId : randomId();
    f.answers = payload.answers;
    f.decideRest = !!decideRest;
    f.lastDecideRest = !!decideRest;
    setFormBusy(f, true);
    window.compassSend.batchAnswer(f.batchId, payload.answers, { decideRest: !!decideRest, eventId: eventId, onAck: function () { confirmForm(f); }, onError: function (err) {
      if (f.confirmed) { return; }   // the stream already showed the next questions: the server has the answers
      f.failed = { key: key, eventId: eventId };
      setFormBusy(f, false);
      f.ui.errText.textContent = sendFailureText(err);
      f.ui.error.hidden = false;
      f.ui.retry.focus();
    } }).then(function (ack) { if (ack) { confirmForm(f); } });
  }

  /** The server has the answers (the reply came, or it already sent the next questions): now, and only now, the form
      becomes a line in the chat and its questions count as answered. */
  function confirmForm(f) {
    if (!f || f.confirmed) { return; }
    f.confirmed = true;
    f.sending = false;
    f.cards.forEach(function (c) { markAnswered(c.id); });   // a question left out of the form is dropped by the server
    state.submitted[f.batchId] = true;
    var byId = {};
    f.cards.forEach(function (c) { byId[c.id] = c; });
    var lines = f.answers.map(function (a) {
      var c = byId[a.card_id];
      var said = a.action === "skip" ? t("ui.v2.line_skipped") : a.action === "you_decide" ? t("ui.you_decide")
        : a.action === "none" ? [t("ui.v2.line_none"), a.custom_text].filter(Boolean).join(": ") : cardSummary(c, a.value, a.custom_text);
      return c.question + " — " + said;
    });
    if (f.decideRest) { lines.push(t("ui.v2.decide_rest")); }
    if (lines.length) { addMsg("user", lines.join("\n")); }
    clear(f.el);
    f.el.className = "qcard done";
    f.el.removeAttribute("aria-busy");
    f.el.appendChild(h("div", { class: "small muted",
      text: t(f.decideRest ? "ui.v2.form_done_rest" : "ui.interview.form_sent", { count: f.answers.length }) }));
    releaseForm(f);
  }
  function releaseForm(f) {
    if (state.form === f) { state.form = null; }
    if (state.activeCard && state.activeCard.form === f) { state.activeCard = null; $("chat-actions").replaceChildren(); }
  }
  /** A form the server moved past without an answer (a newer batch replaced it): it just goes away. */
  function dropForm(f) {
    if (!f || f.confirmed) { return; }
    f.confirmed = true;
    if (f.el.parentNode) { f.el.parentNode.removeChild(f.el); }
    releaseForm(f);
  }

  /** An older server may still plan a batch for one-at-a-time display. The form is the only layout, so ask once for
      the form layout: the server re-sends the batch (same id and questions), which showForm does not redraw. */
  function ensureFormLayout(batch) {
    if (batch.ui_mode !== "one_at_a_time" || state.layoutAsked) { return; }
    state.layoutAsked = true;
    sendEvent({ type: "command", name: I.modeCommand("answer_all") }, { quiet: true });
  }

  /** Show the form for a batch (a `question_batch` event, or a `card` of a batch). Idempotent by batch id and
      questions: the same batch again (a replay after a reconnect, a repeat of the first card) changes nothing; the
      same batch with other questions, or a newer batch, redraws the form and keeps what was typed where the question
      still exists. */
  function showForm(batch, quiet) {
    if (state.submitted[batch.batch_id]) { return; }   // already sent: a repeat of its events draws nothing
    var groups = I.pendingGroups(batch, state.answered);
    var cards = I.flatCards(groups);
    var old = state.form;
    if (!cards.length) { return; }
    if (old && !old.confirmed && old.batchId === batch.batch_id && I.sameQuestions(old.batch, batch)) { old.batch = batch; return; }
    var carried = null;
    var hadFocus = !document.activeElement || document.activeElement === document.body;
    if (old && !old.confirmed) {
      if (old.sending) { confirmForm(old); }       // the server answered with new questions: it has the answers
      else { carried = old.draft; hadFocus = hadFocus || old.el.contains(document.activeElement); }
    }
    if (state.activeCard && !state.activeCard.form) { collapseActive(); }   // a single card is replaced by the form
    var kept = I.carryDraft(carried, cards);
    var f = { batch: batch, batchId: batch.batch_id, groups: groups, cards: cards, draft: kept.draft, nums: I.batchNumbers(batch, cards.length),
      closing: I.isClosingBatch(batch), sending: false, confirmed: false, failed: null, answers: [], lastDecideRest: false, items: {}, ui: null, el: null };
    f.el = buildForm(f, carried && kept.kept > 0 ? t("ui.v2.updated_kept") : "");
    if (old && !old.confirmed && old.el.parentNode && old.batchId === batch.batch_id) {
      old.el.parentNode.replaceChild(f.el, old.el);   // the same batch, redrawn in place
      old.confirmed = true;
    } else {
      dropForm(old);
      log().appendChild(f.el);
    }
    state.form = f;
    state.activeCard = { card: cards[0], el: f.el, batch: batch, form: f };
    renderChatActions(null);
    f.el.scrollIntoView({ block: "start" });   // a form is read from its top
    if (hadFocus && !quiet) { f.ui.title.focus({ preventScroll: true }); }
  }

  function renderChatActions(card) {
    var bar = $("chat-actions");
    clear(bar);
    function act(label, fn) { bar.appendChild(h("button", { class: "btn", type: "button", text: label, on: { click: fn } })); }
    act(t("ui.back"), function () { sendEvent({ type: "card_back" }); });
    act(t("ui.undo"), function () { sendEvent({ type: "command", name: "undo" }); });
    if (card && !card.must_confirm) {
      if (card.skip && card.skip.enabled) {
        act(t("ui.skip"), function () {
          markAnswered(card.id);
          collapseActive(card.question + " — " + t("ui.skip"));
          sendEvent({ type: "card_skip", card_id: card.id });
        });
      }
      if (card.you_decide && card.you_decide.enabled) {
        act(t("ui.you_decide"), function () {
          markAnswered(card.id);
          addMsg("system", t("ui.you_decide"));
          collapseActive(card.question + " — " + t("ui.you_decide"));
          sendEvent({ type: "card_you_decide", card_id: card.id });
        });
      }
    }
  }

  // ---- interview controls: layout toggle, "Ask me more", depth chips, mock banner ---------------------------------
  function isApproved() {
    var s = state.session && state.session.state;
    var viewStatus = state.view && state.view.header ? state.view.header.status : "";
    return s === "APPROVED" || s === "ARCHIVED" || viewStatus === "approved";
  }
  function answeredCount() { return Math.max(Object.keys(state.answered).length, state.qaAnswered); }

  function renderInterviewBar() {
    var bar = $("interview-bar");
    var more = $("btn-ask-more");
    var n = answeredCount();
    bar.hidden = !state.sessionId || isApproved() || (!state.batch && n === 0);
    more.hidden = n === 0;
    more.textContent = t("ui.interview.ask_more");
  }

  function askMore() {
    var btn = $("btn-ask-more");
    btn.disabled = true;
    addMsg("system", t("ui.interview.ask_more"));
    sendEvent({ type: "command", name: "ask_more" }).then(function () { btn.disabled = false; });
  }

  function depthLabels(id) {
    if (id === "quick") {
      return { name: t("ui.interview.depth_quick"), hint: t("ui.interview.depth_quick_hint"), desc: t("ui.v2.tier_quick_desc") };
    }
    if (id === "thorough") {
      return { name: t("ui.interview.depth_thorough"), hint: t("ui.interview.depth_thorough_hint"), desc: t("ui.v2.tier_thorough_desc") };
    }
    return { name: t("ui.interview.depth_standard"), hint: t("ui.interview.depth_standard_hint"), desc: t("ui.v2.tier_standard_desc") };
  }
  /** Compact Quick / Standard / Thorough chips. Only the playback card still offers them, and only when an idea
      arrived without a tier (the welcome block asks for the tier first). */
  function depthChips() {
    var row = h("div", { class: "depth-chips" });
    I.BUDGETS.forEach(function (b) {
      var lab = depthLabels(b.id);
      row.appendChild(h("button", { class: "depth-chip", type: "button", "aria-pressed": state.depth === b.id ? "true" : "false",
        data: { budget: b.id }, on: { click: function () { chooseDepth(b.id); } } },
        h("span", { class: "depth-name", text: lab.name }), h("span", { class: "hint", text: lab.hint })));
    });
    return h("div", { class: "depth", role: "group", "aria-label": t("ui.interview.depth_title") },
      h("div", { class: "depth-title", text: t("ui.interview.depth_title") }), row,
      h("div", { class: "hint depth-note", role: "status", text: depthKnown() }));
  }
  function depthKnown() {
    return state.progress && state.progress.of_about ? t("ui.interview.depth_now", { total: state.progress.of_about }) : "";
  }
  /** The tier: sent as the budget_* command. The choice shows at once; if the server cannot save it the page goes
      back to the previous state and the chat says why (sendEvent shows the error). */
  function chooseDepth(id) {
    var prev = state.depth;
    state.tierEditing = false;
    if (prev === id) { renderWelcome(); return; }
    state.depth = id;
    refreshDepth();
    renderWelcome();
    applyComposerGate();
    if (state.awaitingTier) { $("composer-input").focus(); }
    sendEvent({ type: "command", name: I.budgetCommand(id) }, { quiet: true }).then(function (ack) {
      if (ack) { return; }
      state.depth = prev;
      refreshDepth();
      renderWelcome();
      applyComposerGate();
    });
  }
  /** Pressed state of every tier card and depth chip on screen, and the budget we know about (the planner's progress). */
  function refreshDepth() {
    Array.prototype.forEach.call(document.querySelectorAll(".depth-chip, .tier-card"), function (b) {
      b.setAttribute("aria-pressed", b.dataset.budget === state.depth ? "true" : "false");
    });
    var known = depthKnown();
    Array.prototype.forEach.call(document.querySelectorAll(".depth-note"), function (n) { n.textContent = known; });
  }

  // ---- the welcome block: the tier first, then the idea ---------------------------------------------------------
  function tierLocked() {
    return I.tierRequired({ hasSession: !!state.sessionId, awaiting: state.awaitingTier, tier: state.depth });
  }
  /** The idea box (and the idea buttons) wait for the tier; a visible line says why. Called whenever the tier, the
      project or the language changes. */
  function applyComposerGate() {
    var locked = tierLocked();
    var input = $("composer-input");
    input.disabled = locked;
    $("composer-send").disabled = locked;
    $("composer-mic").disabled = locked;
    var gate = $("composer-gate");
    gate.hidden = !locked;
    gate.textContent = locked ? t("ui.v2.tier_needed") : "";
    input.placeholder = locked ? t("ui.v2.composer_locked") : (state.awaitingTier ? t("ui.v2.idea_placeholder") : t("ui.type_here"));
    if (locked) { input.setAttribute("aria-describedby", "composer-gate"); } else { input.removeAttribute("aria-describedby"); }
  }
  /** Three large cards: what each tier means in plain words. A button each (keyboard: Tab, Enter or Space). */
  function tierCards() {
    var cards = h("div", { class: "tier-cards", role: "group", "aria-labelledby": "tier-title", "aria-describedby": "tier-help" });
    I.BUDGETS.forEach(function (b) {
      var lab = depthLabels(b.id);
      cards.appendChild(h("button", { class: "tier-card", type: "button", "aria-pressed": state.depth === b.id ? "true" : "false",
        data: { budget: b.id }, on: { click: function () { chooseDepth(b.id); } } },
        h("span", { class: "tier-name", text: lab.name }), h("span", { class: "tier-count", text: lab.hint }),
        h("span", { class: "tier-desc", text: lab.desc })));
    });
    return h("div", { class: "tier" }, h("div", { class: "tier-title", id: "tier-title", text: t("ui.v2.tier_title") }), cards,
      h("div", { class: "hint", id: "tier-help", text: t("ui.v2.tier_help") }));
  }
  /** The four ways to start. Disabled (with the reason in the line above the idea box) until a tier is chosen. */
  function startButtons() {
    var locked = tierLocked();
    var wrap = h("div", { class: "row-actions start-buttons" });
    function b(key, fn) {
      wrap.appendChild(h("button", { class: "btn", type: "button", text: t(key), disabled: locked, on: { click: fn } }));
    }
    b("welcome.start_describe", function () { $("composer-input").focus(); });
    b("welcome.start_upload", function () { pickFile(); });
    b("welcome.start_like_app", function () { $("composer-input").focus(); });
    b("welcome.start_just_build", function () { postUser(t("welcome.start_just_build")); });
    return wrap;
  }
  /** Rebuild the welcome block from the state: the three tier cards, or (once chosen) a chip with a "Change" link
      that stays until the first questions arrive. */
  function renderWelcome() {
    var host = state.welcomeEl;
    if (!host || !host.isConnected) { return; }
    clear(host);
    if (state.depth && !state.tierEditing) {
      var lab = depthLabels(state.depth);
      var row = h("div", { class: "tier-chosen" },
        h("span", { class: "tier-chip", text: t("ui.v2.tier_chosen", { name: lab.name, count: lab.hint }) }));
      if (!state.batches.length) {
        row.appendChild(h("button", { class: "btn-link tier-change", type: "button", "aria-label": t("ui.v2.tier_change_label"),
          text: t("ui.v2.tier_change"), on: { click: function () {
            state.tierEditing = true;
            renderWelcome();
            var pressed = host.querySelector('.tier-card[aria-pressed="true"]');
            if (pressed) { pressed.focus(); }
          } } }));
      }
      host.appendChild(row);
    } else {
      host.appendChild(tierCards());
    }
    if (state.awaitingTier) { host.appendChild(startButtons()); }
  }
  function showWelcome() {
    state.welcomeEl = h("div", { class: "welcome-actions" });
    log().appendChild(state.welcomeEl);
    renderWelcome();
    scrollChat();
  }

  /** A mock session must never be mistaken for the product: a banner the user cannot dismiss. */
  function renderMockBanner() {
    var banner = $("mock-banner");
    banner.textContent = t("ui.interview.mock_banner");
    banner.hidden = !(state.budget && state.budget.mode === "mock");
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
    card: function (p, replay) {
      var card = p.card;
      if (card.frame === WELCOME_FRAME) { return; }   // the welcome block (tier first) replaces the canned first card
      var loc = I.locate(state.batches, card);
      if (loc && !loc.followup && (state.answered[card.id] || state.submitted[loc.batch.batch_id])) { return; }   // already answered
      var pending = loc ? I.flatCards(I.pendingGroups(loc.batch, state.answered)) : [];
      if (I.useForm(loc, pending.length) && pending.some(function (c) { return c.id === card.id; })) {
        if (!replay) { showForm(loc.batch); }   // a reopened project draws its form once, after the whole log (replayHistory)
        return;
      }
      // a question that belongs to no batch (a follow-up card, an older server) is shown on its own
      collapseActive();
      var el = renderCard(card);
      log().appendChild(el);
      state.activeCard = { card: card, el: el, batch: null };
      renderChatActions(card);
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
      if (!state.batches.length && !state.depth) { el.appendChild(depthChips()); }   // an idea that came without a tier
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
    question_batch: function (p, replay) {
      state.batch = p;
      I.upsertBatch(state.batches, p, 40);   // a batch that is sent again replaces its earlier copy
      renderInterviewBar();
      renderWelcome();   // the tier can no longer change once questions exist
      if (replay) { return; }
      showForm(p);
      ensureFormLayout(p);
    },
    notice: function (p) {
      addMsg("system", p.text, p.chips || [], p.severity === "warning" ? "msg-warning" : "");
    },
    progress: function (p) {
      followStage(p.stage);   // the wait line follows a stage the server names (the Brief, a document)
      state.progress = p;
      refreshDepth();
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
    // drawn once: the stream replays events after a reconnect, and one event can arrive twice (the stream and the reply)
    if (!I.registerEvent(state.seen, ev)) { return; }
    if (ev.direction === "out" && ev.seq !== null && ev.seq !== undefined) { state.lastSeq = Math.max(state.lastSeq, ev.seq); }
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
    scheduleView();   // every turn can fill Brief rows: refresh them so the new ones are highlighted
    scheduleQa();
  }

  var IN_FLIGHT = 0;
  var sendQueue = Promise.resolve();
  /** Post one inbound event. Events of a session go out one at a time, in the order they were made (a command that
      sets the tier must reach the server before the idea does). Resolves to the EventAck, or null when the request
      failed. opts: quiet (no waiting indicator, for quick commands), onAck (called with the reply before it is drawn),
      onError (called with the error instead of the chat message, so a form can keep its state and offer a retry). A body that already has an event_id keeps it:
      the server ignores a repeated id, so a retry can never be applied twice. */
  function sendEvent(body, opts) {
    if (!state.sessionId) { return Promise.resolve(null); }
    var o = opts || {};
    var sid = state.sessionId;
    var ev = Object.assign({ event_id: randomId() }, body);
    if (state.contributor && !ev.contributor_id) { ev.contributor_id = state.contributor; }
    if (ev.type !== "command" && state.awaitingTier) { state.awaitingTier = false; applyComposerGate(); renderWelcome(); }
    return enqueue(function () { return postEvent(sid, ev, o); });
  }
  /** One request of a session at a time, in the order the person made them (the job never rejects). */
  function enqueue(job) {
    var p = sendQueue.then(job);
    sendQueue = p;
    return p;
  }
  function postEvent(sid, ev, o) {
    if (sid !== state.sessionId) { return Promise.resolve(null); }   // another project was opened while this one waited
    var acked = null;
    IN_FLIGHT += 1;
    if (!o.quiet) { setTyping(true, I.phaseFor(ev, I.phaseContext(state.batches, ev))); }
    return api("POST", "/sessions/" + sid + "/events", ev).then(function (ack) {
      acked = ack;   // the server has the event: a page error while showing its answer must not look like a failed send
      try {
        if (o.onAck) { o.onAck(ack); }   // before its answer is drawn, so a form closes above the new questions
        processAck(ack);
      } catch (e) { noteRenderError(e); }
    }).catch(function (err) {
      if (o.onError) { o.onError(err); return; }
      var el = addMsg("error", friendly(err));
      el.setAttribute("role", "alert");
    }).then(function () { IN_FLIGHT -= 1; if (!o.quiet) { setTyping(false); } return acked; });
  }

  // ---- upload: one multipart request; the reply is an EventAck, handled like any other event ---------------------
  /** The picker only offers the supported types; the 15 MB limit is checked here, before any bytes are sent. */
  function fileInput(onFile) {
    var input = h("input", { type: "file", class: "sr-only", tabindex: "-1", accept: I.UPLOAD_ACCEPT, "aria-label": t("ui.upload") });
    input.addEventListener("change", function () {
      if (input.files && input.files.length) { onFile(input.files[0]); }
      input.value = "";   // the same file can be chosen again after a refusal
    });
    return input;
  }
  function uploadErrorText(err) {
    var key = I.uploadErrorKey(err && err.status);
    if (key) { return t(key); }
    return err && err.apiMessage ? err.apiMessage : t("ui.v2.upload_failed");
  }
  function uploadFile(file, note) {
    if (!state.sessionId || !file) { return Promise.resolve(); }
    var check = I.checkUpload(file);
    if (!check.ok) {   // too large or empty: said at once, nothing is sent
      if (note) { note.textContent = t(check.key); }
      var refused = addMsg("error", t(check.key));
      refused.setAttribute("role", "alert");
      return Promise.resolve();
    }
    var fd = new FormData();
    fd.append("file", file);
    var sid = state.sessionId;
    if (note) { note.textContent = t("ui.v2.upload_reading", { name: file.name }); }
    addMsg("user", file.name);
    return enqueue(function () {
      if (sid !== state.sessionId) { return null; }
      IN_FLIGHT += 1;
      setTyping(true, "document");   // "Reading your document…", visible and announced
      return api("POST", "/sessions/" + sid + "/uploads", fd).then(function (ack) {
        try { processAck(ack); } catch (e) { noteRenderError(e); }
        if (note) { note.textContent = ""; }
      }).catch(function (err) {   // 413 and 415 included: the page keeps working
        var text = uploadErrorText(err);
        if (note) { note.textContent = text; }
        var el = addMsg("error", text);
        el.setAttribute("role", "alert");
      }).then(function () { IN_FLIGHT -= 1; setTyping(false); });
    });
  }
  function pickFile() {
    var input = fileInput(function (file) { uploadFile(file, null); });
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
    var attempt = 0;   // failed connections in a row: the wait before the next one grows (1, 2, 4, 8, 15 s)
    function connect() {
      if (state.streamAbort !== ctl) { return; }
      var headers = authHeaders();
      headers["Last-Event-ID"] = String(state.lastSeq);
      headers["Accept"] = "text/event-stream";
      fetch(BASE + "/sessions/" + sid + "/stream?after=" + state.lastSeq, { headers: headers, signal: ctl.signal })
        .then(function (res) {
          if (!res.ok || !res.body) { throw new Error("stream " + res.status); }
          state.streamOk = true;
          attempt = 0;
          renderDevHead();
          return pump(res.body.getReader());
        })
        .catch(function () { /* fall through to reconnect */ })
        .then(function () {
          state.streamOk = false;
          renderDevHead();
          if (state.streamAbort === ctl) { window.setTimeout(connect, I.reconnectDelay(attempt) * 1000); attempt += 1; }
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
      var ev;
      try { ev = JSON.parse(data.join("\n")); } catch (e) { return; }   // a malformed frame is skipped
      try { onStored(ev, false); } catch (e) { noteRenderError(e); }     // a drawing bug is never swallowed
    }
    connect();
  }
  function stopStream() {
    if (state.streamAbort) { state.streamAbort.abort(); state.streamAbort = null; }
    state.streamOk = false;
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
    var el = h("div", { class: "brow" + (row.changed ? " changed" : "") + (row.needs_review ? " review" : ""), role: "group",
      "aria-label": row.label, data: { row: row.id } });
    // rows of "Details we captured" sit under their topic heading (row.group), which is also their label
    var repeats = !!row.group && row.label === row.group;
    var top = h("div", { class: "brow-top" },
      repeats ? null : h("span", { class: "brow-label", text: row.label }),
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
    highlightChanged(host, vm);
  }

  /** The rows the last turn filled or changed pulse once; on a wide screen the first one is scrolled into view. */
  function highlightChanged(host, vm) {
    if (!vm || !host.offsetParent) { return; }   // not on screen (another tab or pane): keep the pulse for later
    var fresh = {};
    var first = null;
    I.changedRows(vm).forEach(function (c) {
      var key = I.pulseKey(vm, c.row);
      if (!state.pulsed[key]) { fresh[c.row.id] = key; }
    });
    if (!Object.keys(fresh).length) { return; }
    if (Object.keys(state.pulsed).length > 500) { state.pulsed = {}; }
    Array.prototype.forEach.call(host.querySelectorAll(".brow.changed"), function (el) {
      var key = fresh[el.dataset.row];
      if (!key) { return; }
      state.pulsed[key] = true;
      el.classList.add("pulse");
      if (!first) { first = el; }
    });
    var wide = !(window.matchMedia && window.matchMedia("(max-width: 900px)").matches);
    var still = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (first && wide && state.tab === "brief" && first.scrollIntoView) {
      first.scrollIntoView({ block: "nearest", behavior: still ? "auto" : "smooth" });
    }
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
    var file = fileInput(function (f) { uploadFile(f, note); });
    var link = h("input", { type: "url", placeholder: t("x.link_placeholder"), "aria-label": t("x.add_link") });
    host.appendChild(h("div", { class: "row-actions" }, h("button", { class: "btn", type: "button", text: t("ui.upload"), on: { click: function () { file.click(); } } }), file,
      h("span", { class: "hint", text: t("ui.v2.upload_hint") })));
    host.appendChild(h("div", { class: "custom-row" }, link, h("button", { class: "btn", type: "button", text: t("x.add_link"), on: { click: function () {
      var u = link.value.trim();
      if (u) { link.value = ""; sendEvent({ type: "link_added", url: u }); }
    } } })));
    host.appendChild(note);
  }

  // ---- Questions and answers (History tab) --------------------------------------------------------------------
  function scheduleQa() {
    if (state.qaTimer) { return; }
    state.qaTimer = window.setTimeout(function () { state.qaTimer = null; loadQa(); }, 120);
  }
  function loadQa() {
    if (!state.sessionId) { return Promise.resolve(); }
    var sid = state.sessionId;
    return api("GET", "/sessions/" + sid + "/qa").then(function (d) {
      if (sid !== state.sessionId) { return; }
      state.qa = (d && d.items) || [];
      state.qaAnswered = state.qa.filter(function (q) { return I.qaStatus(q) !== "open"; }).length;
      renderInterviewBar();
      if (!state.qaEdit) { renderQa(); }
    }).catch(function () { /* the list is a convenience: the chat keeps working without it */ });
  }

  function qaStatusLabel(status) {
    if (status === "answered") { return t("ui.interview.qa_status_answered"); }
    if (status === "skipped") { return t("ui.interview.qa_status_skipped"); }
    if (status === "you_decide") { return t("ui.interview.qa_status_you_decide"); }
    return t("ui.interview.qa_status_open");
  }

  /** The inline editor of one answer: options for a choice question, fields for a form, a text area otherwise. */
  function qaEditorEl(item, ed) {
    var m = ed.model;
    var box = h("div", { class: "panel qa-editor" });
    var err = h("div", { class: "approval-error", role: "alert" });
    var name = "qa-" + item.card_id;
    if (m.kind === "single") {
      var group = h("div", { class: "options", role: "radiogroup", "aria-label": item.question });
      m.options.forEach(function (o) {
        var input = checkInput("radio", name, o.id);
        input.checked = m.choice === o.id;
        input.addEventListener("change", function () { m.choice = o.id; });
        group.appendChild(h("label", { class: "form-option" }, input, h("span", { class: "option-main" }, h("span", { class: "option-label", text: o.label }))));
      });
      var radio = checkInput("radio", name, I.OTHER);
      radio.checked = m.choice === I.OTHER;
      radio.addEventListener("change", function () { m.choice = I.OTHER; });
      var otherText = h("input", { type: "text", "aria-label": t("ui.something_else"), placeholder: t("card.custom_placeholder") });
      otherText.value = m.other;
      otherText.addEventListener("input", function () {
        m.other = otherText.value;
        if (otherText.value.trim()) { m.choice = I.OTHER; radio.checked = true; }
      });
      group.appendChild(h("div", { class: "form-option form-other" },
        h("label", { class: "form-other-label" }, radio, h("span", { text: t("ui.something_else") })), otherText));
      box.appendChild(group);
    } else if (m.kind === "multi") {
      var checks = h("div", { class: "options", role: "group", "aria-label": item.question });
      m.options.forEach(function (o) {
        var input = checkInput("checkbox", name, o.id);
        input.checked = m.picks.indexOf(o.id) >= 0;
        input.addEventListener("change", function () {
          m.picks = m.picks.filter(function (x) { return x !== o.id; });
          if (input.checked) { m.picks.push(o.id); }
          if (!m.ordered) { m.picks = m.options.map(function (x) { return x.id; }).filter(function (id) { return m.picks.indexOf(id) >= 0; }); }
        });
        checks.appendChild(h("label", { class: "form-option" }, input, h("span", { class: "option-main" }, h("span", { class: "option-label", text: o.label }))));
      });
      var extra = h("input", { type: "text", "aria-label": t("ui.something_else"), placeholder: t("card.custom_placeholder") });
      extra.value = m.other;
      extra.addEventListener("input", function () { m.other = extra.value; });
      checks.appendChild(h("div", { class: "form-option form-other" }, extra));
      box.appendChild(checks);
    } else if (m.kind === "fields") {
      m.options.forEach(function (o) {
        var input = h("input", { type: "text", "aria-label": o.label });
        input.value = m.fields[o.id];
        input.addEventListener("input", function () { m.fields[o.id] = input.value; });
        box.appendChild(h("label", { class: "field" }, h("span", { text: o.label }), input));
      });
    } else {
      var area = h("textarea", { rows: "3", "aria-label": item.question });
      area.value = m.text;
      area.addEventListener("input", function () { m.text = area.value; });
      box.appendChild(area);
    }
    var save = h("button", { class: "btn btn-primary", type: "button", text: t("x.save") });
    save.addEventListener("click", function () {
      var payload = I.qaEditPayload(item, m);
      if (!payload) { err.textContent = t("ui.interview.qa_edit_empty"); return; }
      err.textContent = "";
      save.disabled = true;
      save.textContent = t("ui.interview.qa_saving");
      window.compassSend.answerEdit(item.card_id, payload.value, payload.custom).then(function (ack) {
        if (!ack) {   // the request failed (the chat says why): keep the editor and the typed answer
          save.disabled = false;
          save.textContent = t("x.save");
          return;
        }
        state.qaEdit = null;
        if (!I.ackHasNotice(ack, "edit_unavailable")) { toast(t("ui.interview.qa_saved")); }
        loadQa();
        loadView();
      });
    });
    box.appendChild(err);
    box.appendChild(h("div", { class: "row-actions" }, save,
      h("button", { class: "btn", type: "button", text: t("x.cancel"), on: { click: function () { state.qaEdit = null; renderQa(); } } })));
    return box;
  }

  function qaItemEl(item) {
    var status = I.qaStatus(item);
    var followup = item.origin === "followup";
    var el = h("article", { class: "qa-item qa-depth-" + I.qaIndent(item) + (followup ? " qa-followup" : ""), data: { card: item.card_id } });
    var head = h("div", { class: "qa-head" });
    if (followup) { head.appendChild(h("span", { class: "followup-tag", text: "↳ " + t("ui.interview.followup_tag") })); }
    head.appendChild(h("span", { class: "qa-q", text: item.question }));
    el.appendChild(head);
    var meta = h("div", { class: "qa-meta" }, h("span", { class: "qa-status qa-status-" + status, text: qaStatusLabel(status) }));
    if (item.fallback) { meta.appendChild(fallbackBadge()); }
    el.appendChild(meta);
    el.appendChild(h("div", { class: "qa-a", text: I.qaAnswerText(item, t) }));
    var facts = item.facts || [];
    if (facts.length) {
      el.appendChild(h("div", { class: "small muted", text: t("ui.interview.qa_facts") }));
      el.appendChild(h("ul", { class: "qa-facts" }, facts.map(function (f) {
        var line = I.qaFactLine(f, t);
        return h("li", { class: line.inactive ? "qa-fact-off" : "" }, line.text, line.tag ? h("span", { class: "hint", text: " (" + line.tag + ")" }) : null);
      })));
    }
    var editing = state.qaEdit && state.qaEdit.cardId === item.card_id;
    if (I.qaCanEdit(item, isApproved()) && !editing) {
      el.appendChild(h("div", { class: "row-actions-bar" }, h("button", { class: "btn-link qa-edit", type: "button", text: t("x.edit"), on: { click: function () {
        state.qaEdit = { cardId: item.card_id, model: I.qaEditModel(item) };
        renderQa();
        var first = document.querySelector(".qa-editor input, .qa-editor textarea");
        if (first) { first.focus(); }
      } } })));
    }
    if (editing) { el.appendChild(qaEditorEl(item, state.qaEdit)); }
    return el;
  }

  function renderQa() {
    var host = $("qa-list");
    $("qa-title").textContent = t("ui.interview.qa_title");
    clear(host);
    if (!state.qa.length) { host.appendChild(h("div", { class: "empty", text: t("ui.interview.qa_empty") })); return; }
    state.qa.forEach(function (item) { host.appendChild(qaItemEl(item)); });
  }

  function turnText(ev) {
    var p = ev.payload || {};
    switch (ev.type) {
      case "user_message": return { who: "user", text: p.text || "" };
      case "card_answer": return { who: "user", text: (p.custom_text || (p.value === null || p.value === undefined ? "" : typeof p.value === "string" ? p.value : JSON.stringify(p.value))) || "(answer)" };
      case "card_skip": return { who: "user", text: t("ui.skip") };
      case "card_you_decide": return { who: "user", text: t("ui.you_decide") };
      case "card_back": return { who: "user", text: t("ui.back") };
      case "batch_answer": return { who: "user", text: t("ui.interview.form_sent", { count: (p.answers || []).length }) };
      case "answer_edit": return { who: "user", text: p.card_id + " → " + (p.custom_text || (typeof p.value === "string" ? p.value : JSON.stringify(p.value))) };
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
    var host = $("history-chat");
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
    if (name === "history") { renderHistoryTab(); renderQa(); loadQa(); }
    if (name === "brief" && state.view) { highlightChanged($("tab-brief"), state.view); }
    if (name === "lookup") { renderLookup(); }
    if (name === "sources") { renderSources(); }
  }
  function setPane(name) {
    $("workspace").dataset.pane = name;
    $("pane-chat").setAttribute("aria-selected", name === "chat" ? "true" : "false");
    $("pane-brief").setAttribute("aria-selected", name === "brief" ? "true" : "false");
    if (name === "brief" && state.view && state.tab === "brief") { highlightChanged($("tab-brief"), state.view); }
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
    return api("GET", "/budget").then(function (b) { state.budget = b; renderMeter(); renderDevHead(); renderMockBanner(); }).catch(function () { renderMeter(); });
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
  function generateClaudeReport() {
    var lines = [];
    lines.push("I encountered an issue while testing agent `" + (AGENT.id || "compass") + "` in the Mobigen static test UI.");
    lines.push("Please analyze this diagnostic trace and suggest or apply the fix in the codebase:\n");
    lines.push("### 📍 Environment & Session");
    lines.push("- **Agent**: `" + (AGENT.id || "compass") + "` | **API Base**: `" + BASE + "`");
    lines.push("- **Session ID**: `" + (state.sessionId || "none") + "`");
    lines.push("- **State Machine**: `" + (state.session ? state.session.state : "NONE") + "` | **Mode**: `" + state.mode + "` | **Locale**: `" + state.lang + "`");
    lines.push("- **Brief Version**: `" + (state.session ? state.session.brief_version : 0) + "` | **Brief Hash**: `" + (state.view ? state.view.brief_hash : "none") + "`");
    lines.push("- **Budget Mode**: `" + (state.budget ? state.budget.mode : "unknown") + "` | **Spent / Cap**: `$" + (state.budget ? state.budget.spent_usd : "0.00") + " / $" + (state.budget ? state.budget.cap_usd : "0.00") + "`");
    lines.push("- **Timestamp**: `" + new Date().toISOString() + "`\n");

    if (state.lastError) {
      lines.push("### ❌ Last Error");
      lines.push("- **Status**: `" + state.lastError.status + "`");
      lines.push("- **Request**: `" + state.lastError.method + " " + state.lastError.path + "`");
      lines.push("- **Code**: `" + state.lastError.code + "`");
      lines.push("- **Message**: " + state.lastError.message + "\n");
    }

    lines.push("### 🌐 Recent Network Transactions (Last 5)");
    if (!state.networkTrace.length) {
      lines.push("*(No network calls recorded)*\n");
    } else {
      lines.push("| # | Method | Path | Status | Latency |");
      lines.push("|---|---|---|---|---|");
      var recents = state.networkTrace.slice(-5).reverse();
      recents.forEach(function (tr, i) {
        lines.push("| " + (i + 1) + " | `" + tr.method + "` | `" + tr.path + "` | `" + tr.status + "` | " + tr.latency + "ms |");
      });
      lines.push("");
    }

    lines.push("### ⚡ Recent SSE Stream Events (Last 5)");
    if (!state.devEvents.length) {
      lines.push("*(No SSE events recorded)*\n");
    } else {
      var evs = state.devEvents.slice(-5);
      evs.forEach(function (ev) {
        lines.push("- `[#" + (ev.seq !== null && ev.seq !== undefined ? ev.seq : "-") + " " + ev.direction + "] " + ev.type + "`: `" + JSON.stringify(ev.payload) + "`");
      });
      lines.push("");
    }

    lines.push("### 🖥️ Active View & State");
    lines.push("- **Active Tab**: `" + state.tab + "`");
    lines.push("- **Layout**: `batch form (submit once)`" + (state.form ? " | **Open form**: `" + state.form.batchId + "` (" + state.form.cards.length + " questions" + (state.form.sending ? ", sending" : "") + ")" : ""));
    lines.push("- **Active Card ID**: `" + (state.activeCard && state.activeCard.card ? state.activeCard.card.id : "none") + "`");
    lines.push("- **Tier**: `" + (state.depth || "not chosen") + "`");

    return lines.join("\n");
  }

  function copyClaudeReport() {
    var report = generateClaudeReport();
    if (window.navigator && window.navigator.clipboard && window.navigator.clipboard.writeText) {
      window.navigator.clipboard.writeText(report).then(function () {
        toast("Diagnostic context copied for Claude Code!");
      }).catch(function () {
        toast("Could not access clipboard.");
      });
    } else {
      toast("Clipboard not available in this environment.");
    }
  }

  var DEV_TABS = [["diagnostics", "x.diagnostics"], ["events", "x.events"], ["state", "x.state"], ["slots", "x.slots"], ["calls", "x.calls"]];
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
    if (state.devTab === "diagnostics") {
      var actRow = h("div", { class: "diag-actions" },
        h("button", { class: "btn btn-primary", type: "button", text: "Copy Context for Claude Code", on: { click: copyClaudeReport } })
      );
      body.appendChild(actRow);
      if (state.lastError) {
        var errBox = h("div", { class: "shell-error" },
          h("div", { class: "evhead", text: "Last Error: [" + state.lastError.status + "] " + state.lastError.method + " " + state.lastError.path }),
          h("div", { class: "mono", text: state.lastError.code + ": " + state.lastError.message })
        );
        body.appendChild(errBox);
      }
      if (!state.networkTrace.length) {
        body.appendChild(h("div", { class: "empty", text: "No network requests recorded yet." }));
      } else {
        var nt = h("table", { class: "diag-table" },
          h("thead", null, h("tr", null,
            ["Method", "Path", "Status", "Latency", "Time"].map(function (x) { return h("th", { text: x }); }))));
        var nb = h("tbody");
        state.networkTrace.slice().reverse().forEach(function (tr) {
          var pillClass = tr.status >= 200 && tr.status < 300 ? "status-pill status-ok" : tr.status >= 400 ? "status-pill status-err" : "status-pill status-warn";
          nb.appendChild(h("tr", null,
            h("td", null, h("strong", { text: tr.method })),
            h("td", { text: tr.path }),
            h("td", null, h("span", { class: pillClass, text: String(tr.status) })),
            h("td", { text: tr.latency + "ms" }),
            h("td", { class: "muted", text: (tr.time || "").split("T")[1] ? (tr.time || "").split("T")[1].slice(0, 8) : "" })
          ));
        });
        nt.appendChild(nb);
        body.appendChild(nt);
      }
    } else if (state.devTab === "events") {
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

  function openSession(id) {
    stopStream();
    state.sessionId = id; state.lastSeq = 0; state.seen = {}; state.devEvents = []; state.view = null; state.viewJson = "";
    state.lookups = []; state.activeCard = null; state.ui = { panel: null, edit: null, prov: {} }; state.calls = [];
    state.batch = null; state.batches = []; state.answered = {}; state.submitted = {}; state.form = null; state.layoutAsked = false;
    state.depth = null; state.progress = null; state.qa = []; state.qaEdit = null; state.qaAnswered = 0; state.pulsed = {};
    state.awaitingTier = false; state.tierEditing = false; state.welcomeEl = null;
    applyComposerGate();
    $("home").hidden = true;
    $("workspace").hidden = false;
    clear(log());
    $("chat-actions").replaceChildren();
    $("progressbar").hidden = true;
    renderTabs(); setTab("brief"); setPane("chat"); renderQa(); renderInterviewBar();
    refreshSession().then(function () {
      if (state.session) { state.lang = state.session.locale || state.lang; $("lang-select").value = state.lang; applyChromeStrings(); }
      return loadStrings(state.lang);
    }).then(function () {
      return replayHistory();
    }).then(function () {
      startStream();
      loadView(); loadBudget(); loadQa(); renderLookup(); renderSources(); renderInterviewBar();
      if (!state.lastSeq) { state.awaitingTier = !state.depth; }
      applyComposerGate();
      if (state.awaitingTier || !state.lastSeq) { showWelcome(); }
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
      var summary = I.replaySummary(items);
      var answered = summary.answered;
      state.answered = answered;   // the form shows only the questions still waiting
      state.submitted = summary.submitted;
      state.depth = summary.tier;   // the tier chosen earlier comes back as the chip
      state.awaitingTier = !summary.started && !!state.session && state.session.state === "WELCOME";
      items.forEach(function (ev) {
        if (ev.direction === "in") {
          if (ev.type === "user_message") { addMsg("user", ev.payload.text || ""); }
          else if (ev.type === "card_answer" || ev.type === "batch_answer") { addMsg("user", turnText(ev).text); }
          return;
        }
        if (ev.type === "card" && ev.payload && ev.payload.card && ev.payload.card.frame === WELCOME_FRAME) {
          state.seen[ev.seq] = true; state.lastSeq = Math.max(state.lastSeq, ev.seq);   // nothing to draw: see showWelcome
          state.devEvents.push(ev);
          return;
        }
        if (ev.type === "card" && ev.payload && ev.payload.card && answered[ev.payload.card.id] && !I.locate(state.batches, ev.payload.card)) {
          var done = h("div", { class: "qcard done" }, h("div", { class: "small muted", text: ev.payload.card.question }));
          log().appendChild(done);
          state.seen[ev.seq] = true; state.lastSeq = Math.max(state.lastSeq, ev.seq);
          state.devEvents.push(ev);
          return;
        }
        onStored(ev, true);
        if (ev.type === "card") { lastCard = ev; }
      });
      var last = state.batches[state.batches.length - 1];
      if (last && !state.submitted[last.batch_id]) { showForm(last, true); }   // the form that was still open, drawn once
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
    $("btn-copy-claude").textContent = t("x.copy_claude");
    $("lang-label").textContent = t("ui.language");
    $("pane-chat").textContent = t("x.chat");
    $("pane-brief").textContent = t("x.brief");
    $("composer-input").placeholder = t("ui.type_here");
    $("composer-label").textContent = t("ui.type_here");
    $("composer-send").textContent = t("ui.send");
    $("composer-mic").setAttribute("aria-label", t("ui.mic"));
    $("typing-text").textContent = t("ui.typing");
    $("history-drawer-title").textContent = t("history.title");
    renderTabs(); renderMeter(); renderDevHead(); renderMockBanner(); renderInterviewBar(); renderQa();
    applyComposerGate(); renderWelcome();
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
    $("btn-copy-claude").addEventListener("click", copyClaudeReport);
    $("btn-settings").addEventListener("click", openSettings);
    $("settings-form").addEventListener("submit", saveSettings);
    $("settings-cancel").addEventListener("click", function () { $("settings-dialog").hidden = true; });
    $("btn-theme").addEventListener("click", toggleTheme);
    $("btn-dev").addEventListener("click", function () { setDev(!state.devOpen); });
    $("dev-close").addEventListener("click", function () { setDev(false); });
    $("history-close").addEventListener("click", function () { $("history-drawer").hidden = true; });
    $("btn-ask-more").addEventListener("click", askMore);
    $("pane-chat").addEventListener("click", function () { setPane("chat"); });
    $("pane-brief").addEventListener("click", function () { setPane("brief"); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        $("history-drawer").hidden = true; $("settings-dialog").hidden = true;
        if (state.ui.panel || state.ui.edit) { state.ui.panel = null; state.ui.edit = null; renderBrief(); }
        if (state.qaEdit) { state.qaEdit = null; renderQa(); }
      }
    });
    var input = $("composer-input");
    var voiceUsed = false;
    $("composer").addEventListener("submit", function (e) {
      e.preventDefault();
      var v = input.value.trim();
      if (!v || !state.sessionId || tierLocked()) { return; }
      input.value = "";
      if (!state.activeCard || !state.activeCard.batch) {   // a typed line answers a single card, never a form
        collapseActive(state.activeCard ? state.activeCard.card.question + " — " + v : "");
      }
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
