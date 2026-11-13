/* Research screens for the test console. Vanilla JS, no framework, no build step. Loaded by the shell as an "extras"
   file of the Compass screen set (shared/registry.js) after research_view.js and before compass.js, and exposed as
   window.MobigenResearch. Compass calls it; it never calls Compass: whatever it needs from Compass (the sign-in
   headers, a way to read the approved export) is handed over through configure() and start()/show().

   What it does (contract: docs/api/research-agent.md; the pure parts are in research_view.js):
     start / show   after Gate 1 the Compass screen starts a run (GET Compass /export, then POST /v1/research/runs) and shows
                    the "Researching your idea" screen in the Research tab; the run id is kept per project in
                    localStorage, so a click twice or a reload re-attaches to the same run instead of starting another
     progress       GET /runs/{id} (the snapshot) first, then GET /runs/{id}/events with fetch and a stream reader (resume
                    with Last-Event-ID, reconnect with backoff, a gap in seq refetches the snapshot); bursts of events are
                    folded at once and drawn at most every ~120 ms; the feed keeps 200 rows
     found          "What we found": GET /programs/{id}/review, per Spec lane, per job, what is usual, suggestions (off),
                    please confirm, gaps, conflicts; reject an item; download research.json; the raw JSON
     homePanel      the start screen's "Load a saved Compass export" control, plus the runs this browser remembers
   Everything the server sends is written with textContent (h and text nodes); the only links are source URLs that pass
   researchView.safeUrl, opened in a new tab with rel="noopener noreferrer". */
(function (root) {
  "use strict";

  var UI = root.MobigenUI;
  var V = root.researchView;
  var h = UI.h, clear = UI.clear, store = UI.store;

  var DEFAULT_BASE = "http://127.0.0.1:8120";   // the Research dev server (Settings can change it)
  var PREFIX = "/v1/research";
  var CFG_KEY = "research.cfg";                  // {baseUrl}
  var RUNS_KEY = "research.runs";                // {projectKey: {runId, programId, status, title, briefHash, at}}
  var PAGE_KEY = "research.page";                // the project key of the page-sized screen that was open (a reload reopens it)
  var MAX_EXPORT_BYTES = 8 * 1024 * 1024;        // the server's limit for brief_export
  var RENDER_GAP_MS = 120;                       // events are folded at once, drawn at most this often
  var ITEMS_PER_PAGE = 100;                      // findings drawn before "Show more"

  var auth = function () { return {}; };         // set by configure(): the sign-in headers, same as Compass
  var view = null;                               // the one screen that is attached right now
  var pending = {};                              // project key -> promise of a POST /runs in flight (never twice)
  var exportCache = {};                          // project key -> an export loaded from a file (a retry needs it)

  // ---- settings and remembered runs (localStorage; every access is guarded by the shared store) ---------------------------

  function readJson(key) {
    try { var v = JSON.parse(store.get(key) || "{}"); return v && typeof v === "object" ? v : {}; } catch (e) { return {}; }
  }
  function getBaseUrl() { return V.normalizeBaseUrl(readJson(CFG_KEY).baseUrl, DEFAULT_BASE); }
  function setBaseUrl(value) {
    var url = V.normalizeBaseUrl(value, DEFAULT_BASE);
    store.set(CFG_KEY, JSON.stringify({ baseUrl: url }));
    return url;
  }
  function knownRun(key) { var r = key ? readJson(RUNS_KEY)[key] : null; return r && r.runId ? r : null; }
  function isActive(key) { var r = knownRun(key); return !!r && V.isActiveStatus(r.status); }
  function remember(key, rec) {
    var clean = { at: Date.now() };
    Object.keys(rec).forEach(function (k) { if (rec[k] !== undefined && rec[k] !== null && rec[k] !== "") { clean[k] = rec[k]; } });
    store.set(RUNS_KEY, JSON.stringify(V.rememberRun(readJson(RUNS_KEY), key, clean)));
  }
  function forget(key) { store.set(RUNS_KEY, JSON.stringify(V.forgetRun(readJson(RUNS_KEY), key))); }

  // ---- HTTP: the same dev headers as Compass, one error shape ------------------------------------------------------------------

  function headersFor() {
    var a = auth() || {};
    var out = {};
    ["Authorization", "X-Tenant-Id", "X-User-Id"].forEach(function (k) { if (a[k]) { out[k] = a[k]; } });
    return out;
  }
  function problemError(status, body) {
    var p = V.problemText(status, body, { baseUrl: getBaseUrl(), custom: getBaseUrl() !== DEFAULT_BASE });
    var e = new Error(p.text);
    e.status = status; e.body = body; e.problem = p;
    return e;
  }
  /** The problem to show for any error: our own HTTP errors carry one; others (a failed export read) get the fallback. */
  function problemOf(err, fallback) {
    if (err && err.problem) { return err.problem; }
    var text = err && err.apiMessage ? V.plainReason(err.apiMessage, fallback) : fallback;
    return { kind: "other", text: text };
  }
  function request(method, path, body, signal) {
    var init = { method: method, headers: headersFor(), signal: signal };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = typeof body === "string" ? body : JSON.stringify(body); }
    return root.fetch(getBaseUrl() + PREFIX + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (!res.ok) { throw problemError(res.status, data); }
        return { data: data, text: text };
      });
    }, function (err) {
      if (err && err.name === "AbortError") { throw err; }
      throw problemError(0, null);      // no answer at all (down, blocked address, offline)
    });
  }
  function call(method, path, body) { return request(method, path, body).then(function (r) { return r.data; }); }
  function enc(s) { return encodeURIComponent(String(s)); }

  // ---- the attached screen ---------------------------------------------------------------------------------------------------

  function alive(v) { return view === v; }

  function newView(o) {
    return {
      host: o.host, mode: o.mode, key: o.key, programId: o.programId || null, title: o.title || "", briefHash: o.briefHash || "",
      loadExport: o.loadExport || null, onBack: o.onBack || null, fresh: !!o.fresh,
      runId: null, state: V.emptyState(), screen: "progress", refs: null,
      config: null, stream: null, attempt: 0, timers: { render: null, clock: null, reconnect: null, peek: null },
      skipping: false, cancelling: {}, resyncing: false, lostNote: false, feedShown: 0, nowAt: 0, announcedAt: 0,
      review: null, reviewState: "idle", reviewProblem: null, found: { tab: "playbook", job: "features", pages: {} },
      toggles: {}, itemEls: {}
    };
  }

  /** Attach a screen to `host` for one project. The same project on the same host is left alone (no second start). */
  function attach(o) {
    if (view && view.host === o.host && view.key === o.key) {
      if (o.title) { view.title = o.title; }
      if (o.loadExport) { view.loadExport = o.loadExport; }
      return view;
    }
    detach();
    var v = newView(o);
    view = v;
    if (v.mode === "page") { store.set(PAGE_KEY, v.key); }
    buildShell(v);
    loadConfig(v);
    boot(v, !!o.autoStart);
    return v;
  }
  function detach() {
    var v = view;
    if (!v) { return; }
    view = null;
    if (v.mode === "page") { store.set(PAGE_KEY, ""); }     // closed on purpose: a reload shows the start screen
    stopStream(v);
    stopClock(v);
    ["render", "reconnect", "peek"].forEach(function (k) { if (v.timers[k]) { root.clearTimeout(v.timers[k]); v.timers[k] = null; } });
    clear(v.host);
  }

  // ---- starting, re-attaching, retrying ---------------------------------------------------------------------------------------

  /** First look: a remembered run is re-attached (its snapshot tells the state); none means start one (or wait for a click). */
  function boot(v, autoStart) {
    var rec = knownRun(v.key);
    if (!rec) {
      if (autoStart && v.loadExport) { return createRun(v, false); }
      return showIdle(v);
    }
    v.runId = rec.runId;
    v.programId = v.programId || rec.programId || null;
    if (rec.title && (!v.title || v.title === v.key)) { v.title = rec.title; }   // a project opened before its name was loaded
    setNotice(v, { busy: true, text: "Checking on your research..." });
    return call("GET", "/runs/" + enc(rec.runId)).then(function (snap) {
      if (!alive(v)) { return null; }
      var newerBrief = autoStart && v.loadExport && V.isTerminalStatus(snap.status) && v.briefHash && rec.briefHash && rec.briefHash !== v.briefHash;
      if (newerBrief) { return createRun(v, true); }    // the Brief was approved again: research that version too
      var deadRun = v.fresh && v.loadExport && (snap.status === "failed" || snap.status === "interrupted");
      if (deadRun) { return createRun(v, true); }       // a file just chosen again after a run that gave nothing: run it anew
      return adopt(v, snap);
    }).catch(function (e) {
      if (!alive(v)) { return null; }
      if (e.status === 404) {                           // the service forgot that run (a restart): start fresh if we may
        forget(v.key); v.runId = null;
        return autoStart && v.loadExport ? createRun(v, false) : showIdle(v);
      }
      return showProblem(v, problemOf(e, "We could not check on the research."), function () { boot(v, autoStart); });
    });
  }

  function showIdle(v) {
    var again = v.loadExport ? function () { createRun(v, false); } : null;
    setNotice(v, {
      text: v.loadExport ? "Research has not started for this project yet." : "There is nothing to show for this project yet. Load a saved Compass export from the start screen to run research.",
      actions: again ? [{ label: "Start research", primary: true, onClick: again }] : []
    });
    renderHead(v);
  }
  function showProblem(v, problem, retry, label) {
    setNotice(v, { tone: "error", text: problem.text, actions: retry ? [{ label: label || "Try again", primary: true, onClick: retry }] : [] });
    renderHead(v);
  }

  /** POST /v1/research/runs with the approved export. `force` is "run it again" (a new run even if one finished). */
  function createRun(v, force) {
    if (pending[v.key]) {                                  // a start for this project is already in flight: wait for it
      setNotice(v, { busy: true, text: "Starting the research..." });
      return pending[v.key].then(function () { if (alive(v)) { boot(v, false); } });
    }
    setNotice(v, { busy: true, text: "Getting your approved Brief ready..." });
    stopStream(v); stopClock(v);
    var p = Promise.resolve().then(function () {
      if (!v.loadExport) { throw Object.assign(new Error("no export"), { problem: { kind: "other", text: "To run this again, load the saved export file again from the start screen." } }); }
      return v.loadExport();
    }).then(function (exp) {
      var info = V.exportInfo(exp);
      if (!info) { throw Object.assign(new Error("not an export"), { problem: { kind: "refused", text: "This does not look like a saved Compass export. Use the file from \"Download full output\"." } }); }
      v.briefHash = info.briefHash || v.briefHash;
      exportCache[v.key] = exp;
      var body = { brief_export: exp };
      var programId = info.programId || v.programId;
      if (programId) { body.program_id = programId; }
      var text = JSON.stringify(body);
      if (new Blob([text]).size > MAX_EXPORT_BYTES) {
        throw Object.assign(new Error("too large"), { problem: V.problemText(413, null, {}) });
      }
      setNotice(v, { busy: true, text: "Starting the research..." });
      return call("POST", "/runs", text);
    }).then(function (res) {
      v.runId = res.run_id;
      v.programId = res.program_id || v.programId;
      remember(v.key, { runId: res.run_id, programId: v.programId, status: res.status || "queued", title: v.title, briefHash: v.briefHash });
      return call("GET", "/runs/" + enc(res.run_id));
    }).then(function (snap) {
      return alive(v) ? adopt(v, snap) : null;
    }).catch(function (e) {
      if (!alive(v)) { return null; }
      var other = e.status === 409 ? V.runIdFrom(e.body) : null;
      if (other) {                                          // one run per project at a time: join the one that is running
        v.runId = other;
        remember(v.key, { runId: other, programId: v.programId, status: "running", title: v.title, briefHash: v.briefHash });
        return call("GET", "/runs/" + enc(other)).then(function (snap) { return alive(v) ? adopt(v, snap) : null; })
          .catch(function (e2) { showProblem(v, problemOf(e2, e.message), function () { boot(v, false); }); });
      }
      return showProblem(v, problemOf(e, "We could not start the research."), function () { createRun(v, force); });
    });
    pending[v.key] = p.then(function () { delete pending[v.key]; }, function () { delete pending[v.key]; });
    return p;
  }

  /** Make a snapshot the screen's state and carry on: tail the stream while active, show the result when finished. */
  function adopt(v, snap) {
    stopStream(v);
    v.runId = snap.run_id || v.runId;
    v.programId = snap.program_id || v.programId;
    v.state = V.applySnapshot(snap, Date.now());
    v.skipping = false; v.cancelling = {}; v.review = null; v.reviewState = "idle";
    remember(v.key, { runId: v.runId, programId: v.programId, status: snap.status, title: v.title, briefHash: v.briefHash });
    clearNotice(v);
    resetFeed(v);
    renderAll(v);
    var st = snap.status;
    if (V.isActiveStatus(st)) {
      showScreen(v, "progress");
      startClock(v);
      startStream(v);
    } else if (st === "complete" || st === "partial" || st === "skipped") {
      showScreen(v, "found");
    } else {
      showScreen(v, "progress");
    }
    return null;
  }

  // ---- the event stream -----------------------------------------------------------------------------------------------------

  function startStream(v) {
    stopStream(v);
    var ctl = new root.AbortController();
    v.stream = ctl;
    v.attempt = 0;
    connect(v, ctl);
  }
  function stopStream(v) {
    if (v.stream) { v.stream.abort(); v.stream = null; }
    if (v.timers.reconnect) { root.clearTimeout(v.timers.reconnect); v.timers.reconnect = null; }
  }
  function connect(v, ctl) {
    if (v.stream !== ctl || !alive(v)) { return; }
    var headers = headersFor();
    headers["Accept"] = "text/event-stream";
    headers["Last-Event-ID"] = String(v.state.lastSeq);
    var got = false;
    var fatal = null;
    root.fetch(getBaseUrl() + PREFIX + "/runs/" + enc(v.runId) + "/events?after=" + v.state.lastSeq, { headers: headers, signal: ctl.signal })
      .then(function (res) {
        if (res.status === 401 || res.status === 404) { fatal = problemError(res.status, null); throw fatal; }
        if (!res.ok || !res.body) { throw new Error("stream " + res.status); }
        setLive(v, true);
        if (v.lostNote) { v.lostNote = false; clearNotice(v); }          // the connection is back
        return pump(v, ctl, res.body.getReader(), function () { got = true; });
      })
      .catch(function () { /* fall through to the reconnect below */ })
      .then(function () {
        setLive(v, false);
        if (v.stream !== ctl || !alive(v)) { return; }
        if (fatal) { stopStream(v); showProblem(v, fatal.problem, function () { boot(v, false); }); return; }
        if (V.isTerminalStatus(v.state.status)) { return; }          // the run ended, so did the stream
        v.attempt = got ? 0 : v.attempt + 1;
        peekStatus(v);                                              // it may have finished while we were away
        if (v.attempt >= 2 && !v.lostNote) {
          v.lostNote = true;
          setNotice(v, { tone: "warn", text: "We lost the live connection to the research service and are trying again. The research keeps going on its own." });
        }
        v.timers.reconnect = root.setTimeout(function () { connect(v, ctl); }, V.reconnectDelay(v.attempt, Math.random()) * 1000);
      });
  }
  function pump(v, ctl, reader, onData) {
    var decoder = new root.TextDecoder();
    var buffer = "";
    function step() {
      return reader.read().then(function (r) {
        if (r.done || v.stream !== ctl) { return null; }
        buffer += decoder.decode(r.value, { stream: true });
        var parsed = V.parseSseFrames(buffer);
        buffer = parsed.rest;
        parsed.frames.forEach(function (frame) {
          if (frame.data === null) { return; }
          var ev;
          try { ev = JSON.parse(frame.data); } catch (e) { return; }   // a malformed frame is skipped
          onData();
          handleEvent(v, ev);
        });
        return step();
      });
    }
    return step();
  }
  function handleEvent(v, ev) {
    var verdict = V.checkSeq(v.state.lastSeq, ev.seq);
    if (verdict === "dup") { return; }
    if (verdict === "gap") { resync(v); return; }                  // an event is missing: start again from the snapshot
    V.applyEvent(v.state, ev, Date.now());
    if (V.isTerminalType(ev.type)) { onTerminal(v); }
    scheduleRender(v);
  }
  /** The stream skipped an event: fetch the snapshot, draw from it, tail from its last_seq. */
  function resync(v) {
    if (v.resyncing) { return; }
    v.resyncing = true;
    stopStream(v);
    call("GET", "/runs/" + enc(v.runId)).then(function (snap) {
      v.resyncing = false;
      if (alive(v)) { adopt(v, snap); }
    }).catch(function (e) {
      v.resyncing = false;
      if (alive(v)) { showProblem(v, problemOf(e, "We lost the live updates."), function () { resync(v); }, "Reconnect"); }
    });
  }
  /** After a dropped stream or a Skip: ask once whether the run has finished while nobody was listening. */
  function peekStatus(v) {
    call("GET", "/runs/" + enc(v.runId)).then(function (snap) {
      if (alive(v) && V.isTerminalStatus(snap.status) && !V.isTerminalStatus(v.state.status)) { adopt(v, snap); }
    }).catch(function () { /* the reconnect loop keeps trying */ });
  }
  function onTerminal(v) {
    stopClock(v);
    remember(v.key, { runId: v.runId, programId: v.programId, status: v.state.status, title: v.title, briefHash: v.briefHash });
    var st = v.state.status;
    if (st === "complete" || st === "partial" || st === "skipped") { showScreen(v, "found"); }
    // the snapshot also carries the reason (why a run is partial) and the hash of the committed file
    call("GET", "/runs/" + enc(v.runId)).then(function (snap) {
      if (!alive(v)) { return; }
      v.state.reason = snap.reason || null;
      v.state.contentHash = snap.content_hash || null;
      if (v.screen === "found" && v.reviewState === "ready") { renderFound(v); }
    }).catch(function () { /* the findings page works without it */ });
  }

  // ---- throttled drawing and the clocks ------------------------------------------------------------------------------------------

  function afterFrame(fn) {
    var done = false;
    function go() { if (!done) { done = true; fn(); } }
    if (root.requestAnimationFrame) { root.requestAnimationFrame(go); }
    root.setTimeout(go, 300);           // a hidden tab pauses animation frames: never wait for one
  }
  function scheduleRender(v) {
    if (v.timers.render) { return; }
    v.timers.render = root.setTimeout(function () {
      afterFrame(function () {
        v.timers.render = null;
        if (alive(v)) { renderProgress(v); renderHead(v); }
      });
    }, RENDER_GAP_MS);
  }
  function startClock(v) {
    stopClock(v);
    v.timers.clock = root.setInterval(function () { if (alive(v)) { updateClocks(v); } }, 1000);
  }
  function stopClock(v) { if (v.timers.clock) { root.clearInterval(v.timers.clock); v.timers.clock = null; } }

  // ---- shell: head, notice, the two screens ----------------------------------------------------------------------------------------

  /** A row of tabs (role=tablist) with arrow-key movement. Returns {el, select(id)}. */
  function makeTabs(label, tabs, current, onPick, cls) {
    var list = h("div", { class: "rs-tabs " + (cls || ""), role: "tablist", "aria-label": label });
    var buttons = {};
    function select(id, focus) {
      Object.keys(buttons).forEach(function (k) {
        var on = k === id;
        buttons[k].setAttribute("aria-selected", on ? "true" : "false");
        buttons[k].tabIndex = on ? 0 : -1;
      });
      if (focus && buttons[id]) { buttons[id].focus(); }
    }
    tabs.forEach(function (tab, i) {
      var b = h("button", { class: "rs-tab", type: "button", role: "tab", id: tab.domId, "aria-controls": tab.controls, text: tab.label, disabled: tab.disabled });
      b.addEventListener("click", function () { select(tab.id); onPick(tab.id); });
      b.addEventListener("keydown", function (e) {
        var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) { return; }
        var j = i;
        do { j = (j + step + tabs.length) % tabs.length; } while (tabs[j].disabled && j !== i);
        e.preventDefault();
        select(tabs[j].id, true);
        onPick(tabs[j].id);
      });
      buttons[tab.id] = b;
      list.appendChild(b);
    });
    select(current);
    return { el: list, select: select, buttons: buttons };
  }

  function buildShell(v) {
    var r = v.refs = {};
    clear(v.host);
    r.banner = h("div", { class: "rs-banner", role: "status", hidden: true });
    r.back = v.onBack ? h("button", { class: "btn btn-quiet rs-back", type: "button", text: "← Back", on: { click: function () { v.onBack(); } } }) : null;
    r.title = h("h2", { class: "rs-title", text: "Research" });
    r.sub = h("p", { class: "rs-sub muted small", text: v.title });
    r.pill = h("span", { class: "pill rs-pill", hidden: true });
    r.live = h("span", { class: "rs-live", hidden: true, title: "Live updates" }, h("span", { class: "rs-live-dot", "aria-hidden": "true" }), h("span", { text: "Live" }));
    r.skip = h("button", { class: "btn rs-skip", type: "button", text: "Skip research", hidden: true, on: { click: function () { skip(v); } } });
    r.again = h("button", { class: "btn rs-again", type: "button", text: "Run again", hidden: true, on: { click: function () { createRun(v, true); } } });
    r.head = h("div", { class: "rs-head" }, r.back,
      h("div", { class: "rs-headtext" }, r.title, r.sub),
      h("div", { class: "rs-headside" }, r.live, r.pill, r.skip, r.again));
    r.notice = h("div", { class: "rs-notice", hidden: true });
    r.sr = h("p", { class: "sr-only", role: "status", "aria-live": "polite" });
    var tabs = makeTabs("Research screens", [
      { id: "progress", label: "Progress", domId: "rs-tab-progress", controls: "rs-panel-progress" },
      { id: "found", label: "What we found", domId: "rs-tab-found", controls: "rs-panel-found" }
    ], "progress", function (id) { showScreen(v, id); });
    r.screens = tabs;
    r.switch = h("div", { class: "rs-switch", hidden: true }, tabs.el);
    r.progress = buildProgress(v);
    r.found = h("section", { class: "rs-found", id: "rs-panel-found", role: "tabpanel", "aria-labelledby": "rs-tab-found", hidden: true });
    v.host.appendChild(h("div", { class: "rs" }, r.banner, r.head, r.notice, r.sr, r.switch, r.progress.el, r.found));
    renderHead(v);
  }

  function loadConfig(v) {
    call("GET", "/config").then(function (cfg) {
      if (!alive(v)) { return; }
      v.config = cfg;
      var mock = cfg && cfg.mode === "mock";
      v.refs.banner.hidden = !mock;
      v.refs.banner.textContent = mock ? "MOCK MODE: this research is scripted, not real web research." : "";
    }).catch(function () { /* the banner is a nicety; the run itself reports real problems */ });
  }

  function setNotice(v, spec) {
    var el = v.refs.notice;
    clear(el);
    el.hidden = false;
    el.className = "rs-notice" + (spec.tone === "error" ? " is-error" : spec.tone === "warn" ? " is-warn" : "");
    el.setAttribute("role", spec.tone === "error" ? "alert" : "status");
    if (spec.busy) { el.appendChild(h("span", { class: "rs-dots", "aria-hidden": "true" }, h("span", { class: "dot" }), h("span", { class: "dot" }), h("span", { class: "dot" }))); }
    el.appendChild(h("span", { class: "rs-notice-text", text: spec.text }));
    (spec.actions || []).forEach(function (a) {
      el.appendChild(h("button", { class: "btn btn-sm" + (a.primary ? " btn-primary" : ""), type: "button", text: a.label, on: { click: a.onClick } }));
    });
  }
  function clearNotice(v) { v.refs.notice.hidden = true; clear(v.refs.notice); }
  function setLive(v, on) { if (alive(v)) { v.refs.live.hidden = !on; } }

  function showScreen(v, name) {
    v.screen = name;
    var r = v.refs;
    r.progress.el.hidden = name !== "progress" || !v.runId;
    r.found.hidden = name !== "found" || !v.runId;
    r.screens.select(name);
    if (name === "found" && v.reviewState === "idle") { loadReview(v); }
    renderHead(v);
  }
  function renderAll(v) {
    var r = v.refs;
    var has = !!v.runId;
    r.switch.hidden = !has;
    var finished = ["complete", "partial", "skipped"].indexOf(v.state.status) !== -1;
    r.screens.buttons.found.disabled = !finished;
    r.screens.buttons.found.title = finished ? "" : "Available when the research ends";
    renderProgress(v);
    renderHead(v);
  }

  var PILL_TONE = { complete: "ok", running: "", queued: "", committing: "", partial: "warn", skipped: "warn", failed: "danger", interrupted: "danger" };
  function renderHead(v) {
    var r = v.refs;
    var st = v.state.status;
    var has = !!v.runId;
    var active = has && V.isActiveStatus(st);
    var finished = ["complete", "partial", "skipped"].indexOf(st) !== -1;
    r.title.textContent = v.screen === "found" && finished ? "What we found" : active ? "Researching your idea" : has ? "Research summary" : "Research";
    r.sub.textContent = v.title;
    r.pill.hidden = !has;
    if (has) {
      r.pill.textContent = V.runWord(st);
      r.pill.className = "pill rs-pill" + (PILL_TONE[st] ? " rs-pill-" + PILL_TONE[st] : "");
    }
    r.skip.hidden = !active;
    if (!v.skipping) { r.skip.disabled = false; r.skip.textContent = "Skip research"; }
    r.again.hidden = !has || active || !v.loadExport || st === "failed" || st === "interrupted";   // those have "Try again" in the panel
    var tab = r.screens.buttons.found;
    tab.disabled = !finished;
    tab.title = finished ? "" : "Available when the research ends";
  }

  // ---- the progress screen ("Researching your idea") ----------------------------------------------------------------------------------

  function stat(label, extraClass) {
    var value = h("span", { class: "rs-stat-value", text: "0" });
    return { el: h("div", { class: "rs-stat " + (extraClass || "") }, value, h("span", { class: "rs-stat-label", text: label })), value: value };
  }

  function buildProgress(v) {
    var p = {};
    p.now = h("p", { class: "rs-now", "aria-live": "off", text: "" });
    p.depth = h("p", { class: "rs-depth muted", text: "" });
    p.warn = h("div", { class: "rs-warn", role: "note", hidden: true });
    p.coverageText = h("span", { class: "rs-coverage-text", text: "" });
    p.barFill = h("div", { class: "rs-bar-fill" });
    p.bar = h("div", { class: "rs-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0", "aria-label": "Coverage" }, p.barFill);
    p.stats = {
      searches: stat("Searches"), pages: stat("Pages read"), facts: stat("Facts checked"),
      topics: stat("Topics done"), elapsed: stat("Time so far"), eta: stat("Time left")
    };
    p.topics = h("ul", { class: "rs-topics" });
    p.topicRows = {};
    p.declined = h("ul", { class: "rs-declined muted small" });
    p.declinedKey = "";
    p.second = h("p", { class: "rs-second small", hidden: true });
    p.facts = h("ul", { class: "rs-facts" });
    p.factRows = {};
    p.factsEmpty = h("p", { class: "muted small", text: "Nothing yet. Facts show up here while they are being checked." });
    p.entities = h("ul", { class: "rs-entities chips" });
    p.entityRows = {};
    p.entitiesBox = h("div", { class: "panel rs-panel", hidden: true }, h("h3", { text: "What we found along the way" }), p.entities);
    p.feed = h("ol", { class: "rs-feed", role: "log", "aria-live": "off", "aria-label": "Live feed", tabindex: "0" });
    p.end = h("div", { class: "rs-end", hidden: true });
    p.el = h("section", { class: "rs-progress", id: "rs-panel-progress", role: "tabpanel", "aria-labelledby": "rs-tab-progress", hidden: true },
      h("div", { class: "rs-summary" }, p.now, p.depth),
      p.warn, p.end,
      h("div", { class: "rs-coverage" }, h("div", { class: "rs-coverage-head" }, h("h3", { text: "How much we have covered" }), p.coverageText), p.bar),
      h("div", { class: "rs-stats" }, p.stats.searches.el, p.stats.pages.el, p.stats.facts.el, p.stats.topics.el, p.stats.elapsed.el, p.stats.eta.el),
      h("div", { class: "rs-cols" },
        h("div", { class: "panel rs-panel" }, h("h3", { text: "What we are looking into" }), p.topics, p.second, p.declined),
        h("div", { class: "panel rs-panel" }, h("h3", { text: "Facts we are checking" }), p.factsEmpty, p.facts)),
      p.entitiesBox,
      h("div", { class: "panel rs-panel" }, h("h3", { text: "Live feed" }), p.feed));
    return p;
  }

  /** Keep a list's rows in step with `items` without rebuilding them (so a focused Stop button keeps its focus). */
  function syncRows(ul, rows, items, keyOf, make, update) {
    var seen = {};
    items.forEach(function (item, i) {
      var k = keyOf(item);
      seen[k] = true;
      var row = rows[k];
      if (!row) { row = rows[k] = make(item); }
      update(row, item);
      var at = ul.children[i];
      if (at !== row.el) { ul.insertBefore(row.el, at || null); }
    });
    Object.keys(rows).forEach(function (k) { if (!seen[k]) { rows[k].el.remove(); delete rows[k]; } });
  }
  function setText(el, text) { if (el.textContent !== text) { el.textContent = text; } }
  function setCls(el, cls) { if (el.className !== cls) { el.className = cls; } }

  function topicRow(v) {
    var label = h("span", { class: "rs-topic-label" });
    var status = h("span", { class: "rs-chip" });
    var slots = h("span", { class: "rs-topic-slots muted small" });
    var stop = h("button", { class: "btn btn-sm rs-stop", type: "button", text: "Stop this topic", hidden: true });
    var row = { el: h("li", { class: "rs-topic" }, h("div", { class: "rs-topic-main" }, label, status), h("div", { class: "rs-topic-meta" }, slots, stop)), label: label, status: status, slots: slots, stop: stop, target: null };
    stop.addEventListener("click", function () { if (row.target) { cancelTopic(v, row.target); } });
    return row;
  }
  function updateTopicRow(v, row, t) {
    row.target = t;
    setText(row.label, t.label);
    setText(row.status, v.cancelling[t.id] && t.status === "running" ? "Stopping..." : V.topicWord(t.status));
    setCls(row.status, "rs-chip rs-chip-" + t.status);
    setText(row.slots, t.slotsRequired ? t.slotsFilled + " of " + t.slotsRequired + " checks" : "");
    var canStop = t.status === "running" && !v.cancelling[t.id];
    row.stop.hidden = !canStop;
    row.stop.setAttribute("aria-label", "Stop researching: " + t.label);
    setCls(row.el, "rs-topic rs-topic-" + t.status);
  }
  function factRow() {
    var chip = h("span", { class: "rs-chip" });
    var label = h("span", { class: "rs-fact-label" });
    var src = h("span", { class: "rs-fact-src muted small" });
    return { el: h("li", { class: "rs-fact" }, chip, h("span", { class: "rs-fact-text" }, label, src)), chip: chip, label: label, src: src };
  }
  function updateFactRow(row, f) {
    setText(row.chip, f.status === "checked" ? "checked" : "checking");
    setCls(row.chip, "rs-chip rs-chip-" + (f.status === "checked" ? "ok" : "checking"));
    setText(row.label, f.label);
    setText(row.src, f.domain ? " · " + f.domain : "");
  }

  function resetFeed(v) {
    v.feedShown = 0;
    clear(v.refs.progress.feed);
  }
  function appendFeed(v) {
    var p = v.refs.progress;
    var fresh = v.state.feed.filter(function (e) { return e.seq > v.feedShown; });
    if (!fresh.length) { return; }
    var pinned = p.feed.scrollTop + p.feed.clientHeight >= p.feed.scrollHeight - 40;
    fresh.forEach(function (e) {
      var row = V.feedRow({ type: e.type, label: e.label, detail: e.detail });
      var li = h("li", { class: "rs-row rs-row-" + e.type.replace(/\./g, "-") + (row.quiet ? " is-quiet" : "") },
        h("span", { class: "rs-row-kind" }, row.kind),
        h("span", { class: "rs-row-body" },
          h("span", { class: "rs-row-text", text: row.text }),
          row.extra ? h("span", { class: "rs-row-extra", text: row.extra }) : null,
          row.meta ? h("span", { class: "rs-row-meta", text: row.meta }) : null));
      p.feed.appendChild(li);
      v.feedShown = Math.max(v.feedShown, e.seq);
    });
    while (p.feed.children.length > V.FEED_CAP) { p.feed.removeChild(p.feed.firstChild); }
    if (pinned) { p.feed.scrollTop = p.feed.scrollHeight; }
  }

  function renderProgress(v) {
    var p = v.refs.progress;
    var s = v.state;
    var active = V.isActiveStatus(s.status);
    p.el.hidden = v.screen !== "progress" || !v.runId;
    // one line about what is happening now (not a live region: the quiet one below says it every few seconds)
    setText(p.now, active ? (s.now || "Getting started...") : V.runWord(s.status));
    setText(p.depth, V.depthSentence(s));
    // slow notes
    var w = V.warningSummary(s);
    p.warn.hidden = !w.notes.length && !w.slow;
    if (!p.warn.hidden) {
      clear(p.warn);
      p.warn.appendChild(h("strong", { text: w.slow ? "This is taking a bit longer than usual." : "A note from the research." }));
      w.notes.forEach(function (n) { p.warn.appendChild(h("p", { class: "small", text: n })); });
    }
    // coverage
    var cov = V.coverage(s);
    var covText = cov.required ? cov.filled + " of " + (cov.estimated ? "about " : "") + cov.required + " checks filled" : "Waiting for the plan";
    setText(p.coverageText, cov.required ? covText + " (" + cov.percent + "%)" : covText);
    p.bar.setAttribute("aria-valuenow", String(cov.percent));
    p.bar.setAttribute("aria-valuetext", covText);
    p.barFill.style.width = cov.percent + "%";
    // counters
    var ct = V.counterTexts(s);
    setText(p.stats.searches.value, ct.searches);
    setText(p.stats.pages.value, ct.pages);
    setText(p.stats.facts.value, ct.facts);
    setText(p.stats.topics.value, ct.topics);
    updateClocks(v);
    // topics
    syncRows(p.topics, p.topicRows, s.targets, function (t) { return t.id; }, function () { return topicRow(v); }, function (row, t) { updateTopicRow(v, row, t); });
    var dk = s.declined.join("\u0001");
    if (dk !== p.declinedKey) {
      p.declinedKey = dk;
      clear(p.declined);
      s.declined.forEach(function (d) { p.declined.appendChild(h("li", { text: "We already know this: " + d })); });
    }
    p.second.hidden = !(s.gap && s.gap.count > 0 && active);
    if (!p.second.hidden) { p.second.textContent = "Taking a second look at " + V.plural(s.gap.count, "topic") + "."; }
    // facts being checked
    var facts = s.facts.slice(-12);
    p.factsEmpty.hidden = facts.length > 0;
    syncRows(p.facts, p.factRows, facts, function (f) { return f.key; }, factRow, updateFactRow);
    // entities found along the way
    p.entitiesBox.hidden = !s.entities.length;
    syncRows(p.entities, p.entityRows, s.entities, function (n) { return n; }, function () {
      var li = h("li", { class: "chip" });
      return { el: li };
    }, function (row, n) { setText(row.el, "Found: " + n); });
    appendFeed(v);
    renderEnd(v);
    announce(v);
    if (V.isTerminalStatus(s.status)) { v.refs.screens.buttons.found.disabled = ["complete", "partial", "skipped"].indexOf(s.status) === -1; }
  }

  function updateClocks(v) {
    var p = v.refs.progress;
    var c = V.clocks(v.state, Date.now());
    setText(p.stats.elapsed.value, c.elapsed === null ? "0 s" : V.formatDuration(c.elapsed));
    setText(p.stats.eta.value, V.isActiveStatus(v.state.status) ? V.remainingText(c.eta) : "—");
  }
  /** The polite live region says where the run is at most every 6 seconds, and at the end: not one line per event. */
  function announce(v) {
    var now = Date.now();
    var s = v.state;
    var done = V.isTerminalStatus(s.status);
    if (!done && now - v.announcedAt < 6000) { return; }
    v.announcedAt = now;
    var tt = V.topicTotals(s);
    var text = done ? V.runWord(s.status) : "Researching: " + tt.done + " of " + tt.total + " topics done";
    if (v.refs.sr.textContent !== text) { v.refs.sr.textContent = text; }
  }

  function renderEnd(v) {
    var el = v.refs.progress.end;
    var s = v.state;
    var st = s.status;
    if (V.isActiveStatus(st)) { el.hidden = true; clear(el); return; }
    el.hidden = false;
    clear(el);
    el.className = "rs-end" + (st === "failed" || st === "interrupted" ? " is-error" : "");
    if (st === "failed") {
      el.appendChild(h("h3", { text: "We could not finish this research." }));
      el.appendChild(h("p", { text: V.plainReason(s.failure && s.failure.reason || s.reason, "The research stopped before it could finish.") }));
      el.appendChild(h("p", { class: "muted small", text: v.loadExport ? "Nothing was added to your Brief. You can try again." : "Nothing was added to your Brief. To try again, load the saved export file again from the start screen." }));
      if (v.loadExport) { el.appendChild(h("button", { class: "btn btn-primary", type: "button", text: "Try again", on: { click: function () { createRun(v, true); } } })); }
    } else if (st === "interrupted") {
      el.appendChild(h("h3", { text: "This research was interrupted." }));
      el.appendChild(h("p", { text: "It stopped before it finished, so there are no findings to show. You can run it again." }));
      if (v.loadExport) { el.appendChild(h("button", { class: "btn btn-primary", type: "button", text: "Try again", on: { click: function () { createRun(v, true); } } })); }
    } else {
      var r = s.result || {};
      var bits = [];
      if (typeof r.facts === "number") { bits.push(V.plural(r.facts, "checked finding")); }
      if (typeof r.pages === "number") { bits.push(V.plural(r.pages, "page") + " read"); }
      if (typeof r.searches === "number") { bits.push(V.plural(r.searches, "search", "searches")); }
      if (typeof r.elapsed_s === "number") { bits.push("in " + V.formatDuration(r.elapsed_s)); }
      el.appendChild(h("h3", { text: st === "skipped" ? "Research stopped early." : st === "partial" ? "Research finished with some gaps." : "Research finished." }));
      if (bits.length) { el.appendChild(h("p", { text: bits.join(", ") + "." })); }
      if (["complete", "partial", "skipped"].indexOf(st) !== -1) {
        el.appendChild(h("button", { class: "btn btn-primary", type: "button", text: "See what we found", on: { click: function () { showScreen(v, "found"); } } }));
      }
    }
  }

  // ---- Skip and "stop this topic" -------------------------------------------------------------------------------------------------------

  function skip(v) {
    if (v.skipping || !v.runId) { return; }
    v.skipping = true;
    v.refs.skip.disabled = true;
    v.refs.skip.textContent = "Stopping...";
    call("POST", "/runs/" + enc(v.runId) + "/skip").then(function () {
      if (!alive(v)) { return; }
      setNotice(v, { busy: true, text: "Stopping. We will keep the findings that are already checked." });
      v.timers.peek = root.setTimeout(function () { if (alive(v)) { peekStatus(v); } }, 8000);   // in case the stream is gone
    }).catch(function (e) {
      if (!alive(v)) { return; }
      v.skipping = false;
      if (e.status === 409) { peekStatus(v); return; }          // already over: the snapshot will say how
      setNotice(v, { tone: "error", text: problemOf(e, "We could not stop the research.").text });
      renderHead(v);
    });
  }
  function cancelTopic(v, t) {
    if (v.cancelling[t.id]) { return; }
    v.cancelling[t.id] = true;
    renderProgress(v);
    call("POST", "/runs/" + enc(v.runId) + "/targets/" + enc(t.id) + "/cancel").catch(function (e) {
      if (!alive(v)) { return; }
      delete v.cancelling[t.id];
      if (e.status !== 409) { setNotice(v, { tone: "error", text: problemOf(e, "We could not stop that topic.").text }); }
      renderProgress(v);
    });
  }

  // ---- "What we found" -------------------------------------------------------------------------------------------------------------------------

  function loadReview(v) {
    if (!v.programId) { return; }
    v.reviewState = "loading";
    renderFound(v);
    call("GET", "/programs/" + enc(v.programId) + "/review").then(function (data) {
      if (!alive(v)) { return; }
      v.review = data;
      v.reviewState = "ready";
      renderFound(v);
    }).catch(function (e) {
      if (!alive(v)) { return; }
      v.reviewState = "error";
      v.reviewProblem = problemOf(e, "We could not load the findings.");
      renderFound(v);
    });
  }

  function chip(text, cls, title) { return h("span", { class: "rs-chip" + (cls ? " " + cls : ""), title: title || null, text: text }); }

  function sourcesLine(item) {
    var links = V.sourceLinks(item);
    if (!links.length) { return null; }
    var line = h("div", { class: "rs-sources small" }, h("span", { class: "muted", text: links.length === 1 ? "Source: " : "Sources: " }));
    links.forEach(function (s, i) {
      if (i) { line.appendChild(document.createTextNode(", ")); }
      line.appendChild(s.href
        ? h("a", { href: s.href, target: "_blank", rel: "noopener noreferrer", text: s.domain })
        : h("span", { text: s.domain }));
      if (s.date) { line.appendChild(h("span", { class: "muted", text: " (" + V.formatDate(s.date) + ")" })); }
    });
    return line;
  }

  /** One finding. Every view that shows an item draws it through here, so a rejection changes all of them. */
  function itemCard(v, item, opts) {
    var o = opts || {};
    var rejected = !!item.rejected;
    var error = h("span", { class: "rs-item-error small", role: "alert" });
    var actions = h("span", { class: "rs-item-actions" });
    var li = h("li", { class: "rs-item" + (rejected ? " is-rejected" : ""), "data-item": item.id });
    function showReject() {
      clear(actions);
      if (item.rejected) { actions.appendChild(chip("Rejected", "rs-chip-danger")); return; }
      actions.appendChild(h("button", { class: "btn btn-link rs-reject", type: "button", text: "Reject", "aria-label": "Reject: " + (item.label || item.id), on: { click: confirmReject } }));
    }
    function confirmReject() {
      clear(actions);
      var yes = h("button", { class: "btn btn-sm btn-danger", type: "button", text: "Yes, reject" });
      var no = h("button", { class: "btn btn-sm", type: "button", text: "Keep it", on: { click: function () { error.textContent = ""; showReject(); } } });
      actions.appendChild(h("span", { class: "small", text: "Reject this finding? It is withdrawn from the findings." }));
      actions.appendChild(yes); actions.appendChild(no);
      yes.addEventListener("click", function () {
        yes.disabled = true; no.disabled = true; yes.textContent = "Rejecting...";
        call("POST", "/runs/" + enc((v.review && v.review.run_id) || v.runId) + "/items/" + enc(item.id) + "/reject", {}).then(function () {
          V.markRejected(v.review, item.id);
          (v.itemEls[item.id] || []).forEach(function (el) { if (el.isConnected) { el.replaceWith(itemCard(v, item, o)); } });
          renderFoundCounts(v);
        }).catch(function (e) {
          yes.disabled = false; no.disabled = false; yes.textContent = "Yes, reject";
          error.textContent = problemOf(e, "We could not reject that finding.").text;
        });
      });
      yes.focus();
    }
    var top = h("div", { class: "rs-item-top" },
      h("strong", { class: "rs-item-label", text: item.label || item.id }),
      o.show === "lane" && item.lane ? chip(V.laneTitle(item.lane), "rs-chip-quiet") : null,
      o.show === "job" && item.job ? chip(V.jobTitle(item.job), "rs-chip-quiet") : null,
      item.kind ? h("span", { class: "muted small", text: V.humanize(item.kind) }) : null,
      actions);
    li.appendChild(top);
    if (item.claim) { li.appendChild(h("p", { class: "rs-item-claim", text: item.claim })); }
    if (item.use_note) { li.appendChild(h("p", { class: "rs-item-use muted small", text: "Use: " + item.use_note })); }
    var src = sourcesLine(item);
    if (src) { li.appendChild(src); }
    var date = V.itemDate(item);
    var meta = [];
    if (date) { meta.push("Newest source: " + V.formatDate(date)); }
    if (typeof item.independent_domains === "number") { meta.push(V.plural(item.independent_domains, "independent site")); }
    if (item.recency && item.recency.trend && item.recency.trend !== "unknown" && item.recency.trend !== "stable") { meta.push("Trend: " + item.recency.trend); }
    if (meta.length) { li.appendChild(h("p", { class: "rs-item-meta muted small", text: meta.join(" · ") })); }
    var chips = h("div", { class: "rs-chips" });
    if (item.confidence) { chips.appendChild(chip("Confidence: " + item.confidence, "rs-chip-" + item.confidence)); }
    if (item.impact) { chips.appendChild(chip("Impact: " + item.impact, "rs-chip-quiet")); }
    var prev = V.prevalenceText(item.prevalence);
    if (prev) { chips.appendChild(chip(prev + (item.prevalence.level ? " · " + V.levelWord(item.prevalence.level) : ""), "rs-chip-level rs-chip-" + item.prevalence.level, V.levelHint(item.prevalence.level))); }
    (item.flags || []).forEach(function (f) {
      var word = V.flagWord(f);
      if (word) { chips.appendChild(chip(word, "rs-chip-flag rs-flag-" + String(f).replace(/[^a-z_-]/gi, ""))); }
    });
    if (chips.children.length) { li.appendChild(chips); }
    li.appendChild(error);
    showReject();
    (v.itemEls[item.id] = v.itemEls[item.id] || []).push(li);
    return li;
  }

  function itemList(v, items, opts) {
    var ul = h("ul", { class: "rs-items" });
    var shown = 0;
    var more = null;
    function draw() {
      var end = Math.min(items.length, shown + ITEMS_PER_PAGE);
      for (var i = shown; i < end; i++) { ul.appendChild(itemCard(v, items[i], opts)); }
      shown = end;
      if (more) { more.remove(); more = null; }
      if (shown < items.length) {
        more = h("li", { class: "rs-more" }, h("button", { class: "btn btn-sm", type: "button", text: "Show " + Math.min(ITEMS_PER_PAGE, items.length - shown) + " more of " + (items.length - shown), on: { click: draw } }));
        ul.appendChild(more);
      }
    }
    draw();
    return ul;
  }
  function emptyNote(text) { return h("p", { class: "muted rs-empty", text: text }); }

  var FOUND_TABS = [
    { id: "playbook", label: "By lane" }, { id: "jobs", label: "By topic" }, { id: "norms", label: "What is usual" },
    { id: "suggestions", label: "Ideas" }, { id: "confirm", label: "Please confirm" }, { id: "gaps", label: "Gaps" },
    { id: "conflicts", label: "Conflicts" }
  ];
  function foundCounts(v) {
    var rv = v.review;
    return {
      playbook: V.visibleCount(V.itemsOf(rv)), jobs: V.visibleCount(V.itemsOf(rv)),
      norms: V.normGroups(rv).reduce(function (n, g) { return n + g.norms.length; }, 0),
      suggestions: V.suggestionCount(rv), confirm: V.confirmEntries(rv).length,
      gaps: V.gapEntries(rv).length, conflicts: V.conflictEntries(rv).length
    };
  }
  function renderFoundCounts(v) {
    if (!v.refs.foundTabs) { return; }
    var counts = foundCounts(v);
    FOUND_TABS.forEach(function (t) { v.refs.foundTabs.buttons[t.id].textContent = t.label + " (" + counts[t.id] + ")"; });
  }

  function renderFound(v) {
    var el = v.refs.found;
    clear(el);
    v.itemEls = {};
    v.refs.foundTabs = null;
    if (v.reviewState === "loading" || v.reviewState === "idle") {
      el.appendChild(h("p", { class: "rs-loading", role: "status" }, h("span", { class: "rs-dots", "aria-hidden": "true" }, h("span", { class: "dot" }), h("span", { class: "dot" }), h("span", { class: "dot" })), " Loading what we found..."));
      return;
    }
    if (v.reviewState === "error") {
      var box = h("div", { class: "rs-notice is-error", role: "alert" }, h("span", { class: "rs-notice-text", text: v.reviewProblem.text }),
        h("button", { class: "btn btn-sm btn-primary", type: "button", text: "Try again", on: { click: function () { loadReview(v); } } }));
      el.appendChild(box);
      return;
    }
    var rv = v.review;
    var status = rv.status || v.state.status;
    // head: what happened, the numbers, the download
    var downloadStatus = h("span", { class: "small muted", role: "status", "aria-live": "polite" });
    var dl = h("button", { class: "btn", type: "button", text: "Download research.json" });
    dl.addEventListener("click", function () { downloadResult(v, dl, downloadStatus); });
    el.appendChild(h("div", { class: "rs-found-head" },
      h("div", { class: "rs-found-facts" },
        h("span", { class: "muted small", text: V.reviewFacts(rv).join(" · ") })),
      h("div", { class: "rs-found-actions" }, dl, downloadStatus)));
    el.appendChild(whyBox(v, rv, status));
    // the views
    var counts = foundCounts(v);
    var tabs = makeTabs("What we found", FOUND_TABS.map(function (t) {
      return { id: t.id, label: t.label + " (" + counts[t.id] + ")", domId: "rs-ftab-" + t.id, controls: "rs-fbody" };
    }), v.found.tab, function (id) { v.found.tab = id; renderFoundBody(v); }, "rs-views");
    v.refs.foundTabs = tabs;
    el.appendChild(tabs.el);
    v.refs.foundBody = h("div", { class: "rs-fbody", id: "rs-fbody", role: "tabpanel" });
    el.appendChild(v.refs.foundBody);
    renderFoundBody(v);
    // raw JSON, built only when opened (a review can be large)
    var pre = h("pre", { class: "rs-raw mono" });
    var raw = h("details", { class: "rs-rawbox" }, h("summary", { text: "Show the raw findings (JSON)" }), pre);
    raw.addEventListener("toggle", function () { if (raw.open && !pre.textContent) { pre.textContent = JSON.stringify(rv, null, 2); } });
    el.appendChild(raw);
  }

  /** Why the findings are not complete: stopped early, or some topics unfinished (gaps, the reason, slow notes). */
  function whyBox(v, rv, status) {
    var gaps = V.gapEntries(rv);
    var notes = V.warningSummary(v.state).notes;
    var reason = v.state.reason ? V.plainReason(v.state.reason, "") : "";
    if (status === "complete" && !gaps.length) { return h("div", { hidden: true }); }
    var box = h("div", { class: "rs-why" });
    if (status === "skipped") { box.appendChild(h("p", { text: "You stopped the research early. These are the findings that were already checked." })); }
    else if (status === "partial") { box.appendChild(h("p", { text: "Some topics did not finish, so these findings are not complete." })); }
    else if (gaps.length) { box.appendChild(h("p", { text: "Most topics are covered. A few were left open:" })); }
    if (reason) { box.appendChild(h("p", { class: "small", text: reason })); }
    if (gaps.length) {
      box.appendChild(h("ul", { class: "small" }, gaps.map(function (g) { return h("li", { text: g.label + ": " + g.why }); })));
    }
    notes.forEach(function (n) { box.appendChild(h("p", { class: "small muted", text: n })); });
    return box;
  }

  function downloadResult(v, btn, statusEl) {
    btn.disabled = true;
    statusEl.textContent = "Preparing the file...";
    request("GET", "/programs/" + enc(v.programId) + "/latest").then(function (r) {
      var blob = new Blob([r.text], { type: "application/json" });
      var a = h("a", { href: root.URL.createObjectURL(blob), download: "research-" + String(v.programId).replace(/[^A-Za-z0-9_-]/g, "") + ".json" });
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      root.setTimeout(function () { root.URL.revokeObjectURL(a.href); }, 4000);
      statusEl.textContent = "Saved research.json.";
    }).catch(function (e) {
      statusEl.textContent = problemOf(e, "We could not download the file.").text;
    }).then(function () { btn.disabled = false; });
  }

  function renderFoundBody(v) {
    var body = v.refs.foundBody;
    clear(body);
    v.itemEls = {};
    var rv = v.review;
    var tab = v.found.tab;
    body.setAttribute("aria-labelledby", "rs-ftab-" + tab);
    if (tab === "playbook") { body.appendChild(playbookView(v, rv)); }
    else if (tab === "jobs") { body.appendChild(jobsView(v, rv)); }
    else if (tab === "norms") { body.appendChild(normsView(rv)); }
    else if (tab === "suggestions") { body.appendChild(suggestionsView(v, rv)); }
    else if (tab === "confirm") { body.appendChild(confirmView(v, rv)); }
    else if (tab === "gaps") { body.appendChild(gapsView(rv)); }
    else { body.appendChild(conflictsView(v, rv)); }
  }

  function playbookView(v, rv) {
    var wrap = h("div", { class: "rs-lanes" });
    V.playbook(rv).forEach(function (lane) {
      var count = V.visibleCount(lane.items);
      var d = h("details", { class: "rs-lane", open: lane.items.length > 0 || !!lane.digest },
        h("summary", null, h("span", { class: "rs-lane-title", text: lane.title }), " ", chip(V.plural(count, "finding"), "rs-chip-quiet"), " ",
          h("span", { class: "muted small", text: lane.note })));
      if (lane.digest) {
        d.appendChild(h("p", { class: "rs-digest-label muted small", text: "What the next step reads for this lane (" + lane.lane + "):" }));
        d.appendChild(h("pre", { class: "rs-digest mono", text: lane.digest }));
      }
      d.appendChild(lane.items.length ? itemList(v, lane.items, { show: "job" }) : emptyNote("Nothing was found for this lane."));
      wrap.appendChild(d);
    });
    return wrap;
  }

  function jobsView(v, rv) {
    var groups = V.jobGroups(rv);
    var wrap = h("div", { class: "rs-jobs" });
    var list = h("div", { class: "rs-jobs-list", id: "rs-job-panel", role: "tabpanel" });
    function draw() {
      clear(list);
      v.itemEls = {};
      var g = groups.filter(function (x) { return x.job === v.found.job; })[0] || groups[0];
      list.setAttribute("aria-labelledby", "rs-jtab-" + g.job);
      list.appendChild(g.items.length ? itemList(v, g.items, { show: "lane" }) : emptyNote("Nothing was found for this topic."));
    }
    var tabs = makeTabs("Topics", groups.map(function (g) {
      return { id: g.job, label: g.title + " (" + V.visibleCount(g.items) + ")", domId: "rs-jtab-" + g.job, controls: "rs-job-panel" };
    }), v.found.job, function (id) { v.found.job = id; draw(); }, "rs-subtabs");
    wrap.appendChild(tabs.el);
    wrap.appendChild(list);
    draw();
    return wrap;
  }

  function normsView(rv) {
    var groups = V.normGroups(rv);
    if (!groups.length) { return emptyNote("No norms were recorded for this run."); }
    var wrap = h("div", { class: "rs-norms" }, h("p", { class: "muted small", text: "What comparable apps usually have, and how many of the ones we looked at have it." }));
    groups.forEach(function (g) {
      wrap.appendChild(h("h3", { class: "rs-group-title", text: g.title }));
      wrap.appendChild(h("ul", { class: "rs-normlist" }, g.norms.map(function (n) {
        var level = n.prevalence && n.prevalence.level;
        var newest = n.recency && n.recency.newest_source;
        return h("li", { class: "rs-norm" },
          h("strong", { class: "rs-norm-label", text: n.label || n.key }),
          h("span", { class: "rs-chips" },
            V.prevalenceText(n.prevalence) ? chip(V.prevalenceText(n.prevalence), "rs-chip-quiet") : null,
            level ? chip(V.levelWord(level), "rs-chip-level rs-chip-" + level, V.levelHint(level)) : null,
            n.matches_brief ? chip("In your Brief (" + n.matches_brief + ")", "rs-chip-ok") : chip("Not in your Brief yet", "rs-chip-quiet")),
          newest ? h("span", { class: "muted small", text: "Newest source: " + V.formatDate(newest) }) : null);
      })));
    });
    return wrap;
  }

  function suggestionsView(v, rv) {
    var groups = V.suggestionGroups(rv);
    var wrap = h("div", { class: "rs-suggest" });
    var total = V.suggestionCount(rv);
    var tally = h("p", { class: "small", role: "status", "aria-live": "polite" });
    function count() {
      var on = Object.keys(v.toggles).filter(function (k) { return v.toggles[k]; }).length;
      tally.textContent = on + " of " + V.plural(total, "idea") + " switched on";
    }
    wrap.appendChild(h("p", { class: "rs-suggest-note", text: "These are ideas only. They are never added to your app's scope automatically. The switches here only live on this page; the real choice comes at the final approval." }));
    if (!total) { wrap.appendChild(emptyNote("There are no ideas to suggest.")); return wrap; }
    wrap.appendChild(tally);
    groups.forEach(function (g) {
      if (!g.items.length) { return; }
      wrap.appendChild(h("h3", { class: "rs-group-title", text: g.title }));
      wrap.appendChild(h("ul", { class: "rs-suggestions" }, g.items.map(function (s) {
        var on = !!v.toggles[s.key];
        var btn = h("button", { class: "rs-switch-btn", type: "button", role: "switch", "aria-checked": on ? "true" : "false" },
          h("span", { class: "rs-switch-track", "aria-hidden": "true" }, h("span", { class: "rs-switch-knob" })),
          h("span", { class: "rs-switch-label", text: s.label }));
        btn.addEventListener("click", function () {
          v.toggles[s.key] = !v.toggles[s.key];
          btn.setAttribute("aria-checked", v.toggles[s.key] ? "true" : "false");
          count();
        });
        return h("li", { class: "rs-suggestion" }, btn,
          V.prevalenceText(s.prevalence) ? h("span", { class: "muted small", text: V.prevalenceText(s.prevalence) }) : null);
      })));
    });
    count();
    return wrap;
  }

  function confirmView(v, rv) {
    var entries = V.confirmEntries(rv);
    if (!entries.length) { return emptyNote("Nothing needs a second pair of eyes."); }
    var wrap = h("div", { class: "rs-confirm" }, h("p", { class: "muted small", text: "Please check these yourself before relying on them: numbers, prices and anything legal or regulatory." }));
    var ul = h("ul", { class: "rs-items" });
    entries.forEach(function (c) {
      if (c.item) {
        var li = itemCard(v, c.item, { show: "job" });
        li.insertBefore(h("p", { class: "rs-why-confirm small", text: "Why: " + c.why }), li.children[1] || null);
        ul.appendChild(li);
      } else {
        ul.appendChild(h("li", { class: "rs-item" }, h("strong", { text: c.itemId }), h("p", { class: "small", text: c.why })));
      }
    });
    wrap.appendChild(ul);
    return wrap;
  }

  function gapsView(rv) {
    var gaps = V.gapEntries(rv);
    if (!gaps.length) { return emptyNote("No topics were left open."); }
    return h("ul", { class: "rs-gaps" }, gaps.map(function (g) {
      return h("li", { class: "rs-gap" }, h("strong", { text: g.label }), h("p", { class: "small", text: g.why }),
        g.queries.length ? h("p", { class: "muted small", text: "We tried: " + g.queries.join("; ") }) : null);
    }));
  }

  function conflictsView(v, rv) {
    var list = V.conflictEntries(rv);
    if (!list.length) { return emptyNote("The sources did not disagree about anything important."); }
    return h("ul", { class: "rs-conflicts" }, list.map(function (c) {
      var li = h("li", { class: "rs-conflict" }, h("strong", { text: c.topic }), h("p", { class: "small", text: c.note }));
      if (c.items.length) { li.appendChild(h("ul", { class: "rs-items" }, c.items.map(function (it) { return itemCard(v, it, { show: "job" }); }))); }
      return li;
    }));
  }

  // ---- the start screen: load a saved export, reopen a remembered run ----------------------------------------------------------------------------

  function openPage(opts, o) {
    var page = opts.openPage();
    return attach({
      mode: "page", host: page.host, onBack: page.onBack, key: o.key, programId: o.programId, title: o.title, briefHash: o.briefHash,
      loadExport: o.loadExport, autoStart: o.autoStart, fresh: o.fresh
    });
  }

  /** The "Load a saved Compass export" control for the Compass start screen. opts.openPage() shows the page-sized screen and
      returns {host, onBack}. A file is read with file.text() and parsed once, so even a 4 MB export does not freeze the page. */
  function homePanel(opts) {
    var status = h("p", { class: "small rs-load-status", role: "status", "aria-live": "polite" });
    var input = h("input", { type: "file", accept: ".json,application/json", id: "rs-file", class: "rs-file" });
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) { return; }
      status.className = "small rs-load-status";
      status.textContent = "Reading " + file.name + " (" + (file.size / 1048576).toFixed(1) + " MB)...";
      file.text().then(function (text) {
        return new Promise(function (resolve) { root.setTimeout(function () { resolve(text); }, 0); });   // let the message paint first
      }).then(function (text) {
        var exp;
        try { exp = JSON.parse(text); } catch (e) { exp = null; }
        var info = V.exportInfo(exp);
        if (!info) {
          status.className = "small rs-load-status is-error";
          status.textContent = "That file is not a saved Compass export. Use the file from \"Download full output\" in the Brief panel.";
          return;
        }
        status.textContent = "";
        input.value = "";
        exportCache[info.key] = exp;
        openPage(opts, { key: info.key, programId: info.programId, title: info.title, briefHash: info.briefHash, loadExport: function () { return Promise.resolve(exp); }, autoStart: true, fresh: true });
      }).catch(function () {
        status.className = "small rs-load-status is-error";
        status.textContent = "We could not read that file.";
      });
    });
    var recent = V.recentRuns(readJson(RUNS_KEY), 5);
    var list = h("ul", { class: "rs-recent" });
    recent.forEach(function (r) {
      list.appendChild(h("li", { class: "rs-recent-row" },
        h("span", { class: "rs-recent-main" }, h("strong", { text: r.title || r.programId || r.key }),
          h("span", { class: "muted small", text: V.runWord(r.status) + " · " + new Date(r.at).toLocaleString() })),
        h("button", { class: "btn btn-sm", type: "button", text: "Open", "aria-label": "Open research: " + (r.title || r.key), on: { click: function () {
          var cached = exportCache[r.key];
          openPage(opts, { key: r.key, programId: r.programId, title: r.title, briefHash: r.briefHash, loadExport: cached ? function () { return Promise.resolve(cached); } : null, autoStart: false });
        } } })));
    });
    return h("section", { class: "panel rs-load", "aria-labelledby": "rs-load-title" },
      h("h2", { id: "rs-load-title", text: "Try the research on a saved export" }),
      h("p", { class: "muted", text: "Load a Compass export you saved earlier (the JSON file from \"Download full output\"). The research runs on it right away, with no interview." }),
      h("label", { class: "rs-file-label", for: "rs-file" }, h("span", { class: "small muted", text: "Saved export (.json)" }), input),
      status,
      recent.length ? h("div", { class: "rs-recent-box" }, h("h3", { text: "Research in this browser" }), list) : null);
  }

  /** After a page reload: reopen the page-sized screen that was open (a run started from a saved export), if its run is known. */
  function resumePage(opts) {
    var key = store.get(PAGE_KEY);
    var rec = key ? knownRun(key) : null;
    if (!rec) { return; }
    var cached = exportCache[key];
    openPage(opts, { key: key, programId: rec.programId, title: rec.title, briefHash: rec.briefHash, loadExport: cached ? function () { return Promise.resolve(cached); } : null, autoStart: false });
  }

  // ---- what Compass calls ------------------------------------------------------------------------------------------------------------------------------

  /** Called once by the Compass screen: the sign-in headers (a function, read at every request). */
  function configure(o) { if (o && typeof o.headers === "function") { auth = o.headers; } }

  /** After Gate 1: show the Research screen for this project and start (or re-attach to) its run.
      o: {sessionId, host, title, briefHash, loadExport(): Promise<export object>, onShown()} */
  function start(o) {
    attach({ mode: "tab", host: o.host, key: o.sessionId, programId: o.sessionId, title: o.title, briefHash: o.briefHash, loadExport: o.loadExport, autoStart: true });
    if (o.onShown) { o.onShown(); }
  }
  /** Open the Research tab of a project: its remembered run, or a "Start research" button when there is none. */
  function show(o) {
    attach({ mode: "tab", host: o.host, key: o.sessionId, programId: o.sessionId, title: o.title, briefHash: o.briefHash, loadExport: o.loadExport, autoStart: false });
  }

  root.MobigenResearch = {
    configure: configure, start: start, show: show, detach: detach, homePanel: homePanel, resumePage: resumePage,
    knownRun: knownRun, isActive: isActive, getBaseUrl: getBaseUrl, setBaseUrl: setBaseUrl, DEFAULT_BASE: DEFAULT_BASE
  };
})(window);
