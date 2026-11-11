/* Sources: every page the run used, paged (100 at a time) with a search box. */
import { h, clear, setKids, untrusted, link } from "../lib/dom.js";
import { debounce, latestOnly } from "../lib/results.js";
import { formatDate, humanize, plural } from "../lib/format.js";
import { ApiError } from "../lib/api.js";
import { problemBanner } from "./shared.js";

const PAGE = 100;

export function mountTab(panel, state) {
  const ctx = state.ctx;
  let offset = 0;
  let q = "";
  let gone = false;
  const gate = latestOnly();
  const search = h("input", { type: "search", class: "search", id: "src-q", placeholder: "Search sources", "aria-label": "Search sources" });
  const info = h("p", { class: "muted small", role: "status" }, "");
  const box = h("div", null);
  const pager = h("div", { class: "row" });
  panel.append(h("p", { class: "lead" }, "Every page this run used. Open a page to check a finding yourself."), h("div", { class: "row wrap" }, search), info, box, pager);

  async function load() {
    const mine = gate.begin();
    try {
      const res = await ctx.api.get("/v1/research/runs/" + encodeURIComponent(state.runId) + "/sources?limit=" + PAGE + "&offset=" + offset + (q ? "&q=" + encodeURIComponent(q) : ""), { signal: mine.signal });
      if (!gate.current(mine.id) || gone) return;
      draw(res);
    } catch (e) {
      if (!gate.current(mine.id) || gone || (e && e.name === "AbortError")) return;
      setKids(box, e instanceof ApiError ? problemBanner(e.problem) : h("p", { class: "tone-bad" }, "The sources could not be loaded."));
    }
  }
  function draw(res) {
    info.textContent = res.total ? "Showing " + (offset + 1) + " to " + Math.min(offset + PAGE, res.total) + " of " + plural(res.total, "page") + "." : "No source matches.";
    clear(box);
    if (res.items.length) {
      box.appendChild(
        h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "table responsive" },
            h("thead", null, h("tr", null, ["Site", "Kind", "Date", "Page", "Used by"].map((t) => h("th", { scope: "col" }, t)))),
            h(
              "tbody",
              null,
              res.items.map((s) =>
                h("tr", null, h("td", { "data-label": "Site" }, untrusted(s.domain)), h("td", { "data-label": "Kind" }, h("span", { class: "chip chip-plain" }, humanize(s["class"]))), h("td", { "data-label": "Date" }, s.published ? formatDate(s.published) : "not dated"), h("td", { "data-label": "Page" }, s.title ? untrusted(s.title) : null, h("div", { class: "small" }, link(s.url, "Open the page"))), h("td", { "data-label": "Used by" }, plural(s.used_items, "finding")))
              )
            )
          )
        )
      );
    }
    clear(pager);
    if (offset > 0) pager.appendChild(h("button", { type: "button", class: "btn", "data-fk": "src-prev", onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, "Previous"));
    if (offset + PAGE < res.total) pager.appendChild(h("button", { type: "button", class: "btn", "data-fk": "src-next", onclick: () => { offset += PAGE; load(); } }, "Next"));
  }
  search.addEventListener(
    "input",
    debounce(() => {
      q = search.value.trim();
      offset = 0;
      load();
    }, 150)
  );
  load();
  return () => {
    gone = true;
    gate.begin();
  };
}
