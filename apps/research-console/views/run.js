/* The run screen (#/run/<id>): the plan, a live feed, counters, cost, warnings and the controls for one run.
   Reload-safe: the snapshot is loaded first, then the event stream is tailed with Last-Event-ID (fetch and a stream
   reader). Events are de-duplicated by sequence number and drawn in batches (one DOM pass per animation frame).
   Two tabs on the same run read the same stored events, so they agree. */
import { h, clear, setKids, untrusted, navLink, keepFocus } from "../lib/dom.js";
import { confirmDialog } from "./dialogs.js";
import { problemBanner } from "./shared.js";
import { createRunFeed } from "../lib/runfeed.js";
import { ApiError } from "../lib/api.js";
import { problemText } from "../lib/safe.js";
import {
  applySnapshot, snapshotFacts, ingest, groupTargets, coverage, counterTexts, clocks, nowLine, depthSentence, failureText, partialText, feedRow, topicTotals, topicPercent,
  isActiveStatus, isTerminalStatus, FEED_CAP,
} from "../lib/reducer.js";
import { usd, formatDuration, remainingText, stepWord, jobTitle, topicWord, runWord, formatDateTime, plural, statusTone, humanize } from "../lib/format.js";
import { costSteps, stageRows } from "../lib/results.js";

const META_MS = 4000;

export function mount(root, ctx, params) {
  const runId = params.id;
  let state = null;
  let meta = null; // this run's row of GET /api/runs
  let spendEntry = null; // its entry in the spend record (cost by step)
  let queue = [];
  let conn = { name: "connecting", seconds: 0 };
  let gone = false;
  let feed = null;
  let renderedSeq = 0;
  let planSig = "";
  let factSig = "";
  let entitySig = "";
  let warnSig = "";
  let lastStatus = "";
  let resyncing = false;
  let actionError = null;
  let timers = [];

  const ui = {
    title: h("h2", null, "Research run"),
    chip: h("span", { class: "chip chip-plain" }, "Loading"),
    sub: h("p", { class: "muted small" }, ""),
    banners: h("div", { class: "banners" }),
    nowLabel: h("p", { class: "now-label muted small" }, "What it is doing now"),
    now: h("p", { class: "nowline", "aria-hidden": "true" }, ""),
    nowNote: h("p", { class: "now-note muted small" }, ""),
    controls: h("div", { class: "row controls" }),
    tiles: {},
    tileBox: h("div", { class: "tiles" }),
    progress: h("progress", { max: "100", value: "0", "aria-label": "Topics done" }),
    progressText: h("span", { class: "muted small" }, ""),
    reqProgress: h("progress", { max: "100", value: "0", "aria-label": "Required facts found" }),
    reqText: h("span", { class: "muted small" }, ""),
    summary: h("div", { class: "run-summary" }),
    cost: h("div", { class: "cost" }),
    plan: h("div", { class: "plan" }),
    feedList: h("ol", { class: "feed", tabindex: "0", "aria-label": "Live feed, newest first" }),
    facts: h("ul", { class: "plain-list facts-list", tabindex: "0", "aria-label": "Facts being checked" }),
    entities: h("div", { class: "chips" }),
    warnings: h("div", { class: "warnings" }),
    foot: h("p", { class: "muted small foot" }, ""),
  };
  for (const [key, label] of [
    ["searches", "Searches"], ["pages", "Pages read"], ["facts", "Facts checked"], ["required", "Required facts found"], ["rejected", "Facts dropped"], ["topics", "Topics done"], ["elapsed", "Time so far"], ["eta", "Time left"], ["cost", "Cost so far"],
  ]) {
    const value = h("strong", { class: "tile-value" }, "–");
    ui.tiles[key] = value;
    ui.tileBox.appendChild(h("div", { class: "tile" }, h("span", { class: "tile-label" }, label), value));
  }

  root.append(
    h("div", { class: "page-head" }, h("div", null, ui.title, ui.sub), ui.chip),
    ui.banners,
    h("div", { class: "card now" }, ui.nowLabel, ui.now, ui.nowNote, ui.controls),
    h("section", { class: "card", "aria-label": "Progress" }, ui.tileBox, h("div", { class: "progress-row" }, ui.progress, ui.progressText), h("div", { class: "progress-row" }, ui.reqProgress, ui.reqText), ui.cost),
    ui.summary,
    h(
      "div",
      { class: "run-grid" },
      h("section", { class: "card", "aria-labelledby": "plan-h" }, h("h3", { id: "plan-h" }, "The plan"), ui.plan),
      h("section", { class: "card", "aria-labelledby": "feed-h" }, h("h3", { id: "feed-h" }, "Live feed"), ui.feedList)
    ),
    h(
      "div",
      { class: "run-grid" },
      h("section", { class: "card", "aria-labelledby": "facts-h" }, h("h3", { id: "facts-h" }, "Facts being checked"), ui.facts, h("h3", null, "Found along the way"), ui.entities),
      h("section", { class: "card", "aria-labelledby": "warn-h" }, h("h3", { id: "warn-h" }, "Notes and warnings"), ui.warnings)
    ),
    ui.foot
  );

  // ---- small helpers ------------------------------------------------------------------------------------------------------
  const setText = (el, text) => {
    if (el.textContent !== text) el.textContent = text;
  };
  const later = (fn) => ctx.schedule(fn);
  const every = (fn, ms) => timers.push(setInterval(fn, ms));
  const input_id = () => (meta && meta.input_id) || null;

  // ---- loading ----------------------------------------------------------------------------------------------------------------
  async function loadSnapshot() {
    const snap = await ctx.api.get("/v1/research/runs/" + encodeURIComponent(runId));
    state = applySnapshot(snap, Date.now());
    renderedSeq = 0;
    planSig = factSig = entitySig = warnSig = "";
    clear(ui.feedList);
    return snap;
  }
  async function refreshMeta() {
    try {
      const [runs, spend] = await Promise.all([ctx.api.get("/api/runs"), ctx.api.get("/api/spend").catch(() => null)]);
      meta = (runs.runs || []).find((r) => r.run_id === runId) || meta;
      const entries = spend && spend.record ? spend.record.entries || [] : [];
      spendEntry = entries.find((e) => e.run_id === runId && e.kind === "run") || spendEntry;
      later(renderAll);
    } catch (e) {
      /* the bar already says when the app cannot be reached */
    }
  }

  async function start() {
    try {
      await loadSnapshot();
    } catch (e) {
      const problem = e instanceof ApiError ? e.problem : problemText(0, null);
      setKids(ui.banners, problemBanner(problem), h("p", null, h("button", { type: "button", class: "btn", onclick: () => reload() }, "Try again"), " ", navLink("#/runs", "Back to all runs", "btn")));
      setText(ui.title, "Research run");
      return;
    }
    await refreshMeta();
    renderAll();
    lastStatus = state.status;
    if (isActiveStatus(state.status)) openStream();
    else if (state.status) ctx.announce(runWord(state.status));
    every(() => {
      if (!document.hidden && state && isActiveStatus(state.status)) refreshMeta();
    }, META_MS);
    every(tickClocks, 1000);
  }
  function reload() {
    clear(ui.banners);
    start();
  }

  function openStream() {
    if (feed) feed.stop();
    feed = createRunFeed({
      afterSeq: state.lastSeq,
      open: (after, signal) => ctx.api.stream(runId, after, signal),
      onEvents: (evs) => {
        queue.push(...evs);
        later(flush);
      },
      onState: (name, info) => {
        conn = { name: name, seconds: Math.round((info && info.seconds) || 0) };
        if (name === "fatal") conn.error = info.error;
        later(renderBanners);
        later(renderFoot);
      },
    });
    feed.start();
  }

  function flush() {
    if (gone || !state || !queue.length) return;
    const batch = queue.splice(0, queue.length);
    const r = ingest(state, batch, Date.now());
    if (r.gap) {
      resync();
      return;
    }
    renderAll();
    ctx.refreshBudget();
    if (isTerminalStatus(state.status)) finish();
    else if (state.status !== lastStatus) {
      lastStatus = state.status;
      ctx.announce(runWord(state.status));
    }
  }
  async function resync() {
    if (resyncing) return;
    resyncing = true;
    if (feed) feed.stop();
    queue = [];
    try {
      await loadSnapshot();
      renderAll();
      if (isActiveStatus(state.status)) openStream();
      else finish();
    } catch (e) {
      conn = { name: "retry", seconds: 5 };
      renderBanners();
      setTimeout(() => {
        resyncing = false;
        resync();
      }, 5000);
      return;
    }
    resyncing = false;
  }
  async function finish() {
    if (feed) feed.stop();
    try {
      const snap = await ctx.api.get("/v1/research/runs/" + encodeURIComponent(runId));
      state.status = snap.status || state.status;
      state.reason = snap.reason || state.reason;
      state.contentHash = snap.content_hash || state.contentHash;
      state.finishedAt = snap.finished_at || state.finishedAt;
      if (snap.counters) state.counters = Object.assign({}, snap.counters);
      if (Array.isArray(snap.facts)) {
        state.facts = snapshotFacts(snap.facts); // the final snapshot is the authoritative list of findings
        state.factsRejected = Math.max(state.factsRejected, state.facts.filter((f) => f.status === "rejected").length);
      }
      if (isTerminalStatus(state.status) && !state.failure && (state.status === "failed" || state.status === "interrupted" || state.status === "cancelled")) {
        state.failure = { reason: snap.reason || "", code: state.status };
      }
    } catch (e) {
      /* the events already told the story */
    }
    await refreshMeta();
    ctx.refreshBudget();
    renderAll();
    if (state.status !== lastStatus) {
      lastStatus = state.status;
    }
    ctx.announce("Run finished: " + runWord(state.status));
  }

  // ---- drawing --------------------------------------------------------------------------------------------------------------------
  function renderAll() {
    if (gone || !state) return;
    keepFocus(root, () => {
      renderHeader();
      renderBanners();
      renderTiles();
      renderControls();
      renderPlan();
      renderFeed();
      renderFacts();
      renderSummary();
      renderEntities();
      renderWarnings();
      renderCost();
      renderFoot();
    });
  }
  function renderHeader() {
    const title = (meta && (meta.idea || meta.input_name)) || "Research run";
    if (ui.title.textContent !== title) {
      setKids(ui.title, untrusted(title));
      document.title = "Run · Research";
    }
    const chipText = runWord(state.status);
    const chipClass = "chip chip-" + (statusTone(state.status) || "plain");
    if (ui.chip.textContent !== chipText) ui.chip.textContent = chipText;
    if (ui.chip.className !== chipClass) ui.chip.className = chipClass;
    const bits = [];
    if (meta && meta.input_name) bits.push("Input: " + meta.input_name);
    if (meta && meta.size_class) bits.push(meta.size_class[0].toUpperCase() + meta.size_class.slice(1) + " size");
    const depth = depthSentence(state);
    setText(ui.sub, bits.join(" · ") + (bits.length && depth ? " · " : "") + depth);
    const queuePos = meta ? meta.queue_position : null;
    const isTerm = isTerminalStatus(state.status);
    if (isTerm) {
      setText(ui.nowLabel, "Run status");
      const summaryText =
        state.status === "complete"
          ? "All planned topics are complete and verified. Findings are saved in the result."
          : state.status === "partial"
            ? "Finished with gaps. Some topics could not be completed; verified findings are saved."
            : state.status === "skipped"
              ? "Stopped early. Research was skipped; findings verified so far are saved."
              : "Run ended (" + runWord(state.status) + ").";
      setText(ui.now, summaryText);
      ui.now.hidden = false;
      setText(ui.nowNote, "");
      ui.nowNote.hidden = true;
    } else {
      setText(ui.nowLabel, state.status === "queued" ? "Queue status" : "What it is doing now");
      setText(ui.now, nowLine(state, queuePos));
      ui.now.hidden = !ui.now.textContent;
      if (isActiveStatus(state.status) && state.countersAt && Date.now() - state.countersAt > 25000) {
        setText(ui.nowNote, "Waiting for search or page responses… research is active in the background.");
        ui.nowNote.hidden = false;
      } else {
        setText(ui.nowNote, "");
        ui.nowNote.hidden = true;
      }
    }
  }
  function renderBanners() {
    const out = [];
    const st = state.status;
    if (conn.name === "retry" && isActiveStatus(st)) {
      out.push(h("div", { class: "banner banner-warn", role: "status" }, h("p", null, "The connection to the app dropped. Reconnecting" + (conn.seconds ? " in " + conn.seconds + " s" : "") + "… Nothing is lost: the run goes on and this page catches up.")));
    }
    if (conn.name === "fatal") out.push(h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, "This page lost the run. Reload it; if that does not help, the app may need restarting.")));
    if (actionError) out.push(problemBanner(actionError));
    if (st === "queued") out.push(h("div", { class: "banner banner-info" }, h("p", null, "Waiting for its turn. Only one run goes at a time; this one starts by itself when the one before it ends.")));
    if (st === "interrupted") {
      out.push(
        h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, h("strong", null, "This run was interrupted. "), failureText(state)), h("p", null, rerunButton(), " ", navLink("#/inputs", "Back to inputs", "btn")))
      );
    } else if (st === "failed" || st === "cancelled") {
      out.push(
        h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, h("strong", null, st === "cancelled" ? "This run was cancelled. " : "This run could not finish. "), failureText(state)), h("p", null, rerunButton(), " ", navLink("#/inputs", "Back to inputs", "btn")))
      );
    } else if (st === "partial") {
      out.push(h("div", { class: "banner banner-warn", role: "status" }, h("p", null, h("strong", null, "Finished with gaps. "), partialText(state), " What was checked is kept."), h("p", null, navLink("#/results/" + runId, "Open the results", "btn btn-primary"))));
    } else if (st === "complete" || st === "skipped") {
      const r = state.result || {};
      const facts = typeof r.facts === "number" ? plural(r.facts, "checked finding") : "the findings";
      out.push(
        h("div", { class: "banner banner-ok", role: "status" }, h("p", null, h("strong", null, st === "skipped" ? "Stopped early. " : "Finished. "), facts + (typeof r.pages === "number" ? " from " + plural(r.pages, "page") : "") + "."), h("p", null, navLink("#/results/" + runId, "Open the results", "btn btn-primary")))
      );
    }
    if (state.warnings.some((w) => w.code === "budget_stop") && isActiveStatus(st)) {
      out.push(h("div", { class: "banner banner-warn", role: "status" }, h("p", null, "The budget limit is stopping this run early. What has been checked so far is kept.")));
    }
    const sig = out.map((n) => n.textContent).join("|");
    if (sig !== ui.banners.getAttribute("data-sig")) {
      setKids(ui.banners, out);
      ui.banners.setAttribute("data-sig", sig);
    }
  }
  function rerunButton() {
    return h("button", { type: "button", class: "btn btn-primary", "data-fk": "rerun", disabled: !input_id(), onclick: rerun }, "Re-run this input");
  }
  async function rerun() {
    try {
      const res = await ctx.api.post("/api/runs", { input_id: input_id() });
      actionError = null;
      ctx.navigate("#/run/" + res.run_id);
    } catch (e) {
      actionError = e instanceof ApiError ? e.problem : problemText(0, null);
      later(renderAll);
    }
  }
  function renderTiles() {
    const t = counterTexts(state);
    const c = clocks(state, Date.now());
    setText(ui.tiles.searches, t.searches);
    setText(ui.tiles.pages, t.pages);
    setText(ui.tiles.facts, t.facts);
    setText(ui.tiles.rejected, t.rejected);
    setText(ui.tiles.topics, t.topics);
    setText(ui.tiles.elapsed, c.elapsed === null ? "–" : formatDuration(c.elapsed));
    setText(ui.tiles.eta, isActiveStatus(state.status) ? remainingText(c.eta) : "–");
    const tt = topicTotals(state);
    ui.progress.value = topicPercent(state);
    setText(ui.progressText, tt.total ? tt.done + " of " + tt.total + " topics done" : "");
    const cov = coverage(state);
    setText(ui.tiles.required, cov.required ? cov.filled + " of " + cov.required : "–");
    ui.reqProgress.value = cov.percent;
    setText(ui.reqText, cov.required ? "required facts found: " + cov.filled + " of " + cov.required : "");
  }
  function tickClocks() {
    if (gone || document.hidden || !state || !isActiveStatus(state.status)) return;
    const c = clocks(state, Date.now());
    setText(ui.tiles.elapsed, c.elapsed === null ? "–" : formatDuration(c.elapsed));
    setText(ui.tiles.eta, remainingText(c.eta));
    if (state.countersAt && Date.now() - state.countersAt > 25000) {
      setText(ui.nowNote, "Waiting for search or page responses… research is active in the background.");
      ui.nowNote.hidden = false;
    } else {
      setText(ui.nowNote, "");
      ui.nowNote.hidden = true;
    }
  }
  function renderCost() {
    const total = meta ? meta.spend_micros || 0 : spendEntry ? spendEntry.total_micros || 0 : 0;
    setText(ui.tiles.cost, usd(total));
    const steps = spendEntry && spendEntry.by_step ? Object.keys(spendEntry.by_step).filter((k) => spendEntry.by_step[k] > 0) : [];
    const sig = steps.map((k) => k + spendEntry.by_step[k]).join(",") + "|" + total;
    if (ui.cost.getAttribute("data-sig") === sig) return;
    ui.cost.setAttribute("data-sig", sig);
    if (!steps.length) {
      setKids(ui.cost, h("p", { class: "muted small" }, "Cost by step appears here once something is spent."));
      return;
    }
    steps.sort((a, b) => spendEntry.by_step[b] - spendEntry.by_step[a]);
    setKids(ui.cost, h("p", { class: "muted small" }, "Cost so far, by step:"), h("ul", { class: "steps" }, steps.map((k) => h("li", null, stepWord(k) + ": ", h("strong", null, usd(spendEntry.by_step[k]))))));
  }
  function renderControls() {
    const st = state.status;
    const kids = [];
    if (st === "queued") {
      kids.push(h("button", { type: "button", class: "btn", "data-fk": "cancel-queued", onclick: cancelQueued }, "Cancel (it has not started)"));
    } else if (isActiveStatus(st)) {
      kids.push(h("button", { type: "button", class: "btn btn-danger", "data-fk": "skip", onclick: skip }, "Skip research"));
      kids.push(h("span", { class: "muted small" }, "Stops now and keeps what has been checked."));
    }
    const sig = kids.length + st;
    if (ui.controls.getAttribute("data-sig") !== sig) {
      setKids(ui.controls, kids);
      ui.controls.setAttribute("data-sig", sig);
    }
  }
  async function skip() {
    const ok = await confirmDialog({
      title: "Stop researching?",
      lines: ["The run stops now. Findings that were already checked are kept; the topics not finished become gaps.", "You can run the input again later."],
      confirmText: "Skip research",
      danger: true,
    });
    if (!ok) return;
    try {
      await ctx.api.post("/v1/research/runs/" + encodeURIComponent(runId) + "/skip", {});
      actionError = null;
      ctx.announce("Stopping the research");
    } catch (e) {
      actionError = e instanceof ApiError ? e.problem : problemText(0, null);
      later(renderAll);
    }
  }
  async function cancelQueued() {
    try {
      await ctx.api.post("/api/runs/" + encodeURIComponent(runId) + "/cancel-queued", {});
      actionError = null;
      await loadSnapshot();
      renderAll();
      finish();
    } catch (e) {
      actionError = e instanceof ApiError ? e.problem : problemText(0, null);
      later(renderAll);
    }
  }
  async function cancelTopic(t, btn) {
    btn.disabled = true;
    try {
      await ctx.api.post("/v1/research/runs/" + encodeURIComponent(runId) + "/targets/" + encodeURIComponent(t.id) + "/cancel", {});
      actionError = null;
    } catch (e) {
      actionError = e instanceof ApiError ? e.problem : problemText(0, null);
      btn.disabled = false;
      later(renderAll);
    }
  }
  function renderPlan() {
    const active = isActiveStatus(state.status);
    const sig = JSON.stringify([state.targets.map((t) => [t.id, t.status, t.slotsFilled, t.slotsRequired]), state.declined, active]);
    if (sig === planSig) return;
    planSig = sig;
    if (!state.targets.length && !state.declined.length) {
      setKids(ui.plan, h("p", { class: "muted" }, state.status === "queued" ? "The plan appears when the run starts." : "The plan appears as soon as the run has looked at the Brief."));
      return;
    }
    const groups = groupTargets(state).map((g) =>
      h(
        "div",
        { class: "job" },
        h("h4", null, jobTitle(g.job)),
        h(
          "ul",
          { class: "topics" },
          g.targets.map((t) =>
            h(
              "li",
              { class: "topic topic-" + t.status, "data-target": t.id },
              h("span", { class: "chip chip-" + topicTone(t.status) }, topicWord(t.status)),
              h("span", { class: "topic-label" }, untrusted(t.label)),
              h("span", { class: "muted small" }, "priority " + t.priority + (t.slotsRequired ? " · " + t.slotsFilled + " of " + t.slotsRequired + " details" : "")),
              active && (t.status === "running" || t.status === "queued")
                ? h("button", { type: "button", class: "btn btn-small", "data-fk": "cancel:" + t.id, "aria-label": "Cancel this topic: " + t.label, onclick: (e) => cancelTopic(t, e.currentTarget) }, "Cancel")
                : null
            )
          )
        )
      )
    );
    const declined = state.declined.length
      ? h("div", { class: "job declined" }, h("h4", null, "We already know this"), h("p", { class: "muted small" }, "These were left out because the Brief already answers them."), h("ul", { class: "plain-list" }, state.declined.map((d) => h("li", null, untrusted(d)))))
      : null;
    setKids(ui.plan, groups, declined);
  }
  function topicTone(status) {
    return status === "covered" ? "ok" : status === "running" ? "busy" : status === "not_found" || status === "partial" || status === "cancelled" ? "warn" : "plain";
  }
  function renderFeed() {
    const fresh = state.feed.filter((e) => e.seq > renderedSeq);
    if (!fresh.length) return;
    const frag = document.createDocumentFragment();
    for (let i = fresh.length - 1; i >= 0; i--) {
      const ev = fresh[i];
      const r = feedRow(ev);
      frag.appendChild(
        h(
          "li",
          { class: "feed-row" + (r.quiet ? " quiet" : ""), "data-seq": ev.seq },
          h("span", { class: "feed-kind" }, r.kind),
          h("span", { class: "feed-text" }, untrusted(r.text), r.extra ? h("span", { class: "feed-extra" }, " ", untrusted(r.extra)) : null, r.meta ? h("span", { class: "feed-meta muted small" }, " ", untrusted(r.meta)) : null)
        )
      );
    }
    ui.feedList.insertBefore(frag, ui.feedList.firstChild);
    renderedSeq = fresh[fresh.length - 1].seq;
    while (ui.feedList.children.length > FEED_CAP) ui.feedList.removeChild(ui.feedList.lastChild);
  }
  let showAllFacts = false;
  function renderFacts() {
    const all = state.facts.slice().reverse();
    const sig = all.map((f) => f.key + f.status + (f.domain || "")).join("|") + showAllFacts;
    if (sig === factSig) return;
    factSig = sig;
    if (!all.length) {
      setKids(ui.facts, h("li", { class: "muted" }, "Facts appear here while they are being checked against their sources."));
      return;
    }
    const word = { checking: "checking", checked: "checked", rejected: "dropped" };
    const tone = { checking: "busy", checked: "ok", rejected: "warn" };
    const counts = { checking: 0, checked: 0, rejected: 0 };
    for (const f of all) counts[f.status] += 1;
    const facts = showAllFacts ? all : all.slice(0, 120);
    const head = h("li", { class: "muted small", "data-fact-counts": all.length }, all.length + " in all: " + counts.checked + " checked, " + counts.checking + " being checked, " + counts.rejected + " dropped.");
    const rows = facts.map((f) => h("li", { class: "fact fact-" + f.status }, h("span", { class: "chip chip-" + tone[f.status], title: f.reason ? "Dropped: " + f.reason.replace(/_/g, " ") : "" }, word[f.status]), " ", untrusted(f.label), f.domain ? h("span", { class: "muted small" }, " \u2014 ", untrusted(f.domain)) : null));
    const more = all.length > 120 ? h("li", null, h("button", { type: "button", class: "btn btn-small", "data-fk": "facts-all", onclick: () => { showAllFacts = !showAllFacts; later(renderAll); } }, showAllFacts ? "Show only the newest 120" : "Show all " + all.length)) : null;
    setKids(ui.facts, head, rows, more);
  }
  function renderSummary() {
    const r = state.result;
    const done = isTerminalStatus(state.status) && r && (r.stages || r.cost_by_step || r.fallbacks);
    if (!done) {
      if (ui.summary.firstChild) clear(ui.summary);
      return;
    }
    const sig = JSON.stringify([r.stages, r.cost_by_step, r.fallbacks]);
    if (ui.summary.getAttribute("data-sig") === sig) return;
    ui.summary.setAttribute("data-sig", sig);
    const stages = stageRows(r.stages);
    const steps = costSteps(r.cost_by_step);
    const fb = r.fallbacks && typeof r.fallbacks === "object" ? Object.keys(r.fallbacks) : [];
    setKids(
      ui.summary,
      h(
        "section",
        { class: "card", "aria-labelledby": "sum-h" },
        h("h3", { id: "sum-h" }, "What the run did"),
        h("p", { class: "muted small" }, [typeof r.facts === "number" ? plural(r.facts, "checked finding") : "", typeof r.pages === "number" ? plural(r.pages, "page") + " read" : "", typeof r.searches === "number" ? plural(r.searches, "search", "searches") : "", typeof r.usd === "string" || typeof r.usd === "number" ? "USD " + Number(r.usd).toFixed(2) : ""].filter(Boolean).join(" \u00b7 ")),
        h("h4", null, "Time by stage"),
        stages.length ? h("ul", { class: "steps" }, stages.map((x) => h("li", null, x.label + ": ", h("strong", null, formatDuration(x.seconds))))) : h("p", { class: "muted small" }, "No timings recorded."),
        h("h4", null, "Cost by step"),
        steps.length ? h("ul", { class: "steps" }, steps.map((x) => h("li", null, stepWord(x.step) + ": ", h("strong", null, usd(x.micros))))) : h("p", { class: "muted small" }, "No cost recorded."),
        h("h4", null, "Fallbacks used"),
        fb.length ? h("ul", { class: "plain-list" }, fb.map((k) => h("li", null, humanize(k) + ": " + (typeof r.fallbacks[k] === "object" ? JSON.stringify(r.fallbacks[k]) : r.fallbacks[k])))) : h("p", { class: "muted small" }, "None: every model step answered normally.")
      )
    );
  }
  function renderEntities() {
    const sig = state.entities.join("|");
    if (sig === entitySig) return;
    entitySig = sig;
    setKids(ui.entities, state.entities.length ? state.entities.map((n) => h("span", { class: "chip chip-plain" }, "Found: ", untrusted(n))) : h("span", { class: "muted small" }, "Competitors, providers and authorities show up here."));
  }
  function renderWarnings() {
    const sig = state.warnings.map((w) => w.seq).join(",");
    if (sig === warnSig) return;
    warnSig = sig;
    setKids(
      ui.warnings,
      state.warnings.length
        ? h("ul", { class: "plain-list" }, state.warnings.slice().reverse().map((w) => h("li", { class: "warn-item" }, h("strong", null, w.title), w.text ? h("span", null, " — ", untrusted(w.text)) : null)))
        : h("p", { class: "muted small" }, "Nothing to report. Slow providers, fallbacks, rate limits and budget stops would be listed here.")
    );
  }
  function renderFoot() {
    const conns = { connecting: "connecting", live: "live", retry: "reconnecting", ended: "finished", fatal: "lost" };
    ui.foot.setAttribute("data-last-seq", String(state ? state.lastSeq : 0));
    setText(ui.foot, "Run " + runId + " · updates seen: " + (state ? state.lastSeq : 0) + " · connection: " + (isActiveStatus(state && state.status) ? conns[conn.name] || conn.name : "not needed") + (state && state.startedAt ? " · started " + formatDateTime(state.startedAt) : ""));
  }

  start();
  return {
    unmount() {
      gone = true;
      if (feed) feed.stop();
      for (const t of timers) clearInterval(t);
      timers = [];
    },
  };
}
