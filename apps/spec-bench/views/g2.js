/* Step 5, G2: the review of the finished spec. The summary comes first, then everything the owner needs to decide:
   what is confirmed and what is not, in plain words. The approve button binds the content hash shown on the page. */
import { h, setKids, untrusted, navLink, put, keepFocus } from "../lib/dom.js";
import { arr, textOf, texts, rankConfirm, normalizeScreen } from "../lib/model.js";
import { severityTone, capitalize, humanize } from "../lib/format.js";
import { renderContentMap, ILLUSTRATIVE } from "./contentmap.js";
import { chip, statusChip, problemBanner, problemOf, loading, pageHead, card, facts, bullets, genericTable } from "./shared.js";

const SHOW_FIRST = 25;
const money = (c) => (c === "money" ? "Money" : c === "legal" ? "Legal" : c === "identity" ? "Identity" : capitalize(humanize(c)) || "Other");

export function mount(root, ctx, route) {
  const id = route.id;
  let gone = false;
  const body = h("div", null, loading());
  put(root, pageHead("5. Spec review (G2)", "Read the summary first. If it matches what you want, approve. Your approval is tied to the exact content shown here (its hash), so a later change needs a new approval."), body);
  let snap = {};
  let view = {};
  let result = null;
  let busy = false;
  let shown = SHOW_FIRST;
  let ack = false; // "I have read the failed checks and approve anyway" (recorded with the approval)
  const choice = new Map(); // suggestion id -> included (true) or left out (false)

  async function load() {
    try {
      const [s, v] = await Promise.all([ctx.api.get(ctx.api.runPath(id)), ctx.api.get(ctx.api.runPath(id) + "/views/g2")]);
      snap = s || {};
      view = v || {};
      for (const sg of arr(view.suggestions)) {
        const sid = suggestionId(sg);
        if (!choice.has(sid)) choice.set(sid, !(sg && sg.included === false));
      }
    } catch (e) {
      if (!gone) setKids(body, problemBanner(problemOf(e)), h("button", { type: "button", class: "btn", onclick: load }, "Try again"));
      return;
    }
    draw();
  }
  const suggestionId = (sg) => String(sg && typeof sg === "object" && sg.id !== undefined ? sg.id : textOf(sg));

  async function approve() {
    if (busy) return;
    busy = true;
    result = null;
    draw();
    const payload = { decision: "approve", content_hash: String(view.content_hash || "") };
    if (choice.size) payload.suggestion_choices = Object.fromEntries(Array.from(choice.entries()));
    if (ack) payload.acknowledge_blockers = true;
    try {
      await ctx.api.post(ctx.api.runPath(id) + "/g2", payload);
      result = { ok: true };
      ctx.refreshBudget();
      ctx.navigate("#/handoff/" + id + "/sim");
      return;
    } catch (e) {
      result = { ok: false, problem: problemOf(e) };
    }
    busy = false;
    draw();
  }

  function draw() {
    if (gone) return;
    const kids = [];
    const hash = String(view.content_hash || snap.content_hash || "");
    const open = snap.status === "awaiting_g2" || (snap.gates && snap.gates.g2 === "open");
    const summary = view.summary && typeof view.summary === "object" ? view.summary : { text: textOf(view.summary) };

    kids.push(card("In short", h("p", { class: "summary-text" }, untrusted(summary.text || summary.paragraph || "No summary was written.")), summary.tables ? arr(summary.tables).map((t) => genericTable(t.rows || t, {})) : null));
    const degraded = texts(view.degraded_nodes, ["nodes"]);
    kids.push(
      card(
        "How it was checked",
        facts([
          ["Checked by", texts(snap.checked_by || view.checked_by).join(", ") || "not stated"],
          ["Reviewer model's maker", textOf(snap.critic_vendor || view.critic_vendor) || "not stated"],
        ]),
        degraded.length ? h("div", null, h("p", { class: "tone-warn" }, "These parts could not be fully repaired and are marked degraded. Check them:"), bullets(degraded)) : h("p", { class: "tone-ok" }, "No part is marked degraded.")
      )
    );
    const apps = arr(view.app_map || view.apps);
    kids.push(card("App map", apps.length ? h("div", { class: "appmap" }, apps.map((a) => h("div", { class: "appbox" }, h("h4", null, untrusted(textOf(a && typeof a === "object" ? a.name || a.title || a.id : a))), bullets(texts(a && a.modules), "")))) : h("p", { class: "muted" }, "No app map.")));
    const flows = arr(view.flows);
    kids.push(card("Flows (what a person does, step by step)", flows.length ? h("div", null, flows.map((f) => h("details", { class: "details" }, h("summary", null, untrusted(textOf(f && typeof f === "object" ? f.title || f.name || f.id : f))), bullets(texts(f && (f.steps || f.screens)), "No steps listed.")))) : h("p", { class: "muted" }, "No flows listed.")));

    const maps = arr(view.content_maps || view.content_map);
    kids.push(
      card(
        "Content map for each screen (" + maps.length + ")",
        h("p", { class: "illustrative", role: "note" }, ILLUSTRATIVE, " These pages say what each screen must communicate and do, not where anything goes."),
        maps.slice(0, shown).map((m) => {
          const sc = normalizeScreen(m);
          return h("details", { class: "details" }, h("summary", null, untrusted(sc.title || sc.id)), renderContentMap(sc, {}));
        }),
        maps.length > shown ? h("button", { type: "button", class: "btn", "data-fk": "more-maps", onclick: () => { shown += SHOW_FIRST; draw(); } }, "Show " + Math.min(SHOW_FIRST, maps.length - shown) + " more (" + (maps.length - shown) + " left)") : null
      )
    );
    const dataPlain = arr(view.data || view.data_plain);
    kids.push(card("What the app keeps track of, in plain words", dataPlain.length ? h("ul", null, dataPlain.map((d) => h("li", null, d && typeof d === "object" && (d.entity || d.name) ? [untrusted(textOf(d.entity || d.name), "", "strong"), ": "] : null, untrusted(textOf(d && typeof d === "object" ? d.plain || d.text || d.description : d))))) : h("p", { class: "muted" }, "Nothing listed.")));
    kids.push(card("What differs from the Brief", bullets(texts(view.differences_from_brief || view.differences), "Nothing differs from the Brief.")));
    kids.push(card("Assumptions (things the Spec decided because nobody said)", bullets(texts(view.assumptions), "No assumptions were needed.")));
    const confirm = rankConfirm(view.confirm_list || view.confirm, 20);
    kids.push(
      card(
        "Please confirm (the 20 that matter most; money, legal and identity first)",
        confirm.length
          ? h("ol", { class: "confirm" }, confirm.map((c) => h("li", null, chip(money(c.category), c.category === "money" || c.category === "legal" || c.category === "identity" ? "warn" : "plain"), " ", untrusted(c.text), c.why ? h("span", { class: "muted" }, " Why: ", untrusted(c.why)) : null)))
          : h("p", { class: "muted" }, "Nothing needs confirming.")
      )
    );
    const tiers = arr(view.capability_tiers || view.capabilities);
    kids.push(card("What the app needs from the phone and when it asks (capability tiers)", tiers.length ? genericTable(tiers, { max: 5 }) : h("p", { class: "muted" }, "None.")));
    const store = arr(view.store_rules);
    kids.push(card("App store rules that apply, in plain words", store.length ? h("ul", null, store.map((r) => h("li", null, r && typeof r === "object" && r.store ? [untrusted(textOf(r.store), "", "strong"), ": "] : null, untrusted(textOf(r && typeof r === "object" ? r.plain || r.rule || r.text : r))))) : h("p", { class: "muted" }, "None.")));
    const sugg = arr(view.suggestions || view.research_suggestions);
    kids.push(
      card(
        "Suggestions from the research (you choose)",
        sugg.length
          ? h("ul", { class: "suggestions" }, sugg.map((sg) => {
              const sid = suggestionId(sg);
              const on = choice.get(sid) !== false;
              return h("li", null, h("span", null, untrusted(textOf(sg && typeof sg === "object" ? sg.text || sg.title || sg.name : sg)), sg && sg.why ? h("span", { class: "muted" }, " ", untrusted(textOf(sg.why))) : null), " ", h("button", { type: "button", class: "btn btn-chip", "aria-pressed": String(on), "data-fk": "sg-" + sid, onclick: () => { choice.set(sid, !on); draw(); } }, on ? "Included" : "Left out"));
            }))
          : h("p", { class: "muted" }, "No suggestions.")
      )
    );
    const fixes = arr(view.code_fixes);
    const flags = arr(view.flags);
    kids.push(card("Fixes made by the checks and what is flagged", h("h4", null, "Mechanical fixes (" + fixes.length + ")"), fixes.length ? genericTable(fixes, { max: 4 }) : h("p", { class: "muted" }, "None."), h("h4", null, "Flags (" + flags.length + ")"), flags.length ? genericTable(flags, { max: 4 }) : h("p", { class: "muted" }, "None.")));
    const risks = arr(view.risks);
    kids.push(card("Risks", risks.length ? h("ul", null, risks.map((r) => h("li", { class: "risk-" + severityTone(r && r.severity) }, untrusted(textOf(r && typeof r === "object" ? r.text || r.risk || r.title : r))))) : h("p", { class: "muted" }, "No risks listed.")));

    const blocking = texts(view.blocking_needs || view.missing_needs);
    const failed = arr(view.blocking_checks);
    const failedTotal = typeof view.blocking_checks_total === "number" ? view.blocking_checks_total : failed.length;
    const refuseWhy = !open ? "The spec is not waiting for approval right now (" + (snap.status || "unknown") + ")." : !hash ? "The spec has no content hash yet." : blocking.length ? "The pack is missing something the front end needs, so it cannot be approved yet." : failedTotal && !ack ? "Some checks failed as blockers. Read them above and tick the box if you accept them." : "";
    if (result && !result.ok) kids.push(problemBanner(result.problem));
    kids.push(
      card(
        "Your decision",
        statusChip(snap.status),
        facts([["Content hash this approval binds", hash ? h("code", null, hash) : "none yet"]]),
        blocking.length ? h("div", null, h("p", { class: "tone-bad" }, "Missing for the front end:"), bullets(blocking)) : null,
        failedTotal
          ? h(
              "div",
              { class: "block", "data-fk": "failed-checks" },
              h("p", { class: "tone-bad" }, failedTotal + " check" + (failedTotal === 1 ? "" : "s") + " failed as blockers" + (failed.length < failedTotal ? " (the first " + failed.length + " are listed)" : "") + ":"),
              genericTable(failed, { cols: ["code", "where", "why", "fix"] }),
              h("label", { class: "row" }, h("input", { type: "checkbox", checked: ack, "data-fk": "g2-ack", onchange: (e) => { ack = !!e.target.checked; draw(); } }), h("span", null, "I have read these checks and approve anyway. This is recorded with my approval."))
            )
          : null,
        h("div", { class: "actions row" }, h("button", { type: "button", class: "btn btn-primary", "data-fk": "g2-approve", disabled: busy || !!refuseWhy, onclick: approve }, busy ? "Approving…" : "Approve this spec"), navLink("#/run/" + id, "Back to the live run", "btn")),
        refuseWhy ? h("p", { class: "why-off" }, refuseWhy) : null
      )
    );
    keepFocus(body, () => setKids(body, kids));
  }
  load();
  return { unmount() { gone = true; } };
}
