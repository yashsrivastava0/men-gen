/* The run state: spec.event.v1 events and the run snapshot folded into one plain object, plus what the run screen
   draws from it. Pure (no DOM, network or clock). Text from events is kept as plain strings; screens write it with
   textContent only. Every list is capped so a long run cannot grow the page. Event shape:
   {seq, type, at, run_id, step?, data{}}; seq is dense and starts at 1. */
import { checkSeq } from "./sse.js";

export const FEED_CAP = 150;
export const CALL_CAP = 60;
export const VALIDATOR_CAP = 100;
export const REPAIR_CAP = 100;
export const BANNER_CAP = 30;
const ACTIVE = { queued: true, running: true };
const TERMINAL_TYPES = { "run.finished": true, "run.failed": true };

export const isTerminalType = (t) => !!TERMINAL_TYPES[t];
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

export function emptyState() {
  return {
    runId: null,
    status: "queued",
    step: "",
    steps: [], // [{name, status: running|done, startedAt, finishedAt}] in the order they started
    plan: null,
    calls: { count: 0, tokens: 0, costMicros: 0, repaired: 0, byKind: {}, recent: [] },
    validators: [], // [{seq, gate, blockers, warnings}]
    repairs: { rcode: 0, reask: 0, items: [] },
    banners: [],
    gates: { g15: null, g2: null }, // null | "open" | "closed"
    partial: null, // {reason}
    failure: null,
    checkpoints: 0,
    resumed: 0,
    spendMicros: 0,
    estimate: null,
    feed: [],
    lastSeq: 0,
    received: 0,
    startedAt: null,
    finishedAt: null,
  };
}

/** Tokens in a call event: a number, or {in, out} / {input, output}. */
export function tokensOf(t) {
  if (num(t) !== null) return t;
  if (t && typeof t === "object") return (num(t.in) || num(t.input) || 0) + (num(t.out) || num(t.output) || 0);
  return 0;
}

function stepOf(state, name) {
  for (const s of state.steps) if (s.name === name) return s;
  return null;
}

/** Fold one event into `state` (in place) and return it. */
export function applyEvent(state, ev) {
  const d = ev.data && typeof ev.data === "object" ? ev.data : {};
  const type = ev.type;
  if (typeof ev.seq === "number" && ev.seq > state.lastSeq) state.lastSeq = ev.seq;
  state.received += 1;
  if (ev.run_id && !state.runId) state.runId = ev.run_id;
  if (ev.step) state.step = ev.step;
  state.feed.push({ seq: ev.seq, type: type, at: ev.at || "", step: ev.step || "", data: d });
  if (state.feed.length > FEED_CAP) state.feed.splice(0, state.feed.length - FEED_CAP);

  if (type === "run.queued") {
    state.status = "queued";
  } else if (type === "run.started") {
    state.status = "running";
    state.startedAt = state.startedAt || ev.at || null;
  } else if (type === "plan.ready") {
    state.plan = d;
    if (d.estimate && typeof d.estimate === "object") state.estimate = d.estimate;
  } else if (type === "step.started") {
    const name = ev.step || d.step || "";
    if (name) {
      let s = stepOf(state, name);
      if (!s) {
        s = { name: name, status: "running", startedAt: ev.at || null, finishedAt: null };
        state.steps.push(s);
      } else {
        s.status = "running";
      }
      state.step = name;
    }
  } else if (type === "step.finished") {
    const name = ev.step || d.step || "";
    const s = name ? stepOf(state, name) : null;
    if (s) {
      s.status = "done";
      s.finishedAt = ev.at || null;
    } else if (name) {
      state.steps.push({ name: name, status: "done", startedAt: null, finishedAt: ev.at || null });
    }
  } else if (type === "call.finished") {
    const kind = String(d.kind || "call");
    const tokens = tokensOf(d.tokens);
    const cost = num(d.cost_micros) || 0;
    const c = state.calls;
    c.count += 1;
    c.tokens += tokens;
    c.costMicros += cost;
    if (d.repaired) c.repaired += 1;
    const k = c.byKind[kind] || (c.byKind[kind] = { count: 0, tokens: 0, costMicros: 0 });
    k.count += 1;
    k.tokens += tokens;
    k.costMicros += cost;
    c.recent.push({ seq: ev.seq, kind: kind, tokens: tokens, costMicros: cost, repaired: !!d.repaired, step: ev.step || "" });
    if (c.recent.length > CALL_CAP) c.recent.splice(0, c.recent.length - CALL_CAP);
    state.spendMicros += cost;
  } else if (type === "validators.done") {
    state.validators.push({ seq: ev.seq, gate: String(d.gate || ""), blockers: num(d.blockers) || 0, warnings: num(d.warnings) || 0 });
    if (state.validators.length > VALIDATOR_CAP) state.validators.shift();
  } else if (type === "repair.rcode" || type === "repair.reask") {
    state.repairs[type === "repair.rcode" ? "rcode" : "reask"] += 1;
    state.repairs.items.push({ seq: ev.seq, type: type, text: String(d.message || d.code || d.node || d.what || "") });
    if (state.repairs.items.length > REPAIR_CAP) state.repairs.items.shift();
  } else if (type === "banner") {
    state.banners.push({ seq: ev.seq, code: String(d.code || d.kind || "banner"), text: String(d.message || "") });
    if (state.banners.length > BANNER_CAP) state.banners.shift();
  } else if (type === "gate.opened") {
    const g = d.gate === "g15" || d.gate === "g2" ? d.gate : null;
    if (g) {
      state.gates[g] = "open";
      state.status = g === "g15" ? "paused_g15" : "awaiting_g2";
    }
  } else if (type === "gate.closed") {
    const g = d.gate === "g15" || d.gate === "g2" ? d.gate : null;
    if (g) {
      state.gates[g] = "closed";
      if (g === "g15") state.status = "running";
    }
  } else if (type === "checkpoint") {
    state.checkpoints += 1;
  } else if (type === "run.partial") {
    state.status = "partial";
    state.partial = { reason: String(d.reason || "") };
    state.finishedAt = ev.at || state.finishedAt;
  } else if (type === "run.resumed") {
    state.status = "running";
    state.partial = null;
    state.failure = null;
    state.resumed += 1;
  } else if (type === "run.finished") {
    state.status = d.status || "approved";
    state.finishedAt = ev.at || state.finishedAt;
  } else if (type === "run.failed") {
    state.status = "failed";
    state.failure = { reason: String(d.reason || d.message || ""), code: String(d.code || "") };
    state.finishedAt = ev.at || state.finishedAt;
  }
  return state;
}

/** GET /api/runs/{id} -> state. Its own fields win; the stream continues after `last_seq` when the server gives it. */
export function applySnapshot(snap, prior) {
  const state = prior || emptyState();
  state.runId = snap.run_id || snap.id || state.runId;
  state.status = snap.status || state.status;
  state.step = snap.step || state.step;
  if (num(snap.spend_micros) !== null) state.spendMicros = snap.spend_micros;
  if (snap.estimate && typeof snap.estimate === "object") state.estimate = snap.estimate;
  if (snap.gates && typeof snap.gates === "object") {
    for (const g of ["g15", "g2"]) {
      const v = snap.gates[g];
      if (v === true || v === "open") state.gates[g] = "open";
      else if (v === "closed" || v === "approved" || v === "done") state.gates[g] = "closed";
    }
  }
  if (Array.isArray(snap.banners)) {
    state.banners = snap.banners.slice(-BANNER_CAP).map((b, i) => (typeof b === "string" ? { seq: -i - 1, code: b, text: "" } : { seq: -i - 1, code: String(b.code || b.kind || "banner"), text: String(b.message || "") }));
  }
  if (num(snap.last_seq) !== null && snap.last_seq > state.lastSeq) state.lastSeq = snap.last_seq;
  if (snap.status === "partial" && !state.partial) state.partial = { reason: String(snap.partial_reason || "") };
  return state;
}

/** Apply a batch of events in order, skipping ones already seen. Stops at a gap (then the caller refetches the
    snapshot and the log). Returns {applied, dup, gap}. */
export function ingest(state, events) {
  let applied = 0;
  let dup = 0;
  for (const ev of events) {
    const c = checkSeq(state.lastSeq, ev.seq);
    if (c === "dup") dup += 1;
    else if (c === "gap") return { applied: applied, dup: dup, gap: true };
    else {
      applyEvent(state, ev);
      applied += 1;
    }
  }
  return { applied: applied, dup: dup, gap: false };
}

/** The steps for the run screen: the known ones in the order they started; the current one is marked. */
export function stepRows(state) {
  return state.steps.map((s) => ({ name: s.name, status: s.status, current: s.status === "running" && s.name === state.step }));
}
/** Call kinds, most expensive first: [{kind, count, tokens, costMicros}]. */
export function callKinds(state) {
  return Object.keys(state.calls.byKind)
    .map((k) => Object.assign({ kind: k }, state.calls.byKind[k]))
    .sort((a, b) => b.costMicros - a.costMicros || (a.kind < b.kind ? -1 : 1));
}
/** What the run can do now: {stop, resume, openG15, openG2}. */
export function runActions(state) {
  const active = !!ACTIVE[state.status] || state.status === "paused_g15" || state.status === "awaiting_g2";
  return {
    stop: active,
    resume: state.status === "partial",
    openG15: state.gates.g15 === "open" || state.status === "paused_g15",
    openG2: state.gates.g2 === "open" || state.status === "awaiting_g2",
  };
}
