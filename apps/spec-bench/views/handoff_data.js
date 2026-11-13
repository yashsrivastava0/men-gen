/* Data and contract: entities and their lifecycles, operations, fixtures, mock behaviours, and integrations with
   their interrupted-flow and pending-timeout rules. All text from the pack is text. */
import { h, setKids, untrusted, put } from "../lib/dom.js";
import { arr, textOf, texts } from "../lib/model.js";
import { problemBanner, problemOf, loading, card, genericTable } from "./shared.js";

export function mountTab(root, ctx, env) {
  let gone = false;
  const pane = h("div", null, loading());
  put(root, h("h3", null, "Data and contract"), h("p", { class: "muted" }, "What the app stores, what its server must offer, the example records the mock front end shows, and how it behaves when an outside service is slow or the person is interrupted."), pane);
  (async () => {
    try {
      const d = (await ctx.api.get(ctx.api.runPath(env.runId) + "/views/data")) || {};
      if (gone) return;
      const ents = arr(d.entities);
      const kids = [
        card("Entities and their lifecycles", ents.length ? h("div", { class: "appmap" }, ents.map((e) => h("div", { class: "appbox" }, h("h4", null, untrusted(textOf(e && typeof e === "object" ? e.name || e.title : e))), e && e.plain ? h("p", null, untrusted(textOf(e.plain))) : null, e && e.lifecycle ? h("p", null, h("strong", null, "Goes through: "), untrusted(texts(e.lifecycle).join(" → "))) : null, e && e.fields ? h("p", { class: "muted small" }, untrusted(texts(e.fields).join(", "))) : null))) : h("p", { class: "muted" }, "None.")),
        card("Operations the server must offer", genericTable(d.operations, { max: 5, empty: "None." })),
        card("Example records (fixtures)", genericTable(d.fixtures, { max: 5, empty: "None." })),
        card("Mock behaviours", genericTable(d.mock_behaviours || d.mock_behaviors, { max: 5, empty: "None." })),
        card("Outside services and what happens when they stall", arr(d.integrations).length ? genericTable(d.integrations, { max: 6 }) : h("p", { class: "muted" }, "None.")),
      ];
      setKids(pane, kids);
    } catch (e) {
      if (!gone) setKids(pane, problemBanner(problemOf(e)));
    }
  })();
  return { unmount() { gone = true; } };
}
