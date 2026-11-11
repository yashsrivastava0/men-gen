/* Plain-words formatters and word tables. Pure: no DOM, no network, no storage (they run under Node in the tests).
   Money is integer micro-USD everywhere; "USD" with 2 decimals, 3 for tiny amounts; minutes, not milliseconds. */

export const MICRO = 1000000;

function num(v) {
  return typeof v === "number" && isFinite(v) ? v : null;
}

/** "1.23" (2 decimals; 3 when the amount is under one cent and not zero). */
export function usdNum(micros) {
  const m = num(micros) || 0;
  const v = m / MICRO;
  const tiny = v !== 0 && Math.abs(v) < 0.01;
  return v.toFixed(tiny ? 3 : 2);
}
export function usd(micros) {
  return "USD " + usdNum(micros);
}
/** "USD 0.20 to 0.65" */
export function usdRange(low, high) {
  return "USD " + usdNum(low) + " to " + usdNum(high);
}

export function plural(n, one, many) {
  return n + " " + (n === 1 ? one : many || one + "s");
}
export function pct(filled, required) {
  if (!(required > 0)) return 0;
  return Math.max(0, Math.min(100, Math.round((100 * filled) / required)));
}

/** Seconds as "45 s", "1 min 20 s", "3 min", "1 h 5 min". null -> "". */
export function formatDuration(sec) {
  let s = num(sec);
  if (s === null) return "";
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + " s";
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return m + " min" + (r ? " " + r + " s" : "");
  }
  let hh = Math.floor(s / 3600);
  let mm = Math.round((s % 3600) / 60);
  if (mm === 60) {
    hh += 1;
    mm = 0;
  }
  return hh + " h" + (mm ? " " + mm + " min" : "");
}
/** Time left, rounded so it does not flicker. null -> "estimating"; under 10 s -> "almost done". */
export function remainingText(eta) {
  const s = num(eta);
  if (s === null) return "estimating";
  if (s < 10) return "almost done";
  const rounded = s < 120 ? Math.round(s / 5) * 5 : Math.round(s / 60) * 60;
  return "about " + formatDuration(rounded);
}
/** An estimate's minutes ({low, mid, high}) as "about 3 minutes (2 to 5)". */
export function minutesText(m) {
  if (!m || num(m.mid) === null) return "";
  const range = num(m.low) !== null && num(m.high) !== null && (m.low !== m.mid || m.high !== m.mid) ? " (" + m.low + " to " + m.high + ")" : "";
  return "about " + plural(m.mid, "minute") + range;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function two(n) {
  return n < 10 ? "0" + n : String(n);
}
function toDate(value) {
  if (!value) return null;
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d;
}
/** "11 Nov 2026" (local time). Anything that is not a date comes back as text. */
export function formatDate(value) {
  const d = toDate(value);
  if (!d) return value ? String(value) : "";
  return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
}
/** "11 Nov 2026, 14:05" (local time). */
export function formatDateTime(value) {
  const d = toDate(value);
  if (!d) return value ? String(value) : "";
  return formatDate(value) + ", " + two(d.getHours()) + ":" + two(d.getMinutes());
}
/** "just now", "5 min ago", "3 h ago", "yesterday", "5 days ago", else the date. */
export function ago(value, nowMs) {
  const d = toDate(value);
  if (!d) return "";
  const secs = Math.round(((nowMs === undefined ? Date.now() : nowMs) - d.getTime()) / 1000);
  if (secs < -60) return formatDate(value);
  if (secs < 45) return "just now";
  if (secs < 3600) return Math.max(1, Math.round(secs / 60)) + " min ago";
  if (secs < 86400) return Math.round(secs / 3600) + " h ago";
  const days = Math.round(secs / 86400);
  if (days === 1) return "yesterday";
  if (days < 14) return days + " days ago";
  return formatDate(value);
}
/** Seconds between two timestamps (end defaults to now); null when the start is unknown. */
export function secondsBetween(start, end, nowMs) {
  const a = toDate(start);
  if (!a) return null;
  const b = end ? toDate(end) : new Date(nowMs === undefined ? Date.now() : nowMs);
  if (!b) return null;
  return Math.max(0, Math.round((b.getTime() - a.getTime()) / 1000));
}
export function bytesText(n) {
  const v = num(n);
  if (v === null) return "";
  if (v < 1024) return v + " B";
  if (v < 1024 * 1024) return Math.round(v / 1024) + " KB";
  return (v / 1048576).toFixed(1) + " MB";
}
/** Seconds to wait before the next stream reconnect: 1, 2, 4, 8, then 15 (rand 0..1 adds up to 20 percent). */
export function reconnectDelay(attempt, rand) {
  const base = Math.min(15, Math.pow(2, Math.max(0, attempt || 0)));
  return rand === undefined ? base : base * (1 + 0.2 * rand);
}
/** "no_content" -> "no content" (an unknown code from a newer server still reads as words). */
export function humanize(code) {
  return String(code === undefined || code === null ? "" : code)
    .replace(/[_.-]+/g, " ")
    .trim();
}
export function capitalize(text) {
  const s = String(text || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// ---- the word tables (the one place the copy for statuses, jobs, steps and warnings lives) ---------------------------

function pick(map, key, fallback) {
  if (key === undefined || key === null || key === "") return fallback === undefined ? "" : fallback;
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : capitalize(humanize(key));
}

export const RUN_WORDS = {
  queued: "Waiting to start",
  running: "Researching",
  committing: "Saving the findings",
  complete: "Done",
  partial: "Done, with gaps",
  failed: "Could not finish",
  skipped: "Stopped early",
  interrupted: "Interrupted",
  cancelled: "Cancelled",
};
export const TOPIC_WORDS = {
  queued: "Waiting",
  running: "Researching",
  covered: "Done",
  not_found: "Nothing reliable found",
  cancelled: "Stopped",
  skipped: "Skipped",
  partial: "Partly done",
};
export const JOB_ORDER = ["features", "structure", "flows", "data", "services", "design", "screens", "local", "trust"];
export const JOB_TITLES = {
  features: "Features of similar apps",
  structure: "How such apps are organised",
  flows: "How people use them",
  data: "Rules, limits and data",
  services: "Outside services and providers",
  design: "Look and feel",
  screens: "Key screens",
  local: "Local data and language",
  trust: "Privacy, security and store rules",
};
export const STEP_WORDS = {
  plan: "Planning",
  queries: "Search wording",
  extract: "Reading pages",
  check: "Checking facts",
  assemble: "Putting the result together",
  probe: "Provider test",
  ping: "Connection test",
  reconciliation: "Adjustment to match the ledger",
};
export const KIND_WORDS = {
  run: "Research run",
  bakeoff: "Provider comparison",
  probe: "Probe",
  ping: "Connection test",
  smoke: "Live check",
};
export const WARNING_WORDS = {
  taking_longer: "Taking a bit longer",
  slow_provider: "A search service is slow",
  rate_limited: "Waiting because of a rate limit",
  provider_down: "A search service is not answering",
  no_results: "A search found nothing",
  no_keys: "A search key is missing",
  model_failed: "A model step failed, a safe fallback was used",
  budget_stop: "The budget stopped the research",
};
export const SIZE_ORDER = ["tiny", "small", "medium", "big"];
export const SIZE_WORDS = { tiny: "Tiny", small: "Small", medium: "Medium", big: "Big" };
export const LOAD_WORDS = { focused: "focused", standard: "standard", deep: "deep" };
export const FEED_KINDS = {
  "query.issued": "Search",
  "search.results": "Sources",
  "page.reading": "Reading",
  "page.read": "Read",
  "page.skipped": "Skipped",
  "fact.found": "Checking",
  "fact.verified": "Checked",
  "fact.rejected": "Dropped",
  "entity.discovered": "Found",
  "round.completed": "Round",
  "gap.detected": "Second look",
  warning: "Note",
  "plan.ready": "Plan",
  "run.started": "Start",
  "target.started": "Topic",
  "target.covered": "Topic done",
  "target.not_found": "Topic",
  "target.cancelled": "Topic stopped",
  "run.finished": "Finished",
  "run.skipped": "Stopped",
  "run.failed": "Problem",
};
export const SKIP_REASONS = {
  no_content: "no readable text",
  paywall: "behind a paywall",
  blocked: "the site did not let us in",
  not_html: "not a web page",
  too_large: "too large to read",
  timeout: "too slow to open",
  not_found: "page not found",
};

export const runWord = (s) => pick(RUN_WORDS, s, "Waiting to start");
export const topicWord = (s) => pick(TOPIC_WORDS, s, "Waiting");
export const jobTitle = (j) => pick(JOB_TITLES, j, "Other");
export const sizeWord = (s) => pick(SIZE_WORDS, s, "");
export const warningTitle = (c) => pick(WARNING_WORDS, c, "Note");
export const kindWord = (k) => pick(KIND_WORDS, k, "Action");
export function stepWord(step) {
  const s = String(step || "");
  if (s.indexOf("search.") === 0) return "Web search (" + s.slice(7) + ")";
  return pick(STEP_WORDS, s, "Other");
}
/** The class names a status wears in the interface: ok, busy, warn or bad. */
export function statusTone(status) {
  if (status === "complete") return "ok";
  if (status === "running" || status === "queued" || status === "committing") return "busy";
  if (status === "partial" || status === "skipped" || status === "cancelled") return "warn";
  if (status === "failed" || status === "interrupted") return "bad";
  return "";
}
