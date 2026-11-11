/* The run state: research.event.v1 events and the run snapshot folded into one plain object, plus what the run screen
   draws from it. Pure (no DOM, network or clock except what is passed in). Text from events (labels, domains, titles,
   names) is kept as plain strings; the screens write it with textContent only. The server's own `label` is shown as is. */
import { pct, FEED_KINDS, SKIP_REASONS, JOB_ORDER, humanize, plural, warningTitle } from "./format.js";
import { checkSeq } from "./sse.js";
import { plainReason } from "./safe.js";

export const FEED_CAP = 100; // lines kept in the live feed
export const FACT_CAP = 40; // facts kept in the "being checked" list
export const WARNING_CAP = 30;
const ACTIVE = { queued: true, running: true, committing: true };
const TERMINAL_TYPES = { "run.finished": true, "run.skipped": true, "run.failed": true };

export const isActiveStatus = (s) => !!ACTIVE[s];
export const isTerminalStatus = (s) => !!s && !ACTIVE[s];
export const isTerminalType = (t) => !!TERMINAL_TYPES[t];

function num(v) {
  return typeof v === "number" && isFinite(v) ? v : null;
}
const nameKey = (text) =>
  String(text || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();

export function emptyState() {
  return {
    runId: null,
    programId: null,
    status: "queued",
    phase: "",
    round: 0,
    loadClass: "",
    depthWord: "",
    minutes: null,
    degraded: false,
    targets: [],
    declined: [],
    entities: [],
    facts: [],
    factsRejected: 0,
    warnings: [],
    feed: [],
    counters: {},
    countersAt: 0,
    roundCoverage: null,
    gap: null,
    lastSeq: 0,
    received: 0, // events folded in (for the "updates seen" line and the tests)
    result: null,
    failure: null,
    reason: null,
    contentHash: null,
    startedAt: null,
    finishedAt: null,
    now: "",
  };
}

function toTarget(t) {
  return {
    id: t.id,
    label: t.label || t.topic || t.id,
    job: t.job || "",
    lane: t.lane || "",
    priority: num(t.priority) || 0,
    status: t.status || "queued",
    slotsFilled: num(t.slots_filled) || 0,
    slotsRequired: num(t.slots_required) || 0,
  };
}
function targetOf(state, id) {
  for (const t of state.targets) if (t.id === id) return t;
  return null;
}
const TARGET_STATUS = {
  "target.started": "running",
  "target.covered": "covered",
  "target.not_found": "not_found",
  "target.cancelled": "cancelled",
};

/** Fold one event into `state` (in place) and return it. `nowMs` stamps the counters so clocks can tick between events. */
export function applyEvent(state, ev, nowMs) {
  const d = ev.detail || {};
  const type = ev.type;
  if (typeof ev.seq === "number" && ev.seq > state.lastSeq) state.lastSeq = ev.seq;
  state.received += 1;
  if (ev.run_id && !state.runId) state.runId = ev.run_id;
  if (ev.counters) {
    state.counters = Object.assign({}, ev.counters);
    state.countersAt = nowMs || 0;
  }
  if (ev.phase) state.phase = ev.phase;
  if (typeof ev.round === "number") state.round = ev.round;
  if (type !== "fact.found" && type !== "fact.verified" && type !== "fact.rejected") state.now = ev.label || state.now;
  state.feed.push({ seq: ev.seq, type: type, ts: ev.ts || "", label: ev.label || "", targetId: ev.target_id || null, detail: d });
  if (state.feed.length > FEED_CAP) state.feed.splice(0, state.feed.length - FEED_CAP);

  if (type === "run.started") {
    if (!isTerminalStatus(state.status)) state.status = "running";
    state.loadClass = d.load_class || state.loadClass;
    state.depthWord = d.depth_word || state.depthWord;
    state.minutes = num(d.minutes) !== null ? d.minutes : state.minutes;
    state.degraded = !!d.degraded;
    state.startedAt = state.startedAt || ev.ts || null;
  } else if (type === "plan.ready") {
    state.targets = (d.targets || []).map(toTarget);
    state.declined = (d.declined || []).map(String);
  } else if (TARGET_STATUS[type]) {
    let t = targetOf(state, ev.target_id);
    if (!t && ev.target_id) {
      t = toTarget({ id: ev.target_id, label: d.topic || ev.label, job: d.job });
      state.targets.push(t);
    }
    if (t) {
      t.status = TARGET_STATUS[type];
      if (num(d.slots_filled) !== null) t.slotsFilled = d.slots_filled;
      if (num(d.slots_required) !== null) t.slotsRequired = d.slots_required;
      if (d.queries && d.queries.length) t.queries = d.queries.map(String);
    }
  } else if (type === "fact.found" || type === "fact.verified") {
    const label = d.label || ev.label;
    const key = d.fact_id ? "id:" + d.fact_id : nameKey(label);
    let fact = null;
    for (const f of state.facts) if (f.key === key && f.status !== "rejected") fact = f;
    if (!fact) {
      fact = { key: key, label: label, kind: d.kind || "", topic: d.topic || "", status: "checking", domain: "", level: "" };
      state.facts.push(fact);
      if (state.facts.length > FACT_CAP) state.facts.shift();
    }
    if (type === "fact.verified") {
      fact.status = "checked";
      fact.domain = d.domain || fact.domain;
      fact.level = d.level || fact.level;
    }
  } else if (type === "fact.rejected") {
    state.factsRejected += 1;
    const key = d.fact_id ? "id:" + d.fact_id : nameKey(d.label);
    if (key && key !== "id:") for (const f of state.facts) if (f.key === key && f.status === "checking") f.status = "rejected";
  } else if (type === "entity.discovered") {
    const name = String(d.name || "").trim();
    if (name && !state.entities.some((n) => nameKey(n) === nameKey(name))) state.entities.push(name);
  } else if (type === "round.completed") {
    state.roundCoverage = {
      round: d.round || ev.round || 0,
      filled: num(d.slots_filled) || 0,
      required: num(d.slots_required) || 0,
      newSlots: num(d.new_slots) || 0,
    };
  } else if (type === "gap.detected") {
    state.gap = { count: num(d.count) !== null ? d.count : (d.targets || []).length, targets: (d.targets || []).map(String) };
  } else if (type === "warning") {
    const code = d.code || "";
    state.warnings.push({ seq: ev.seq, code: code, title: warningTitle(code), text: d.message || ev.label || "" });
    if (state.warnings.length > WARNING_CAP) state.warnings.shift();
  } else if (type === "run.finished" || type === "run.skipped") {
    state.status = d.status || (type === "run.skipped" ? "skipped" : "complete");
    state.result = d;
    state.finishedAt = ev.ts || state.finishedAt;
  } else if (type === "run.failed") {
    state.status = d.status || "failed";
    state.failure = { reason: d.reason || "", code: d.code || "" };
    state.finishedAt = ev.ts || state.finishedAt;
  }
  return state;
}

/** GET /runs/{id} -> state. The snapshot's feed (the last 60 events) is folded first, then its own fields win, so a
    reloaded page shows what the server knows now and continues from `last_seq`. */
export function applySnapshot(snap, nowMs) {
  const state = emptyState();
  for (const ev of snap.feed || []) applyEvent(state, ev, nowMs);
  state.received = 0;
  state.runId = snap.run_id || state.runId;
  state.programId = snap.program_id || state.programId;
  state.status = snap.status || state.status;
  state.phase = snap.phase || state.phase;
  state.round = num(snap.round) !== null ? snap.round : state.round;
  state.loadClass = snap.load_class || state.loadClass;
  state.depthWord = snap.depth_word || state.depthWord;
  state.degraded = !!(snap.input_quality && snap.input_quality.degraded) || state.degraded;
  state.startedAt = snap.started_at || state.startedAt;
  state.finishedAt = snap.finished_at || null;
  state.reason = snap.reason || null;
  state.contentHash = snap.content_hash || null;
  if (snap.counters) {
    state.counters = Object.assign({}, snap.counters);
    state.countersAt = nowMs || 0;
  }
  if (snap.targets) state.targets = snap.targets.map(toTarget);
  if (snap.declined) state.declined = snap.declined.map(String);
  if (Array.isArray(snap.entities)) {
    const names = [];
    for (const n of snap.entities.concat(state.entities)) if (!names.some((e) => nameKey(e) === nameKey(n))) names.push(String(n));
    state.entities = names;
  }
  state.lastSeq = num(snap.last_seq) !== null ? snap.last_seq : state.lastSeq;
  if (isTerminalStatus(snap.status) && snap.status !== "complete" && !state.failure && (snap.status === "failed" || snap.status === "interrupted" || snap.status === "cancelled")) {
    state.failure = { reason: snap.reason || "", code: snap.status };
  }
  if (snap.status === "partial" || snap.status === "complete" || snap.status === "skipped") state.result = state.result || {};
  return state;
}

/** Apply a batch of events in order, skipping ones already seen. Stops at a gap (then the caller refetches the
    snapshot). Returns {applied, dup, gap}. */
export function ingest(state, events, nowMs) {
  let applied = 0;
  let dup = 0;
  for (const ev of events) {
    const c = checkSeq(state.lastSeq, ev.seq);
    if (c === "dup") dup += 1;
    else if (c === "gap") return { applied: applied, dup: dup, gap: true };
    else {
      applyEvent(state, ev, nowMs);
      applied += 1;
    }
  }
  return { applied: applied, dup: dup, gap: false };
}

// ---- what the run screen shows ---------------------------------------------------------------------------------------

/** Topics grouped by job in a fixed order, highest priority first inside a group: [{job, targets[]}]. */
export function groupTargets(state) {
  const by = {};
  for (const t of state.targets) (by[t.job || "other"] = by[t.job || "other"] || []).push(t);
  const order = JOB_ORDER.filter((j) => by[j]).concat(Object.keys(by).filter((j) => JOB_ORDER.indexOf(j) === -1).sort());
  return order.map((job) => ({ job: job, targets: by[job].slice().sort((a, b) => b.priority - a.priority || (a.id < b.id ? -1 : 1)) }));
}
/** Topics done of total: the server's counters when sent, else counted from the topic rows. */
export function topicTotals(state) {
  const c = state.counters || {};
  if (num(c.targets_total) !== null && c.targets_total > 0) return { done: num(c.targets_done) || 0, total: c.targets_total };
  const done = state.targets.filter((t) => t.status !== "queued" && t.status !== "running").length;
  return { done: done, total: state.targets.length };
}
/** The numbers on the tiles, as text. */
export function counterTexts(state) {
  const c = state.counters || {};
  const tt = topicTotals(state);
  const planned = num(c.searches_planned);
  return {
    searches: String(num(c.searches_used) || 0) + (planned ? " of about " + planned : ""),
    pages: String(num(c.pages_read) || 0),
    facts: String(num(c.facts_verified) || 0),
    rejected: String(state.factsRejected),
    topics: tt.done + " of " + tt.total,
  };
}
/** Elapsed and time left now: the last counters plus the time since they arrived (only while the run is active). */
export function clocks(state, nowMs) {
  const c = state.counters || {};
  const ticking = isActiveStatus(state.status) && state.countersAt && nowMs;
  const drift = ticking ? Math.max(0, (nowMs - state.countersAt) / 1000) : 0;
  const elapsed = num(c.elapsed_s);
  const eta = num(c.eta_s);
  return {
    elapsed: elapsed === null ? null : elapsed + drift,
    eta: eta === null || !isActiveStatus(state.status) ? null : Math.max(0, eta - drift),
  };
}
/** One line: what the run is doing now. */
export function nowLine(state, queuePosition) {
  if (state.status === "queued") return queuePosition ? "Waiting for its turn: position " + queuePosition + "." : "Waiting for its turn.";
  if (state.status === "committing") return "Saving the findings.";
  if (isTerminalStatus(state.status)) return "";
  return state.now || "Getting started.";
}
export function depthSentence(state) {
  if (!state.depthWord && !state.loadClass) return "";
  const base = state.depthWord ? "We are doing " + state.depthWord : "We are doing a " + state.loadClass + "-depth study";
  return base + (num(state.minutes) ? ", with up to about " + plural(state.minutes, "minute") + " set aside" : "") + ".";
}
/** The plain-language reason a run did not finish (never a stack trace). */
export function failureText(state) {
  const f = state.failure || {};
  if (state.status === "interrupted" || f.code === "interrupted")
    return "The app was stopped or restarted while this run was in progress. What it spent is recorded, but its findings were not saved.";
  if (f.code === "cancelled" || state.status === "cancelled") return "This run was cancelled before it started.";
  if (f.code === "stalled") return "This run stopped reporting, so it was marked as failed to free its input.";
  return plainReason(f.reason || state.reason, "The run could not finish. Nothing was saved from it.");
}
/** Why a partial run is partial: the server's reason, else a budget-stop note from the warnings. */
export function partialText(state) {
  if (state.warnings.some((w) => w.code === "budget_stop")) return "The budget limit was reached, so some topics were not finished.";
  return plainReason(state.reason, "Some topics could not be finished.");
}

/** One row of the live feed: {kind, text, extra, meta, quiet, type}. `text` is the server's label, as is. */
export function feedRow(ev) {
  const d = ev.detail || {};
  const kind = FEED_KINDS[ev.type] || "Update";
  const text = ev.label || fallbackLabel(ev);
  let extra = "";
  let meta = "";
  let quiet = false;
  if (ev.type === "query.issued") {
    extra = d.query ? "“" + d.query + "”" : "";
    meta = [d.variant, [d.lang, d.country].filter(Boolean).join("-"), d.provider].filter(Boolean).join(" · ");
  } else if (ev.type === "search.results") {
    const domains = (d.domains || []).slice(0, 6);
    extra = (num(d.count) !== null ? plural(d.count, "result") : "") + (domains.length ? ": " + domains.join(", ") : "");
  } else if (ev.type === "page.reading" || ev.type === "page.read") {
    extra = [d.domain, d.title].filter(Boolean).join(" — ");
  } else if (ev.type === "page.skipped") {
    quiet = true;
    extra = [d.domain, d.reason ? SKIP_REASONS[d.reason] || humanize(d.reason) : ""].filter(Boolean).join(" — ");
  } else if (ev.type === "fact.found") {
    extra = d.topic ? "For: " + d.topic : "";
  } else if (ev.type === "fact.verified") {
    extra = d.domain ? "Source: " + d.domain : "";
  } else if (ev.type === "fact.rejected") {
    quiet = true;
  } else if (ev.type === "entity.discovered") {
    extra = d.kind ? "(" + humanize(d.kind) + ")" : "";
  } else if (ev.type === "round.completed") {
    extra = num(d.slots_required) ? d.slots_filled + " of " + d.slots_required + " details found" : "";
  } else if (ev.type === "warning") {
    extra = d.message && d.message !== ev.label ? d.message : "";
  } else if (ev.type === "target.not_found") {
    extra = d.queries && d.queries.length ? "We tried: " + d.queries.slice(0, 3).join("; ") : "";
  }
  if (extra === text) extra = "";
  return { kind: kind, text: text, extra: extra, meta: meta, quiet: quiet, type: ev.type };
}
function fallbackLabel(ev) {
  const d = ev.detail || {};
  return d.query || d.title || d.name || d.label || humanize(String(ev.type || "update").replace(/\./g, " "));
}

/** 0..100 for the topic progress bar. */
export function topicPercent(state) {
  const t = topicTotals(state);
  return pct(t.done, t.total);
}
