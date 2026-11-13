/* Step 6, Hand-off: the mock front end. It plays the front end's role (never a design): it reads the pack the way
   the front end engine will, so problems show up here first. Tabs are real links (#/handoff/<run>/<tab>/<more>). */
import { h, setKids, clear, fileLink, put } from "../lib/dom.js";
import { normalizeProfiles } from "../lib/model.js";
import { MOCK_PROFILES, mockWord } from "../lib/format.js";
import { problemBanner, problemOf, pageHead, tabLinks, loading } from "./shared.js";

export const TABS = [
  { id: "sim", label: "Reading the pack", load: () => import("./handoff_sim.js") },
  { id: "probe", label: "Weakest reader test", load: () => import("./handoff_probe.js") },
  { id: "screens", label: "Screens", load: () => import("./handoff_screens.js") },
  { id: "journeys", label: "Journeys", load: () => import("./handoff_journey.js") },
  { id: "graph", label: "Navigation", load: () => import("./handoff_graph.js") },
  { id: "data", label: "Data and contract", load: () => import("./handoff_data.js") },
  { id: "needs", label: "Needs and assets", load: () => import("./handoff_needs.js") },
  { id: "files", label: "Pack files", load: () => import("./handoff_pack.js") },
  { id: "tokens", label: "Size report", load: () => import("./handoff_pack.js") },
  { id: "checks", label: "Checks", load: () => import("./handoff_pack.js") },
  { id: "selfcheck", label: "Self-check", load: () => import("./handoff_pack.js") },
  { id: "verify", label: "Verify (big apps)", load: () => import("./handoff_pack.js") },
  { id: "diff", label: "Compare runs", load: () => import("./handoff_pack.js") },
  { id: "history", label: "Run history", load: () => import("./handoff_pack.js") },
];

export function mount(root, ctx, route) {
  const id = route.id;
  const tabId = TABS.some((t) => t.id === route.tab) ? route.tab : "sim";
  let gone = false;
  let inner = null;
  let token = 0;
  const env = { runId: id, tab: tabId, extra: route.extra || null, consumer: ctx.store.get("consumer", ""), mock: ctx.store.get("mock", "typical"), profiles: [], go: (r) => ctx.navigate(r) };
  const controls = h("div", { class: "row wrap controls" });
  const content = h("div", { class: "tab-body", tabindex: "-1" }, loading());
  put(
    root,
    pageHead("6. Hand-off to the mock front end", "This plays the part of the front end engine, which builds the real app from the pack. It is a test stand: what you see here is illustrative, never the final design.", fileLink("/api/runs/" + encodeURIComponent(id) + "/export/preview.html", "Download preview.html", "btn", "preview.html")),
    tabLinks(TABS.map((t) => ({ id: t.id, label: t.label, route: "#/handoff/" + id + "/" + t.id })), tabId, "Hand-off sections"),
    controls,
    content
  );

  function drawControls() {
    const consumer = h("select", { class: "text-input", "data-fk": "consumer", onchange: (e) => { env.consumer = e.target.value; ctx.store.set("consumer", env.consumer); remount(); } }, env.profiles.length ? env.profiles.map((p) => h("option", { value: p.id, selected: p.id === env.consumer }, p.label + (p.capabilityClass ? " (" + p.capabilityClass + ")" : ""))) : h("option", { value: "" }, "Default reader"));
    const mock = h("select", { class: "text-input", "data-fk": "mock", onchange: (e) => { env.mock = e.target.value; ctx.store.set("mock", env.mock); remount(); } }, MOCK_PROFILES.map((m) => h("option", { value: m, selected: m === env.mock }, mockWord(m))));
    setKids(controls, h("label", null, "Pretend the front end engine is: ", consumer), h("label", null, "Example data: ", mock));
  }
  async function remount() {
    const my = ++token;
    if (inner && inner.unmount) {
      try {
        inner.unmount();
      } catch (e) {
        console.error(e);
      }
    }
    inner = null;
    clear(content);
    content.appendChild(loading());
    try {
      const mod = await TABS.find((t) => t.id === tabId).load();
      if (my !== token || gone) return;
      clear(content);
      inner = (await mod.mountTab(content, ctx, env, tabId)) || null;
    } catch (e) {
      if (my !== token || gone) return;
      console.error(e);
      clear(content);
      content.appendChild(problemBanner(problemOf(e)));
    }
  }
  (async () => {
    try {
      env.profiles = normalizeProfiles(await ctx.api.get("/api/profiles"));
    } catch (e) {
      env.profiles = [];
    }
    if (!env.consumer || !env.profiles.some((p) => p.id === env.consumer)) env.consumer = env.profiles.length ? env.profiles[0].id : "";
    if (gone) return;
    drawControls();
    remount();
  })();
  return {
    unmount() {
      gone = true;
      token++;
      if (inner && inner.unmount) inner.unmount();
    },
  };
}
