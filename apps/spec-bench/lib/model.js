/* View models: the API's answers turned into the plain shapes the screens draw. Pure and forgiving: a missing field
   becomes an empty list or a blank, never an exception, and a field named a little differently by a newer server is
   still found. Nothing here touches the page. */
import { confirmRank } from "./format.js";

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
export const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const first = (o, keys) => {
  if (!isObj(o)) return undefined;
  for (const k of keys) if (o[k] !== undefined && o[k] !== null) return o[k];
  return undefined;
};

/** Any value as display text: strings and numbers as they are, a list joined, an object by its first text field. */
export function textOf(v) {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(textOf).filter(Boolean).join(", ");
  const t = first(v, ["text", "title", "name", "label", "summary", "plain", "description", "id"]);
  if (t !== undefined) return textOf(t);
  const s = JSON.stringify(v);
  return s.length > 140 ? s.slice(0, 137) + "..." : s;
}
/** A list from an array, or from an object holding one under a known name. */
export function listOf(v, keys) {
  if (Array.isArray(v)) return v;
  const inner = first(v, keys || []);
  return Array.isArray(inner) ? inner : [];
}
/** Strings from a list of strings or of objects. */
export const texts = (v, keys) => listOf(v, keys).map(textOf).filter(Boolean);

// ---- inputs ------------------------------------------------------------------------------------------------------
export function normalizeInputs(body) {
  const compass = listOf(first(body, ["compass", "exports", "compass_exports"]) || body, ["compass", "exports"]).map((c) => {
    const ready = isObj(c.readiness) ? c.readiness : {};
    return {
      id: String(c.id || ""),
      path: String(c.path || ""),
      format: String(c.format || ""),
      tier: String(c.tier || ""),
      sizeBand: String(c.size_band || c.sizeBand || ""),
      profile: textOf(c.profile),
      ready: ready.ready !== false,
      missing: texts(ready.missing),
      drift: texts(c.drift_flags),
      matches: texts(c.research_matches),
    };
  });
  const research = listOf(first(body, ["research", "research_runs", "researches"]), []).map((r) => ({
    id: String(r.id || ""),
    path: String(r.path || ""),
    status: String(r.status || ""),
    inputHash: String(r.input_hash || ""),
  }));
  return { compass: compass.filter((c) => c.id), research: research.filter((r) => r.id) };
}
/** One auto-select proposal {compass_id, research_id|null, reason, gaps[]}, or null. */
export function normalizeProposal(p) {
  const o = isObj(p) && isObj(p.proposal) ? p.proposal : p;
  if (!isObj(o) || !o.compass_id) return null;
  return { compassId: String(o.compass_id), researchId: o.research_id ? String(o.research_id) : null, reason: textOf(o.reason), gaps: texts(o.gaps) };
}
export function normalizeProfiles(body) {
  return listOf(body, ["profiles"]).map((p) => ({
    id: String(p.id || ""),
    label: String(p.label || p.name || p.id || ""),
    capabilityClass: String(p.capability_class || ""),
    window: num(p.effective_window),
    wholePackTokens: num(p.whole_pack_tokens),
    k: num(p.k),
    unset: texts(p.unset || p.unset_fields),
  })).filter((p) => p.id);
}
export function normalizeRuns(body) {
  return listOf(body, ["runs"]).map((r) => ({
    id: String(r.id || r.run_id || ""),
    status: String(r.status || ""),
    profile: textOf(r.profile),
    spendMicros: num(r.spend_micros),
    at: String(r.created_at || r.started_at || r.at || ""),
    hash: String(r.pack_hash || r.content_hash || r.hash || ""),
  })).filter((r) => r.id);
}
export function normalizePreview(body) {
  const b = isObj(body) ? body : {};
  const calls = b.calls_by_step;
  let callRows = [];
  if (Array.isArray(calls)) callRows = calls.map((c) => ({ step: String(c.step || c.name || ""), calls: num(c.calls) !== null ? c.calls : num(c.count) || 0 }));
  else if (isObj(calls)) callRows = Object.keys(calls).map((k) => ({ step: k, calls: num(calls[k]) !== null ? calls[k] : num(calls[k] && calls[k].calls) || 0 }));
  const phases = listOf(b.phases, []).map((p) => (isObj(p) ? { name: textOf(p.name || p.label || p.id), screens: num(p.screens) !== null ? p.screens : num(p.count), note: textOf(p.note || p.why) } : { name: textOf(p), screens: null, note: "" }));
  const est = isObj(b.estimate) ? b.estimate : isObj(b.estimated_cost) ? b.estimated_cost : {};
  return {
    profile: isObj(b.profile) ? b.profile : { id: textOf(b.profile) },
    inventoryCount: num(b.inventory_count) !== null ? b.inventory_count : num(b.inventory),
    phases: phases,
    estimate: { low_micros: num(est.low_micros), high_micros: num(est.high_micros) },
    calls: callRows,
    totalCalls: callRows.reduce((n, c) => n + c.calls, 0),
  };
}

// ---- content maps (the screen as what it must communicate and do) --------------------------------------------------
function region(r) {
  if (!isObj(r)) return { name: "", text: textOf(r), items: [] };
  return { name: textOf(r.name || r.title || r.id), text: textOf(r.text || r.says || r.purpose || r.description), items: texts(r.items || r.records || r.content) };
}
/** One screen's content map: regions grouped as must, usually, could (no positions), actions, states, copy, records. */
export function normalizeScreen(body) {
  const b = isObj(body) ? body : {};
  const groups = { must: [], usually: [], could: [] };
  for (const k of Object.keys(groups)) {
    const v = b[k] !== undefined ? b[k] : isObj(b.importance) ? b.importance[k] : undefined;
    groups[k] = arr(v).map(region);
  }
  for (const r of arr(b.regions)) {
    const k = String(r && r.importance ? r.importance : "").toLowerCase();
    if (groups[k]) groups[k].push(region(r));
    else groups.usually.push(region(r));
  }
  return {
    id: String(b.id || b.screen_id || ""),
    title: textOf(b.title || b.name || b.id || b.screen_id),
    purpose: textOf(b.purpose || b.summary || b.must_communicate),
    must: groups.must,
    usually: groups.usually,
    could: groups.could,
    actions: arr(b.actions).map((a) => (isObj(a) ? { label: textOf(a.label || a.name || a.id), intent: textOf(a.intent), effect: textOf(a.effect) } : { label: textOf(a), intent: "", effect: "" })),
    states: arr(b.states).map((s) => (isObj(s) ? { name: textOf(s.name || s.id), text: textOf(s.text || s.shows || s.description) } : { name: "", text: textOf(s) })),
    copy: arr(b.copy).map((c) => (isObj(c) ? { key: textOf(c.key || c.id || c.where), text: textOf(c.text || c.value) } : { key: "", text: textOf(c) })),
    records: arr(b.hero_records || b.records).map(textOf),
  };
}

// ---- G2 ------------------------------------------------------------------------------------------------------------
/** The confirm list: money first, then legal, then identity, then the rest; the server's order inside a group; top 20. */
export function rankConfirm(list, limit) {
  const rows = arr(list).map((c, i) => ({
    i: i,
    category: String(isObj(c) ? c.category || c.kind || c.class || "" : "").toLowerCase(),
    text: textOf(isObj(c) ? c.text || c.question || c.title || c.what || c : c),
    why: textOf(isObj(c) ? c.why || c.reason : ""),
  }));
  rows.sort((a, b) => confirmRank(a.category) - confirmRank(b.category) || a.i - b.i);
  return rows.slice(0, limit === undefined ? 20 : limit).map((r, n) => ({ rank: n + 1, category: r.category, text: r.text, why: r.why }));
}

// ---- pack tree -----------------------------------------------------------------------------------------------------
/** The files list into folders: [{name, path, tokens, dir, children[]}]; a folder's tokens are the sum of its files. */
export function buildTree(files) {
  const root = { name: "", path: "", tokens: 0, dir: true, children: [] };
  for (const f of arr(files)) {
    const path = String(isObj(f) ? f.path : f || "");
    if (!path) continue;
    const tokens = isObj(f) && num(f.tokens) !== null ? f.tokens : 0;
    const parts = path.split("/").filter(Boolean);
    let node = root;
    node.tokens += tokens;
    parts.forEach((part, i) => {
      const last = i === parts.length - 1;
      let child = node.children.find((c) => c.name === part && c.dir === !last);
      if (!child) {
        child = { name: part, path: parts.slice(0, i + 1).join("/"), tokens: 0, dir: !last, children: [] };
        node.children.push(child);
      }
      child.tokens += tokens;
      node = child;
    });
  }
  const sort = (n) => {
    n.children.sort((a, b) => (a.dir === b.dir ? (a.name < b.name ? -1 : 1) : a.dir ? -1 : 1));
    n.children.forEach(sort);
  };
  sort(root);
  return root.children;
}
/** Task read totals: [{id, title, tokens, budget, over}] from the tree answer. */
export function normalizeTaskTotals(body) {
  const list = listOf(first(body, ["tasks", "task_totals", "per_task"]), []);
  const budget = num(first(body, ["task_read_budget", "budget"]));
  return list.map((t) => {
    const tokens = num(t.read_tokens) !== null ? t.read_tokens : num(t.tokens) !== null ? t.tokens : num(t.total);
    const b = num(t.budget) !== null ? t.budget : budget;
    return { id: String(t.id || t.task_id || ""), title: textOf(t.title || t.name), tokens: tokens, budget: b, over: b !== null && tokens !== null && tokens > b };
  });
}
/** Task cards by wave: [{wave, tasks:[{id, title}]}] from the answer (a list of waves, or a flat list with a wave field). */
export function normalizeWaves(body) {
  const list = listOf(first(body, ["waves", "tasks"]) || body, ["waves", "tasks"]);
  if (list.length && list.every((w) => isObj(w) && Array.isArray(w.tasks))) {
    return list.map((w, i) => ({ wave: textOf(w.wave !== undefined ? w.wave : w.id !== undefined ? w.id : "W" + i), tasks: w.tasks.map(taskOf) }));
  }
  const by = {};
  const order = [];
  for (const t of list) {
    const w = textOf(isObj(t) && t.wave !== undefined ? t.wave : "W0");
    if (!by[w]) {
      by[w] = [];
      order.push(w);
    }
    by[w].push(taskOf(t));
  }
  return order.map((w) => ({ wave: w, tasks: by[w] }));
}
function taskOf(t) {
  return isObj(t) ? { id: String(t.id || t.task_id || ""), title: textOf(t.title || t.name || t.id || t.task_id) } : { id: textOf(t), title: textOf(t) };
}

// ---- table columns for the generic rows -----------------------------------------------------------------------------
/** Column names for a list of objects: the keys that hold plain values, in first-seen order, at most `max`. */
export function columnsOf(rows, max) {
  const cols = [];
  for (const r of arr(rows)) {
    if (!isObj(r)) continue;
    for (const k of Object.keys(r)) {
      if (cols.indexOf(k) === -1 && r[k] !== null && typeof r[k] !== "undefined") cols.push(k);
    }
  }
  return cols.slice(0, max || 7);
}

// ---- the navigation graph -------------------------------------------------------------------------------------------
export const NODE_W = 150;
export const NODE_H = 38;
const COL_GAP = 70;
const ROW_GAP = 16;

/** Graph data {nodes[{id,title,app,in}], edges[{from,to,label}], unreachable[], dead_ends[], entries[]} into positions.
    Columns are the number of steps from an entry (the declared entries, else the nodes nothing points to); the
    unreachable screens sit in their own column on the right. Pure, so it is tested without a page. */
export function layoutGraph(data) {
  const d = isObj(data) ? data : {};
  const nodes = arr(d.nodes).map((n) => ({
    id: String(isObj(n) ? n.id : n),
    title: textOf(isObj(n) ? n.title || n.name || n.id : n),
    app: textOf(isObj(n) ? n.app : ""),
    later: isObj(n) && (n.in === false || n.in === "later" || n.phase === "later" || n.later === true),
  }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = arr(d.edges).map((e) => ({ from: String(e.from || e.source || ""), to: String(e.to || e.target || ""), label: textOf(e.label) })).filter((e) => ids.has(e.from) && ids.has(e.to));
  const unreachable = new Set(texts(d.unreachable).filter((x) => ids.has(x)));
  const deadEnds = new Set(texts(d.dead_ends).filter((x) => ids.has(x)));
  const hasIn = new Set(edges.map((e) => e.to));
  let entries = texts(d.entries).filter((x) => ids.has(x));
  if (!entries.length) entries = nodes.filter((n) => !hasIn.has(n.id) && !unreachable.has(n.id)).map((n) => n.id);
  const depth = new Map();
  const queue = [];
  for (const e of entries) {
    depth.set(e, 0);
    queue.push(e);
  }
  while (queue.length) {
    const cur = queue.shift();
    for (const e of edges) {
      if (e.from === cur && !depth.has(e.to) && !unreachable.has(e.to)) {
        depth.set(e.to, depth.get(cur) + 1);
        queue.push(e.to);
      }
    }
  }
  let maxDepth = 0;
  for (const v of depth.values()) maxDepth = Math.max(maxDepth, v);
  const loose = maxDepth + 1; // a column for the unreachable and anything else the walk did not reach
  const cols = [];
  for (const n of nodes) {
    const c = depth.has(n.id) ? depth.get(n.id) : loose;
    (cols[c] = cols[c] || []).push(n);
  }
  let height = 0;
  const placed = [];
  cols.forEach((col, c) => {
    if (!col) return;
    col.forEach((n, r) => {
      placed.push(Object.assign({}, n, { x: 10 + c * (NODE_W + COL_GAP), y: 10 + r * (NODE_H + ROW_GAP), col: c, unreachable: unreachable.has(n.id), deadEnd: deadEnds.has(n.id), entry: entries.indexOf(n.id) !== -1 }));
    });
    height = Math.max(height, col.length);
  });
  const at = new Map(placed.map((n) => [n.id, n]));
  const lines = edges.map((e) => {
    const a = at.get(e.from);
    const b = at.get(e.to);
    return { from: e.from, to: e.to, label: e.label, x1: a.x + NODE_W, y1: a.y + NODE_H / 2, x2: b.x, y2: b.y + NODE_H / 2, back: b.col <= a.col };
  });
  return {
    nodes: placed,
    edges: lines,
    width: 20 + cols.length * (NODE_W + COL_GAP) - COL_GAP,
    height: 20 + height * (NODE_H + ROW_GAP) - ROW_GAP,
    counts: { total: nodes.length, unreachable: unreachable.size, deadEnds: deadEnds.size, later: placed.filter((n) => n.later).length },
  };
}

// ---- the journey ---------------------------------------------------------------------------------------------------------
/** Journey data into steps with their declared entries and exits; `start` is the first step's index. */
export function normalizeJourney(body) {
  const b = isObj(body) ? body : {};
  const steps = arr(b.steps).map((s, i) => ({
    id: String(isObj(s) ? s.screen_id || s.id || "s" + i : s),
    title: textOf(isObj(s) ? s.title || s.name || s.screen_id || s.id : s),
    says: textOf(isObj(s) ? s.says || s.text || s.purpose : ""),
    entries: texts(isObj(s) ? s.entries || s.enters_from : []),
    exits: arr(isObj(s) ? s.exits : []).map((x) => (isObj(x) ? { label: textOf(x.label || x.action || x.to), to: String(x.to || x.screen_id || "") } : { label: textOf(x), to: String(x) })),
  }));
  return {
    title: textOf(b.title || b.name || b.flow_id),
    role: textOf(b.role),
    stories: texts(b.stories),
    emotion: arr(b.emotion_line || b.emotion).map((e, i) => (isObj(e) ? { label: textOf(e.label || e.step || e.moment || i + 1), feeling: textOf(e.feeling || e.emotion || e.text), level: num(e.level) !== null ? e.level : num(e.score) } : { label: String(i + 1), feeling: textOf(e), level: null })),
    moments: texts(b.moments_that_matter || b.moments),
    unhappy: arr(b.unhappy_paths || b.unhappy).map((u) => (isObj(u) ? { text: textOf(u.text || u.what || u.title), recovery: textOf(u.recovery || u.then || u.how) } : { text: textOf(u), recovery: "" })),
    steps: steps,
  };
}
