/* The consumption simulator: how the front end engine would read the pack, in three modes against a chosen reader
   profile. The totals come from the app and equal the packager's task totals. */
import { h, setKids, svg, put, keepFocus } from "../lib/dom.js";
import { arr, textOf, normalizeScreen } from "../lib/model.js";
import { count, tokensText, pct } from "../lib/format.js";
import { banner, problemBanner, problemOf, loading, tile, genericTable } from "./shared.js";

const MODES = [
  { id: "all", label: "All at once", note: "Read the whole pack in one go. Only possible when it fits what the reader can hold." },
  { id: "one", label: "One by one", note: "Pick a screen: the exact files and bytes a worker reads for it." },
  { id: "parallel", label: "In parallel", note: "Task cards grouped in waves, spread over the reader's worker slots (a dry run)." },
];

export function mountTab(root, ctx, env) {
  let gone = false;
  let mode = ctx.store.get("simmode", "all");
  let screen = env.extra || "";
  let screens = [];
  const result = h("div", null, loading());
  const controls = h("div", { class: "row wrap" });
  put(root, h("h3", null, "How the front end engine will read this pack"), controls, result);

  async function loadScreens() {
    try {
      const g2 = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/g2");
      screens = arr(g2 && (g2.content_maps || g2.content_map)).map((m) => normalizeScreen(m)).filter((s) => s.id);
      if (!screens.length) {
        const g15 = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/g15");
        screens = arr(g15 && g15.inventory).map((i) => ({ id: String(i.id || ""), title: textOf(i.title || i.name || i.id) })).filter((s) => s.id);
      }
    } catch (e) {
      screens = [];
    }
    if (!screen && screens.length) screen = screens[0].id;
  }

  function drawControls() {
    setKids(
      controls,
      MODES.map((m) => h("button", { type: "button", class: "btn btn-chip", "aria-pressed": String(m.id === mode), "data-fk": "mode-" + m.id, onclick: () => { mode = m.id; ctx.store.set("simmode", mode); run(); } }, m.label)),
      mode === "one" ? h("label", null, "Screen: ", h("select", { class: "text-input", "data-fk": "sim-screen", onchange: (e) => { screen = e.target.value; run(); } }, screens.map((s) => h("option", { value: s.id, selected: s.id === screen }, s.title + " (" + s.id + ")")))) : null
    );
  }

  async function run() {
    drawControls();
    setKids(result, loading());
    try {
      const r = await ctx.api.get(ctx.api.runPath(env.runId) + "/simulate" + ctx.api.q({ mode: mode, consumer: env.consumer, screen: mode === "one" ? screen : "" }));
      if (!gone) keepFocus(root, () => setKids(result, draw(r || {})));
    } catch (e) {
      if (!gone) setKids(result, problemBanner(problemOf(e)));
    }
  }

  function draw(r) {
    const note = MODES.find((m) => m.id === mode).note;
    const out = [h("p", { class: "muted" }, note)];
    if (mode === "all") {
      const fits = r.allowed !== false && r.fits !== false;
      out.push(h("div", { class: "tiles" }, tile("Pack size", tokensText(r.pack_tokens) || "not known"), tile("Reader can hold", tokensText(r.whole_pack_tokens) || "not known"), tile("Fits", fits ? "Yes" : "No")));
      out.push(fits ? banner("The whole pack fits in one reading for this reader." + (r.reason ? " " + textOf(r.reason) : ""), "ok") : banner("Not possible for this reader: " + (textOf(r.reason) || "the pack is larger than what the reader can hold") + ". The front end engine would work from task cards instead.", "warn"));
    } else if (mode === "one") {
      const files = arr(r.files);
      const total = typeof r.total_tokens === "number" ? r.total_tokens : files.reduce((n, f) => n + (typeof f.tokens === "number" ? f.tokens : 0), 0);
      const budget = typeof r.budget_tokens === "number" ? r.budget_tokens : null;
      out.push(h("div", { class: "tiles" }, tile("Files read", String(files.length)), tile("Total", tokensText(total)), tile("Budget for one task", budget === null ? "not known" : tokensText(budget))));
      if (budget !== null) out.push(banner(total <= budget ? "Within the reading budget (" + pct(total, budget) + "% used)." : "Over the reading budget: this task would be too big for the reader.", total <= budget ? "ok" : "bad"));
      out.push(genericTable(files.map((f) => ({ part: textOf(f.part || f.kind), file: textOf(f.path), bytes: typeof f.bytes === "number" ? count(f.bytes) : "", tokens: typeof f.tokens === "number" ? count(f.tokens) : "" })), { cols: ["part", "file", "bytes", "tokens"], empty: "No files were listed for this screen." }));
    } else {
      out.push(...parallelView(r));
    }
    return out;
  }

  function parallelView(r) {
    const slots = typeof r.slots === "number" ? r.slots : typeof r.k === "number" ? r.k : 1;
    const waves = arr(r.waves);
    const tasks = [];
    waves.forEach((w, wi) => arr(w.tasks).forEach((t) => tasks.push({ wave: textOf(w.wave !== undefined ? w.wave : wi), id: textOf(t && t.id !== undefined ? t.id : t && t.task_id !== undefined ? t.task_id : t), slot: typeof (t && t.slot) === "number" ? t.slot : null, start: typeof (t && t.start) === "number" ? t.start : null, end: typeof (t && t.end) === "number" ? t.end : null, tokens: t && typeof t.tokens === "number" ? t.tokens : null })));
    // a dry-run timeline: when the app gave no times, tasks of a wave are dealt out to the slots in turn, one unit each
    let waveStart = 0;
    for (const w of waves) {
      const wt = tasks.filter((t) => t.wave === textOf(w.wave !== undefined ? w.wave : waves.indexOf(w)));
      let used = 0;
      wt.forEach((t, i) => {
        if (t.slot === null) t.slot = i % slots;
        if (t.start === null) {
          t.start = waveStart + Math.floor(i / slots);
          t.end = t.start + 1;
        }
        used = Math.max(used, t.end);
      });
      waveStart = Math.max(waveStart, used);
    }
    const span = Math.max(1, ...tasks.map((t) => t.end || 0));
    const W = 640;
    const rowH = 18;
    const chart = svg("svg", { class: "timeline", role: "img", "aria-label": "Dry-run timeline: " + tasks.length + " task cards over " + slots + " worker slots", viewBox: "0 0 " + W + " " + (slots * rowH + 20), width: "100%" });
    for (let s = 0; s < slots; s++) chart.appendChild(svg("text", { x: 0, y: s * rowH + 13, class: "axis" }, "slot " + (s + 1)));
    for (const t of tasks) {
      chart.appendChild(svg("rect", { x: 50 + ((t.start || 0) / span) * (W - 60), y: (t.slot || 0) * rowH + 2, width: Math.max(2, (((t.end || 0) - (t.start || 0)) / span) * (W - 60) - 1), height: rowH - 4, class: "bar wave-" + (Math.max(0, waves.findIndex((w) => textOf(w.wave) === t.wave)) % 4) }, svg("title", null, t.id + " (" + t.wave + ")")));
    }
    return [
      h("div", { class: "tiles" }, tile("Task cards", String(tasks.length)), tile("Waves", String(waves.length)), tile("Worker slots", String(slots)), tile("Steps in a dry run", String(span))),
      tasks.length ? h("div", { class: "timeline-wrap" }, chart) : h("p", { class: "muted" }, "No task cards were listed."),
      h("div", null, waves.map((w, wi) => h("details", { class: "details" }, h("summary", null, "Wave " + textOf(w.wave !== undefined ? w.wave : wi) + " (" + arr(w.tasks).length + " task cards)"), genericTable(arr(w.tasks).map((t) => ({ task: textOf(t && t.id !== undefined ? t.id : t), title: textOf(t && t.title), tokens: t && typeof t.tokens === "number" ? count(t.tokens) : "" })), { cols: ["task", "title", "tokens"] })))),
    ];
  }

  (async () => {
    await loadScreens();
    if (!gone) run();
  })();
  return { unmount() { gone = true; } };
}
