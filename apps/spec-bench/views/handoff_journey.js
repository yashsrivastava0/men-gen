/* Journey walkthrough: choose a role and a journey, read its stories, emotion line, moments that matter and unhappy
   paths, then step through the screens by their declared entries and exits (never by guessing). */
import { h, setKids, untrusted, svg, put, keepFocus } from "../lib/dom.js";
import { arr, textOf, texts, normalizeJourney } from "../lib/model.js";
import { problemBanner, problemOf, loading, banner, card, bullets } from "./shared.js";

export function mountTab(root, ctx, env) {
  let gone = false;
  let flows = [];
  let flowId = env.extra || "";
  let role = ctx.store.get("jrole", "");
  let journey = null;
  let at = 0;
  let trail = [];
  const pick = h("div", { class: "row wrap" });
  const pane = h("div", null, loading());
  put(root, h("h3", null, "Journeys"), h("p", { class: "muted" }, "Follow one person through the app. Each step shows how the person arrived and where they can go next, exactly as the spec declares."), pick, pane);

  async function loadFlows() {
    try {
      const g2 = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/g2");
      flows = arr(g2 && g2.flows).map((f) => ({ id: String(f && f.id !== undefined ? f.id : textOf(f)), title: textOf(f && typeof f === "object" ? f.title || f.name || f.id : f), roles: texts(f && f.roles) }));
    } catch (e) {
      setKids(pane, problemBanner(problemOf(e)));
      return;
    }
    if (!flows.length) {
      setKids(pane, banner("There are no journeys in this pack yet.", "info"));
      return;
    }
    if (!flowId || !flows.some((f) => f.id === flowId)) flowId = flows[0].id;
    drawPick();
    load();
  }
  function rolesOf() {
    const f = flows.find((x) => x.id === flowId);
    return f ? f.roles : [];
  }
  function drawPick() {
    const roles = rolesOf();
    if (role && roles.length && roles.indexOf(role) === -1) role = roles[0];
    if (!role && roles.length) role = roles[0];
    setKids(
      pick,
      h("label", null, "Journey: ", h("select", { class: "text-input", "data-fk": "flow", onchange: (e) => { flowId = e.target.value; drawPick(); load(); } }, flows.map((f) => h("option", { value: f.id, selected: f.id === flowId }, f.title)))),
      roles.length ? h("label", null, "Role: ", h("select", { class: "text-input", "data-fk": "role", onchange: (e) => { role = e.target.value; ctx.store.set("jrole", role); load(); } }, roles.map((r) => h("option", { value: r, selected: r === role }, r)))) : null
    );
  }
  async function load() {
    setKids(pane, loading());
    try {
      journey = normalizeJourney(await ctx.api.get(ctx.api.runPath(env.runId) + "/views/journey/" + encodeURIComponent(flowId) + ctx.api.q({ role: role, profile: env.mock })));
      at = 0;
      trail = [];
      if (!gone) draw();
    } catch (e) {
      if (!gone) setKids(pane, problemBanner(problemOf(e)));
    }
  }
  function go(stepId) {
    const i = journey.steps.findIndex((s) => s.id === stepId);
    if (i === -1) return;
    trail.push(at);
    at = i;
    draw();
  }
  function emotionChart() {
    const pts = journey.emotion.filter((e) => e.level !== null);
    if (pts.length < 2) return null;
    const W = 420;
    const H = 90;
    const lo = Math.min(...pts.map((p) => p.level));
    const hi = Math.max(...pts.map((p) => p.level));
    const y = (v) => 10 + (hi === lo ? 0.5 : 1 - (v - lo) / (hi - lo)) * (H - 20);
    const x = (i) => 10 + (i / (pts.length - 1)) * (W - 20);
    const chart = svg("svg", { class: "emotion", role: "img", "aria-label": "How the person feels along the journey: " + pts.map((p) => p.label + " " + p.level).join(", "), viewBox: "0 0 " + W + " " + H, width: "100%" });
    chart.appendChild(svg("polyline", { class: "line", fill: "none", points: pts.map((p, i) => x(i) + "," + y(p.level)).join(" ") }));
    pts.forEach((p, i) => chart.appendChild(svg("circle", { cx: x(i), cy: y(p.level), r: 4, class: "dot" }, svg("title", null, p.label + ": " + (p.feeling || p.level)))));
    return chart;
  }
  function draw() {
    const j = journey;
    const kids = [];
    kids.push(h("h4", null, untrusted(j.title || "Journey"), j.role ? [" as ", untrusted(j.role)] : null));
    kids.push(card("Stories this journey serves", bullets(j.stories, "None listed.")));
    kids.push(card("How the person feels", emotionChart(), j.emotion.length ? h("ol", { class: "small" }, j.emotion.map((e) => h("li", null, untrusted(e.label, "", "strong"), e.feeling ? [": ", untrusted(e.feeling)] : null))) : h("p", { class: "muted" }, "No emotion line.")));
    kids.push(card("Moments that matter", bullets(j.moments, "None listed.")));
    kids.push(card("When things go wrong", j.unhappy.length ? h("ul", null, j.unhappy.map((u) => h("li", null, untrusted(u.text), u.recovery ? h("span", { class: "muted" }, " Then: ", untrusted(u.recovery)) : null))) : h("p", { class: "muted" }, "None listed.")));
    if (!j.steps.length) {
      kids.push(banner("This journey lists no steps.", "info"));
    } else {
      const s = j.steps[at];
      kids.push(
        card(
          "Walk through it: step " + (at + 1) + " of " + j.steps.length,
          h("div", { class: "step-card" }, h("h4", null, untrusted(s.title)), s.says ? h("p", null, untrusted(s.says)) : null,
            h("p", { class: "muted" }, "Arrived from: ", untrusted(s.entries.length ? s.entries.join(", ") : at === 0 ? "the start of the journey" : "not declared")),
            h("div", { class: "row wrap" }, h("strong", null, "Can go to:"), s.exits.length ? s.exits.map((x, i) => {
              const known = j.steps.some((t) => t.id === x.to);
              return h("button", { type: "button", class: "btn btn-small", "data-fk": "exit-" + i, disabled: !known, title: known ? null : "This leaves the journey", onclick: () => go(x.to) }, x.label || x.to);
            }) : h("span", { class: "tone-warn" }, "Nowhere (a dead end, or the end of the journey)"))),
          h("div", { class: "row" }, h("button", { type: "button", class: "btn", "data-fk": "back", disabled: !trail.length, onclick: () => { at = trail.pop(); draw(); } }, "Back"), h("button", { type: "button", class: "btn", "data-fk": "restart", disabled: at === 0 && !trail.length, onclick: () => { at = 0; trail = []; draw(); } }, "Start again")),
          h("ol", { class: "small steplist" }, j.steps.map((t, i) => h("li", { class: i === at ? "current" : "" }, untrusted(t.title))))
        )
      );
    }
    keepFocus(pane, () => setKids(pane, kids));
  }
  loadFlows();
  return { unmount() { gone = true; } };
}
