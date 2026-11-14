/* Step 4, Run: the live feed. The stream of spec.event.v1 events is folded by the reducer; the screen shows the steps,
   calls, tokens and money, the checks and repairs, banners, the gates, and the buttons for stop and resume. */
import { h, setKids, untrusted, navLink, put, keepFocus } from "../lib/dom.js";
import { createRunFeed } from "../lib/runfeed.js";
import { emptyState, applySnapshot, ingest, stepRows, callKinds, runActions } from "../lib/reducer.js";
import { describeEvent } from "../lib/eventtext.js";
import { usd, usdRange, stepWord, statusWord, reasonWord, bannerWord, tokensText, plural, formatTime, count } from "../lib/format.js";
import { banner, statusChip, modeChip, problemBanner, problemOf, loading, pageHead, card, tile, genericTable } from "./shared.js";

export function mount(root, ctx, route) {
  const id = route.id;
  let gone = false;
  let state = emptyState();
  let snap = {};
  let live = "connecting";
  let actionProblem = null;
  let busy = false;
  const body = h("div", null, loading());
  put(root, pageHead("4. Run", "What the Spec is doing right now. You can leave this page; the run goes on."), body);
  const schedule = ctx.schedule || ((fn) => fn());

  const feed = createRunFeed({
    afterSeq: 0,
    open: (after, signal) => ctx.api.stream(id, after, signal),
    onEvents: (events) => {
      const r = ingest(state, events);
      if (r.gap) return resync();
      if (r.applied) {
        ctx.refreshBudget();
        if (state.status !== snap.status) ctx.announce(statusWord(state.status));
      }
      schedule(draw);
    },
    onState: (name, info) => {
      live = name;
      if (name === "fatal") actionProblem = problemOf(info && info.error);
      schedule(draw);
    },
  });

  async function loadSnapshot() {
    snap = (await ctx.api.get(ctx.api.runPath(id))) || {};
    // The steps, calls and checks are folded from the events, and the feed replays them from the number this page
    // has folded so far. The snapshot's own last_seq must not move that number, or a page opened on a run that already
    // has events would count every one of them as seen and show an empty run (found in a real browser).
    const folded = state.lastSeq;
    state = applySnapshot(snap, state);
    state.lastSeq = folded;
  }
  /** An event was missing: take the snapshot again and read the log from the start (the folds are cheap). */
  async function resync() {
    feed.stop();
    state = emptyState();
    try {
      await loadSnapshot();
    } catch (e) {
      actionProblem = problemOf(e);
    }
    state.lastSeq = 0;
    feed.reset(0);
    if (!gone) feed.start();
  }

  async function act(path, then) {
    if (busy) return;
    busy = true;
    actionProblem = null;
    draw();
    try {
      await ctx.api.post(ctx.api.runPath(id) + path, {});
      await loadSnapshot();
      if (then) then();
      ctx.refreshBudget();
    } catch (e) {
      actionProblem = problemOf(e);
    }
    busy = false;
    draw();
  }
  const stop = () => act("/stop", null);
  const resume = () => act("/resume", () => { feed.reset(state.lastSeq); feed.start(); });

  function draw() {
    if (gone) return;
    const acts = runActions(state);
    const kids = [];
    kids.push(h("div", { class: "row" }, h("p", { class: "nowline" }, statusChip(state.status), " ", modeChip(snap), " ", state.step ? stepWord(state.step) : ""), h("span", { class: "muted small" }, live === "live" ? "Live" : live === "ended" ? "Feed ended" : live === "retry" ? "Reconnecting…" : "Connecting…")));
    if (actionProblem) kids.push(problemBanner(actionProblem));
    for (const b of state.banners) kids.push(banner(bannerWord(b.code) + (b.text ? " " + b.text : ""), "warn"));
    if (state.status === "partial") {
      kids.push(banner("The run stopped early because " + reasonWord(state.partial && state.partial.reason) + ". Everything done so far is saved. " + (acts.resume ? "You can continue from the last saved point; the result will be the same as an uninterrupted run." : ""), "warn"));
    }
    if (state.status === "failed") kids.push(banner("The run failed. " + (state.failure && state.failure.reason ? state.failure.reason : "No reason was given.") + " Your money record is safe.", "bad"));
    if (acts.openG15) kids.push(h("div", { class: "banner banner-info", role: "status" }, h("p", null, "The structure is ready and waiting for your OK."), navLink("#/g15/" + id, "Review the structure (G1.5)", "btn btn-primary")));
    if (acts.openG2) kids.push(h("div", { class: "banner banner-info", role: "status" }, h("p", null, "The spec is ready and waiting for your OK."), navLink("#/g2/" + id, "Review the spec (G2)", "btn btn-primary")));
    if (state.status === "approved") kids.push(h("div", { class: "banner banner-ok" }, h("p", null, "Approved. The pack is ready to hand off."), navLink("#/handoff/" + id + "/sim", "Open the hand-off (mock front end)", "btn btn-primary")));

    const est = state.estimate || snap.estimate;
    kids.push(
      h(
        "div",
        { class: "tiles" },
        tile("Spent on this run", usd(state.spendMicros || snap.spend_micros || 0)),
        tile("Estimate", est && typeof est.low_micros === "number" ? usdRange(est.low_micros, est.high_micros) : "not known"),
        tile("Model calls", String(state.calls.count)),
        tile("Tokens", tokensText(state.calls.tokens) || "0 tokens"),
        tile("Calls repaired", String(state.calls.repaired)),
        tile("Saved points", String(state.checkpoints))
      )
    );
    kids.push(
      h(
        "div",
        { class: "actions row" },
        h("button", { type: "button", class: "btn btn-danger", "data-fk": "stop", disabled: busy || !acts.stop, onclick: stop }, "Stop (saves what is done)"),
        h("button", { type: "button", class: "btn btn-primary", "data-fk": "resume", disabled: busy || !acts.resume, onclick: resume }, "Continue from the last saved point"),
        !acts.resume ? h("span", { class: "why-off" }, "Continue is available after a run stops early.") : null
      )
    );
    const steps = stepRows(state);
    const kinds = callKinds(state);
    kids.push(
      h(
        "div",
        { class: "run-grid" },
        card("Steps", steps.length ? h("ol", { class: "steps" }, steps.map((s) => h("li", { class: "step step-" + s.status + (s.current ? " current" : "") }, h("span", { class: "chip chip-" + (s.status === "done" ? "ok" : "busy") }, s.status === "done" ? "Done" : "Working"), " ", stepWord(s.name)))) : h("p", { class: "muted" }, "Waiting for the first step.")),
        card("Model calls by kind", kinds.length ? genericTable(kinds.map((k) => ({ kind: k.kind, calls: k.count, tokens: count(k.tokens), cost: usd(k.costMicros) })), { cols: ["kind", "calls", "tokens", "cost"] }) : h("p", { class: "muted" }, "No model calls yet."))
      )
    );
    kids.push(
      h(
        "div",
        { class: "run-grid" },
        card("Checks", state.validators.length ? genericTable(state.validators.slice(-12).reverse().map((v) => ({ gate: v.gate, must_fix: v.blockers, to_check: v.warnings })), { cols: ["gate", "must_fix", "to_check"], labels: { must_fix: "Must fix", to_check: "To check" } }) : h("p", { class: "muted" }, "No checks have run yet.")),
        card("Repairs", h("p", null, plural(state.repairs.rcode, "mechanical fix", "mechanical fixes"), " and ", plural(state.repairs.reask, "re-ask"), "."), state.repairs.items.length ? h("ul", { class: "small" }, state.repairs.items.slice(-8).reverse().map((r) => h("li", null, untrusted(describeEvent({ type: r.type, data: { message: r.text } }).text)))) : null)
      )
    );
    kids.push(
      card(
        "Latest",
        h(
          "ul",
          { class: "feed" },
          state.feed.slice(-40).reverse().map((e) => {
            const d = describeEvent(e);
            return h("li", { class: "feed-row" }, h("span", { class: "feed-kind" }, d.kind), h("span", null, untrusted(d.text), e.at ? h("span", { class: "feed-extra" }, " " + formatTime(e.at)) : null));
          })
        )
      )
    );
    keepFocus(body, () => setKids(body, kids));
  }

  (async () => {
    try {
      await loadSnapshot();
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)), h("button", { type: "button", class: "btn", onclick: () => ctx.navigate("#/run/" + id) }, "Try again"));
      return;
    }
    draw();
    feed.start();
  })();
  return {
    unmount() {
      gone = true;
      feed.stop();
    },
    /** For tests: the folded state. */
    get state() {
      return state;
    },
  };
}
