/* Search-credit arithmetic and words for the top-bar chip, the Budget page, and the confirm dialog. Pure: no DOM, no
   network, no storage (it runs under Node in the tests). A credit is what a search provider counts; it is NOT money.
   Everything here is ADVISORY: it warns and explains, it never decides that a run may not start.

   The server's GET /api/credits answers {enabled, providers: [{provider, label, unit, used, limit, remaining, level,
   source, as_of, read_at, plan, note, key_present, can_check, baseline}], per_run, estimate_hint, primary, check}. */

const LEVEL_RANK = { unknown: 0, ok: 1, amber: 2, red: 3 };
const ORDER = ["tavily", "serper"];

/** Provider keys in the order people expect: Tavily, Serper, then anything newer in name order. */
function inOrder(obj) {
  const keys = Object.keys(obj || {});
  return keys.sort((a, b) => {
    const ia = ORDER.indexOf(a);
    const ib = ORDER.indexOf(b);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    return a < b ? -1 : a > b ? 1 : 0;
  });
}

function whole(v) {
  return typeof v === "number" && isFinite(v) && v >= 0 ? v : null;
}

/** 1000 -> "1,000" (no locale: the same text in every browser and under Node). */
export function grouped(n) {
  const v = whole(n);
  if (v === null) return "";
  return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** The providers the person has a key for, in the server's order. */
export function configured(credits) {
  if (!credits || !credits.enabled) return [];
  return (credits.providers || []).filter((p) => p.key_present !== false);
}

/** "93 of 1,000 used" | "93 used" | "2,389 left" | "" when there is no figure. */
export function figureText(p) {
  if (!p) return "";
  const used = whole(p.used);
  const limit = whole(p.limit);
  const left = whole(p.remaining);
  if (used !== null && limit) return grouped(used) + " of " + grouped(limit) + " used";
  if (left !== null && used === null) return grouped(left) + " left";
  if (used !== null) return grouped(used) + " used";
  return "";
}

/** "Tavily 93 of 1,000 used" | "Tavily 93 used" | "Serper 2,389 left" | "Tavily: no count yet". */
export function creditText(p) {
  if (!p) return "";
  const figure = figureText(p);
  return figure ? p.label + " " + figure : p.label + ": no count yet";
}

/** The words for a level (the colour is only a help). */
export function levelWords(level) {
  return { ok: "Plenty left", amber: "Running low", red: "Almost out", unknown: "Not known yet" }[level] || "Not known yet";
}

/** What a run of one size usually needs, per provider: "Tavily about 32 (up to 41) · Serper about 26 (up to 41)". */
export function needText(hintForClass) {
  if (!hintForClass) return "";
  const parts = [];
  for (const name of inOrder(hintForClass)) {
    const c = hintForClass[name];
    if (c && whole(c.mid) !== null && whole(c.high) !== null) {
      parts.push(name.charAt(0).toUpperCase() + name.slice(1) + " about " + grouped(c.mid) + " (up to " + grouped(c.high) + ")");
    }
  }
  return parts.join(" · ");
}

/** The chip's text: every configured provider, "Credits: Tavily 93 of 1,000 used · Serper 2,389 left". "" when there is
    nothing to show. A word is added when credits run low (colour is never the only signal). */
export function creditsChipText(credits) {
  const list = configured(credits);
  if (!list.length) return "";
  const level = creditsLevel(credits);
  const word = level === "red" ? " · almost out" : level === "amber" ? " · running low" : "";
  return "Credits: " + list.map(creditText).join(" · ") + word;
}

/** The worst level among the configured providers: "red" | "amber" | "ok" | "unknown". */
export function creditsLevel(credits) {
  let worst = "unknown";
  for (const p of configured(credits)) {
    const lv = LEVEL_RANK[p.level] === undefined ? "unknown" : p.level;
    if (LEVEL_RANK[lv] > LEVEL_RANK[worst]) worst = lv;
  }
  return worst;
}

/** How much of the allowance is used, 0 to 100; null when the allowance is not known. */
export function creditPercent(p) {
  const used = whole(p && p.used);
  const limit = whole(p && p.limit);
  if (used === null || !limit) return null;
  return Math.max(0, Math.min(100, (100 * used) / limit));
}

/** Where a figure came from, in plain words. */
export function sourceWords(p) {
  if (!p || (whole(p.used) === null && whole(p.remaining) === null)) return "no count yet";
  if (p.source === "provider") return "from " + p.label;
  if (p.source === "unverified_endpoint") return "unverified";
  return "counted by this app";
}

/** "Checked with Tavily 3 min ago" | "Tavily has not been asked yet" | "Serper does not report its balance". */
export function checkedText(p, nowMs) {
  if (!p) return "";
  if (!p.can_check) return p.label + " does not report its balance, so this app counts it.";
  if (!p.read_at) return p.label + " has not been asked yet.";
  const t = Date.parse(p.read_at);
  if (!isFinite(t)) return "";
  const secs = Math.max(0, Math.round(((nowMs === undefined ? Date.now() : nowMs) - t) / 1000));
  let when;
  if (secs < 45) when = "just now";
  else if (secs < 3600) when = Math.max(1, Math.round(secs / 60)) + " min ago";
  else if (secs < 86400) when = Math.round(secs / 3600) + " h ago";
  else when = Math.round(secs / 86400) + " days ago";
  return "Checked with " + p.label + " " + when + ".";
}

/** The credits one run was billed: "Tavily 33 · Serper 2". */
export function runCreditsText(row) {
  const parts = [];
  const c = (row && row.credits) || {};
  for (const name of inOrder(c)) {
    if (whole(c[name]) !== null) parts.push(name.charAt(0).toUpperCase() + name.slice(1) + " " + grouped(c[name]));
  }
  return parts.join(" · ");
}

/** What to say about the credits before a run, from the run's estimate and what is left with the search provider the
    app will use. ADVISORY: {advisory: true, provider, partial, low, unknown, lines[]}; nothing here can stop a run. */
export function creditAdvice(estimate, credits) {
  const out = { advisory: true, provider: null, partial: false, low: false, unknown: false, lines: [] };
  if (!credits || !credits.enabled || !credits.primary || !estimate || !estimate.credits) return out;
  const need = estimate.credits[credits.primary];
  const p = (credits.providers || []).find((x) => x.provider === credits.primary);
  if (!need || !p) return out;
  out.provider = p.provider;
  const left = whole(p.remaining);
  if (left === null) {
    out.unknown = true;
    out.lines.push(
      "This run needs about " + grouped(need.mid) + " " + p.label + " credits. How many are left is not known yet: enter your " + p.label + " balance on the Budget page."
    );
    return out;
  }
  if (left < need.high) {
    out.partial = true;
    out.lines.push(
      "This may stop early (partial): this run needs about " + grouped(need.mid) + " " + p.label + " credits (up to " + grouped(need.high) + ") and " + grouped(left) + " are left. Nothing stops you; the run keeps what it has."
    );
    return out;
  }
  out.low = p.level === "amber" || p.level === "red";
  out.lines.push("This run needs about " + grouped(need.mid) + " " + p.label + " credits; " + grouped(left) + " left.");
  return out;
}

/** Check the baseline form before it is sent. {ok, error, body}. Whole numbers only. */
export function baselineForm(f) {
  const text = (v) => (v === undefined || v === null ? "" : String(v).trim());
  const num = (v) => (/^\d{1,9}$/.test(v) ? Number(v) : null);
  const provider = f && f.provider;
  const kind = f && f.kind;
  if (provider !== "tavily" && provider !== "serper") return { ok: false, error: "Pick Tavily or Serper.", body: null };
  if (kind !== "used" && kind !== "remaining") return { ok: false, error: "Say whether the number is used or left.", body: null };
  const amount = num(text(f.amount));
  if (amount === null) return { ok: false, error: "Type the number your account shows, as a whole number (for example 93).", body: null };
  let limit = null;
  if (text(f.limit) !== "") {
    limit = num(text(f.limit));
    if (limit === null) return { ok: false, error: "The allowance must be a whole number (for example 1000), or left empty.", body: null };
    if (amount > limit) return { ok: false, error: "That number is bigger than the allowance.", body: null };
  }
  const body = { provider: provider, kind: kind, amount: amount };
  if (limit !== null) body.limit = limit;
  return { ok: true, error: "", body: body };
}

/** The outcome of a Refresh in plain words (tone: "ok" | "warn"). */
export function refreshWords(outcome) {
  if (!outcome) return { tone: "ok", text: "" };
  if (outcome.done) return { tone: "ok", text: "Tavily answered: the figures are up to date." };
  if (outcome.throttled) {
    return { tone: "ok", text: "Tavily was asked a moment ago. It can be asked again in " + Math.max(1, Math.round(outcome.wait_s || 60)) + " s." };
  }
  const why = {
    auth: "Tavily did not accept the key.",
    rate_limited: "Tavily asked us to wait a little.",
    timeout: "Tavily did not answer in time.",
    network: "Tavily could not be reached.",
    bad_response: "Tavily sent an answer this page cannot read.",
    http_error: "Tavily had a problem.",
    quota: "Tavily says the allowance is used up.",
    not_available: "There is no Tavily key set, so there is nothing to ask.",
  };
  return { tone: "warn", text: (why[outcome.error] || "Tavily could not be asked.") + " The figures shown are the ones this app has counted." };
}
