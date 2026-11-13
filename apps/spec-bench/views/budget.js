/* The budget page: the hard money cap in plain words, what each run spent, the numbers in force (read only) and the
   one big stop button. The cap can only be changed by the owner outside this page. */
import { h, setKids, put } from "../lib/dom.js";
import { normalizeRuns, textOf } from "../lib/model.js";
import { usd } from "../lib/format.js";
import { moneyLevel, moneyText, moneyBanner, moneyPercents } from "../lib/money.js";
import { banner, problemBanner, problemOf, loading, pageHead, card, facts, tile, genericTable } from "./shared.js";
import { runsTable } from "./runs.js";

const LEVEL_WORDS = {
  ok: "All fine: money can be spent.",
  tripwire: "Tripwire reached: no new step starts. Work already started finishes.",
  exhausted: "Cap reached: nothing that costs money can start.",
  unknown: "Not known yet.",
};

/** The config as rows of [name, value], nested objects flattened with dots. */
export function flattenConfig(obj, prefix, out) {
  const rows = out || [];
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const k of Object.keys(obj)) flattenConfig(obj[k], prefix ? prefix + "." + k : k, rows);
  } else if (prefix) {
    rows.push({ name: prefix, value: textOf(obj) });
  }
  return rows;
}

export function mount(root, ctx) {
  let gone = false;
  const body = h("div", null, loading());
  put(root, pageHead("Money", "The Spec Bench has a hard cap on what it can spend. Every model call is checked against it before it is made."), body);
  let stopNote = null;

  async function load() {
    try {
      const [b, cfg, runs] = await Promise.all([ctx.api.get("/api/budget"), ctx.api.get("/api/config").catch(() => null), ctx.api.get("/api/runs").catch(() => null)]);
      if (gone) return;
      ctx.state.budget = b;
      draw(b || {}, cfg, normalizeRuns(runs));
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
    }
  }
  async function stopAll() {
    try {
      await ctx.api.post("/api/stop", {});
      stopNote = banner("Asked every running run to stop. Each saves its work and ends as partial.", "ok");
    } catch (e) {
      stopNote = problemBanner(problemOf(e));
    }
    ctx.refreshBudget();
    load();
  }
  function draw(b, cfg, runs) {
    const level = moneyLevel(b);
    const mb = moneyBanner(b);
    const p = moneyPercents(b);
    const kids = [
      h("p", { class: "big money-" + level }, moneyText(b)),
      mb ? banner(mb.text, mb.level === "exhausted" ? "bad" : "warn") : null,
      h("div", { class: "tiles" }, tile("Cap", usd(b.cap_micros)), tile("Tripwire", usd(b.tripwire_micros)), tile("Spent", usd(b.spent_micros)), tile("Held for calls in progress", usd(b.reserved_micros)), tile("Left", usd(Math.max(0, b.remaining_micros || 0)))),
      card("State", h("p", null, h("strong", null, level === "unknown" ? "Unknown" : level), ": ", LEVEL_WORDS[level]), facts([["Period", textOf(b.period_id)], ["Used so far", Math.round(p.used) + "% of the cap"]])),
      card("Spent by run", runsTable(runs.filter((r) => r.spendMicros !== null).slice().sort((a, c) => (a.at < c.at ? 1 : -1)))),
      card("Stop everything", h("p", null, "Asks every running run to save its work and end as partial. It costs nothing and can be resumed later."), h("button", { type: "button", class: "btn btn-danger", "data-fk": "stop-all", onclick: stopAll }, "Stop all runs"), stopNote),
      card("Numbers in force (read only)", cfg ? genericTable(flattenConfig(cfg), { cols: ["name", "value"] }) : h("p", { class: "muted" }, "The app did not give its settings.")),
    ];
    setKids(body, kids);
  }
  load();
  return { unmount() { gone = true; } };
}
