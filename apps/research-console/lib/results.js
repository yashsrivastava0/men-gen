/* Plain words and small pure helpers for the results screens: evidence badges, flags, lanes, gaps, quality, the
   acceptance rows of the metrics page, readable item data, and the review query. No DOM, no network. */
import { usd, humanize, capitalize, formatDate, pct } from "./format.js";

function pick(map, key, fallback) {
  if (key === undefined || key === null || key === "") return fallback === undefined ? "" : fallback;
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : capitalize(humanize(key));
}

export const EVIDENCE = {
  official: { word: "Official source", tone: "ok", hint: "A page from the provider or authority itself." },
  corroborated: { word: "Several sources agree", tone: "ok", hint: "Independent sites say the same thing." },
  single_source: { word: "Only one source", tone: "warn", hint: "Found on one site only. Treat it as a lead." },
  unverified: { word: "Not fully verified", tone: "bad", hint: "The check could not confirm it. Do not rely on it." },
};
export const evidenceWord = (e) => (EVIDENCE[e] ? EVIDENCE[e].word : pick({}, e, "Unknown"));
export const evidenceTone = (e) => (EVIDENCE[e] ? EVIDENCE[e].tone : "plain");

export const FLAG_WORDS = {
  "single-source": "Only one source",
  conflict: "Sources disagree",
  stale: "May be out of date",
  "needs-confirm": "Please confirm",
  "legal-adjacent": "Legal-adjacent",
  entity_from_model: "Name not confirmed online",
  assumption_derived: "Based on an assumption",
  geo_uncertain: "Place uncertain",
  limited_evidence: "Limited evidence",
  withdrawn: "Withdrawn",
  unchecked: "Not fully checked",
  checker_same_vendor: "Checked by the same model family",
  duplicate_source: "Same text on several sites",
  other_region: "About another country",
  other_entity: "About a different company",
  number_widened: "Number found in a wider passage",
  partial_support: "Only partly supported",
};
export const flagWord = (f) => pick(FLAG_WORDS, f, "");
export const FLAG_TONE = { conflict: "bad", unchecked: "bad", other_region: "bad", other_entity: "bad", stale: "warn", "needs-confirm": "warn", "single-source": "warn", "legal-adjacent": "warn" };

export const IMPACT = { high: "High impact", normal: "Normal impact", low: "Low impact" };
export const impactWord = (i) => pick(IMPACT, i, "");

export const KIND_WORDS = {
  competitor: "Comparable app",
  service_option: "Provider option",
  rule: "Rule or limit",
  workflow: "Journey",
  screen_pattern: "Screen pattern",
  design_convention: "Design convention",
  entity_pattern: "Data record",
  lifecycle: "Lifecycle",
  compliance_note: "Compliance note",
  trend: "Trend",
  navigation_pattern: "Navigation pattern",
  sample_data: "Sample data",
  local_format: "Local format",
};
export const kindWord = (k) => pick(KIND_WORDS, k, "");

export const LANES = ["blueprint", "integrations", "design", "screens", "mock", "verify"];
export const LANE_INFO = {
  blueprint: { title: "Blueprint", note: "What the app does and how its parts fit together" },
  integrations: { title: "Integrations", note: "Outside services the app will need to connect to" },
  design: { title: "Design", note: "How apps like this usually look and feel" },
  screens: { title: "Screens", note: "Which screens apps like this usually have" },
  mock: { title: "Sample data", note: "Realistic example content for the mock-up" },
  verify: { title: "Checks", note: "Things to double-check before building" },
};
export const laneTitle = (l) => (LANE_INFO[l] ? LANE_INFO[l].title : capitalize(humanize(l)));
export const laneNote = (l) => (LANE_INFO[l] ? LANE_INFO[l].note : "");

export const GAP_WORDS = {
  not_found: "We could not find reliable sources.",
  budget_stop: "The budget limit stopped the research before this topic was finished.",
  provider_down: "A search service was not available.",
  not_run: "Research ended before this topic was reached.",
  not_worthwhile: "This was not worth looking up for this app.",
  conflict_unresolved: "Sources disagreed and we could not settle it.",
  user_cancelled: "You stopped this topic.",
  ambiguous_reference: "We could not tell which one the Brief meant.",
  declined: "We left this out on purpose: the Brief already answers it.",
};
export const gapWord = (r) => (GAP_WORDS[r] ? GAP_WORDS[r] : "We could not finish this topic.");

export const LEVEL_WORDS = { core: "Core", common: "Common", advanced: "Advanced", emerging: "Emerging", limited_evidence: "Limited evidence" };
export const LEVEL_HINTS = {
  core: "Almost every comparable app does this",
  common: "Most comparable apps do this",
  advanced: "Only some of the more mature apps do this",
  emerging: "New and growing, not yet usual",
  limited_evidence: "We found little evidence either way",
};
export const levelWord = (l) => pick(LEVEL_WORDS, l, "");
export const AREA_TITLES = { feature: "Features", screen: "Screens", flow: "Flows", entity: "Data the app keeps", integration: "Integrations", design: "Design" };
export const SUGGESTION_TITLES = { features: "Feature ideas", screens: "Screen ideas", fields: "Field ideas", states: "Screen states", validations: "Input checks" };

export const QUALITY_WORDS = { strong: "Strong", partial: "Partly covered", thin: "Thin", none: "Nothing needed here" };
export const QUALITY_TONE = { strong: "ok", partial: "warn", thin: "bad", none: "plain" };
export const BN_WORDS = {
  researched: "Researched",
  defaulted_only: "Only a default, no research found",
  defaulted: "A default is proposed",
  not_needed: "Not needed",
};
export const STAGE_WORDS = {
  plan: "Planning",
  queries: "Search wording",
  search: "Searching",
  read: "Reading pages",
  extract: "Extracting facts",
  check: "Checking facts",
  assemble: "Putting the result together",
};
export const APPLIES_KINDS = { FTR: "Feature", INT: "Integration", ROL: "Role", ROLE: "Role", APP: "App", SCR: "Screen", FLW: "Flow", ENT: "Data record", REF: "Reference", NEW: "Not in your Brief" };
export const appliesText = (a) => pick(APPLIES_KINDS, a.kind, "Item") + (a.id ? " " + a.id : "") + (a.relation ? " (" + humanize(a.relation) + ")" : "");
export const STRATUM_WORDS = { high_impact: "A fact where a mistake would matter", service: "A provider or integration fact", typed_data: "A data record, screen or lifecycle", ordinary: "An ordinary fact" };

/** "mentioned in 3 of 14" and the level word. */
export function prevalenceText(p) {
  if (!p) return "";
  return "mentioned in " + (p.seen_in || 0) + " of " + (p.of || 0) + " sources";
}
export function recencyText(r) {
  if (!r || !r.newest_source) return "";
  const age = typeof r.age_months === "number" ? (r.age_months <= 0 ? "this month" : r.age_months + " months ago") : "";
  return "newest source " + formatDate(r.newest_source) + (age ? " (" + age + ")" : "");
}
export const ratio = (p) => (p && p.of > 0 ? (p.seen_in || 0) / p.of : 0);

/** Norms grouped by area in a fixed order, strongest first inside a group: [{area, title, norms[]}]. */
export function normGroups(norms) {
  const by = {};
  for (const n of norms || []) (by[n.area] = by[n.area] || []).push(n);
  const order = Object.keys(AREA_TITLES).filter((a) => by[a]).concat(Object.keys(by).filter((a) => !AREA_TITLES[a]).sort());
  return order.map((area) => ({
    area: area,
    title: AREA_TITLES[area] || capitalize(humanize(area)),
    norms: by[area].slice().sort((a, b) => ratio(b.prevalence) - ratio(a.prevalence) || (b.prevalence.seen_in || 0) - (a.prevalence.seen_in || 0) || (a.label < b.label ? -1 : 1)),
  }));
}
export function gapGroups(gaps) {
  const by = {};
  for (const g of gaps || []) (by[g.reason] = by[g.reason] || []).push(g);
  const order = Object.keys(GAP_WORDS).filter((r) => by[r]).concat(Object.keys(by).filter((r) => !GAP_WORDS[r]));
  return order.map((reason) => ({ reason: reason, text: gapWord(reason), gaps: by[reason] }));
}

// ---- the metrics page -------------------------------------------------------------------------------------------------------
export const ACCEPTANCE_LABELS = {
  extraction_quote_pass_first_try: "Quotes found in the page the first time",
  number_rejection_rate: "Findings dropped because a number was not in the quote",
  fetch_success: "Pages that could be read",
  searches_contributing: "Searches that led to a kept finding",
  slots_filled: "Required facts found",
  checker_not_supported: "Findings the checker did not support",
  norms_with_3_plus_sources: "Norms seen in three or more sources",
  recent_24m_of_dated: "Dated findings that are under two years old",
};
export const fmtPct = (v) => (typeof v === "number" ? Math.round(v * 1000) / 10 + "%" : "not measured");
/** One row per acceptance measure: {key, label, value, target, rule, state: "ok"|"bad"|"none"}. */
export function acceptanceRows(metrics) {
  const acc = (metrics && metrics.acceptance) || {};
  return Object.keys(acc).map((key) => {
    const a = acc[key];
    return {
      key: key,
      label: ACCEPTANCE_LABELS[key] || capitalize(humanize(key)),
      value: fmtPct(a.value),
      target: (a.kind === "max" ? "at most " : "at least ") + fmtPct(a.target),
      state: a.ok === null || a.ok === undefined ? "none" : a.ok ? "ok" : "bad",
    };
  });
}
export const acceptState = { ok: "OK", bad: "Not OK", none: "Not measured" };
/** Cost by step from either micro-USD integers (metrics) or decimal strings (the run.finished event). */
export function costSteps(cost) {
  const out = [];
  for (const k of Object.keys(cost || {})) {
    const v = cost[k];
    const micros = typeof v === "number" ? v : Math.round(parseFloat(v) * 1000000);
    if (isFinite(micros) && micros > 0) out.push({ step: k, micros: micros });
  }
  return out.sort((a, b) => b.micros - a.micros);
}
export function stageRows(stages) {
  return Object.keys(stages || {}).map((k) => ({ stage: k, label: STAGE_WORDS[k] || capitalize(humanize(k)), seconds: stages[k] }));
}

// ---- item data in readable form --------------------------------------------------------------------------------------------------
const MAX_ROWS = 60;
function scalar(v) {
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}
function listText(x) {
  if (x !== null && typeof x === "object" && !Array.isArray(x)) {
    return Object.keys(x)
      .filter((k) => x[k] !== null && x[k] !== "" && !(Array.isArray(x[k]) && !x[k].length))
      .map((k) => capitalize(humanize(k)) + ": " + (typeof x[k] === "object" ? JSON.stringify(x[k]) : scalar(x[k])))
      .join("; ");
  }
  return scalar(x);
}
/** Item data as rows: {label, value} | {label, list: [text]} | {label, rows: [...]}. Empty values are left out. */
export function dataRows(data, depth) {
  const d = depth || 0;
  const rows = [];
  if (!data || typeof data !== "object") return rows;
  for (const k of Object.keys(data)) {
    if (rows.length >= MAX_ROWS) break;
    const v = data[k];
    if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length) || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length)) continue;
    const label = capitalize(humanize(k));
    if (Array.isArray(v)) rows.push({ label: label, list: v.slice(0, 40).map(listText) });
    else if (typeof v === "object") rows.push(d >= 2 ? { label: label, value: listText(v) } : { label: label, rows: dataRows(v, d + 1) });
    else rows.push({ label: label, value: scalar(v) });
  }
  return rows;
}

// ---- the review query and request control ---------------------------------------------------------------------------------------------
export const FILTER_KEYS = ["job", "lane", "evidence", "flag", "impact", "kind", "q"];
export function reviewQuery(filters, store, offset, limit) {
  const p = [];
  const f = filters || {};
  for (const k of FILTER_KEYS) if (f[k]) p.push(k + "=" + encodeURIComponent(f[k]));
  if (store) p.push("store=" + encodeURIComponent(store));
  p.push("offset=" + (offset || 0));
  p.push("limit=" + (limit || 50));
  return p.join("&");
}
export function activeFilterCount(filters) {
  return FILTER_KEYS.filter((k) => filters && filters[k]).length;
}
/** Delay a function until the caller has been quiet for `ms`; `.cancel()` drops a pending call. */
export function debounce(fn, ms, timers) {
  const t = timers || { set: (fn, ms) => setTimeout(fn, ms), clear: (id) => clearTimeout(id) };
  let handle = null;
  const wrapped = (...args) => {
    if (handle !== null) t.clear(handle);
    handle = t.set(() => {
      handle = null;
      fn(...args);
    }, ms);
  };
  wrapped.cancel = () => {
    if (handle !== null) t.clear(handle);
    handle = null;
  };
  return wrapped;
}
/** Only the latest request may answer: begin() aborts the one before; current(id) says whether an answer is still wanted. */
export function latestOnly(makeController) {
  let n = 0;
  let ctl = null;
  const mk = makeController || (() => new AbortController());
  return {
    begin() {
      if (ctl) ctl.abort();
      ctl = mk();
      n += 1;
      return { id: n, signal: ctl.signal };
    },
    current: (id) => id === n,
  };
}
/** Which 100-item pages (0-based) a window of rows needs and does not have yet. */
export function pagesNeeded(start, end, pageSize, loaded) {
  const out = [];
  if (end <= start) return out;
  for (let p = Math.floor(start / pageSize); p <= Math.floor((end - 1) / pageSize); p++) if (!loaded.has(p)) out.push(p);
  return out;
}

/** The trust summary lines of the results header (from overview.metrics and profile). */
export function trustLines(overview) {
  const m = overview.metrics || {};
  const lines = [];
  const ev = m.evidence || {};
  const total = Object.keys(ev).reduce((n, k) => n + ev[k], 0);
  if (total) {
    const strong = (ev.official || 0) + (ev.corroborated || 0);
    lines.push(pct(strong, total) + "% of the " + total + " findings come from official or several agreeing sources.");
  }
  const c = m.checker || {};
  if (typeof c.supported === "number") lines.push("The checker supported " + fmtPct(c.supported) + " of findings" + (c.unchecked ? " (" + fmtPct(c.unchecked) + " not checked)" : "") + ".");
  if (c.rubber_stamp_signal) lines.push("The checker rejected almost nothing, which can mean it is too lenient.");
  const fl = m.flags || {};
  if (fl["needs-confirm"]) lines.push(fl["needs-confirm"] + " findings need your confirmation.");
  if (fl["single-source"]) lines.push(fl["single-source"] + " rest on a single source.");
  const p = overview.profile || {};
  if (p.appendix_items) lines.push(p.appendix_items + " lower-ranked findings are in the appendix.");
  return lines;
}
export const usdOf = (micros) => usd(micros || 0);
