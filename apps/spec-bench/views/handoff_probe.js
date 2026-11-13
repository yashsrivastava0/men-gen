/* The consumer probe runner: the weakest reader profile reads one task card and says what it understood, what it
   could not decide, what it assumed, and whether each acceptance point can be met. Live inside the money budget, or
   an offline stub that costs nothing. */
import { h, setKids, untrusted, put, keepFocus } from "../lib/dom.js";
import { arr, textOf, texts, normalizeWaves } from "../lib/model.js";
import { banner, chip, problemBanner, problemOf, loading, card, bullets } from "./shared.js";

export function mountTab(root, ctx, env) {
  let gone = false;
  let waves = [];
  let taskId = env.extra || "";
  let offline = true;
  let running = false;
  let outcome = null;
  const body = h("div", null, loading());
  put(root, h("h3", null, "Weakest reader test"), h("p", { class: "muted" }, "The weakest reader profile is given one task card and nothing else. If even it can follow the card, a stronger front end engine can too. Blocking questions mean the card is unclear."), body);

  async function load() {
    try {
      waves = normalizeWaves(await ctx.api.get(ctx.api.runPath(env.runId) + "/pack/tasks"));
      if (!taskId) {
        for (const w of waves) if (w.tasks.length && !taskId) taskId = w.tasks[0].id;
      }
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)));
      return;
    }
    draw();
  }

  async function probe() {
    if (running || !taskId) return;
    running = true;
    outcome = null;
    draw();
    try {
      outcome = { ok: true, r: (await ctx.api.post(ctx.api.runPath(env.runId) + "/probe", { task_id: taskId, offline: offline })) || {} };
      ctx.refreshBudget();
    } catch (e) {
      outcome = { ok: false, problem: problemOf(e) };
    }
    running = false;
    draw();
  }

  function draw() {
    if (gone) return;
    const all = [].concat(...waves.map((w) => w.tasks.map((t) => Object.assign({ wave: w.wave }, t))));
    const kids = [];
    if (!all.length) {
      kids.push(h("p", { class: "muted" }, "There are no task cards in this pack yet."));
    } else {
      kids.push(
        h(
          "div",
          { class: "row wrap" },
          h("label", null, "Task card: ", h("select", { class: "text-input", "data-fk": "probe-task", onchange: (e) => { taskId = e.target.value; outcome = null; draw(); } }, waves.map((w) => h("optgroup", { label: "Wave " + w.wave }, w.tasks.map((t) => h("option", { value: t.id, selected: t.id === taskId }, t.title + " (" + t.id + ")")))))),
          h("label", { class: "row" }, h("input", { type: "checkbox", checked: offline, "data-fk": "probe-offline", onchange: (e) => { offline = !!e.target.checked; draw(); } }), h("span", null, "Offline stub (costs nothing)")),
          h("button", { type: "button", class: "btn btn-primary", "data-fk": "probe-run", disabled: running || !taskId, onclick: probe }, running ? "Reading…" : offline ? "Run the offline stub" : "Run the real test (uses money)")
        )
      );
    }
    if (outcome && !outcome.ok) kids.push(problemBanner(outcome.problem));
    if (outcome && outcome.ok) {
      const r = outcome.r;
      const blocking = arr(r.blocking_questions);
      kids.push(banner(blocking.length ? blocking.length + " question" + (blocking.length === 1 ? "" : "s") + " the reader could not answer from the card. The card needs work." : "The reader had no blocking questions.", blocking.length ? "warn" : "ok"));
      kids.push(card("What the reader understood it must do", arr(r.outline).length ? h("ol", null, arr(r.outline).map((o) => h("li", null, untrusted(textOf(o))))) : h("p", { class: "muted" }, "No outline.")));
      kids.push(card("Questions it could not answer", bullets(texts(blocking), "None.")));
      kids.push(card("What it assumed", bullets(texts(r.assumptions), "Nothing.")));
      const cov = arr(r.acceptance_coverage);
      kids.push(card("Can each acceptance point be met?", cov.length ? h("ul", null, cov.map((c) => h("li", null, chip(c && c.satisfiable === false ? "No" : "Yes", c && c.satisfiable === false ? "bad" : "ok"), " ", untrusted(textOf(c && typeof c === "object" ? c.criterion : c)), c && c.how ? h("span", { class: "muted" }, " ", untrusted(textOf(c.how))) : null))) : h("p", { class: "muted" }, "None listed.")));
    }
    keepFocus(body, () => setKids(body, kids));
  }
  load();
  return { unmount() { gone = true; } };
}
