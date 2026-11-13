/* Norms ("what apps like this usually have") with how many sources mention each, plus the suggestions as toggles that
   start OFF. Toggles are your choices for the next step; this page keeps them only while it is open. */
import { h, clear, setKids, untrusted } from "../lib/dom.js";
import { normGroups, levelWord, LEVEL_HINTS, prevalenceText, recencyText, SUGGESTION_TITLES, ratio } from "../lib/results.js";
import { plural } from "../lib/format.js";
import { ensureAllItems } from "./results_data.js";
import { openItem, evidenceBadge } from "./item.js";
import { problemBanner } from "./shared.js";
import { ApiError } from "../lib/api.js";

export function mountTab(panel, state) {
  const ov = state.overview;
  const chosen = (state.chosen = state.chosen || new Set());
  const groups = normGroups(ov.norms);
  const out = [h("p", { class: "lead" }, "How often comparable apps do each thing, counted over the sources found. “Mentioned in 3 of 14” means three of the fourteen sources said so.")];
  if (!groups.length) out.push(h("p", { class: "muted" }, "No norms were found for this run."));
  for (const g of groups) {
    out.push(
      h(
        "section",
        { class: "card", "data-area": g.area },
        h("h3", null, g.title + " (" + g.norms.length + ")"),
        h(
          "ul",
          { class: "norms" },
          g.norms.map((n) => normRow(n))
        )
      )
    );
  }
  out.push(suggestions(ov.suggestions || {}));
  setKids(panel, out);

  function normRow(n) {
    const p = n.prevalence || {};
    const box = h("div", { class: "norm-items" });
    const det = h(
      "details",
      { class: "details" },
      h("summary", { "data-fk": "norm:" + n.key }, plural((n.item_ids || []).length, "finding")),
      box
    );
    let loaded = false;
    det.addEventListener("toggle", async () => {
      if (!det.open || loaded) return;
      loaded = true;
      setKids(box, h("p", { class: "muted small" }, "Loading…"));
      try {
        const map = await ensureAllItems(state);
        setKids(
          box,
          h(
            "ul",
            { class: "plain-list" },
            (n.item_ids || []).map((id) => {
              const it = map.get(id);
              return it
                ? h("li", null, h("button", { type: "button", class: "linkish", onclick: () => openItem(it, { ctx: state.ctx, runId: state.runId }) }, it.label || id), " ", evidenceBadge(it.evidence))
                : h("li", null, id);
            })
          )
        );
      } catch (e) {
        loaded = false;
        setKids(box, e instanceof ApiError ? problemBanner(e.problem) : h("p", { class: "tone-bad" }, "The findings could not be loaded."));
      }
    });
    return h(
      "li",
      { class: "norm" },
      h("div", { class: "norm-head" }, h("strong", null, untrusted(n.label)), " ", h("span", { class: "chip chip-" + (ratio(p) >= 0.6 ? "ok" : "plain"), title: LEVEL_HINTS[p.level] || "" }, levelWord(p.level) || "")),
      h("div", { class: "muted small" }, prevalenceText(p), recencyText(n.recency) ? " · " + recencyText(n.recency) : "", n.matches_brief ? " · in your Brief as " + n.matches_brief : ""),
      det
    );
  }

  function suggestions(sug) {
    const keys = Object.keys(sug).filter((k) => (sug[k] || []).length);
    const count = h("p", { class: "muted small", role: "status" }, "");
    const redraw = () => {
      count.textContent = chosen.size ? plural(chosen.size, "suggestion") + " switched on. These are your choices for the next step; nothing is added until you confirm them there." : "Nothing is switched on. Suggestions never become part of the app by themselves.";
    };
    redraw();
    const card = h("section", { class: "card" }, h("h3", null, "Ideas you may want to add"), h("p", { class: "muted" }, "Things comparable apps often have that your Brief does not mention. They start switched off."), count);
    if (!keys.length) card.appendChild(h("p", { class: "muted" }, "There are no suggestions for this run."));
    for (const k of keys) {
      card.appendChild(h("h4", null, SUGGESTION_TITLES[k] || k));
      card.appendChild(
        h(
          "ul",
          { class: "plain-list toggles" },
          sug[k].map((s, i) => {
            const id = "sug-" + k + "-" + i;
            const key = k + ":" + s.label;
            const box = h("input", { type: "checkbox", id: id, "data-fk": id, checked: chosen.has(key) });
            box.addEventListener("change", () => {
              if (box.checked) chosen.add(key);
              else chosen.delete(key);
              redraw();
            });
            return h("li", null, box, " ", h("label", { for: id }, untrusted(s.label), s.prevalence ? h("span", { class: "muted small" }, " — " + prevalenceText(s.prevalence)) : null));
          })
        )
      );
    }
    return card;
  }
}
