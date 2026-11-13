/* "Confirm" (findings that need your OK before they are used, and the defaults proposed) and "Gaps and conflicts"
   (what is missing and why, in plain words, and where sources disagree). */
import { h, setKids, untrusted } from "../lib/dom.js";
import { gapGroups } from "../lib/results.js";
import { plural } from "../lib/format.js";
import { ensureAllItems } from "./results_data.js";
import { openItem, evidenceBadge, flagChips } from "./item.js";
import { problemBanner } from "./shared.js";
import { ApiError } from "../lib/api.js";

export async function mountTab(panel, state, tabId) {
  let items;
  try {
    items = await ensureAllItems(state);
  } catch (e) {
    setKids(panel, e instanceof ApiError ? problemBanner(e.problem) : h("p", { class: "tone-bad" }, "The findings could not be loaded."));
    return;
  }
  const open = (it) => openItem(it, { ctx: state.ctx, runId: state.runId });
  const itemLine = (id) => {
    const it = items.get(id);
    return it ? h("button", { type: "button", class: "linkish", "data-fk": "open:" + id, onclick: () => open(it) }, it.label || id) : h("span", null, id);
  };
  if (tabId === "confirm") confirmTab(panel, state.overview, items, itemLine);
  else gapsTab(panel, state.overview, items, itemLine);
}

function confirmTab(panel, ov, items, itemLine) {
  const list = ov.confirm || [];
  const out = [h("p", { class: "lead" }, "These findings contain a number, a fee, a limit or a legal point. Check them yourself before anyone builds on them.")];
  if (!list.length) out.push(h("p", { class: "muted" }, "Nothing needs confirming."));
  else {
    out.push(
      h(
        "section",
        { class: "card" },
        h("h3", null, plural(list.length, "finding") + " to confirm"),
        h(
          "ul",
          { class: "plain-list confirm-list" },
          list.map((c) => {
            const it = items.get(c.item_id);
            return h("li", { class: "confirm-item" }, h("div", null, itemLine(c.item_id), " ", it ? evidenceBadge(it.evidence) : null, it ? flagChips(it.flags, 2) : null), h("div", { class: "muted small" }, untrusted(c.why || "")));
          })
        )
      )
    );
  }
  const defaults = ov.defaults_proposed || [];
  if (defaults.length) {
    out.push(
      h(
        "section",
        { class: "card" },
        h("h3", null, "Defaults we propose"),
        h("p", { class: "muted" }, "Where the research gave a clear answer, or none, this is what the next step would assume unless you say otherwise."),
        h("ul", { class: "plain-list" }, defaults.map((d) => h("li", null, untrusted(d.default || ""), h("span", { class: "muted small" }, " — based on " + plural((d.item_ids || []).length, "finding") + (d.state ? ", " + String(d.state).replace(/_/g, " ") : "")))))
      )
    );
  }
  setKids(panel, out);
}

function gapsTab(panel, ov, items, itemLine) {
  const groups = gapGroups(ov.gaps);
  const out = [h("p", { class: "lead" }, "What the research could not settle, and why. A gap is not a failure: it tells you where to look yourself.")];
  if (!groups.length) out.push(h("section", { class: "card" }, h("p", null, "No gaps: every topic was covered.")));
  for (const g of groups) {
    out.push(
      h(
        "section",
        { class: "card", "data-reason": g.reason },
        h("h3", null, plural(g.gaps.length, "topic") + ": " + g.text),
        h(
          "ul",
          { class: "plain-list" },
          g.gaps.map((gap) =>
            h("li", null, h("strong", null, untrusted(gap.label || gap.target_id)), (gap.queries || []).length ? h("details", { class: "details" }, h("summary", null, "What we searched for"), h("ul", null, gap.queries.map((q) => h("li", null, untrusted(q))))) : null)
          )
        )
      )
    );
  }
  const conflicts = ov.conflicts || [];
  out.push(
    h(
      "section",
      { class: "card" },
      h("h3", null, "Where sources disagree"),
      conflicts.length
        ? h("ul", { class: "plain-list" }, conflicts.map((c) => h("li", null, h("strong", null, untrusted(c.topic || "")), h("div", { class: "muted small" }, untrusted(c.note || "")), h("div", null, (c.item_ids || []).map((id, i) => [i ? " · " : "", itemLine(id)])))))
        : h("p", { class: "muted" }, "No conflicts were found.")
    )
  );
  setKids(panel, out);
}
