/* Step 2, Preview (zero cost): what the run will be, worked out with no model call. */
import { h, setKids, untrusted, navLink, put } from "../lib/dom.js";
import { normalizePreview, textOf } from "../lib/model.js";
import { usd, usdRange, stepWord, plural, count } from "../lib/format.js";
import { estimateVerdict } from "../lib/money.js";
import { banner, statusChip, modeChip, problemBanner, problemOf, loading, pageHead, card, facts, tile, genericTable } from "./shared.js";

export function mount(root, ctx, route) {
  const id = route.id;
  let gone = false;
  const body = h("div", null, loading());
  put(root, pageHead("2. Preview (costs nothing)", "This is worked out from the inputs alone, before any model is called. Nothing has been spent on it."), body);

  async function load() {
    try {
      const [snap, prev] = await Promise.all([ctx.api.get(ctx.api.runPath(id)), ctx.api.get(ctx.api.runPath(id) + "/preview")]);
      if (gone) return;
      draw(snap || {}, normalizePreview(prev));
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
    }
  }
  function draw(snap, p) {
    const budget = ctx.state.budget;
    const est = p.estimate.low_micros !== null ? p.estimate : snap.estimate || {};
    const verdict = estimateVerdict(est, budget ? budget.remaining_micros : null);
    const prof = p.profile || {};
    const kids = [];
    const offlineRun = snap.offline !== false;
    kids.push(h("p", null, "Run ", untrusted(id, "fname"), " ", statusChip(snap.status), " ", modeChip(snap)));
    kids.push(
      h(
        "div",
        { class: "tiles" },
        tile("Screens and items found", p.inventoryCount === null ? "not known" : count(p.inventoryCount)),
        tile("Model calls planned", p.totalCalls ? String(p.totalCalls) : "not known"),
        tile(offlineRun ? "A live run would cost" : "Likely cost", est.low_micros !== undefined && est.low_micros !== null && est.high_micros !== null && est.high_micros !== undefined ? usdRange(est.low_micros, est.high_micros) : "not known"),
        tile("Money left", budget && typeof budget.remaining_micros === "number" ? usd(budget.remaining_micros) : "not known")
      )
    );
    if (offlineRun) kids.push(banner("This run is offline: no model is called and it costs nothing. " + verdict.lines.join(" ").replace("This run will not start:", "A live run of this input would not start:"), "info"));
    else kids.push(banner(verdict.lines.join(" "), verdict.refused ? "bad" : "ok"));
    if (verdict.refused && !offlineRun) {
      kids.push(h("p", { class: "tone-bad" }, "The app refuses to start a run like this. Choose a smaller input, or run it offline (it costs nothing)."));
    }
    kids.push(card("What the Spec worked out about this app", facts([["Size and kind", textOf(prof.label || prof.id || prof.size || prof.name)], ["Why", textOf(prof.why || prof.reason || prof.summary)]]), prof && Object.keys(prof).length > 1 ? facts(Object.keys(prof).filter((k) => ["id", "label", "name", "why", "reason", "summary"].indexOf(k) === -1).slice(0, 8).map((k) => [k.replace(/_/g, " "), textOf(prof[k])])) : null));
    kids.push(card("Phases", p.phases.length ? genericTable(p.phases.map((x) => ({ phase: x.name, screens: x.screens === null ? "" : x.screens, note: x.note })), { cols: ["phase", "screens", "note"] }) : h("p", { class: "muted" }, "The phases are worked out when the structure is built.")));
    kids.push(card("Model calls by step", p.calls.length ? genericTable(p.calls.map((c) => ({ step: stepWord(c.step), calls: c.calls })), { cols: ["step", "calls"] }) : h("p", { class: "muted" }, "No call plan yet."), p.totalCalls ? h("p", { class: "muted" }, plural(p.totalCalls, "call") + " in all.") : null));
    kids.push(
      h(
        "div",
        { class: "actions" },
        navLink("#/g15/" + id, "Next: check the structure (G1.5)", "btn btn-primary"),
        " ",
        navLink("#/run/" + id, "Watch the run", "btn"),
        verdict.refused ? h("p", { class: "why-off" }, "The run was refused for cost; the next steps may have nothing to show.") : null
      )
    );
    setKids(body, kids);
  }
  load();
  return { unmount() { gone = true; } };
}
