/* Results ("What we found", #/results/<id>): the run header and nine lazy-loaded tabs. A run that did not commit a result
   (failed, interrupted, cancelled, still running) shows the same header from the run snapshot and no tabs. */
import { h, clear, setKids, untrusted, navLink, downloadBlob } from "../lib/dom.js";
import { problemBanner, row, statusChip } from "./shared.js";
import { ApiError } from "../lib/api.js";
import { problemText } from "../lib/safe.js";
import { applySnapshot, isActiveStatus, failureText } from "../lib/reducer.js";
import { usd, formatDuration, formatDateTime, secondsBetween, sizeWord } from "../lib/format.js";
import { trustLines } from "../lib/results.js";

const TABS = [
  ["playbook", "Playbook", () => import("./results_items.js")],
  ["norms", "Norms", () => import("./results_norms.js")],
  ["confirm", "Confirm", () => import("./results_notes.js")],
  ["gaps", "Gaps and conflicts", () => import("./results_notes.js")],
  ["appendix", "Appendix", () => import("./results_items.js")],
  ["quality", "Quality", () => import("./results_quality.js")],
  ["sources", "Sources", () => import("./results_sources.js")],
  ["report", "Report", () => import("./results_report.js")],
  ["raw", "Raw JSON", null],
];

/** Everything the header and the tabs share for one run. */
async function loadRun(ctx, runId) {
  const base = "/v1/research/runs/" + encodeURIComponent(runId);
  let overview = null;
  try {
    overview = await ctx.api.get(base + "/overview");
  } catch (e) {
    if (!(e instanceof ApiError) || (e.status !== 404 && e.status !== 409)) throw e;
  }
  if (overview) return { overview: overview, snapshot: null };
  const snap = await ctx.api.get(base);
  return { overview: null, snapshot: applySnapshot(snap, Date.now()), snap: snap };
}

function header(run) {
  const ov = run.overview;
  const box = h("section", { class: "card results-head" });
  if (ov) {
    const p = ov.profile || {};
    const seconds = secondsBetween((ov.run || {}).started_at || ov.created_at, ov.finished_at);
    const lines = trustLines(ov);
    const tile = (label, value) => h("div", { class: "tile" }, h("span", { class: "tile-label" }, label), h("strong", { class: "tile-value" }, value));
    const topics = ov.coverage ? ov.coverage.covered + " of " + ov.coverage.targets + " covered" : "";
    box.append(
      h("div", { class: "page-head" }, h("h2", null, untrusted(ov.input_name || "Research results")), statusChip(ov.status)),
      h(
        "div",
        { class: "tiles", "data-head": "1" },
        tile("Size", sizeWord(ov.size_class) || p.load_class || "unknown"),
        tile("Cost", usd(ov.spend_micros || (ov.budget && ov.budget.usd_micros) || 0)),
        tile("Time", seconds === null ? "not known" : formatDuration(seconds)),
        tile("Findings", String(ov.item_count)),
        tile("Topics", topics),
        tile("Required facts", ov.coverage ? ov.coverage.slots_filled + " of " + ov.coverage.slots_required : "")
      )
    );
    const more = h(
      "details",
      { class: "details" },
      h("summary", { "data-fk": "head-more" }, "How far to trust it, and more about this result"),
      h(
        "dl",
        { class: "facts" },
        ...row("Depth", p.load_class ? p.load_class + " (load " + p.load_score + ")" : "unknown"),
        ...(p.signals && p.signals.length ? row("Signals", p.signals.map((s) => s.replace(/_/g, " ")).join(", ")) : row("Signals", p.light ? "none (light mode: a very small app)" : "none")),
        ...row("In the appendix", String(p.appendix_items || 0)),
        ...row("Finished", ov.finished_at ? formatDateTime(ov.finished_at) : "")
      ),
      lines.length ? h("ul", { class: "trust-lines" }, lines.map((l) => h("li", null, l))) : null
    );
    box.appendChild(more);
    if (ov.status === "partial") box.appendChild(h("div", { class: "banner banner-warn" }, h("p", null, h("strong", null, "Finished with gaps. "), partialReason(ov))));
    if (ov.status === "skipped") box.appendChild(h("div", { class: "banner banner-warn" }, h("p", null, h("strong", null, "Stopped early. "), "You skipped the rest of the research; what had been checked is kept. Topics not reached are listed under Gaps and conflicts.")));
  } else {
    const st = run.snapshot;
    box.append(h("div", { class: "page-head" }, h("h2", null, "Research results"), statusChip(st.status)));
    if (st.status === "failed" || st.status === "interrupted" || st.status === "cancelled") {
      box.appendChild(h("div", { class: "banner banner-bad", role: "status" }, h("p", null, h("strong", null, st.status === "interrupted" ? "This run was interrupted. " : st.status === "cancelled" ? "This run was cancelled. " : "This run could not finish. "), failureText(st)), h("p", null, "Nothing was saved from it, so there are no results to read.")));
    } else if (isActiveStatus(st.status)) {
      box.appendChild(h("div", { class: "banner banner-info" }, h("p", null, "This run is still going. "), h("p", null, navLink("#/run/" + st.runId, "Watch it live", "btn btn-primary"))));
    } else {
      box.appendChild(h("p", { class: "muted" }, "No results were saved for this run."));
    }
    box.appendChild(h("p", null, navLink("#/run/" + st.runId, "See what the run did", "btn"), " ", navLink("#/runs", "All runs", "btn")));
  }
  return box;
}
function partialReason(ov) {
  const gaps = ov.gaps || [];
  if (gaps.some((g) => g.reason === "budget_stop")) return "The budget limit was reached, so some topics were not finished. What was checked is kept; the missing topics are under Gaps and conflicts.";
  if (gaps.some((g) => g.reason === "provider_down")) return "A search service stopped answering, so some topics were not finished. See Gaps and conflicts.";
  return "Some topics could not be finished. See Gaps and conflicts for what is missing and why.";
}

export async function mount(root, ctx, params) {
  const runId = params.id;
  if (!runId) {
    root.appendChild(h("p", null, "Choose a run from the ", navLink("#/runs", "Runs"), " page."));
    return {};
  }
  let run;
  try {
    run = await loadRun(ctx, runId);
  } catch (e) {
    clear(root);
    root.append(problemBanner(e instanceof ApiError ? e.problem : problemText(0, null)), h("p", null, navLink("#/runs", "Back to all runs", "btn")));
    return {};
  }
  run.runId = runId;
  root.append(h("div", { class: "page-head" }, h("h2", null, "What we found")), header(run));
  if (!run.overview) return {};

  // ---- tabs --------------------------------------------------------------------------------------------------------------------
  const state = { runId: runId, ctx: ctx, overview: run.overview, itemsAll: null, filters: {}, panelDisposers: [] };
  let current = ctx.store.get("results.tab", "playbook");
  if (!TABS.some((t) => t[0] === current)) current = "playbook";
  const list = h("div", { class: "tabs", role: "tablist", "aria-label": "Results" });
  const panel = h("div", { class: "tab-panel", role: "tabpanel", tabindex: "0" });
  let token = 0;
  let disposer = null;
  const buttons = {};
  for (const [id, label] of TABS) {
    const b = h("button", { type: "button", class: "tab", role: "tab", id: "tab-" + id, "data-tab": id, "data-fk": "tab:" + id, onclick: () => select(id, true), onkeydown: (e) => keys(e, id) }, label);
    buttons[id] = b;
    list.appendChild(b);
  }
  root.append(list, panel);
  function keys(e, id) {
    const ids = TABS.map((t) => t[0]);
    let i = ids.indexOf(id);
    if (e.key === "ArrowRight") i = (i + 1) % ids.length;
    else if (e.key === "ArrowLeft") i = (i + ids.length - 1) % ids.length;
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = ids.length - 1;
    else return;
    e.preventDefault();
    select(ids[i], true);
    buttons[ids[i]].focus();
  }
  async function select(id, remember) {
    const my = ++token;
    current = id;
    if (remember) ctx.store.set("results.tab", id);
    for (const [tid] of TABS) {
      buttons[tid].setAttribute("aria-selected", tid === id ? "true" : "false");
      buttons[tid].tabIndex = tid === id ? 0 : -1;
    }
    panel.setAttribute("aria-labelledby", "tab-" + id);
    if (disposer) {
      try {
        disposer();
      } catch (e) {
        console.error(e);
      }
      disposer = null;
    }
    setKids(panel, h("p", { class: "muted" }, "Loading…"));
    try {
      if (id === "raw") {
        setKids(panel, rawTab(ctx, runId, run.overview));
        return;
      }
      const loader = TABS.find((t) => t[0] === id)[2];
      const mod = await loader();
      if (my !== token) return;
      clear(panel);
      disposer = (await mod.mountTab(panel, state, id)) || null;
    } catch (e) {
      if (my !== token) return;
      console.error(e);
      setKids(panel, e instanceof ApiError ? problemBanner(e.problem) : h("div", { class: "banner banner-bad", role: "alert" }, h("p", null, "This tab could not be drawn. Try another tab, or reload the page.")));
    }
  }
  select(current, false);
  return {
    unmount() {
      token += 1;
      if (disposer) disposer();
    },
  };
}

function rawTab(ctx, runId, ov) {
  const note = h("div", { role: "status" });
  const btn = h("button", { type: "button", class: "btn btn-primary", "data-fk": "download-json" }, "Download research.json");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    clear(note);
    try {
      downloadBlob(await ctx.api.blob("/v1/research/runs/" + encodeURIComponent(runId) + "/result"), "research-" + runId + ".json");
      note.appendChild(h("p", { class: "tone-ok" }, "Saved research.json."));
    } catch (e) {
      note.appendChild(h("p", { class: "tone-bad" }, e instanceof ApiError ? e.problem.text : "The download failed."));
    }
    btn.disabled = false;
  });
  return h(
    "section",
    { class: "card" },
    h("h3", null, "The committed file"),
    h("p", { class: "muted" }, "This is exactly what the next step reads: " + ov.item_count + " findings in the " + (ov.schema || "research") + " format. It never changes once saved. Your rejections are kept separately and are not in this file."),
    ov.content_hash ? h("p", null, "Fingerprint: ", h("code", null, ov.content_hash)) : null,
    h("div", { class: "row" }, btn),
    note
  );
}
