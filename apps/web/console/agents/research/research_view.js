/* Research screens: the pure, DOM-free helpers. Loaded by the shell as an "extras" file of the Compass screen set and
   exposed as window.researchView. Nothing here touches the page, the network or storage, so every function can be
   run under Node (apps/web/tests/test_research_ui.py does exactly that).

   It is also the written-down way to consume the Research HTTP contract (docs/api/research-agent.md):
     - parseSseFrames   bytes of the event stream -> frames (fetch + reader, because EventSource cannot send headers)
     - checkSeq         "ok" | "dup" | "gap": a gap means "refetch the snapshot"
     - applyEvent       fold one research.event.v1 event into the run state
     - applySnapshot    GET /runs/{id} -> the same state (a reloaded page draws from it, then tails from last_seq)
     - the review helpers group GET /programs/{id}/review for the "What we found" screens
   Text that comes from the server (labels, claims, domains) only ever passes through here as plain strings; the
   screens write it with textContent. A URL becomes a link only through safeUrl (http and https only). */
(function (root) {
  "use strict";

  var TERMINAL_TYPES = { "run.finished": true, "run.skipped": true, "run.failed": true };
  var ACTIVE_STATUS = { queued: true, running: true, committing: true };
  var FEED_CAP = 200;      // rows kept in the live feed
  var FACT_CAP = 40;       // facts kept in the "being checked" list
  var WARNING_CAP = 20;
  var LANES = ["blueprint", "integrations", "design", "screens", "mock", "verify"];
  var JOBS = ["features", "structure", "flows", "data", "services", "design", "screens", "local"];

  // ---- plain words (the one place copy for statuses, levels, flags and reasons lives) ----------------------------------

  var RUN_WORDS = {
    queued: "Starting", running: "Researching", committing: "Saving the findings", complete: "Done",
    partial: "Done, with gaps", failed: "Could not finish", skipped: "Stopped early", interrupted: "Interrupted"
  };
  var TOPIC_WORDS = {
    queued: "Waiting", running: "Researching", covered: "Done", not_found: "Nothing reliable found",
    cancelled: "Stopped", skipped: "Skipped", partial: "Partly done"
  };
  var LANE_INFO = {
    blueprint: { title: "Blueprint", note: "What the app does and how its parts fit together" },
    integrations: { title: "Integrations", note: "Outside services the app will need to connect to" },
    design: { title: "Design", note: "How apps like this usually look and feel" },
    screens: { title: "Screens", note: "Which screens apps like this usually have" },
    mock: { title: "Sample data", note: "Realistic example content for the mock-up" },
    verify: { title: "Checks", note: "Things to double-check before building" }
  };
  var JOB_TITLES = {
    features: "Features", structure: "Structure", flows: "Flows", data: "Data", services: "Services",
    design: "Design", screens: "Screens", local: "Local"
  };
  var AREA_TITLES = {
    feature: "Features", screen: "Screens", flow: "Flows", entity: "Data the app keeps",
    integration: "Integrations", design: "Design"
  };
  var SUGGESTION_TITLES = {
    features: "Feature ideas", screens: "Screen ideas", fields: "Field ideas",
    states: "Screen states", validations: "Input checks"
  };
  var LEVEL_WORDS = {
    core: "Core", common: "Common", advanced: "Advanced", emerging: "Emerging", limited_evidence: "Limited evidence"
  };
  var LEVEL_HINTS = {
    core: "Almost every comparable app does this", common: "Most comparable apps do this",
    advanced: "Only some of the more mature apps do this", emerging: "New and growing, not yet usual",
    limited_evidence: "We found little evidence either way"
  };
  var FLAG_WORDS = {
    "single-source": "Only one source", conflict: "Sources disagree", stale: "May be out of date",
    "needs-confirm": "Please confirm", "legal-adjacent": "Legal-adjacent",
    entity_from_model: "Name not confirmed online", assumption_derived: "Based on an assumption",
    geo_uncertain: "Place uncertain", limited_evidence: "Limited evidence", withdrawn: "Withdrawn",
    unchecked: "Not fully checked", checker_same_vendor: "Checked by the same model family"
  };
  var GAP_WORDS = {
    not_found: "We could not find reliable sources.", user_cancelled: "You stopped this topic.",
    provider_down: "A search service was not available.", skipped: "Research stopped before this topic.",
    budget: "We reached the time or search limit.", timeout: "It took too long."
  };
  var SKIP_WORDS = {
    no_content: "no readable text", paywall: "behind a paywall", blocked: "the site did not let us in",
    not_html: "not a web page", too_large: "too large to read", timeout: "too slow to open"
  };
  var KIND_WORDS = {
    "query.issued": "Search", "search.results": "Sources", "page.reading": "Reading", "page.read": "Read",
    "page.skipped": "Skipped", "fact.found": "Checking", "fact.verified": "Checked", "fact.rejected": "Dropped",
    "entity.discovered": "Found", "round.completed": "Round", "gap.detected": "Second look",
    warning: "Note", "plan.ready": "Plan", "run.started": "Start", "target.started": "Topic",
    "target.covered": "Topic done", "target.not_found": "Topic", "target.cancelled": "Topic stopped",
    "run.finished": "Finished", "run.skipped": "Stopped", "run.failed": "Problem"
  };

  function words(map, key, fallback) {
    if (key === undefined || key === null || key === "") { return fallback || ""; }
    return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : humanize(key);
  }
  /** "no_content" -> "no content" (an unknown code from a newer server still reads as words). */
  function humanize(code) { return String(code === undefined || code === null ? "" : code).replace(/[_-]+/g, " ").trim(); }
  function runWord(status) { return words(RUN_WORDS, status, "Starting"); }
  function topicWord(status) { return words(TOPIC_WORDS, status, "Waiting"); }
  function levelWord(level) { return words(LEVEL_WORDS, level, ""); }
  function levelHint(level) { return Object.prototype.hasOwnProperty.call(LEVEL_HINTS, level) ? LEVEL_HINTS[level] : ""; }
  function flagWord(flag) { return words(FLAG_WORDS, flag, ""); }
  function gapWord(reason) {
    return Object.prototype.hasOwnProperty.call(GAP_WORDS, reason) ? GAP_WORDS[reason] : "We could not finish this topic.";
  }
  function laneTitle(lane) { return LANE_INFO[lane] ? LANE_INFO[lane].title : humanize(lane); }
  function laneNote(lane) { return LANE_INFO[lane] ? LANE_INFO[lane].note : ""; }
  function jobTitle(job) { return words(JOB_TITLES, job, ""); }
  function areaTitle(area) { return words(AREA_TITLES, area, ""); }
  function suggestionTitle(group) { return words(SUGGESTION_TITLES, group, ""); }
  function isActiveStatus(status) { return !!ACTIVE_STATUS[status]; }
  function isTerminalType(type) { return !!TERMINAL_TYPES[type]; }
  function isTerminalStatus(status) { return !!status && !ACTIVE_STATUS[status]; }

  // ---- small formatters ---------------------------------------------------------------------------------------------

  function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }
  /** Seconds as "45 s", "1 min 20 s", "3 min", "1 h 5 min". null/undefined -> "". */
  function formatDuration(sec) {
    var s = num(sec);
    if (s === null) { return ""; }
    s = Math.max(0, Math.round(s));
    if (s < 60) { return s + " s"; }
    if (s < 3600) {
      var m = Math.floor(s / 60), r = s % 60;
      return m + " min" + (r ? " " + r + " s" : "");
    }
    var hh = Math.floor(s / 3600), mm = Math.round((s % 3600) / 60);
    if (mm === 60) { hh += 1; mm = 0; }
    return hh + " h" + (mm ? " " + mm + " min" : "");
  }
  /** The time left, rounded so it does not flicker: null -> "estimating", under 10 s -> "almost done". */
  function remainingText(eta) {
    var s = num(eta);
    if (s === null) { return "estimating"; }
    if (s < 10) { return "almost done"; }
    var rounded = s < 120 ? Math.round(s / 5) * 5 : Math.round(s / 60) * 60;
    return "about " + formatDuration(rounded);
  }
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  /** "2026-05-02" (or a full timestamp) -> "2 May 2026"; anything else is returned as it came. */
  function formatDate(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
    if (!m) { return value ? String(value) : ""; }
    var month = parseInt(m[2], 10);
    if (month < 1 || month > 12) { return String(value); }
    return parseInt(m[3], 10) + " " + MONTHS[month - 1] + " " + m[1];
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
  function pct(filled, required) {
    if (!(required > 0)) { return 0; }
    return Math.max(0, Math.min(100, Math.round(100 * filled / required)));
  }
  /** Seconds to wait before the next stream reconnect: 1, 2, 4, 8, then 15. rand (0..1) adds up to 20 percent. */
  function reconnectDelay(attempt, rand) {
    var base = Math.min(15, Math.pow(2, Math.max(0, attempt || 0)));
    return rand === undefined ? base : base * (1 + 0.2 * rand);
  }

  // ---- URLs ----------------------------------------------------------------------------------------------------------

  /** The href for a link, or null. Only http and https; no credentials in the address; nothing relative. */
  function safeUrl(value) {
    if (typeof value !== "string") { return null; }
    var text = value.trim();
    if (!text || text.length > 2048) { return null; }
    var u;
    try { u = new URL(text); } catch (e) { return null; }
    if (u.protocol !== "http:" && u.protocol !== "https:") { return null; }
    if (u.username || u.password || !u.hostname) { return null; }
    return u.href;
  }
  /** Normalise the research service address from Settings: http(s) origin plus an optional path, no trailing slash. */
  function normalizeBaseUrl(value, fallback) {
    var href = safeUrl(String(value || ""));
    if (!href) { return fallback; }
    var u = new URL(href);
    if (u.search || u.hash) { return fallback; }
    return (u.origin + u.pathname).replace(/\/+$/, "");
  }

  // ---- the event stream ----------------------------------------------------------------------------------------------

  /** Split a buffer of server-sent events into complete frames; the unfinished tail comes back as `rest`.
      A frame is {id, event, data, retry}; `data` is null for a frame that has none (retry, keepalive comments). */
  function parseSseFrames(buffer) {
    var text = String(buffer || "").replace(/\r\n/g, "\n");
    var parts = text.split("\n\n");
    var rest = parts.pop();
    var frames = [];
    parts.forEach(function (block) {
      var frame = { id: null, event: null, data: null, retry: null };
      var data = [];
      block.split("\n").forEach(function (line) {
        if (!line || line.charAt(0) === ":") { return; }
        var at = line.indexOf(":");
        var field = at === -1 ? line : line.slice(0, at);
        var value = at === -1 ? "" : line.slice(at + 1).replace(/^ /, "");
        if (field === "data") { data.push(value); }
        else if (field === "id") { frame.id = value; }
        else if (field === "event") { frame.event = value; }
        else if (field === "retry") { frame.retry = parseInt(value, 10) || null; }
      });
      if (data.length) { frame.data = data.join("\n"); }
      if (frame.data !== null || frame.retry !== null || frame.id !== null) { frames.push(frame); }
    });
    return { frames: frames, rest: rest };
  }
  /** "ok" (the next event), "dup" (already seen) or "gap" (an event is missing: refetch the snapshot). */
  function checkSeq(lastSeq, seq) {
    if (typeof seq !== "number") { return "dup"; }
    var last = lastSeq || 0;
    if (seq <= last) { return "dup"; }
    return seq === last + 1 ? "ok" : "gap";
  }

  // ---- run state: events and snapshot folded into one object -----------------------------------------------------------

  function emptyState() {
    return {
      runId: null, programId: null, status: "queued", phase: "", round: 0,
      loadClass: "", depthWord: "", minutes: null, planSource: "", degraded: false,
      targets: [], declined: [], entities: [], facts: [], factsRejected: 0, warnings: [], feed: [],
      counters: {}, countersAt: 0, roundCoverage: null, gap: null,
      lastSeq: 0, result: null, failure: null, reason: null, contentHash: null, startedAt: null, finishedAt: null,
      now: ""
    };
  }

  function targetIndex(state, id) {
    for (var i = 0; i < state.targets.length; i++) { if (state.targets[i].id === id) { return i; } }
    return -1;
  }
  function targetOf(state, id) {
    var i = targetIndex(state, id);
    return i === -1 ? null : state.targets[i];
  }
  function toTarget(t) {
    return {
      id: t.id, label: t.label || t.topic || t.id, job: t.job || "", lane: t.lane || "", priority: num(t.priority) || 0,
      status: t.status || "queued", slotsFilled: num(t.slots_filled) || 0, slotsRequired: num(t.slots_required) || 0
    };
  }
  function nameKey(text) { return String(text || "").toLowerCase().replace(/\s+/g, " ").trim(); }

  /** Fold one research.event.v1 event into `state` (in place) and return it. `nowMs` stamps the counters so the
      screen can tick the clocks between events. */
  function applyEvent(state, ev, nowMs) {
    var d = ev.detail || {};
    var type = ev.type;
    if (typeof ev.seq === "number" && ev.seq > state.lastSeq) { state.lastSeq = ev.seq; }
    if (ev.run_id && !state.runId) { state.runId = ev.run_id; }
    if (ev.counters) { state.counters = Object.assign({}, ev.counters); state.countersAt = nowMs || 0; }
    if (ev.phase) { state.phase = ev.phase; }
    if (typeof ev.round === "number") { state.round = ev.round; }
    state.now = ev.label || state.now;
    state.feed.push({
      seq: ev.seq, type: type, ts: ev.ts || "", label: ev.label || "", targetId: ev.target_id || null, detail: d
    });
    if (state.feed.length > FEED_CAP) { state.feed.splice(0, state.feed.length - FEED_CAP); }

    if (type === "run.started") {
      if (!isTerminalStatus(state.status)) { state.status = "running"; }
      state.loadClass = d.load_class || state.loadClass;
      state.depthWord = d.depth_word || state.depthWord;
      state.minutes = num(d.minutes) !== null ? d.minutes : state.minutes;
      state.planSource = d.plan_source || state.planSource;
      state.degraded = !!d.degraded;
    } else if (type === "plan.ready") {
      state.targets = (d.targets || []).map(toTarget);
      state.declined = (d.declined || []).map(String);
    } else if (type === "target.started" || type === "target.covered" || type === "target.not_found" ||
               type === "target.cancelled") {
      var t = targetOf(state, ev.target_id);
      if (!t && ev.target_id) {
        t = toTarget({ id: ev.target_id, label: d.topic || ev.label, job: d.job });
        state.targets.push(t);
      }
      if (t) {
        t.status = { "target.started": "running", "target.covered": "covered", "target.not_found": "not_found",
          "target.cancelled": "cancelled" }[type];
        if (num(d.slots_filled) !== null) { t.slotsFilled = d.slots_filled; }
        if (num(d.slots_required) !== null) { t.slotsRequired = d.slots_required; }
        if (d.queries && d.queries.length) { t.queries = d.queries.map(String); }
      }
    } else if (type === "fact.found" || type === "fact.verified") {
      var label = d.label || ev.label;
      var key = nameKey(label);
      var fact = null;
      for (var i = 0; i < state.facts.length; i++) { if (state.facts[i].key === key) { fact = state.facts[i]; } }
      if (!fact) {
        fact = { key: key, label: label, kind: d.kind || "", topic: d.topic || "", status: "checking", domain: "", level: "" };
        state.facts.push(fact);
        if (state.facts.length > FACT_CAP) { state.facts.shift(); }
      }
      if (type === "fact.verified") {
        fact.status = "checked";
        fact.domain = d.domain || fact.domain;
        fact.level = d.level || fact.level;
      }
    } else if (type === "fact.rejected") {
      state.factsRejected += 1;
      var rk = nameKey(d.label);
      if (rk) { state.facts = state.facts.filter(function (f) { return f.key !== rk; }); }
    } else if (type === "entity.discovered") {
      var name = String(d.name || "").trim();
      if (name && !state.entities.some(function (n) { return nameKey(n) === nameKey(name); })) { state.entities.push(name); }
    } else if (type === "round.completed") {
      state.roundCoverage = { round: d.round || ev.round || 0, filled: num(d.slots_filled) || 0,
        required: num(d.slots_required) || 0, newSlots: num(d.new_slots) || 0 };
    } else if (type === "gap.detected") {
      state.gap = { count: num(d.count) !== null ? d.count : (d.targets || []).length, targets: (d.targets || []).map(String) };
    } else if (type === "warning") {
      var code = d.code || "";
      state.warnings.push({ seq: ev.seq, code: code, text: d.message || ev.label || "", slow: warningIsSlow(code) });
      if (state.warnings.length > WARNING_CAP) { state.warnings.shift(); }
    } else if (type === "run.finished") {
      state.status = d.status || "complete";
      state.result = d;
      state.finishedAt = ev.ts || state.finishedAt;
    } else if (type === "run.skipped") {
      state.status = "skipped";
      state.result = d;
      state.finishedAt = ev.ts || state.finishedAt;
    } else if (type === "run.failed") {
      state.status = "failed";
      state.failure = { reason: d.reason || "", code: d.code || "" };
      state.finishedAt = ev.ts || state.finishedAt;
    }
    return state;
  }

  /** A warning code that means "slower than usual" (the screen then says so in its own words). */
  function warningIsSlow(code) { return /slow|longer|timeout|timed_out|rate|delay|retry/i.test(String(code || "")); }

  /** GET /runs/{id} -> state. The feed (the last 60 events) is folded first, then the snapshot's own fields overwrite
      whatever the older events said, so a reload shows exactly what the server knows now. */
  function applySnapshot(snap, nowMs) {
    var state = emptyState();
    (snap.feed || []).forEach(function (ev) { applyEvent(state, ev, nowMs); });
    state.runId = snap.run_id || state.runId;
    state.programId = snap.program_id || state.programId;
    state.status = snap.status || state.status;
    state.phase = snap.phase || state.phase;
    state.round = num(snap.round) !== null ? snap.round : state.round;
    state.loadClass = snap.load_class || state.loadClass;
    state.depthWord = snap.depth_word || state.depthWord;
    state.planSource = snap.plan_source || state.planSource;
    state.degraded = !!(snap.input_quality && snap.input_quality.degraded) || state.degraded;
    state.startedAt = snap.started_at || null;
    state.finishedAt = snap.finished_at || null;
    state.reason = snap.reason || null;
    state.contentHash = snap.content_hash || null;
    if (snap.counters) { state.counters = Object.assign({}, snap.counters); state.countersAt = nowMs || 0; }
    if (snap.targets) { state.targets = snap.targets.map(toTarget); }
    if (snap.declined) { state.declined = snap.declined.map(String); }
    if (Array.isArray(snap.entities)) {       // the snapshot's list is the whole list, in the server's order
      var names = [];
      snap.entities.concat(state.entities).forEach(function (n) {
        if (!names.some(function (e) { return nameKey(e) === nameKey(n); })) { names.push(String(n)); }
      });
      state.entities = names;
    }
    state.lastSeq = num(snap.last_seq) !== null ? snap.last_seq : state.lastSeq;
    if (snap.status === "failed" && !state.failure) { state.failure = { reason: snap.reason || "", code: "" }; }
    return state;
  }

  // ---- what the progress screen shows -----------------------------------------------------------------------------------

  /** The coverage bar: slots filled of required. The per-topic numbers (they move with every topic event) win; the last
      round.completed numbers are the fallback before any topic reports slots. plan.ready does not say how many slots a
      topic needs, so topics that have not reported yet count as the average of those that have (`estimated` is then
      true) and the bar does not leap when each topic starts. */
  function coverage(state) {
    var filled = 0, required = 0, known = 0, unknown = 0;
    state.targets.forEach(function (t) {
      if (t.slotsRequired > 0) { required += t.slotsRequired; known += 1; filled += Math.min(t.slotsFilled, t.slotsRequired); }
      else if (t.status === "queued" || t.status === "running") { unknown += 1; }
    });
    var estimated = false;
    if (known > 0 && unknown > 0) { required += unknown * Math.ceil(required / known); estimated = true; }
    if (!(required > 0) && state.roundCoverage) { filled = state.roundCoverage.filled; required = state.roundCoverage.required; }
    return { filled: filled, required: required, percent: pct(filled, required), estimated: estimated };
  }
  /** Topics done of total: the server's counters when it sent them, else counted from the topic rows. */
  function topicTotals(state) {
    var c = state.counters || {};
    if (num(c.targets_total) !== null && c.targets_total > 0) { return { done: num(c.targets_done) || 0, total: c.targets_total }; }
    var done = state.targets.filter(function (t) { return t.status !== "queued" && t.status !== "running"; }).length;
    return { done: done, total: state.targets.length };
  }
  /** The numbers on the tiles, as text. */
  function counterTexts(state) {
    var c = state.counters || {};
    var tt = topicTotals(state);
    var planned = num(c.searches_planned);
    return {
      searches: String(num(c.searches_used) || 0) + (planned ? " of about " + planned : ""),
      pages: String(num(c.pages_read) || 0),
      facts: String(num(c.facts_verified) || 0),
      topics: tt.done + " of " + tt.total
    };
  }
  /** Elapsed and time left now: the last counters plus the time since they arrived (only while the run is active). */
  function clocks(state, nowMs) {
    var c = state.counters || {};
    var ticking = isActiveStatus(state.status) && state.countersAt && nowMs;
    var drift = ticking ? Math.max(0, (nowMs - state.countersAt) / 1000) : 0;
    var elapsed = num(c.elapsed_s);
    var eta = num(c.eta_s);
    return {
      elapsed: elapsed === null ? null : elapsed + drift,
      eta: eta === null || !isActiveStatus(state.status) ? null : Math.max(0, eta - drift)
    };
  }
  /** The slow-provider note for the progress screen: {slow, notes[]}; the notes are the server's own plain words. */
  function warningSummary(state) {
    var notes = [];
    state.warnings.forEach(function (w) { if (w.text && notes.indexOf(w.text) === -1) { notes.push(w.text); } });
    return { slow: state.warnings.some(function (w) { return w.slow; }), notes: notes.slice(-3) };
  }
  function depthSentence(state) {
    if (!state.depthWord && !state.loadClass) { return ""; }
    var base = state.depthWord ? "We are doing " + state.depthWord : "We are doing a " + state.loadClass + "-depth study";
    var minutes = num(state.minutes);
    return base + (minutes ? ", with about " + plural(minutes, "minute") + " set aside" : "") + ".";
  }

  /** One row of the live feed: {kind, text, extra, meta, quiet}. `text` is the server's label, as is. */
  function feedRow(ev) {
    var d = ev.detail || {};
    var kind = KIND_WORDS[ev.type] || "Update";
    var text = ev.label || fallbackLabel(ev);
    var extra = "", meta = "", quiet = false;
    if (ev.type === "query.issued") {
      extra = d.query ? "“" + d.query + "”" : "";
      meta = [d.variant, [d.lang, d.country].filter(Boolean).join("-"), d.provider].filter(Boolean).join(" · ");
    } else if (ev.type === "search.results") {
      var domains = (d.domains || []).slice(0, 6);
      extra = (num(d.count) !== null ? plural(d.count, "result") : "") + (domains.length ? ": " + domains.join(", ") : "");
    } else if (ev.type === "page.reading" || ev.type === "page.read") {
      extra = [d.domain, d.title].filter(Boolean).join(" — ");
    } else if (ev.type === "page.skipped") {
      quiet = true;
      extra = [d.domain, d.reason ? words(SKIP_WORDS, d.reason, "") : ""].filter(Boolean).join(" — ");
    } else if (ev.type === "fact.found") {
      extra = d.topic ? "For: " + d.topic : "";
    } else if (ev.type === "fact.verified") {
      extra = d.domain ? "Source: " + d.domain : "";
    } else if (ev.type === "fact.rejected") {
      quiet = true;
    } else if (ev.type === "entity.discovered") {
      extra = [d.name, d.kind ? "(" + humanize(d.kind) + ")" : ""].filter(Boolean).join(" ");
    } else if (ev.type === "round.completed") {
      extra = num(d.slots_required) ? d.slots_filled + " of " + d.slots_required + " checks filled" : "";
    } else if (ev.type === "warning") {
      extra = d.message && d.message !== ev.label ? d.message : "";
    } else if (ev.type === "target.not_found") {
      extra = d.queries && d.queries.length ? "We tried: " + d.queries.slice(0, 3).join("; ") : "";
    }
    if (extra === text) { extra = ""; }
    return { kind: kind, text: text, extra: extra, meta: meta, quiet: quiet, type: ev.type };
  }
  function fallbackLabel(ev) {
    var d = ev.detail || {};
    return d.query || d.title || d.name || d.label || humanize(String(ev.type || "update").replace(/\./g, " "));
  }

  // ---- a failure in plain words ---------------------------------------------------------------------------------------------

  /** A short sentence for the owner: whatever the server says if it reads like a sentence, never a stack trace. */
  function plainReason(text, fallback) {
    var s = String(text === undefined || text === null ? "" : text).replace(/\s+/g, " ").trim();
    var generic = fallback || "Something went wrong while researching.";
    if (!s) { return generic; }
    if (/traceback|exception|\bat\s+[\w.$]+\(|file "|\.py\b|\bstack\b|errno|0x[0-9a-f]{4,}/i.test(s)) { return generic; }
    return s.length > 240 ? s.slice(0, 237) + "..." : s;
  }
  /** An HTTP problem as {kind, text}. status 0 is "no answer at all" (a network error or a blocked address). */
  function problemText(status, body, ctx) {
    var c = ctx || {};
    var message = body && typeof body.message === "string" ? plainReason(body.message, "") : "";
    var code = body && typeof body.code === "string" ? body.code : "";
    if (!status) {
      return { kind: "network", text: "We could not reach the research service" + (c.baseUrl ? " at " + c.baseUrl : "") +
        ". Check that it is running and that the address in Settings is right, then try again." +
        (c.custom ? " If you changed the address, the Compass server has to allow it too (its MDE_RESEARCH_ORIGIN setting)." : "") };
    }
    if (status === 401) { return { kind: "auth", text: "The research service did not accept your sign-in. Open Settings and check the Token, Tenant and User." }; }
    if (status === 409) { return { kind: "conflict", text: message || "Research is already running for this project." }; }
    if (status === 404) { return { kind: "missing", text: message || "The research service does not know that run." }; }
    if (status === 413) { return { kind: "too_large", text: "This export is too large for the research service (the limit is 8 MB)." }; }
    if (status === 422 || status === 400) {
      return { kind: "refused", text: message || "The research service could not use this Brief.", code: code };
    }
    if (status >= 500) { return { kind: "server", text: "The research service had a problem. Please try again in a moment." }; }
    return { kind: "other", text: message || "Something went wrong talking to the research service." };
  }
  /** The run id named in a 409 run_active answer: a `run_id` field, else an id inside the message. */
  function runIdFrom(body) {
    if (!body || typeof body !== "object") { return null; }
    if (typeof body.run_id === "string" && body.run_id) { return body.run_id; }
    var m = /\brrn_[0-9A-Za-z]+/.exec(String(body.message || ""));
    return m ? m[0] : null;
  }

  // ---- the export a run starts from ---------------------------------------------------------------------------------------------

  /** What the screens need to know about a Compass export without reading all of it:
      {programId (the session id or null), key (a stable per-project key), title, briefHash, version} or null. */
  function exportInfo(exp) {
    if (!exp || typeof exp !== "object" || Array.isArray(exp)) { return null; }
    if (!exp.export || typeof exp.export !== "object" || !exp.brief || typeof exp.brief !== "object") { return null; }
    var session = exp.session && typeof exp.session === "object" ? exp.session : {};
    var programId = typeof session.id === "string" && session.id ? session.id : null;
    var baseline = exp.export.approved_baseline || {};
    var hash = String(exp.export.brief_hash || baseline.hash || exp.brief._hash || "");
    var idea = exp.consumer_views && exp.consumer_views.research && exp.consumer_views.research.idea || {};
    var title = String(idea.category || session.user_label || session.title || "").trim();
    if (!title) { title = String(idea.pitch || "").trim().slice(0, 60); }
    return {
      programId: programId, key: programId || (hash ? "brief:" + hash.slice(0, 16) : ""),
      title: title || "Saved export", briefHash: hash, version: exp.export.brief_version || baseline.version || null
    };
  }

  // ---- remembered runs (the screens keep this map in localStorage) ---------------------------------------------------------------

  var MAX_REMEMBERED = 12;
  /** map {key: {runId, programId, status, title, briefHash, at}} + one record -> a new map, newest 12 only. */
  function rememberRun(map, key, rec) {
    var next = Object.assign({}, map || {});
    next[key] = Object.assign({}, next[key] || {}, rec);
    var keys = Object.keys(next).sort(function (a, b) { return (next[b].at || 0) - (next[a].at || 0); });
    keys.slice(MAX_REMEMBERED).forEach(function (k) { delete next[k]; });
    return next;
  }
  function forgetRun(map, key) {
    var next = Object.assign({}, map || {});
    delete next[key];
    return next;
  }
  function recentRuns(map, limit) {
    var m = map || {};
    return Object.keys(m).map(function (k) { return Object.assign({ key: k }, m[k]); })
      .filter(function (r) { return r && r.runId; })
      .sort(function (a, b) { return (b.at || 0) - (a.at || 0); }).slice(0, limit || 5);
  }

  // ---- the findings ("What we found") ---------------------------------------------------------------------------------------------

  function itemsOf(review) { return review && Array.isArray(review.items) ? review.items : []; }
  function itemMap(review) {
    var m = {};
    itemsOf(review).forEach(function (it) { m[it.id] = it; });
    return m;
  }
  /** The items of one Spec lane, in the order of its slice; a slice without item_ids falls back to the items' own lane. */
  function laneItems(review, lane) {
    var slice = review && review.slices && review.slices[lane];
    var by = itemMap(review);
    if (slice && Array.isArray(slice.item_ids)) {
      return slice.item_ids.map(function (id) { return by[id]; }).filter(Boolean);
    }
    return itemsOf(review).filter(function (it) { return it.lane === lane; });
  }
  function laneDigest(review, lane) {
    var slice = review && review.slices && review.slices[lane];
    return slice && typeof slice.digest === "string" ? slice.digest : "";
  }
  function jobItems(review, job) { return itemsOf(review).filter(function (it) { return it.job === job; }); }
  function visibleCount(items) { return items.filter(function (it) { return !it.rejected; }).length; }
  /** [{lane, title, note, digest, items}] for the six lanes, in the contract's order. */
  function playbook(review) {
    return LANES.map(function (lane) {
      return { lane: lane, title: laneTitle(lane), note: laneNote(lane), digest: laneDigest(review, lane), items: laneItems(review, lane) };
    });
  }
  /** [{job, title, items}] for the eight jobs. */
  function jobGroups(review) {
    return JOBS.map(function (job) { return { job: job, title: jobTitle(job), items: jobItems(review, job) }; });
  }
  /** "Seen in 4 of 5": prevalence as words, "" when the item has none. */
  function prevalenceText(p) {
    if (!p || num(p.seen_in) === null || !(p.of > 0)) { return ""; }
    return "Seen in " + p.seen_in + " of " + p.of;
  }
  /** The newest date we can show for an item: its newest source, else the newest source date of its recency block. */
  function itemDate(item) {
    var best = "";
    ((item && item.sources) || []).forEach(function (s) {
      var d = s.published_on || s.retrieved_on || "";
      if (d > best) { best = d; }
    });
    if (!best && item && item.recency && item.recency.newest_source) { best = item.recency.newest_source; }
    return best;
  }
  /** The sources of an item as [{domain, href|null, date}] (href only when the URL is safe). */
  function sourceLinks(item) {
    return ((item && item.sources) || []).map(function (s) {
      var href = safeUrl(s.url);
      return { domain: s.domain || (href ? new URL(href).hostname : "source"), href: href, date: s.published_on || s.retrieved_on || "", cls: s.source_class || "" };
    });
  }
  /** What is usual: norms grouped by area, the most common first. [{area, title, norms}] */
  function normGroups(review) {
    var order = ["feature", "screen", "flow", "entity", "integration", "design"];
    var norms = review && Array.isArray(review.norms) ? review.norms : [];
    var groups = {};
    norms.forEach(function (n) { (groups[n.area] = groups[n.area] || []).push(n); });
    var areas = order.filter(function (a) { return groups[a]; })
      .concat(Object.keys(groups).filter(function (a) { return order.indexOf(a) === -1; }));
    return areas.map(function (a) {
      var list = groups[a].slice().sort(function (x, y) { return ratio(y.prevalence) - ratio(x.prevalence); });
      return { area: a, title: areaTitle(a) || humanize(a), norms: list };
    });
  }
  function ratio(p) { return p && p.of > 0 ? (p.seen_in || 0) / p.of : 0; }
  /** Suggestions per group; every one starts OFF (`on` is always false here: the owner's toggles live on the page). */
  function suggestionGroups(review) {
    var s = (review && review.suggestions) || {};
    return Object.keys(s).filter(function (g) { return Array.isArray(s[g]); }).map(function (g) {
      return {
        group: g, title: suggestionTitle(g) || humanize(g),
        items: s[g].map(function (x, i) {
          return { key: g + ":" + i, label: x.label || "", prevalence: x.prevalence || null, itemIds: x.item_ids || [], on: false };
        })
      };
    });
  }
  function suggestionCount(review) {
    return suggestionGroups(review).reduce(function (n, g) { return n + g.items.length; }, 0);
  }
  /** "Please confirm": [{itemId, why, item|null}] */
  function confirmEntries(review) {
    var by = itemMap(review);
    return ((review && review.confirm) || []).map(function (c) { return { itemId: c.item_id, why: c.why || "", item: by[c.item_id] || null }; });
  }
  function gapEntries(review) {
    return ((review && review.gaps) || []).map(function (g) {
      return { targetId: g.target_id, label: g.label || g.target_id || "", reason: g.reason, why: gapWord(g.reason), queries: (g.queries || []).map(String) };
    });
  }
  function conflictEntries(review) {
    var by = itemMap(review);
    return ((review && review.conflicts) || []).map(function (c) {
      return { topic: c.topic || "", note: c.note || "", items: (c.item_ids || []).map(function (id) { return by[id]; }).filter(Boolean) };
    });
  }
  /** Mark an item rejected in a loaded review (every view reads the same flag). True when the item was found. */
  function markRejected(review, itemId) {
    var found = false;
    itemsOf(review).forEach(function (it) { if (it.id === itemId) { it.rejected = true; found = true; } });
    return found;
  }
  /** The numbers under "What we found": coverage and effort as short sentences. */
  function reviewFacts(review) {
    var cov = (review && review.coverage) || {};
    var b = (review && review.budget) || {};
    var out = [];
    if (num(cov.targets) !== null && cov.targets > 0) {
      out.push((cov.covered || 0) + " of " + plural(cov.targets, "topic") + " covered");
    }
    var live = itemsOf(review);
    out.push(plural(visibleCount(live), "finding"));
    if (num(b.searches_used) !== null) { out.push(plural(b.searches_used, "search", "searches")); }
    if (num(b.pages_read) !== null) { out.push(plural(b.pages_read, "page") + " read"); }
    if (num(b.seconds) !== null) { out.push(formatDuration(b.seconds)); }
    return out;
  }

  root.researchView = {
    LANES: LANES, JOBS: JOBS, FEED_CAP: FEED_CAP, FACT_CAP: FACT_CAP,
    humanize: humanize, runWord: runWord, topicWord: topicWord, levelWord: levelWord, levelHint: levelHint,
    flagWord: flagWord, gapWord: gapWord, laneTitle: laneTitle, laneNote: laneNote, jobTitle: jobTitle,
    areaTitle: areaTitle, suggestionTitle: suggestionTitle,
    isActiveStatus: isActiveStatus, isTerminalType: isTerminalType, isTerminalStatus: isTerminalStatus,
    formatDuration: formatDuration, remainingText: remainingText, formatDate: formatDate, plural: plural, pct: pct,
    reconnectDelay: reconnectDelay, safeUrl: safeUrl, normalizeBaseUrl: normalizeBaseUrl,
    parseSseFrames: parseSseFrames, checkSeq: checkSeq,
    emptyState: emptyState, applyEvent: applyEvent, applySnapshot: applySnapshot, targetOf: targetOf,
    coverage: coverage, topicTotals: topicTotals, counterTexts: counterTexts, clocks: clocks,
    warningSummary: warningSummary, warningIsSlow: warningIsSlow, depthSentence: depthSentence, feedRow: feedRow,
    plainReason: plainReason, problemText: problemText, runIdFrom: runIdFrom,
    exportInfo: exportInfo, rememberRun: rememberRun, forgetRun: forgetRun, recentRuns: recentRuns,
    itemsOf: itemsOf, itemMap: itemMap, laneItems: laneItems, laneDigest: laneDigest, jobItems: jobItems,
    visibleCount: visibleCount, playbook: playbook, jobGroups: jobGroups, prevalenceText: prevalenceText,
    itemDate: itemDate, sourceLinks: sourceLinks, normGroups: normGroups, suggestionGroups: suggestionGroups,
    suggestionCount: suggestionCount, confirmEntries: confirmEntries, gapEntries: gapEntries,
    conflictEntries: conflictEntries, markRejected: markRejected, reviewFacts: reviewFacts
  };
})(window);
