/* Screen content maps: pick a screen and an example-data profile; the app returns what the screen must say and do,
   with MUST, USUALLY and COULD kept apart. Never a layout. */
import { h, setKids, navLink, put, keepFocus } from "../lib/dom.js";
import { arr, textOf, normalizeScreen } from "../lib/model.js";
import { MOCK_PROFILES, mockWord } from "../lib/format.js";
import { problemBanner, problemOf, loading, banner } from "./shared.js";
import { renderContentMap } from "./contentmap.js";

export function mountTab(root, ctx, env) {
  let gone = false;
  let screens = [];
  let sid = env.extra || "";
  const list = h("nav", { class: "screenlist", "aria-label": "Screens" });
  const pane = h("div", { class: "screenpane" }, loading());
  put(root, h("h3", null, "Screens"), h("p", { class: "muted" }, "What each screen must communicate and do, shown on the example data chosen above. Switch the example data to see the screen with nothing, a lot, wrong data, or no connection."), h("div", { class: "split" }, list, pane));

  async function loadList() {
    try {
      const g2 = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/g2");
      screens = arr(g2 && (g2.content_maps || g2.content_map)).map((m) => normalizeScreen(m)).filter((s) => s.id);
      if (!screens.length) {
        const g15 = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/g15");
        screens = arr(g15 && g15.inventory).map((i) => ({ id: String(i.id || ""), title: textOf(i.title || i.name || i.id) })).filter((s) => s.id);
      }
    } catch (e) {
      setKids(pane, problemBanner(problemOf(e)));
      return;
    }
    if (!screens.length) {
      setKids(pane, banner("There are no screens in this pack yet.", "info"));
      return;
    }
    if (!sid || !screens.some((s) => s.id === sid)) sid = screens[0].id;
    drawList();
    show();
  }
  function drawList() {
    setKids(list, h("ul", { class: "plain-list" }, screens.slice(0, 500).map((s) => h("li", null, navLink("#/handoff/" + env.runId + "/screens/" + s.id, s.title || s.id, s.id === sid ? "current" : "")))));
  }
  async function show() {
    setKids(pane, loading());
    try {
      const r = await ctx.api.get(ctx.api.runPath(env.runId) + "/views/screen/" + encodeURIComponent(sid) + ctx.api.q({ profile: env.mock }));
      if (gone) return;
      const sc = normalizeScreen(r);
      if (!sc.id) sc.id = sid;
      const switcher = h("div", { class: "row wrap mockswitch", role: "group", "aria-label": "Example data" }, MOCK_PROFILES.map((m) => h("button", { type: "button", class: "btn btn-chip", "aria-pressed": String(m === env.mock), "data-fk": "mock-" + m, onclick: () => { env.mock = m; ctx.store.set("mock", m); show(); } }, m)));
      keepFocus(pane, () => setKids(pane, switcher, h("p", { class: "muted" }, mockWord(env.mock)), renderContentMap(sc, { mock: env.mock })));
    } catch (e) {
      if (!gone) setKids(pane, problemBanner(problemOf(e)));
    }
  }
  loadList();
  return { unmount() { gone = true; } };
}
