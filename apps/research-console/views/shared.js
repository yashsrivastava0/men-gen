/* Small pieces several screens draw the same way. */
import { h, untrusted } from "../lib/dom.js";
import { usd, usdNum, minutesText, plural, runWord, statusTone, sizeWord } from "../lib/format.js";

/** A status word in a coloured chip (the colour is never the only signal: the word is always there). */
export function statusChip(status, extraWord) {
  return h("span", { class: "chip chip-" + (statusTone(status) || "plain") }, extraWord || runWord(status));
}
export function sizeBadge(sizeClass, label) {
  return h("span", { class: "chip chip-size size-" + sizeClass }, label || sizeWord(sizeClass) || "Size unknown");
}
/** The estimate as plain sentences: {cost, time, work, note, basis}. */
export function estimateParts(est) {
  if (!est || !est.usd_micros) return null;
  const u = est.usd_micros;
  return {
    cost: "about " + usd(u.mid) + " (likely between " + usdNum(u.low) + " and " + usdNum(u.high) + ")",
    time: minutesText(est.minutes),
    work: [est.searches && est.searches.mid ? "about " + plural(est.searches.mid, "search", "searches") : "", est.pages && est.pages.mid ? plural(est.pages.mid, "page") : ""]
      .filter(Boolean)
      .join(", "),
    note: est.note || "estimate, not measured",
    basis: est.basis || "estimate",
  };
}
export function placeText(place) {
  if (!place) return "not stated";
  const parts = [].concat(place.cities || [], place.country ? [place.country] : []);
  return parts.length ? parts.join(", ") + (place.uncertain ? " (not certain)" : "") : "not stated";
}
export function countsText(c) {
  if (!c) return "";
  const facts = c.facts ? Object.keys(c.facts).reduce((n, k) => n + (c.facts[k] || 0), 0) : 0;
  return [
    plural(c.features || 0, "feature"),
    plural(c.roles || 0, "role"),
    plural(c.apps || 0, "app"),
    plural(c.integrations || 0, "integration"),
    plural(c.references || 0, "reference"),
    plural(facts, "fact"),
  ].join(", ");
}
/** A definition row (label, value). The value may be a string (shown as text) or a node. */
export function row(label, value, untrustedValue) {
  const dd = h("dd", null);
  if (value !== null && typeof value === "object") dd.appendChild(value);
  else dd.appendChild(untrustedValue ? untrusted(value) : document.createTextNode(String(value)));
  return [h("dt", null, label), dd];
}
/** A banner for a failed action: the plain sentence, plus the variable names to set when something is missing. */
export function problemBanner(problem, tone) {
  const kids = [h("p", null, problem.text)];
  if (problem.missing && problem.missing.length) {
    kids.push(h("p", null, "To fix it, set: ", ...problem.missing.map((m, i) => [i ? " " : "", h("code", { class: "var" }, m)])));
  }
  return h("div", { class: "banner banner-" + (tone || "bad"), role: "alert" }, kids);
}
