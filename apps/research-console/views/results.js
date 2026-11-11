/* Results (#/results/<id>). Stage 1: the run header (status, cost, time) and a download of the raw research.json.
   Stage 2 adds the tabs (Playbook, Norms, Confirm, Gaps, Appendix, Quality, Sources, Report, Raw JSON) below the header. */
import { h, clear, setKids, untrusted, navLink, downloadBlob } from "../lib/dom.js";
import { problemBanner, row } from "./shared.js";
import { ApiError } from "../lib/api.js";
import { problemText } from "../lib/safe.js";
import { applySnapshot, isActiveStatus, failureText, partialText } from "../lib/reducer.js";
import { usd, formatDuration, formatDateTime, secondsBetween, runWord, statusTone } from "../lib/format.js";

/** The header every results tab sits under: {el, state, meta}. */
export async function loadRun(ctx, runId) {
  const snap = await ctx.api.get("/v1/research/runs/" + encodeURIComponent(runId));
  const state = applySnapshot(snap, Date.now());
  let meta = null;
  let spend = null;
  try {
    const runs = await ctx.api.get("/api/runs");
    meta = (runs.runs || []).find((r) => r.run_id === runId) || null;
    const sp = await ctx.api.get("/api/spend");
    const entries = sp && sp.record ? sp.record.entries || [] : [];
    spend = entries.find((e) => e.run_id === runId && e.kind === "run") || null;
  } catch (e) {
    /* the header still works without the cost */
  }
  return { snap: snap, state: state, meta: meta, spend: spend };
}

export function runHeader(run, runId) {
  const { state, meta } = run;
  const seconds = secondsBetween(state.startedAt, state.finishedAt);
  const cost = meta ? meta.spend_micros || 0 : run.spend ? run.spend.total_micros || 0 : 0;
  const box = h("section", { class: "card results-head" });
  box.append(
    h("div", { class: "page-head" }, h("h2", null, untrusted((meta && (meta.idea || meta.input_name)) || "Research results")), h("span", { class: "chip chip-" + (statusTone(state.status) || "plain") }, runWord(state.status))),
    h(
      "dl",
      { class: "facts" },
      ...row("Cost", usd(cost)),
      ...row("Time", seconds === null ? "not known" : formatDuration(seconds)),
      ...row("Started", state.startedAt ? formatDateTime(state.startedAt) : "not started"),
      ...(meta && meta.input_name ? row("Input", meta.input_name, true) : []),
      ...(state.loadClass ? row("Depth", state.depthWord || state.loadClass) : []),
      ...(state.contentHash ? row("Fingerprint", h("code", null, state.contentHash.slice(0, 12))) : [])
    )
  );
  if (state.status === "partial") box.appendChild(h("p", { class: "banner banner-warn" }, h("strong", null, "Finished with gaps. "), partialText(state)));
  if (state.status === "failed" || state.status === "interrupted" || state.status === "cancelled") box.appendChild(h("p", { class: "banner banner-bad" }, failureText(state)));
  return box;
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
  const body = h("div", { class: "results-body" });
  root.append(h("div", { class: "page-head" }, h("h2", null, "What we found")), runHeader(run, runId), body);
  if (isActiveStatus(run.state.status)) {
    body.append(h("div", { class: "banner banner-info" }, h("p", null, "This run is still going. "), h("p", null, navLink("#/run/" + runId, "Watch it live", "btn btn-primary"))));
    return {};
  }
  const note = h("div", { role: "status" });
  const btn = h("button", { type: "button", class: "btn btn-primary", "data-fk": "download" }, "Download research.json");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    clear(note);
    try {
      const blob = await ctx.api.blob("/v1/research/runs/" + encodeURIComponent(runId) + "/result");
      downloadBlob(blob, "research-" + runId + ".json");
      note.appendChild(h("p", { class: "tone-ok" }, "Saved research.json."));
    } catch (e) {
      const p = e instanceof ApiError ? e.problem : problemText(0, null);
      note.appendChild(h("p", { class: "tone-bad" }, e instanceof ApiError && e.status === 404 ? "No results were saved for this run. A run that did not finish has nothing to download." : p.text));
    }
    btn.disabled = false;
  });
  setKids(
    body,
    h("section", { class: "card" }, h("h3", null, "Results view comes in stage 2"), h("p", { class: "muted" }, "The readable results (what the app should do, how comparable apps work, what to confirm, gaps, sources and the report) arrive in the next version. For now the whole result can be downloaded as one file."), h("div", { class: "row" }, btn, navLink("#/run/" + runId, "See the run again", "btn")), note)
  );
  return {};
}
