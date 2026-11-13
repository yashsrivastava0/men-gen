/* Small pieces several screens draw the same way. Every string from the pack, a model or a file goes in as text. */
import { h, untrusted, navLink } from "../lib/dom.js";
import { statusTone, statusWord, humanize, capitalize } from "../lib/format.js";
import { columnsOf, textOf, arr } from "../lib/model.js";
import { ApiError } from "../lib/api.js";

export const loading = () => h("p", { class: "muted pad" }, "Loading…");

/** A status word in a coloured chip (the colour is never the only signal: the word is always there). */
export function statusChip(status) {
  return h("span", { class: "chip chip-" + statusTone(status) }, statusWord(status));
}
export function chip(text, tone) {
  return h("span", { class: "chip chip-" + (tone || "plain") }, text);
}
/** A banner for a failed action: the plain sentence, and the server's fix when it has one. */
export function problemBanner(problem, tone) {
  const kids = [h("p", null, problem.text)];
  if (problem.fix) kids.push(h("p", null, h("strong", null, "What to do: "), problem.fix));
  return h("div", { class: "banner banner-" + (tone || "bad"), role: "alert" }, kids);
}
/** The problem of any error as {text, fix} (never a stack trace). */
export function problemOf(e) {
  return e instanceof ApiError ? e.problem : { text: "This page could not load what it needed. Reload it; if it happens again, restart the app.", fix: "" };
}
export function banner(text, tone) {
  return h("div", { class: "banner banner-" + (tone || "info"), role: tone === "bad" ? "alert" : "status" }, h("p", null, text));
}
export function pageHead(title, lead, ...extra) {
  return [h("div", { class: "page-head" }, h("h2", null, title), extra), lead ? h("p", { class: "lead" }, lead) : null];
}
/** A titled card. */
export function card(title, ...kids) {
  return h("section", { class: "card" }, title ? h("h3", null, title) : null, kids);
}
/** A definition list from [label, value] pairs (value text or a node). */
export function facts(pairs) {
  const dl = h("dl", { class: "facts" });
  for (const [label, value] of pairs) {
    if (value === null || value === undefined || value === "") continue;
    dl.appendChild(h("dt", null, label));
    dl.appendChild(h("dd", null, value !== null && typeof value === "object" ? value : untrusted(value)));
  }
  return dl;
}
/** A bulleted list of untrusted lines; an empty list says so. */
export function bullets(lines, emptyText) {
  const list = arr(lines);
  if (!list.length) return h("p", { class: "muted" }, emptyText || "Nothing to show.");
  return h("ul", null, list.map((t) => h("li", null, untrusted(textOf(t)))));
}
/** A table from rows of objects: the plain-valued keys become columns, headed in words. `cols` may name them. */
export function genericTable(rows, opts) {
  const o = opts || {};
  const list = arr(rows);
  if (!list.length) return h("p", { class: "muted" }, o.empty || "Nothing to show.");
  const cols = o.cols || columnsOf(list, o.max || 7);
  if (!cols.length) return bullets(list, o.empty);
  const label = (c) => (o.labels && o.labels[c]) || capitalize(humanize(c));
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h("thead", null, h("tr", null, cols.map((c) => h("th", { scope: "col" }, label(c))))),
      h("tbody", null, list.map((r) => h("tr", null, cols.map((c) => h("td", { "data-label": label(c) }, untrusted(textOf(r && typeof r === "object" ? r[c] : c === cols[0] ? r : ""))))))
      )
    )
  );
}
/** Every top-level key of an object as its own titled block (lists as tables, numbers as facts, text as text). */
export function objectSections(obj, titles) {
  const out = [];
  if (!obj || typeof obj !== "object") return out;
  for (const k of Object.keys(obj)) {
    const v = obj[k];
    const title = (titles && titles[k]) || capitalize(humanize(k));
    if (Array.isArray(v)) out.push(h("div", { class: "block" }, h("h4", null, title + " (" + v.length + ")"), v.length && typeof v[0] === "object" ? genericTable(v) : bullets(v)));
    else if (v && typeof v === "object") out.push(h("div", { class: "block" }, h("h4", null, title), facts(Object.keys(v).map((kk) => [capitalize(humanize(kk)), textOf(v[kk])]))));
    else out.push(h("div", { class: "block" }, h("h4", null, title), h("p", null, untrusted(String(v)))));
  }
  return out;
}
/** A row of tab buttons that are real links (a hash route each). `current` is the active id. */
export function tabLinks(items, current, label) {
  return h(
    "nav",
    { class: "tabs", "aria-label": label || "Sections" },
    items.map((it) => {
      const a = navLink(it.route, it.label, "tab");
      if (it.id === current) a.setAttribute("aria-current", "page");
      return a;
    })
  );
}
/** A plain number tile. */
export function tile(label, value) {
  return h("div", { class: "tile" }, h("span", { class: "tile-label" }, label), h("strong", { class: "tile-value" }, value));
}
/** Run a loader and draw its failure as a banner in `box` (never throws). */
export async function guarded(box, fn) {
  try {
    await fn();
  } catch (e) {
    box.appendChild(problemBanner(problemOf(e)));
  }
}
