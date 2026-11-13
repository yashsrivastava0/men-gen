/* Runs history (#/runs): every run, newest first, with a filter. Each row opens the run (while it is active) or its
   results. Refreshes every 10 s while visible and whenever the tab is focused. */
import { h, clear, setKids, untrusted, navLink, keepFocus } from "../lib/dom.js";
import { statusChip, sizeBadge, problemBanner } from "./shared.js";
import { usd, ago, formatDateTime, formatDuration, secondsBetween, sizeWord } from "../lib/format.js";
import { filterRuns, runGroupCounts, RUN_GROUPS } from "../lib/filters.js";
import { isActiveStatus } from "../lib/reducer.js";
import { ApiError } from "../lib/api.js";

export function mount(root, ctx) {
  let runs = null;
  let failure = null;
  let json = "";
  let gone = false;
  let group = ctx.store.get("runs.group", "all");
  let query = ctx.store.get("runs.q", "");
  const filterBox = h("div", { class: "filters", role: "group", "aria-label": "Show runs" });
  const search = h("input", { type: "search", class: "search", placeholder: "Search runs", "aria-label": "Search runs", value: query, oninput: () => {
    query = search.value;
    ctx.store.set("runs.q", query);
    draw();
  } });
  const box = h("div", null);
  root.append(h("div", { class: "page-head" }, h("h2", null, "Runs"), h("button", { type: "button", class: "btn", "data-fk": "refresh", onclick: () => load() }, "Refresh")), h("div", { class: "row wrap" }, filterBox, search), box);

  async function load() {
    if (gone) return;
    try {
      const res = await ctx.api.get("/api/runs");
      failure = null;
      const next = JSON.stringify(res);
      if (next === json && runs) return;
      json = next;
      runs = res.runs || [];
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "The list could not be loaded.", missing: [] };
    }
    draw();
  }
  function draw() {
    if (gone) return;
    keepFocus(root, () => {
      clear(filterBox);
      if (runs) {
        const counts = runGroupCounts(runs);
        for (const g of RUN_GROUPS) {
          filterBox.appendChild(
            h("button", { type: "button", class: "btn btn-chip", "aria-pressed": group === g.id ? "true" : "false", "data-fk": "grp:" + g.id, onclick: () => {
              group = g.id;
              ctx.store.set("runs.group", g.id);
              draw();
            } }, g.label + " (" + counts[g.id] + ")")
          );
        }
      }
      clear(box);
      if (failure) box.appendChild(problemBanner(failure));
      if (!runs) return;
      if (!runs.length) {
        box.appendChild(h("section", { class: "card empty" }, h("h3", null, "No runs yet"), h("p", null, "Start one from the ", navLink("#/inputs", "Inputs"), " page."), h("p", { class: "muted" }, "Past runs, what they cost and how long they took will be listed here.")));
        return;
      }
      const shown = filterRuns(runs, { group: group, q: query });
      if (!shown.length) {
        box.appendChild(h("p", { class: "muted" }, "No run matches. Change the filter or the search words."));
        return;
      }
      box.appendChild(table(shown));
    });
  }
  function table(list) {
    const head = h("thead", null, h("tr", null, ["Status", "Input", "Size", "Started", "How long", "Cost", ""].map((t) => h("th", { scope: "col" }, t))));
    const body = h(
      "tbody",
      null,
      list.map((r) => {
        const active = isActiveStatus(r.status);
        const secs = secondsBetween(r.started_at, r.finished_at);
        return h(
          "tr",
          { "data-run": r.run_id },
          h("td", { "data-label": "Status" }, statusChip(r.status), r.status === "queued" && r.queue_position ? h("span", { class: "muted small" }, " position " + r.queue_position) : null),
          h("td", { "data-label": "Input" }, r.idea ? untrusted(r.idea) : untrusted(r.input_name || r.run_id), r.input_name ? h("div", { class: "muted small" }, untrusted(r.input_name, "fname")) : null),
          h("td", { "data-label": "Size" }, r.size_class ? sizeBadge(r.size_class) : "–"),
          h("td", { "data-label": "Started" }, r.started_at || r.created_at ? h("span", { title: formatDateTime(r.started_at || r.created_at) }, ago(r.started_at || r.created_at)) : "–"),
          h("td", { "data-label": "How long" }, secs === null ? (active && r.started_at ? "running" : "–") : formatDuration(secs)),
          h("td", { "data-label": "Cost" }, usd(r.spend_micros || 0)),
          h("td", { "data-label": "Open" }, navLink(active ? "#/run/" + r.run_id : "#/results/" + r.run_id, active ? "Watch live" : "Open", "btn btn-small"))
        );
      })
    );
    return h("div", { class: "table-wrap" }, h("table", { class: "table responsive" }, head, body));
  }

  const tick = () => {
    if (!document.hidden) load();
  };
  const timer = setInterval(tick, 10000);
  window.addEventListener("focus", tick);
  draw();
  load();
  return {
    unmount() {
      gone = true;
      clearInterval(timer);
      window.removeEventListener("focus", tick);
    },
  };
}
