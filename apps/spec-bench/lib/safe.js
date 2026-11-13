/* Safety and plain-language helpers. Pure. Every link passes through safeUrl (outside addresses), safeApiPath (this
   app's own files) or safeRoute (in-app routes); every failure becomes a plain sentence through problemText (never a
   stack trace, never a raw code alone). A pack holds user and model text: it is only ever shown as text. */

/** The href for an outside link, or null. Only the schemes on the allow-list, no credentials in the address. */
const SCHEMES = ["http:", "https:"];
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
  if (SCHEMES.indexOf(u.protocol) === -1) return null;
  if (u.username || u.password || !u.hostname) return null;
  return u.href;
}

/** A path of this app's own API such as "/api/runs/run_01ABC/export/preview.html", or null. */
export function safeApiPath(value) {
  if (typeof value !== "string") return null;
  if (!/^\/api\/[A-Za-z0-9_\/.-]{1,200}$/.test(value)) return null;
  if (value.indexOf("..") !== -1 || value.indexOf("//") !== -1) return null;
  return value;
}

/** An in-app route like "#/run/run_01ABC", or null. Used for every internal link. */
export function safeRoute(value) {
  if (typeof value !== "string") return null;
  return /^#\/[A-Za-z0-9_\/-]{0,160}$/.test(value) ? value : null;
}

/** A short sentence for the owner: the server's words if they read like a sentence, never a stack trace. */
export function plainReason(text, fallback) {
  const s = String(text === undefined || text === null ? "" : text)
    .replace(/\s+/g, " ")
    .trim();
  const generic = fallback === undefined ? "Something went wrong." : fallback;
  if (!s) return generic;
  if (/traceback|exception|\bat\s+[\w.$]+\(|file "|\.py\b|\bstack\b|errno|0x[0-9a-f]{4,}/i.test(s)) return generic;
  if (/^[A-Z][A-Za-z]*(Error|Exception)$/.test(s)) return generic;
  return s.length > 320 ? s.slice(0, 317) + "..." : s;
}

/** The run id named in an error message. */
export function runIdFrom(text) {
  const m = /\brun_[0-9A-Za-z]+/.exec(String(text || ""));
  return m ? m[0] : null;
}

/** An HTTP problem as {kind, code, text, fix, runId}. status 0 means no answer at all.
    The body is the API's error shape {error:{code, message, fix?}} (a flat {code, message} is read too). */
export function problemText(status, body) {
  const b = body && typeof body === "object" ? body : {};
  const e = b.error && typeof b.error === "object" ? b.error : b;
  const code = typeof e.code === "string" ? e.code : "";
  const message = typeof e.message === "string" ? plainReason(e.message, "") : "";
  const fix = typeof e.fix === "string" ? plainReason(e.fix, "") : "";
  const out = { kind: "other", code: code, text: "", fix: fix, runId: runIdFrom(message) };
  if (!status) {
    out.kind = "network";
    out.text = "We could not reach the Spec Bench app. Check that it is still running in its terminal window, then try again.";
  } else if (code === "bad_origin" || status === 403) {
    out.kind = "origin";
    out.text = "The app refused this request because it did not come from the app's own page. Open the app at its own address and try again.";
  } else if (code === "refused_estimate") {
    out.kind = "refused_estimate";
    out.text = message || "This run was not started: its highest estimated cost is more than the money that is left.";
  } else if (code === "budget_blocked") {
    out.kind = "budget";
    out.text = message || "The money budget does not allow this right now.";
  } else if (code === "not_ready") {
    out.kind = "not_ready";
    out.text = message || "The app cannot do this yet.";
  } else if (code === "run_active") {
    out.kind = "active";
    out.text = message || "A run is already in progress.";
  } else if (code === "gate_required") {
    out.kind = "gate";
    out.text = message || "A decision is needed first: approve the earlier step before this one.";
  } else if (code === "conflict") {
    out.kind = "conflict";
    out.text = message || "That action is not possible right now.";
  } else if (code === "not_found") {
    out.kind = "missing";
    out.text = message || "We could not find that. It may have been removed.";
  } else if (code === "bad_request") {
    out.kind = "refused";
    out.text = message || "The app could not use that.";
  } else if (status === 409) {
    out.kind = "conflict";
    out.text = message || "That action is not possible right now.";
  } else if (status === 404) {
    out.kind = "missing";
    out.text = message || "We could not find that. It may have been removed.";
  } else if (status === 400 || status === 422) {
    out.kind = "refused";
    out.text = message || "The app could not use that.";
  } else if (status === 413) {
    out.kind = "too_large";
    out.text = "That file is too large.";
  } else if (status >= 500) {
    out.kind = "server";
    out.text = "The app had a problem. Please try again in a moment.";
  } else {
    out.text = message || "Something went wrong.";
  }
  return out;
}

/** Whether a string from the outside looks like markup (tests only; text is always inserted as text). */
export function looksLikeMarkup(text) {
  return /[<>]/.test(String(text || ""));
}
