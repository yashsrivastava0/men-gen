/* The navigation graph as an SVG built with createElementNS: screens as boxes, routes as arrows. Role filter, "in" and
   "later" styling, unreachable screens and dead ends highlighted (and listed in words, so colour is never the only signal). */
import { h, setKids, svg, put, keepFocus } from "../lib/dom.js";
import { texts, layoutGraph, NODE_W, NODE_H } from "../lib/model.js";
import { problemBanner, problemOf, loading, banner, bullets } from "./shared.js";

export function mountTab(root, ctx, env) {
  let gone = false;
  let role = ctx.store.get("grole", "");
  let roles = [];
  const controls = h("div", { class: "row wrap" });
  const pane = h("div", null, loading());
  put(root, h("h3", null, "Navigation"), h("p", { class: "muted" }, "Every screen and the routes between them. Solid boxes are in this release; dashed boxes come later. Red marks a screen nobody can reach; amber marks a screen with no way out."), controls, pane);

  async function load() {
    setKids(pane, loading());
    try {
      const data = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/graph" + ctx.api.q({ role: role }));
      if (gone) return;
      if (data && Array.isArray(data.roles)) roles = texts(data.roles);
      drawControls();
      keepFocus(root, () => setKids(pane, draw(data || {})));
    } catch (e) {
      if (!gone) setKids(pane, problemBanner(problemOf(e)));
    }
  }
  function drawControls() {
    setKids(controls, h("label", null, "Show the screens of: ", h("select", { class: "text-input", "data-fk": "graph-role", onchange: (e) => { role = e.target.value; ctx.store.set("grole", role); load(); } }, h("option", { value: "" }, "Every role"), roles.map((r) => h("option", { value: r, selected: r === role }, r)))));
  }
  function draw(data) {
    const g = layoutGraph(data);
    if (!g.nodes.length) return banner("There is nothing to draw: no screens were listed" + (role ? " for this role" : "") + ".", "info");
    const chart = svg("svg", { class: "graph", role: "img", "aria-label": "Navigation graph: " + g.counts.total + " screens, " + g.counts.unreachable + " unreachable, " + g.counts.deadEnds + " dead ends", viewBox: "0 0 " + g.width + " " + g.height, width: g.width, height: g.height });
    const defs = svg("defs", null, svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "arrowhead" })));
    chart.appendChild(defs);
    for (const e of g.edges) {
      const mid = (e.x1 + e.x2) / 2;
      const d = e.back ? "M " + e.x1 + " " + e.y1 + " C " + (e.x1 + 40) + " " + (e.y1 - 40) + ", " + (e.x2 - 40) + " " + (e.y2 - 40) + ", " + e.x2 + " " + e.y2 : "M " + e.x1 + " " + e.y1 + " C " + mid + " " + e.y1 + ", " + mid + " " + e.y2 + ", " + e.x2 + " " + e.y2;
      chart.appendChild(svg("path", { d: d, class: "edge" + (e.back ? " back" : ""), fill: "none", "marker-end": "url(#arrow)" }, e.label ? svg("title", null, e.label) : null));
    }
    for (const n of g.nodes) {
      const cls = "node" + (n.later ? " later" : " in") + (n.unreachable ? " unreachable" : "") + (n.deadEnd ? " deadend" : "") + (n.entry ? " entry" : "");
      const label = n.title.length > 22 ? n.title.slice(0, 21) + "…" : n.title;
      chart.appendChild(svg("g", { class: cls }, svg("rect", { x: n.x, y: n.y, width: NODE_W, height: NODE_H, rx: 6 }), svg("text", { x: n.x + 8, y: n.y + 15, class: "t1" }, label), svg("text", { x: n.x + 8, y: n.y + 30, class: "t2" }, (n.later ? "later" : "in") + (n.unreachable ? " · unreachable" : "") + (n.deadEnd ? " · dead end" : "")), svg("title", null, n.title + (n.app ? " (" + n.app + ")" : ""))));
    }
    const names = (flag) => g.nodes.filter((n) => n[flag]).map((n) => n.title);
    return [
      h("p", null, g.counts.total + " screens, " + g.counts.later + " later, " + g.counts.unreachable + " unreachable, " + g.counts.deadEnds + " dead ends."),
      h("div", { class: "graph-wrap" }, chart),
      g.counts.unreachable ? h("div", null, h("h4", { class: "tone-bad" }, "Nobody can reach these screens"), bullets(names("unreachable"))) : null,
      g.counts.deadEnds ? h("div", null, h("h4", { class: "tone-warn" }, "These screens have no way out"), bullets(names("deadEnd"))) : null,
    ];
  }
  load();
  return { unmount() { gone = true; } };
}
