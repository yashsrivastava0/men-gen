/* Safety and plain-language helpers. Pure. Every link in the interface passes through safeUrl (http and https only);
   every failure becomes a plain sentence through problemText (never a stack trace, never a raw code alone). */

/** The href for a link, or null. Only http and https, no credentials in the address, nothing relative. */
export function safeUrl(value) {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > 2048) return null;
  let u;
  try {
    u = new URL(text);
  } catch (e) {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (u.username || u.password || !u.hostname) return null;
  return u.href;
}

/** An in-app route like "#/run/rrn_01ABC", or null. Used for every internal link. */
export function safeRoute(value) {
  if (typeof value !== "string") return null;
  return /^#\/[A-Za-z0-9_\/-]{0,120}$/.test(value) ? value : null;
}

/** A short sentence for the owner: the server's words if they read like a sentence, never a stack trace. */
export function plainReason(text, fallback) {
  const s = String(text === undefined || text === null ? "" : text)
    .replace(/\s+/g, " ")
    .trim();
  const generic = fallback === undefined ? "Something went wrong while researching." : fallback;
  if (!s) return generic;
  if (/traceback|exception|\bat\s+[\w.$]+\(|file "|\.py\b|\bstack\b|errno|0x[0-9a-f]{4,}/i.test(s)) return generic;
  if (/^[A-Z][A-Za-z]*(Error|Exception)$/.test(s)) return generic; // a bare class name such as AttributeError
  return s.length > 240 ? s.slice(0, 237) + "..." : s;
}

/** The run id named in a 409 run_active answer. */
export function runIdFrom(body) {
  if (!body || typeof body !== "object") return null;
  if (typeof body.run_id === "string" && body.run_id) return body.run_id;
  const m = /\brrn_[0-9A-Za-z]+/.exec(String(body.message || ""));
  return m ? m[0] : null;
}

/** An HTTP problem as {kind, code, text, missing[], runId}. status 0 means no answer at all. */
export function problemText(status, body) {
  const b = body && typeof body === "object" ? body : {};
  const code = typeof b.code === "string" ? b.code : "";
  const message = typeof b.message === "string" ? plainReason(b.message, "") : "";
  const missing = Array.isArray(b.missing) ? b.missing.map(String) : [];
  const out = { kind: "other", code: code, text: "", missing: missing, runId: runIdFrom(b) };
  if (!status) {
    out.kind = "network";
    out.text = "We could not reach the Research app. Check that it is still running in its terminal window, then try again.";
  } else if (status === 401) {
    out.kind = "auth";
    out.text = "The app did not accept this page's sign-in. Reload the page.";
  } else if (status === 403) {
    out.kind = "origin";
    out.text = "The app refused this request because it did not come from the app's own page. Open the app at its own address and try again.";
  } else if (code === "not_ready") {
    out.kind = "not_ready";
    out.text = message || "Research cannot start yet.";
  } else if (code === "budget_tripwire" || code === "budget_exhausted") {
    out.kind = "budget";
    out.text = message || "The budget does not allow a new run.";
  } else if (code === "run_active") {
    out.kind = "active";
    out.text = message || "This idea already has a run in progress.";
  } else if (status === 404) {
    out.kind = "missing";
    out.text = message && code !== "not_found" ? message : "We could not find that. It may have been removed.";
  } else if (status === 409) {
    out.kind = "conflict";
    out.text = message || "That action is not possible right now.";
  } else if (status === 413) {
    out.kind = "too_large";
    out.text = "That file is too large (the limit is 25 MB).";
  } else if (status === 422 || status === 400) {
    out.kind = "refused";
    out.text = message || "The app could not use that.";
  } else if (status >= 500) {
    out.kind = "server";
    out.text = "The app had a problem. Please try again in a moment.";
  } else {
    out.text = message || "Something went wrong.";
  }
  return out;
}

/** Whether a string from the outside looks like markup (used only by tests and the self-check, never to "clean" text:
    text is always inserted as text, so markup is shown as the characters it is). */
export function looksLikeMarkup(text) {
  return /[<>]/.test(String(text || ""));
}
