/* Needs, blocks, assets, motion intents, stories and opportunities: what the front end must build or source. */
import { h, setKids, put } from "../lib/dom.js";
import { arr } from "../lib/model.js";
import { problemBanner, problemOf, loading, card, genericTable } from "./shared.js";

const SECTIONS = [
  ["needs", "Needs (what the front end must provide)"],
  ["blocks", "Building blocks"],
  ["assets", "Assets to source or make"],
  ["motion_intents", "Motion intents (how things should feel when they move)"],
  ["stories", "Stories"],
  ["opportunities", "Opportunities (extra ideas that are not required)"],
];

export function mountTab(root, ctx, env) {
  let gone = false;
  const pane = h("div", null, loading());
  put(root, h("h3", null, "Needs, blocks and assets"), pane);
  (async () => {
    try {
      const d = (await ctx.api.get(ctx.api.runPath(env.runId) + "/views/needs")) || {};
      if (gone) return;
      setKids(pane, SECTIONS.map(([k, title]) => card(title + " (" + arr(d[k]).length + ")", genericTable(d[k], { max: 6, empty: "None." }))));
    } catch (e) {
      if (!gone) setKids(pane, problemBanner(problemOf(e)));
    }
  })();
  return { unmount() { gone = true; } };
}
