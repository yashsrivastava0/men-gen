/* Words and numbers. Pure. Money is integer micro-USD. The one place the copy for statuses, steps and reasons lives:
   an unknown code from a newer server still reads as words. */
const MICRO = 1000000;
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

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
/** 12345 -> "12,345" (no locale, so the same everywhere). */
export function count(n) {
  const v = num(n);
  if (v === null) return "";
  return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
/** Tokens as "12.3k tokens" under a million, "1.2M tokens" above. */
export function tokensText(n) {
  const v = num(n);
  if (v === null) return "";
  if (v >= 1000000) return (v / 1000000).toFixed(1) + "M tokens";
  if (v >= 10000) return (v / 1000).toFixed(1) + "k tokens";
  return count(v) + " tokens";
}
export function pct(part, whole) {
  if (!(whole > 0)) return 0;
  return Math.max(0, Math.min(100, Math.round((100 * part) / whole)));
}
export function formatDuration(sec) {
  let s = num(sec);
  if (s === null) return "";
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + " s";
  const m = Math.floor(s / 60);
  if (s < 3600) return m + " min" + (s % 60 ? " " + (s % 60) + " s" : "");
  return Math.floor(s / 3600) + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
}
/** Seconds to wait before the next stream reconnect: 1, 2, 4, 8, then 15 (rand 0..1 adds up to 20 percent). */
export function reconnectDelay(attempt, rand) {
  const base = Math.min(15, Math.pow(2, Math.max(0, attempt || 0)));
  return rand === undefined ? base : base * (1 + 0.2 * rand);
}
export function humanize(code) {
  return String(code === undefined || code === null ? "" : code)
    .replace(/[_.-]+/g, " ")
    .trim();
}
export function capitalize(text) {
  const s = String(text || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
/** An ISO time as "2026-11-14 10:05 UTC" (the app speaks UTC). */
export function formatTime(iso) {
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(String(iso || ""));
  return m ? m[1] + " " + m[2] + " UTC" : "";
}
const pick = (map, key, fallback) => (Object.prototype.hasOwnProperty.call(map, key) ? map[key] : fallback);

const STATUS = {
  queued: "Waiting to start",
  running: "Running",
  paused_g15: "Waiting for your OK on the structure (G1.5)",
  awaiting_g2: "Waiting for your OK on the spec (G2)",
  approved: "Approved",
  partial: "Stopped early (partial)",
  failed: "Failed",
};
export const statusWord = (s) => pick(STATUS, s, capitalize(humanize(s)) || "Unknown");
export function statusTone(s) {
  if (s === "approved") return "ok";
  if (s === "failed") return "bad";
  if (s === "partial" || s === "paused_g15" || s === "awaiting_g2") return "warn";
  if (s === "running" || s === "queued") return "busy";
  return "plain";
}
export const isActiveStatus = (s) => s === "queued" || s === "running";

const STEPS = {
  load: "Reading the inputs",
  inputs: "Reading the inputs",
  preview: "Working out the size and cost",
  structure: "Building the app structure",
  freeze: "Freezing the structure",
  product: "Writing the product detail",
  design: "Writing the design intent",
  contract: "Writing the data contract",
  content: "Writing screen content",
  verify: "Independent check (Verify)",
  validate: "Running the checks",
  repair: "Repairing problems",
  package: "Packing the files",
  critic: "Final review",
  g2: "Preparing the spec for approval",
};
export const stepWord = (s) => pick(STEPS, s, capitalize(humanize(s)) || "Step");

const REASONS = {
  budget_cap: "the money cap was reached",
  budget_tripwire: "the money tripwire was reached, so no new work started",
  stop: "you asked it to stop",
  rate_limit: "the model provider asked us to slow down",
  no_credit: "the model provider says the account has no credit left",
  crash: "the app stopped unexpectedly",
};
export const reasonWord = (r) => pick(REASONS, r, humanize(r) || "an unstated reason");

const BANNERS = {
  weak_route: "A weaker model route is in use for this run, so the result may be less polished.",
};
export const bannerWord = (code) => pick(BANNERS, code, capitalize(humanize(code)));

const SEVERITY = { blocker: "Must fix", error: "Must fix", warning: "Check", warn: "Check", info: "Note" };
export const severityWord = (s) => pick(SEVERITY, s, capitalize(humanize(s)) || "Note");
export const severityTone = (s) => (s === "blocker" || s === "error" ? "bad" : s === "warning" || s === "warn" ? "warn" : "plain");

const KINDS = { must: "MUST", usually: "USUALLY", could: "COULD" };
export const importanceWord = (k) => pick(KINDS, k, String(k || "").toUpperCase());

export const MOCK_PROFILES = ["empty", "typical", "busy", "extreme", "broken", "offline"];
const MOCK_WORDS = {
  empty: "Empty: a brand-new user with nothing yet",
  typical: "Typical: an ordinary amount of data",
  busy: "Busy: a lot of data",
  extreme: "Extreme: far more than expected",
  broken: "Broken: data that is wrong or missing",
  offline: "Offline: no connection",
};
export const mockWord = (p) => pick(MOCK_WORDS, p, capitalize(humanize(p)));

/** The confirm list is ranked: money first, then legal, then identity, then the rest. */
export const CONFIRM_ORDER = ["money", "legal", "identity"];
export function confirmRank(category) {
  const i = CONFIRM_ORDER.indexOf(String(category || "").toLowerCase());
  return i === -1 ? CONFIRM_ORDER.length : i;
}
