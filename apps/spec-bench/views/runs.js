/* Run history: every run with its state, spend and pack hash. Used as its own page and inside the hand-off. */
import { h, setKids, untrusted, navLink, put } from "../lib/dom.js";
import { normalizeRuns } from "../lib/model.js";
import { usd, formatTime } from "../lib/format.js";
import { statusChip, problemBanner, problemOf, loading, pageHead } from "./shared.js";

/** The table of runs; `extra(run)` may add a cell (a button or link). */
export function runsTable(runs, extra, extraHead) {
  if (!runs.length) return h("p", { class: "muted" }, "No runs yet.");
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table responsive" },
      h("thead", null, h("tr", null, ["Run", "State", "Reader", "Spent", "Started", "Pack hash", extra ? extraHead || "" : null].filter((x) => x !== null).map((t) => h("th", { scope: "col" }, t)))),
      h(
        "tbody",
        null,
        runs.map((r) =>
          h(
            "tr",
            null,
            h("td", { "data-label": "Run" }, navLink("#/run/" + r.id, r.id)),
            h("td", { "data-label": "State" }, statusChip(r.status)),
            h("td", { "data-label": "Reader" }, untrusted(r.profile)),
            h("td", { "data-label": "Spent" }, r.spendMicros === null ? "" : usd(r.spendMicros)),
            h("td", { "data-label": "Started" }, formatTime(r.at)),
            h("td", { "data-label": "Pack hash" }, r.hash ? h("code", null, r.hash.slice(0, 12)) : ""),
            extra ? h("td", { "data-label": extraHead || "" }, extra(r)) : null
          )
        )
      )
    )
  );
}

export function mount(root, ctx) {
  let gone = false;
  const body = h("div", null, loading());
  put(root, pageHead("Run history", "Every run this app has made, newest first."), body);
  (async () => {
    try {
      const runs = normalizeRuns(await ctx.api.get("/api/runs")).slice().sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
      if (!gone) setKids(body, runsTable(runs));
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)));
    }
  })();
  return { unmount() { gone = true; } };
}
