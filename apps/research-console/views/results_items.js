/* Playbook and Appendix: the findings of a committed run as a filterable, paged and VIRTUALIZED list.
   Filters (lane, job, evidence, flag, impact, kind, text) are query parameters: the server answers from memory. Typing is
   debounced (150 ms) and a newer request cancels the older one, so an old answer can never overwrite a newer one. Only
   the rows on screen exist in the page (fixed row height), so 600 findings cost the same as 20. */
import { h, clear, setKids, untrusted } from "../lib/dom.js";
import { fixedWindow } from "../lib/vlist.js";
import { reviewQuery, debounce, latestOnly, pagesNeeded, activeFilterCount, laneTitle, laneNote, QUALITY_WORDS, evidenceWord, flagWord, impactWord, kindWord, FILTER_KEYS } from "../lib/results.js";
import { jobTitle, plural, humanize } from "../lib/format.js";
import { ApiError } from "../lib/api.js";
import { problemBanner } from "./shared.js";
import { itemRow, openItem, ROW_HEIGHT } from "./item.js";

const PAGE = 100;
const SELECTS = [
  ["evidence", "Evidence", evidenceWord],
  ["flag", "Flag", (v) => flagWord(v) || humanize(v)],
  ["impact", "Impact", impactWord],
  ["kind", "Kind", (v) => kindWord(v) || humanize(v)],
];

export async function mountTab(panel, state, tabId) {
  const ctx = state.ctx;
  const store = tabId === "appendix" ? "appendix" : "main";
  const base = "/v1/research/runs/" + encodeURIComponent(state.runId) + "/review?";
  const t0 = performance.now();
  const saved = ctx.store.get("results.filters." + tabId, {});
  let filters = {};
  for (const k of FILTER_KEYS) if (typeof saved[k] === "string") filters[k] = saved[k];
  let baseFacets = null;
  let facets = null;
  let total = null;
  let pages = new Map();
  let inflight = new Set();
  let failure = null;
  let gone = false;
  let filterStart = t0;
  let firstDrawn = false;
  const gate = latestOnly();
  let gen = gate.begin();

  const chipsLane = h("div", { class: "filters", role: "group", "aria-label": "By lane" });
  const chipsJob = h("div", { class: "filters", role: "group", "aria-label": "By topic area" });
  const selects = h("div", { class: "row wrap" });
  const search = h("input", { type: "search", class: "search", id: "item-q", placeholder: "Search findings", "aria-label": "Search findings", value: filters.q || "" });
  const clearBtn = h("button", { type: "button", class: "btn btn-small", "data-fk": "clear-filters", onclick: () => setFilters({}) }, "Clear filters");
  const summary = h("p", { class: "muted small", role: "status" }, "");
  const laneInfo = h("div", { class: "muted small" });
  const scroller = h("div", { class: "vscroll", tabindex: "0", "aria-label": tabId === "appendix" ? "Appendix findings" : "Findings" });
  const inner = h("ul", { class: "vlist", "aria-label": "Findings" });
  scroller.appendChild(inner);
  const errorBox = h("div", null);
  const intro =
    tabId === "appendix"
      ? "Findings ranked below the main store. Nothing was deleted: they are kept here, with the same evidence, in case you need them."
      : "What the research found, strongest first. Open a finding to see the quote it rests on and its sources.";
  panel.append(h("p", { class: "lead" }, intro), chipsLane, chipsJob, h("div", { class: "row wrap" }, search, clearBtn), selects, laneInfo, summary, errorBox, scroller);

  // ---- requests ----------------------------------------------------------------------------------------------------------------
  function url(offset, limit, f) {
    return base + reviewQuery(f === undefined ? filters : f, store, offset, limit);
  }
  async function fetchPage(p) {
    if (pages.has(p) || inflight.has(p)) return;
    inflight.add(p);
    const mine = gen;
    try {
      const res = await ctx.api.get(url(p * PAGE, PAGE), { signal: mine.signal });
      if (!gate.current(mine.id) || gone) return;
      pages.set(p, res.items);
      total = res.total;
      facets = res.facets;
      failure = null;
    } catch (e) {
      if (!gate.current(mine.id) || gone || (e && e.name === "AbortError")) return;
      failure = e instanceof ApiError ? e.problem : { text: "The list could not be loaded.", missing: [] };
    } finally {
      inflight.delete(p);
    }
    ctx.schedule(draw);
  }
  async function loadBase() {
    try {
      const res = await ctx.api.get(base + "store=" + store + "&limit=1");
      baseFacets = res.facets;
      // drop remembered filter values this run does not have
      let dropped = false;
      for (const k of ["job", "lane", "evidence", "flag", "impact", "kind"]) {
        if (filters[k] && !(baseFacets[k] && baseFacets[k][filters[k]])) {
          delete filters[k];
          dropped = true;
        }
      }
      if (!gone) {
        if (dropped) setFilters(filters, true);
        else drawControls();
      }
    } catch (e) {
      failure = e instanceof ApiError ? e.problem : { text: "The list could not be loaded.", missing: [] };
      ctx.schedule(draw);
    }
  }
  let firstFetched = false;
  function setFilters(next, keepQ) {
    filters = Object.assign({}, next);
    if (keepQ && search.value) filters.q = search.value;
    if (!keepQ) search.value = filters.q || "";
    ctx.store.set("results.filters." + tabId, filters);
    gen = gate.begin();
    pages = new Map();
    inflight = new Set();
    total = null;
    filterStart = performance.now();
    scroller.scrollTop = 0;
    firstFetched = true;
    fetchPage(0);
    drawControls();
  }
  const typed = debounce(() => setFilters(Object.assign({}, filters, { q: search.value.trim() || undefined }), true), 150);
  search.addEventListener("input", typed);

  // ---- controls ------------------------------------------------------------------------------------------------------------------
  function chipRow(box, key, titleOf) {
    clear(box);
    const dim = baseFacets ? baseFacets[key] || {} : {};
    const names = Object.keys(dim);
    if (!names.length) return;
    box.appendChild(h("span", { class: "muted small" }, key === "lane" ? "By lane:" : "By topic area:"));
    const mk = (value, label, count) =>
      h("button", { type: "button", class: "btn btn-chip", "aria-pressed": (filters[key] || "") === value ? "true" : "false", "data-fk": "chip:" + key + ":" + value, onclick: () => {
        const next = Object.assign({}, filters);
        if (value) next[key] = value;
        else delete next[key];
        setFilters(next, true);
      } }, label + (count !== undefined ? " (" + count + ")" : ""));
    box.appendChild(mk("", "All"));
    for (const n of names.sort((a, b) => dim[b] - dim[a])) box.appendChild(mk(n, titleOf(n), dim[n]));
  }
  function drawControls() {
    if (gone) return;
    chipRow(chipsLane, "lane", laneTitle);
    chipRow(chipsJob, "job", jobTitle);
    clear(selects);
    for (const [key, label, wordOf] of SELECTS) {
      const dim = baseFacets ? baseFacets[key] || {} : {};
      const names = Object.keys(dim);
      if (!names.length) continue;
      const sel = h("select", { class: "select", id: "f-" + key, "aria-label": label, "data-fk": "sel:" + key }, h("option", { value: "" }, label + ": any"), names.sort().map((n) => h("option", { value: n }, wordOf(n) + " (" + dim[n] + ")")));
      sel.value = filters[key] || "";
      sel.addEventListener("change", () => {
        const next = Object.assign({}, filters);
        if (sel.value) next[key] = sel.value;
        else delete next[key];
        setFilters(next, true);
      });
      selects.appendChild(sel);
    }
    clearBtn.hidden = !activeFilterCount(filters);
    const lane = filters.lane;
    const q = lane && state.overview.lane_quality ? state.overview.lane_quality[lane] : null;
    setKids(laneInfo, lane ? [laneNote(lane), q ? " Quality: " + (QUALITY_WORDS[q.quality] || q.quality) + " (" + q.slots_filled + " of " + q.slots_required + " required facts found)." : ""] : "");
  }

  // ---- the virtual list ------------------------------------------------------------------------------------------------------------
  function viewportH() {
    return scroller.clientHeight || 600;
  }
  function draw() {
    if (gone) return;
    clear(errorBox);
    if (failure) {
      errorBox.appendChild(problemBanner(failure));
      return;
    }
    if (total === null) {
      setKids(inner, h("li", { class: "muted pad" }, "Loading…"));
      inner.style.paddingTop = "0px";
      inner.style.paddingBottom = "0px";
      return;
    }
    const all = state.overview.item_count;
    summary.textContent = total === 0 ? "Nothing matches." : "Showing " + plural(total, "finding") + (activeFilterCount(filters) ? " of " + all : "") + ".";
    if (total === 0) {
      setKids(inner, h("li", { class: "pad" }, h("p", null, activeFilterCount(filters) ? "No finding matches these filters." : tabId === "appendix" ? "The appendix is empty: every finding fit in the main store." : "There are no findings in this result."), activeFilterCount(filters) ? h("button", { type: "button", class: "btn", onclick: () => setFilters({}) }, "Clear the filters") : null));
      inner.style.paddingTop = "0px";
      inner.style.paddingBottom = "0px";
      markDone();
      return;
    }
    const w = fixedWindow({ count: total, itemH: ROW_HEIGHT, scrollTop: scroller.scrollTop, viewportH: viewportH(), overscan: 4 });
    const rows = [];
    for (let i = w.start; i < w.end; i++) {
      const it = (pages.get(Math.floor(i / PAGE)) || [])[i % PAGE];
      if (it) rows.push(itemRow(it, i, total, (item) => openItem(item, { ctx: ctx, runId: state.runId, onRejected: () => ctx.schedule(draw) })));
      else rows.push(h("li", { class: "vrow vrow-loading", "aria-posinset": i + 1, "aria-setsize": total }, h("span", { class: "muted" }, "Loading…")));
    }
    const active = document.activeElement;
    const key = active && inner.contains(active) ? active.getAttribute("data-fk") : null;
    setKids(inner, rows);
    inner.style.paddingTop = w.padTop + "px";
    inner.style.paddingBottom = w.padBottom + "px";
    if (key) {
      const again = inner.querySelector('[data-fk="' + CSS.escape(key) + '"]');
      if (again) again.focus({ preventScroll: true });
    }
    for (const p of pagesNeeded(w.start, w.end, PAGE, pages)) fetchPage(p);
    markDone();
  }
  function markDone() {
    const now = performance.now();
    if (!firstDrawn) {
      firstDrawn = true;
      scroller.setAttribute("data-first-render-ms", String(Math.round(now - t0)));
    }
    scroller.setAttribute("data-filter-ms", String(Math.round(now - filterStart)));
    scroller.setAttribute("data-total", String(total));
    scroller.setAttribute("data-rendered", String(inner.querySelectorAll(".vrow").length));
  }
  scroller.addEventListener("scroll", () => ctx.schedule(draw), { passive: true });
  const onResize = () => ctx.schedule(draw);
  window.addEventListener("resize", onResize);

  draw();
  drawControls();
  firstFetched = true;
  fetchPage(0);
  loadBase();
  return () => {
    gone = true;
    typed.cancel();
    gate.begin(); // abort anything still in flight
    window.removeEventListener("resize", onResize);
  };
}
